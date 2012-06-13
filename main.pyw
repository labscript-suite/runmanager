import os
import sys
import time
import itertools
import logging, logging.handlers
import subprocess
import threading
import Queue
import urllib, urllib2, socket
if os.name == 'nt':
    import win32com.client

import gtk
import gobject
import pango

import h5py

import pylab
import excepthook
import shared_drive
import runmanager
import subproc_utils

shared_drive_prefix = shared_drive.get_prefix('monashbec')

# This provides debug info without having to run from a terminal, and
# avoids a stupid crash on Windows when there is no command window:
if not sys.stdout.isatty():
    sys.stdout = sys.stderr = open('debug.log','w',1)
    
if os.name == 'nt':
    # Make it not look so terrible (if icons and themes are installed):
    gtk.settings_get_default().set_string_property('gtk-icon-theme-name','gnome-human','')
    gtk.settings_get_default().set_string_property('gtk-theme-name','Clearlooks','')
    gtk.settings_get_default().set_string_property('gtk-font-name','ubuntu 11','')
    gtk.settings_get_default().set_long_property('gtk-button-images',False,'')

    # Have Windows 7 consider this program to be a separate app, and not
    # group it with other Python programs in the taskbar:
    import ctypes
    myappid = 'monashbec.labscript.runmanager.2-0' # arbitrary string
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except:
        pass

def setup_logging():
    logger = logging.getLogger('RunManager')
    handler = logging.handlers.RotatingFileHandler(r'runmanager.log', maxBytes=1024*1024*50)
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
    handler.setFormatter(formatter)
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    if sys.stdout.isatty():
        terminalhandler = logging.StreamHandler(sys.stdout)
        terminalhandler.setFormatter(formatter)
        terminalhandler.setLevel(logging.INFO) # only display info or higher in the terminal
        logger.addHandler(terminalhandler)
    else:
        # Prevent bug on windows where writing to stdout without a command
        # window causes a crash:
        sys.stdout = sys.stderr = open(os.devnull,'w')
    logger.setLevel(logging.DEBUG)
    return logger
    
class CellRendererClickablePixbuf(gtk.CellRendererPixbuf):
    __gsignals__    = { 'clicked' :
                        (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, (gobject.TYPE_STRING,)) , }
    def __init__(self):
        gtk.CellRendererPixbuf.__init__(self)
        self.set_property('mode', gtk.CELL_RENDERER_MODE_ACTIVATABLE)
    def do_activate(self, event, widget, path, background_area, cell_area, flags):
        self.emit('clicked', path)

