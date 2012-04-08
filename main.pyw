import os
import sys
import time
import random
import itertools
import logging, logging.handlers
import types
import subprocess
import threading
import urllib, urllib2, socket
if os.name == 'nt':
    import win32com.client

import gtk
import gobject
import pango

import h5py


import pylab
import excepthook
from fileops import FileOps

# This provides debug info without having to run from a terminal, and
# avoids a stupid crash on Windows when there is no command window:
#if not sys.stdout.isatty():
    #sys.stdout = sys.stderr = open('debug.log','w',1)
    
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

class RunManager(object):
    def __init__(self):
        self.builder = gtk.Builder()
        self.builder.add_from_file('interface2.glade')
        
        self.window = self.builder.get_object('window1')
        self.notebook = self.builder.get_object('notebook1')
        self.output_view = self.builder.get_object('textview1')
        self.output_adjustment = self.output_view.get_vadjustment()
        self.output_buffer = self.output_view.get_buffer()
        self.use_globals_vbox = self.builder.get_object('use_globals_vbox')
        self.grouplist_vbox = self.builder.get_object('grouplist_vbox')
        self.no_file_opened = self.builder.get_object('label_no_file_opened')
        #self.chooser_h5_file = self.builder.get_object('chooser_h5_file')
        self.chooser_labscript_file = self.builder.get_object('chooser_labscript_file')
        self.vbox_runcontrol = self.builder.get_object('vbox_runcontrol')
        self.scrolledwindow_output = self.builder.get_object('scrolledwindow_output')
        self.chooser_output_directory = self.builder.get_object('chooser_output_directory')
        self.checkbutton_parse = self.builder.get_object('checkbutton_parse')
        self.checkbutton_make = self.builder.get_object('checkbutton_make')
        self.checkbutton_compile = self.builder.get_object('checkbutton_compile')
        self.checkbutton_view = self.builder.get_object('checkbutton_view')
        self.checkbutton_run = self.builder.get_object('checkbutton_run')
        self.outputscrollbar = self.scrolledwindow_output.get_vadjustment()
        self.global_store = self.builder.get_object('global_store')
        
        self.window.show()
        
        self.output_view.modify_font(pango.FontDescription("monospace 11"))
        self.output_view.modify_base(gtk.STATE_NORMAL, gtk.gdk.color_parse('black'))
        self.output_view.modify_text(gtk.STATE_NORMAL, gtk.gdk.color_parse('white'))
        
        self.window.set_icon_from_file(os.path.join('assets','icon.png'))
        self.builder.get_object('filefilter1').add_pattern('*.h5')
        self.builder.get_object('filefilter2').add_pattern('*.py')
        self.chooser_labscript_file.set_current_folder(r'C:\\user_scripts\\labscriptlib') # Will only happen if folder exists
        self.builder.connect_signals(self)
        #self.outputscrollbar.connect_after('value-changed', self.on_scroll)
        
        self.opentabs = []
        self.grouplist = []
        self.popped_out = False
        self.making_new_file = False
        self.parse = False
        self.make = False
        self.compile = False
        self.view = False
        self.run = False
        self.run_files = []
        self.aborted = False
        self.current_labscript_file = None
        self.text_mark = self.output_buffer.create_mark(None, self.output_buffer.get_end_iter())

        # Add timeout to watch for output folder changes when the day rolls over
        #gobject.timeout_add(1000, self.update_output_dir)
        self.current_day = time.strftime('\\%Y-%b\\%d')
        
        self.output('Ready\n')
    
    def on_window_destroy(self,widget):
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
            self.vbox_runcontrol.remove(self.scrolledwindow_output)
            screen = gtk.gdk.Screen()
            window = gtk.Window()
            window.add(self.scrolledwindow_output)
            window.connect('destroy',self.pop_out_in)
            window.resize(800,800)#self.window.get_size()[1])
            window.set_title('labscript run manager output')
            icon_theme = gtk.icon_theme_get_default()
            pb = icon_theme.load_icon('utilities-terminal', gtk.ICON_SIZE_MENU,0)
            window.set_icon(pb)
            window.show()
            self.builder.get_object('button_popout').hide()
            self.builder.get_object('button_popin').show()
        elif self.popped_out:
            self.popped_out = not self.popped_out
            window = self.scrolledwindow_output.get_parent()
            window.remove(self.scrolledwindow_output)
            self.vbox_runcontrol.pack_start(self.scrolledwindow_output)
            self.vbox_runcontrol.show()
            if not isinstance(widget,gtk.Window):
                window.destroy()
            self.builder.get_object('button_popout').show()
            self.builder.get_object('button_popin').hide()
        while gtk.events_pending():
            gtk.main_iteration()
        self.output_view.scroll_to_mark(self.text_mark,0)
    
    def toggle_parse(self,widget):
        self.parse = widget.get_active()
        if not self.parse:
            self.checkbutton_make.set_active(False)
            self.checkbutton_compile.set_active(False)
            self.checkbutton_view.set_active(False)
            self.checkbutton_run.set_active(False)
           
    def toggle_make(self,widget):
        self.make = widget.get_active()
        if self.make:
            self.checkbutton_parse.set_active(True)
        else:
            self.checkbutton_compile.set_active(False)
            self.checkbutton_view.set_active(False)
            self.checkbutton_run.set_active(False)        
    
    def toggle_compile(self,widget):
        self.compile = widget.get_active()
        if self.compile:
            self.checkbutton_parse.set_active(True)
            self.checkbutton_make.set_active(True)
        else:
            self.checkbutton_view.set_active(False)
            self.checkbutton_run.set_active(False) 
    
    def toggle_view(self,widget):
        self.view = widget.get_active()
        if self.view:
            self.checkbutton_parse.set_active(True)
            self.checkbutton_make.set_active(True)
            self.checkbutton_compile.set_active(True) 
            
    def toggle_run(self,widget):
        self.run = widget.get_active()
        if self.run:
            self.checkbutton_parse.set_active(True)
            self.checkbutton_make.set_active(True)
            self.checkbutton_compile.set_active(True) 
    
    def on_new_file_clicked(self,*args):
        chooser = gtk.FileChooserDialog(title='Save new HDF5 file',action=gtk.FILE_CHOOSER_ACTION_SAVE,
                                    buttons=(gtk.STOCK_CANCEL,gtk.RESPONSE_CANCEL,
                                               gtk.STOCK_SAVE,gtk.RESPONSE_OK))
        chooser.add_filter(self.builder.get_object('filefilter1'))
        chooser.set_default_response(gtk.RESPONSE_OK)
        chooser.set_do_overwrite_confirmation(True)
        # set this to the current location of the h5_chooser
        #if self.chooser_h5_file.get_current_folder():            
        #    chooser.set_current_folder(self.chooser_h5_file.get_current_folder())
            
        chooser.set_current_name('.h5')
        response = chooser.run()
        f = chooser.get_filename()
        d = chooser.get_current_folder()
        chooser.destroy()
        if response == gtk.RESPONSE_OK:
            success = file_ops.new_file(f)
            if not success:
                # Throw error
                pass
            
            # Append to Tree View
            self.global_store.prepend(None,(f,False,None,None,0))
            
    def on_global_toggle(self, cellrenderer_toggle, path):
        new_state = not cellrenderer_toggle.get_active()
        # Update the toggle button
        iter = self.global_store.get_iter(path)
        self.global_store.set_value(iter,1,new_state)
        
        # Toggle buttons that have been clicked are never in an inconsitent state
        self.global_store.set_value(iter,4,False)      
        
        
        # Does it have children? (aka a top level in the tree)
        if self.global_store.iter_has_child(iter):
            child_iter = self.global_store.iter_children(iter)
            while child_iter:
                self.global_store.set_value(child_iter,1,new_state)
                child_iter = self.global_store.iter_next(child_iter)
        # Is it a child?
        elif self.global_store.iter_depth(iter) > 0:
            # check to see if we should set the parent (top level) checkbox to an inconsistent state!
            
            # Get iter for top level
            parent_iter = self.global_store.iter_parent(iter)
            
            # Are the children in an inconsitent state?
            child_iter = self.global_store.iter_children(parent_iter)
            inconsistent = False
            first_state = self.global_store.get(child_iter,1)[0]
            while child_iter:
                if self.global_store.get(child_iter,1)[0] != first_state:
                    inconsistent = True             
                    break
                child_iter = self.global_store.iter_next(child_iter)
            
            self.global_store.set_value(parent_iter,4,inconsistent)
            
            # If we are not in an inconsistent state, make sure the parent checkbox matches the children
            if not inconsistent:
                self.global_store.set_value(parent_iter,1,new_state)
    
    def on_open_h5_file(self, *args):      
        chooser = gtk.FileChooserDialog(title='OpenHDF5 file',action=gtk.FILE_CHOOSER_ACTION_OPEN,
                                    buttons=(gtk.STOCK_CANCEL,gtk.RESPONSE_CANCEL,
                                               gtk.STOCK_SAVE,gtk.RESPONSE_OK))
        chooser.add_filter(self.builder.get_object('filefilter1'))
        chooser.set_default_response(gtk.RESPONSE_OK)
        chooser.set_do_overwrite_confirmation(True)
        # set this to the current location of the h5_chooser
        #if self.chooser_h5_file.get_current_folder():            
            #chooser.set_current_folder(self.chooser_h5_file.get_current_folder())
            
        chooser.set_current_name('.h5')
        response = chooser.run()
        f = chooser.get_filename()
        d = chooser.get_current_folder()
        chooser.destroy()
        if response == gtk.RESPONSE_OK:
            grouplist, success = file_ops.get_grouplist(f) 
            if success:
                # Append to Tree View
                parent = self.global_store.prepend(None,(f,False,None,None,0))            
                for name in grouplist:
                    self.global_store.prepend(parent,(name,False,None,None,0))    
    
logger = setup_logging()
excepthook.set_logger(logger)
if __name__ == "__main__":
    logger.info('\n\n===============starting===============\n')
    gtk.gdk.threads_init()
    
    run_manager = RunManager()
    file_ops = FileOps(run_manager)
    
    with gtk.gdk.lock:
        gtk.main()