class GroupTab(object):
    def __init__(self, app, filepath, name):
        self.name = name
        self.filepath = filepath
        self.app = app
        self.notebook = self.app.notebook
        self.vbox = app.use_globals_vbox
        
        self.builder = gtk.Builder()
        self.builder.add_from_file('grouptab.glade')
        self.toplevel = self.builder.get_object('tab_toplevel')
        self.label_groupname = self.builder.get_object('label_groupname')
        self.label_h5_path = self.builder.get_object('label_h5_path')
        self.global_liststore = self.builder.get_object('global_liststore')
        self.global_treeview = self.builder.get_object('global_treeview')
        self.tab = gtk.HBox()
        
        self.delete_column = self.builder.get_object('delete_column')
        delete_cell_renderer = CellRendererClickablePixbuf()
        self.delete_column.pack_end(delete_cell_renderer)
        self.delete_column.add_attribute(delete_cell_renderer,"stock-id",3)
        delete_cell_renderer.connect("clicked",self.on_delete_global)
        
        #get a stock close button image
        close_image = gtk.image_new_from_stock(gtk.STOCK_CLOSE, gtk.ICON_SIZE_MENU)
        image_w, image_h = gtk.icon_size_lookup(gtk.ICON_SIZE_MENU)
        
        #make the close button
        btn = gtk.Button()
        btn.set_relief(gtk.RELIEF_NONE)
        btn.set_focus_on_click(False)
        btn.add(close_image)
        
        #this reduces the size of the button
        style = gtk.RcStyle()
        style.xthickness = 0
        style.ythickness = 0
        btn.modify_style(style)
        
        self.tablabel = gtk.Label(self.name)
        self.tablabel.set_ellipsize(pango.ELLIPSIZE_END)
        self.tablabel.set_tooltip_text(self.name)
        self.tab.pack_start(self.tablabel)
        self.tab.pack_start(btn, False, False)
        self.tab.show_all()
        self.notebook.append_page(self.toplevel, tab_label = self.tab)                     
        
        self.notebook.set_tab_reorderable(self.toplevel,True)
        
        self.label_groupname.set_text(self.name)
        self.label_h5_path.set_text(self.filepath)
        
        self.notebook.show()

        #connect the close button
        btn.connect('clicked', self.on_closetab_button_clicked)

        self.builder.connect_signals(self)
        
        self.globals = []
        
        global_vars = runmanager.get_globalslist(self.filepath,self.name)
        
        for global_var in global_vars:
            value = runmanager.get_value(self.filepath,self.name,global_var)
            units = runmanager.get_units(self.filepath,self.name,global_var)
            self.global_liststore.append((global_var,value,units,"gtk-delete",True,True))
        
        # Add line to add a new global
        self.global_liststore.append(("<Click to add global>","","",None,False,False))
        
    def on_closetab_button_clicked(self,*args):
        # close tab
        self.close_tab()
        
        # change icon in group list view
        self.app.close_tab(self.filepath,self.name)
        
    def update_name(self,new_name):
        self.name = new_name
        self.label_groupname.set_text(self.name)
        self.tablabel.set_text(self.name)
    
    def close_tab(self):
        # Get the page number of the tab we wanted to close
        pagenum = self.notebook.page_num(self.toplevel)
        # And close it
        self.notebook.remove_page(pagenum)
    
    def on_edit_name(self,cellrenderer,path,new_text):
        iter = self.global_liststore.get_iter(path)
        image = self.global_liststore.get(iter,3)[0]       
        # If the delete image is None, then add a new global!
        if not image:
            if new_text == "<Click to add global>":
                return
                
            runmanager.new_global(self.filepath,self.name,new_text)

            self.global_liststore.insert_after(iter,("<Click to add global>","","",None,False,False))
            self.global_liststore.set(iter,0,new_text,3,"gtk-delete",4,True,5,True)
            
            # Handle weird bug where hitting "tab" after typing the name causes the treeview rendering to break
            # We fix the issue by first moving to the cell without entering edit mode, and then we set up a gobject timeout add
            # to enter editing mode immediately
            self.global_treeview.set_cursor(self.global_liststore.get_path(iter),self.global_treeview.get_column(2),False)
            gobject.timeout_add(1, self.global_treeview.set_cursor,self.global_liststore.get_path(iter),self.global_treeview.get_column(2),True)
            
                
        # We are editing an exiting global
        else:    
            name = self.global_liststore.get(iter,0)[0] 
            runmanager.rename_global(self.filepath, self.name, name, new_text)
            self.global_liststore.set_value(iter,0,new_text)
        
    def on_edit_value(self,cellrenderer,path,new_text):
        iter = self.global_liststore.get_iter(path)
        name = self.global_liststore.get(iter,0)[0]  
        runmanager.set_value(self.filepath, self.name, name, new_text)
        self.global_liststore.set_value(iter,1,new_text)
        # If the units box is empty, make it focussed and editable to encourage people to set units!
        units = self.global_liststore.get(iter,2)[0]  
        if not units:
            # Handle weird bug where hitting "tab" after typing the name causes the treeview rendering to break
            # We fix the issue by first moving to the cell without entering edit mode, and then we set up a gobject timeout add
            # to enter editing mode immediately
            self.global_treeview.set_cursor(path,self.global_treeview.get_column(3),False)
            gobject.timeout_add(1, self.global_treeview.set_cursor,path,self.global_treeview.get_column(3),True)
                
    
    def on_edit_units(self,cellrenderer,path,new_text):
        iter = self.global_liststore.get_iter(path)
        name = self.global_liststore.get(iter,0)[0]  
        runmanager.set_units(self.filepath, self.name, name, new_text)
        self.global_liststore.set_value(iter,2,new_text)
    
    def on_delete_global(self,cellrenderer,path):
        iter = self.global_liststore.get_iter(path)
        name = self.global_liststore.get(iter,0)[0] 
        image = self.global_liststore.get(iter,3)[0] 
        # If the image is None, we have the "<click here to add>" entry, ignore!
        if not image:
            return
        
        md = gtk.MessageDialog(self.app.window, 
                                gtk.DIALOG_DESTROY_WITH_PARENT, gtk.MESSAGE_WARNING,gtk.BUTTONS_OK_CANCEL,
                                "Are you sure? \n\nThis will remove the global variable ("+name+") from the h5 file ("+self.filepath+") and cannot be undone.")
        md.set_default_response(gtk.RESPONSE_CANCEL)
        result = md.run()
        md.destroy()
        if result == gtk.RESPONSE_OK:
            runmanager.delete_global(self.filepath,self.name,name)
            # Remove from the liststore
            self.global_liststore.remove(iter)
        
class RunManager(object):
    def __init__(self):
        self.builder = gtk.Builder()
        self.builder.add_from_file('interface.glade')
        
        self.window = self.builder.get_object('window1')
        self.notebook = self.builder.get_object('notebook1')
        self.output_view = self.builder.get_object('textview1')
        self.output_adjustment = self.output_view.get_vadjustment()
        self.output_buffer = self.output_view.get_buffer()
        self.use_globals_vbox = self.builder.get_object('use_globals_vbox')
        self.grouplist_vbox = self.builder.get_object('grouplist_vbox')
        self.no_file_opened = self.builder.get_object('label_no_file_opened')
        self.chooser_labscript_file = self.builder.get_object('chooser_labscript_file')
        self.output_page = self.builder.get_object('output_page')
        self.scrolledwindow_output = self.builder.get_object('scrolledwindow_output')
        self.chooser_output_directory = self.builder.get_object('chooser_output_directory')
        self.checkbutton_compile = self.builder.get_object('checkbutton_compile')
        self.checkbutton_view = self.builder.get_object('checkbutton_view')
        self.checkbutton_run = self.builder.get_object('checkbutton_run')
        self.toggle_shuffle = self.builder.get_object('toggle_shuffle')
        self.button_abort = self.builder.get_object('button_abort')
        self.outputscrollbar = self.scrolledwindow_output.get_vadjustment()
        self.group_store = self.builder.get_object('group_store')
        self.group_treeview = self.builder.get_object('group_treeview')
        self.current_h5_store = self.builder.get_object('current_h5_store')
        self.current_h5_file = self.builder.get_object('current_h5_file')
        self.add_column = self.builder.get_object('add_column')
        add_cell_renderer = CellRendererClickablePixbuf()
        self.add_column.pack_end(add_cell_renderer)
        self.add_column.add_attribute(add_cell_renderer,"stock-id",2)
        add_cell_renderer.connect("clicked",self.on_toggle_group)
        
        self.delete_column = self.builder.get_object('delete_column')
        delete_cell_renderer = CellRendererClickablePixbuf()
        self.delete_column.pack_end(delete_cell_renderer)
        self.delete_column.add_attribute(delete_cell_renderer,"stock-id",3)
        delete_cell_renderer.connect("clicked",self.on_delete_group)
        
        self.window.show()
        
        self.output_view.modify_font(pango.FontDescription("monospace 9"))
        self.output_view.modify_base(gtk.STATE_NORMAL, gtk.gdk.color_parse('black'))
        self.output_view.modify_text(gtk.STATE_NORMAL, gtk.gdk.color_parse('white'))
        
        self.window.set_icon_from_file(os.path.join('runmanager.svg'))
        self.builder.get_object('filefilter1').add_pattern('*.h5')
        self.builder.get_object('filefilter2').add_pattern('*.py')
        self.chooser_labscript_file.set_current_folder(r'C:\\user_scripts\\labscriptlib') # Will only happen if folder exists
        self.builder.connect_signals(self)
        
        self.opentabs = []
        self.grouplist = []
        self.popped_out = False
        self.making_new_file = False
        self.compile = False
        self.view = False
        self.run = False
        self.run_files = []
        self.aborted = False
        self.current_labscript_file = None
        self.text_mark = self.output_buffer.create_mark(None, self.output_buffer.get_end_iter())

        self.globals_path = None
        # Add timeout to watch for output folder changes when the day rolls over
        gobject.timeout_add(1000, self.update_output_dir)
        self.current_day = time.strftime('\\%Y-%b\\%d')
        
        # Start the compiler subprocess:
        self.to_child, self.from_child, child = subproc_utils.subprocess_with_queues('batch_compiler.py')
        
        # Start the loop that allows compilations to be queued up:
        self.compile_queue = Queue.Queue()
        self.compile_queue_thread = threading.Thread(target=self.compile_loop)
        self.compile_queue_thread.daemon = True
        self.compile_queue_thread.start()
        
        self.output('Ready\n')
    
    def on_window_destroy(self,widget):
        self.to_child.put(['quit',None])
        gtk.main_quit()
    
    def output(self,text,red=False):
        """Prints text to the output textbox and to stdout"""
        print text, 
        # Check if the scrollbar is at the bottom of the textview:
        scrolling = self.output_adjustment.value == self.output_adjustment.upper - self.output_adjustment.page_size
        # We need the initial cursor position so we know what range to make red:
        offset = self.output_buffer.get_end_iter().get_offset()
        # Insert the text at the end:
        self.output_buffer.insert(self.output_buffer.get_end_iter(), text)
        if red:
            start = self.output_buffer.get_iter_at_offset(offset)
            end = self.output_buffer.get_end_iter()
            # Make the text red:
            self.output_buffer.apply_tag(self.output_buffer.create_tag(foreground='red'),start,end)
            self.output_buffer.apply_tag(self.output_buffer.create_tag(weight=pango.WEIGHT_BOLD),start,end)

        # Automatically keep the textbox scrolled to the bottom, but
        # only if it was at the bottom to begin with. If the user has
        # scrolled up we won't jump them back to the bottom:
        if scrolling:
            self.output_view.scroll_to_mark(self.text_mark,0)
    
    def pop_out_in(self,widget):
        if not self.popped_out and not isinstance(widget,gtk.Window):
            self.popped_out = not self.popped_out
            self.output_page.remove(self.scrolledwindow_output)
            window = gtk.Window()
            window.add(self.scrolledwindow_output)
            window.connect('destroy',self.pop_out_in)
            window.resize(800,800)
            window.set_title('labscript run manager output')
            icon_theme = gtk.icon_theme_get_default()
            pixbuf = icon_theme.load_icon('utilities-terminal', gtk.ICON_SIZE_MENU,0)
            window.set_icon(pixbuf)
            window.show()
            self.output_page.hide()
        elif self.popped_out:
            self.popped_out = not self.popped_out
            window = self.scrolledwindow_output.get_parent()
            window.remove(self.scrolledwindow_output)
            self.output_page.add(self.scrolledwindow_output)
            self.output_page.show()
            if not isinstance(widget,gtk.Window):
                window.destroy()
            self.output_page.show()
        self.output_view.scroll_to_mark(self.text_mark,0)
    
    def on_scroll(self,*args):
        """Queue a redraw of the output on Windows, to prevent visual artifacts
           when the window isn't focused"""
        if os.name == 'nt':
            parent = self.scrolledwindow_output.get_parent()
            if isinstance(parent,gtk.Window):
                parent.queue_draw()
                
    def toggle_compile(self,widget):
        self.compile = widget.get_active()
        if not self.compile:
            self.checkbutton_view.set_active(False)
            self.checkbutton_run.set_active(False) 
    
    def toggle_view(self,widget):
        self.view = widget.get_active()
        if self.view:
            self.checkbutton_compile.set_active(True) 
            
    def toggle_run(self,widget):
        self.run = widget.get_active()
        if self.run:
            self.checkbutton_compile.set_active(True) 
    
    def on_new_file_clicked(self,*args):
        chooser = gtk.FileChooserDialog(title='Save new HDF5 file',action=gtk.FILE_CHOOSER_ACTION_SAVE,
                                    buttons=(gtk.STOCK_CANCEL,gtk.RESPONSE_CANCEL,
                                               gtk.STOCK_SAVE,gtk.RESPONSE_OK))
        chooser.add_filter(self.builder.get_object('filefilter1'))
        chooser.set_default_response(gtk.RESPONSE_OK)
        chooser.set_do_overwrite_confirmation(True) 
        
        if self.globals_path:     
            chooser.set_current_folder(self.globals_path)
        else:
            chooser.set_current_folder(self.chooser_labscript_file.get_current_folder())
        
        chooser.set_current_name('.h5')
        response = chooser.run()
        f = chooser.get_filename()
        d = chooser.get_current_folder()
        chooser.destroy()
        if response == gtk.RESPONSE_OK:
            runmanager.new_globals_file(f)
            # Append to Tree View
            parent = self.group_store.prepend(None,(f,False,"gtk-close",None,0,0,1))
            # Add editable option for adding!
            self.group_store.append(parent,("<Click to add group>",False,None,None,0,1,0))  
            
    def on_global_toggle(self, cellrenderer_toggle, path):
        new_state = not cellrenderer_toggle.get_active()
        # Update the toggle button
        iter = self.group_store.get_iter(path)
        self.group_store.set_value(iter,1,new_state)
        
        # Toggle buttons that have been clicked are never in an inconsitent state
        self.group_store.set_value(iter,4,False)      
        
        
        # Does it have children? (aka a top level in the tree)
        if self.group_store.iter_has_child(iter):
            child_iter = self.group_store.iter_children(iter)
            while child_iter:
                self.group_store.set_value(child_iter,1,new_state)
                child_iter = self.group_store.iter_next(child_iter)
        # Is it a child?
        elif self.group_store.iter_depth(iter) > 0:
            # check to see if we should set the parent (top level) checkbox to an inconsistent state!
            self.update_parent_checkbox(iter,new_state)
            
    def update_parent_checkbox(self,iter,child_state):
        # Get iter for top level
        parent_iter = self.group_store.iter_parent(iter)
        
        # Are the children in an inconsitent state?
        child_iter = self.group_store.iter_children(parent_iter)
        if child_iter:
            inconsistent = False
            first_state = self.group_store.get(child_iter,1)[0]
            while child_iter:
                # If state doesn't mathc the first one and the options are visible (not the child used to add a new group)
                # Then break out and set the state to inconsistent
                if self.group_store.get(child_iter,1)[0] != first_state and self.group_store.get(child_iter,6)[0]:
                    inconsistent = True             
                    break
                child_iter = self.group_store.iter_next(child_iter)
        # All the children have been deleted!
        else:
            inconsistent = False
            first_state = False
        
        self.group_store.set_value(parent_iter,4,inconsistent)
        
        # If we are not in an inconsistent state, make sure the parent checkbox matches the children
        if not inconsistent:
            if child_state == None:
                # Use the first_state!
                child_state = first_state
            self.group_store.set_value(parent_iter,1,child_state)
    
    def on_open_h5_file(self, *args):      
        chooser = gtk.FileChooserDialog(title='OpenHDF5 file',action=gtk.FILE_CHOOSER_ACTION_OPEN,
                                    buttons=(gtk.STOCK_CANCEL,gtk.RESPONSE_CANCEL,
                                               gtk.STOCK_OPEN,gtk.RESPONSE_OK))
        chooser.add_filter(self.builder.get_object('filefilter1'))
        chooser.set_default_response(gtk.RESPONSE_OK)
        # set this to the current location of the h5_chooser
        if self.globals_path:     
            chooser.set_current_folder(self.globals_path)
        else:
            chooser.set_current_folder(self.chooser_labscript_file.get_current_folder())
        
        response = chooser.run()
        f = chooser.get_filename()
        d = chooser.get_current_folder()
        chooser.destroy()
        if response == gtk.RESPONSE_OK:
            # Check that the file isn't already in the list!
            iter = self.group_store.get_iter_root()
            while iter:
                fp = self.group_store.get(iter,0)[0]
                if fp == f:
                    return
                iter = self.group_store.iter_next(iter)
            
            # open the file!            
            grouplist = runmanager.get_grouplist(f) 
            # Append to Tree View
            parent = self.group_store.prepend(None,(f,False,"gtk-close",None,0,0,1))            
            for name in grouplist:
                self.group_store.append(parent,(name,False,"gtk-add","gtk-remove",0,1,1))  
                            
            self.group_treeview.expand_row(self.group_store.get_path(parent),True) 
            # Add editable option for adding!
            add = self.group_store.append(parent,("<Click to add group>",False,None,None,0,1,0)) 
            self.group_treeview.set_cursor(self.group_store.get_path(add),self.group_treeview.get_column(2),True)

    def on_add_group(self,cellrenderer,path,new_text):
        # Find the filepath      
        iter = self.group_store.get_iter(path)
        parent = self.group_store.iter_parent(iter)
        filepath = self.group_store.get(parent,0)[0]
        image = self.group_store.get(iter,2)[0]
        
        # If the +/x image is none, then this is an entry for adding global groups
        if not image:        
            # Ignore if theyhave clicked in the box, and then out!
            if new_text == "<Click to add group>":
                return
        
                  
            runmanager.new_group(filepath, new_text)
            self.group_store.insert_before(parent,iter,(new_text,True,"gtk-close","gtk-delete",0,1,1))
            # Update parent checkbox state
            self.update_parent_checkbox(iter,True)
            
            # Automatically open this new group!
            self.opentabs.append(GroupTab(self,filepath,new_text))
        
        else:
            # We want to rename an existing group!
            old_name = self.group_store.get(iter,0)[0]
            runmanager.rename_group(filepath,old_name,new_text)
            self.group_store.set_value(iter,0,new_text)
            
            # TODO: Update the names in the open groups!
            for group in self.opentabs:
                if group.filepath == filepath and group.name == old_name:
                    group.update_name(new_text)
                    break
        
    def on_delete_group(self,cellrenderer,path):
        iter = self.group_store.get_iter(path)
        
        image = self.group_store.get(iter,2)[0]
        # If the image is None, we have the <"click here to add>" entry, so ignore!
        if not image:
            return
        
        # If we have a top level row (a h5 file), then do nothing!
        if self.group_store.iter_depth(iter) == 0:
            return
        
        # Else we want to delete a group!
        else:
            # Find the parent filename
            parent = self.group_store.iter_parent(iter)
            filepath = self.group_store.get(parent,0)[0]

            # get the group name
            group_name = self.group_store.get(iter,0)[0]
            
            md = gtk.MessageDialog(self.window, 
                                    gtk.DIALOG_DESTROY_WITH_PARENT, gtk.MESSAGE_WARNING, 
                                    buttons =(gtk.BUTTONS_OK_CANCEL),
                                    message_format = "Are you sure? This will remove the group ("+group_name+") from the h5 file ("+filepath+") and cannot be undone.")
            md.set_default_response(gtk.RESPONSE_CANCEL)
            result = md.run()
            md.destroy()
            if result == gtk.RESPONSE_OK:
                runmanager.delete_group(filepath,group_name)
                
                # Remove the row from the treeview
                self.group_store.remove(iter)
                
                # Update state of parent checkbox
                self.update_parent_checkbox(iter,None)
                
                # TODO: Close the open group tab!
                for gt in self.opentabs:
                    if gt.filepath == filepath and gt.name == group_name:
                        gt.close_tab()
                        self.opentabs.remove(gt)
                        break
    
    def on_toggle_group(self,cellrenderer,path):
        iter = self.group_store.get_iter(path)  

        # TODO: Handle clicking the +/x button on a toplevel entry (a h5 file)
        #       This should open all groups if not all are open, or close all groups if all are open
        
        
        # If we are closing a h5 file, close all open tabs and remove from the group list
        # If we have a top level row (a h5 file), then do nothing!
        if self.group_store.iter_depth(iter) == 0:
            filepath = self.group_store.get(iter,0)[0]
            iter2 = self.group_store.iter_children(iter)
            while iter2:
                gn = self.group_store.get(iter2,0)[0]
                # remove the tab from the list!
                for gt in self.opentabs:
                    if gt.filepath == filepath and gt.name == gn:
                        gt.close_tab()
                        self.opentabs.remove(gt)
                        break
                # Remove the entry from the group list
                iter2 = self.group_store.iter_next(iter2)
            # remove the h5 file from the group list
            self.group_store.remove(iter)
        else:                
            image = self.group_store.get(iter,2)[0]
            name = self.group_store.get(iter,0)[0]
            filepath = self.group_store.get(self.group_store.iter_parent(iter),0)[0]
            
            if image == "gtk-add":
                self.group_store.set_value(iter,2,"gtk-close")
                self.opentabs.append(GroupTab(self,filepath,name))
            elif image == "gtk-close":
                # update the icon in the group list
                self.group_store.set_value(iter,2,"gtk-add")
                # close the tab!
                # Note we don't use the close tab functionality as we already have the iterator.
                # Instead we just find the GroupTab, remove it from the list, and ask it to delete itself
                # (calling gt.close_tab does not invoke the close_tab function in the RunManager class 
                for gt in self.opentabs:
                    if gt.filepath == filepath and gt.name == name:
                        gt.close_tab()
                        self.opentabs.remove(gt)
                        break
            else:
                # no image, which means the "<click here to add"> line, so return!
                return
    
    # This function is poorly named. It actually only updates the +/x icon in the group list!   
    # This function is called by the GroupTab class to clean up the state of the group treeview
    def close_tab(self,filepath,group_name):
        # find entry in treemodel
        iter = self.group_store.get_iter_root()
        
        while iter:
            fp = self.group_store.get(iter,0)[0]
            if fp == filepath:
                # find group entry
                iter2 = self.group_store.iter_children(iter)
                while iter2:
                    gn = self.group_store.get(iter2,0)[0]
                    if gn == group_name:
                        # Change icon
                        self.group_store.set_value(iter2,2,"gtk-add")
                        # remove the tab from the list!
                        for gt in self.opentabs:
                            if gt.filepath == filepath and gt.name == group_name:
                                self.opentabs.remove(gt)
                                break
                        break
                    iter2 = self.group_store.iter_next(iter2)
                break
            iter = self.group_store.iter_next(iter)
    
    def labscript_file_selected(self,chooser):
        filename = chooser.get_filename()
        self.mk_output_dir(filename)
        self.current_labscript_file = filename
    
    def update_output_dir(self):
        if time.strftime('\\%Y-%b\\%d') != self.current_day:        
            # Update output dir - We do this outside of a thread, otherwise we have to initialise the win32 library in each thread
            # See: http://devnulled.com/com-objects-and-threading-in-python/
            # Caling this will update the output folder if it is on the share drive, and it is set for a previous day 
            # eg run manager was left running overnight, a new sequence is compiled without changing labscript files,
            # This will update the output dir.
            self.current_day = time.strftime('\\%Y-%b\\%d')
            if self.chooser_labscript_file.get_filename():
                self.mk_output_dir(self.chooser_labscript_file.get_filename())
        return True
    
    # Makes the output dir for a labscript file
    def mk_output_dir(self,filename):
        # If the output dir has been changed since we last did this, then just pass!
        if hasattr(self,'new_path') and self.new_path != self.chooser_output_directory.get_filename():
            print 'mk_output_dir: ignoring request to make new output dir on the share drive'
            print self.chooser_output_directory.get_filename()
            if hasattr(self,'new_path'):
                print self.new_path
            print time.asctime(time.localtime())
            self.globals_path = None
            return
    
        try:
            # If we aren't in windows, don't bother!
            if os.name != 'nt':
                self.globals_path = None
                return
        
            # path is Z:\Experiments\<lab>\<labscript>\<year>-<month>\<day>\            
            def grouper(n, iterable, fillvalue=None):
                "grouper(3, 'ABCDEFG', 'x') --> ABC DEF Gxx"
                args = [iter(iterable)] * n
                return itertools.izip_longest(fillvalue=fillvalue, *args)
            network = win32com.client.Dispatch('WScript.Network')            
            drives = network.EnumNetworkDrives()
            result = dict(grouper(2, drives))
            
            new_path = ''
            
            acceptable_paths = ['\\\\becnas.physics.monash.edu\\monashbec',
                                '\\\\becnas.physics.monash.edu.au\\monasbec',
                                '\\\\becnas.physics.monash.edu.au\\monasbec\\',
                                '\\\\becnas.physics.monash.edu\\monasbec\\',
                                '\\\\becnas\\monasbec',
                                '\\\\becnas\\monasbec\\',
                                '\\\\becnas.physics\\monasbec',
                                '\\\\becnas.physics\\monasbec\\']
            
            for drive_letter,network_path in result.items():
                if network_path in acceptable_paths:
                    new_path += drive_letter+'\\Experiments'
                    break   
            
            # If no mapping was found
            if new_path == '':
                # leave the output dir as is
                self.globals_path = None
                return
            
            # work out the lab
            server_name = self.builder.get_object('entry_server').get_text()
            if server_name == 'localhost':
                server_name = socket.gethostname()             
                
            if 'g07a' in server_name:
                new_path += '\\spinorbec'
            elif 'g46' in server_name:
                if 'krb' in server_name:
                    new_path += '\\dual_species_lab\\krb'
                elif 'narb' in server_name:
                    new_path += '\\dual_species_lab\\narb'
                else:
                    new_path += '\\dual_species_lab\\other'
            else:
                new_path += '\\other'
                
            new_path += '\\'+os.path.basename(filename)[:-3]+'\\'
            new_path2 = new_path
            # get year, month, day
            new_path += time.strftime('%Y-%b\\%d')
            print new_path
            os.makedirs(new_path)
        except OSError, e:  
            print 'mk_output_dir: ignoring exception, folder probably already exists'
            print self.chooser_output_directory.get_filename()
            if hasattr(self,'new_path'):
                print self.new_path
            print time.asctime(time.localtime())
            print e.message
            self.globals_path = None
        except Exception, e:
            print type(e)
            self.globals_path = None
            raise
        print 'aa'
        if os.path.exists(new_path):     
            print 'mk_output_dir: updating output chooser'
            self.chooser_output_directory.set_current_folder(new_path)
            #self.chooser_h5_file.set_current_folder(new_path2)
            
            # Update storage of the path so we can check each time we hit engage, whether we should check to see if the 
            # output dir needs to be advanced to todays folder (if run manager is left on overnight)
            #
            # This folder is only stored *IF* we have updated the out dir via this function. Thus function only updates the
            # out dir if the outdir has not been changed since the last time this function ran, and if the share drive is mapped and accessible on windows.
            
            self.new_path = new_path
            self.globals_path = new_path2
        else:
            self.globals_path = None
    
    def labscript_selection_changed(self, chooser):
        """A hack to allow a file which is deleted and quickly recreated to not
        be unselected by the file chooser widget. This is the case when Vim saves a file,
        so this saves Vim users from reselecting the labscript file constantly."""
        if not chooser.get_filename():
            def keep_current_filename(filename):
                chooser.select_filename(filename)
            if self.current_labscript_file:
                gobject.timeout_add(100, keep_current_filename,self.current_labscript_file)
                              
    def on_keypress(self, widget, event):
        if gtk.gdk.keyval_name(event.keyval) == 'F5':
            self.engage()
    
    def engage(self, *args):
        try:
            if self.compile:
                sequenceglobals, shots = self.parse_globals()
                labscript_file, run_files = self.make_h5_files(sequenceglobals, shots)
                self.compile_queue.put([labscript_file,run_files])
        except Exception as e:
            self.output(str(e)+'\n',red=True)
            self.aborted = True
            
    def compile_loop(self):
        work_was_done = False
        while True:
            try:
                labscript_file, run_files = self.compile_queue.get(timeout=0.5)
                with gtk.gdk.lock:
                    self.button_abort.set_sensitive(True)
            except Queue.Empty:
                if work_was_done:
                    work_was_done = False
                    with gtk.gdk.lock:
                        if self.aborted:
                            self.output('Compilation aborted\n',red=True)
                            self.aborted = False
                        else:
                            self.output('Ready\n')
                            if self.view:
                                subprocess.Popen([sys.executable,'-m','runviewer.qtrunviewer',last_run])
                    self.button_abort.set_sensitive(False)
                continue
            if not self.aborted:
                last_run = self.compile_labscript(labscript_file, run_files)
                work_was_done = True
                                
    def on_abort_clicked(self, *args):
        self.aborted = True
    
    def get_active_groups(self):
        active_groups = {} # goupname: filepath
        for toplevel in self.group_store:
            filepath = toplevel[0]
            for row in toplevel.iterchildren():
                group_name = row[0]
                group_active = row[1]
                is_a_group = row[3] # ignore the <Click to add new group> row
                if group_active and is_a_group:
                    if group_name in active_groups:
                        raise ValueError('There are two active groups named %s. Active groups must have unique names to be used together.'%group_name)
                    active_groups[group_name] = filepath
        return active_groups
                    
    def parse_globals(self):
        active_groups = self.get_active_groups()
        sequence_globals = runmanager.get_sequence_globals(active_groups)
        shots = runmanager.get_shot_globals(sequence_globals)
        return sequence_globals, shots
        
    def make_h5_files(self, sequence_globals, shots):
        labscript_file = self.chooser_labscript_file.get_filename()
        if not labscript_file:
            raise Exception('Error: No labscript file selected')
        output_folder = self.chooser_output_directory.get_filename()
        if not output_folder:
            raise Exception('Error: No output folder selected')
        sequence_id = runmanager.generate_sequence_id(labscript_file)
        shuffle = self.toggle_shuffle.get_active()
        run_files = runmanager.make_run_files(output_folder, sequence_globals, shots, sequence_id, shuffle)
        return labscript_file, run_files

    def compile_labscript(self, labscript_file, run_files):
        for run_file in run_files:
            self.to_child.put(['compile',[labscript_file,run_file]])
            while True:
                signal,data = self.from_child.get()
                if signal == 'stdout':
                    with gtk.gdk.lock:
                        self.output(data)
                elif signal == 'stderr':
                    with gtk.gdk.lock:
                        self.output(data,red=True)
                elif signal == 'done':
                    success = data
                    break
            if self.aborted or not success:
                break
            if self.run:
                self.submit_job(run_file)
            if self.aborted:
                break
        return run_file
                
    def submit_job(self, run_file):
        server = self.builder.get_object('entry_server').get_text()
        port = 42517
        # Workaround to force python not to use IPv6 for the request:
        address  = socket.gethostbyname(server)
        run_file = run_file.replace(shared_drive_prefix,'Z:/').replace('/','\\')
        with gtk.gdk.lock:
            self.output('Submitting run file %s.\n'%os.path.basename(run_file))
        params = urllib.urlencode({'filepath': run_file})
        try:
            response = urllib2.urlopen('http://%s:%d'%(address,port), params, 2).read()
            if 'added successfully' in response:
                with gtk.gdk.lock:
                    self.output(response)
            else:
                raise Exception(response)
        except Exception as e:
            with gtk.gdk.lock:
                self.output('Couldn\'t submit job to control server: %s\n'%str(e),red=True)
            self.aborted = True
    
logger = setup_logging()
excepthook.set_logger(logger)
if __name__ == "__main__":
    logger.info('\n\n===============starting===============\n')
    gtk.gdk.threads_init()
    
    app = RunManager()
    
    with gtk.gdk.lock:
        gtk.main()
