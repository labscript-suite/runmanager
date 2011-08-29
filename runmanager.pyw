#!/usr/bin/env python

import os
import random

import gtk
import pango
import h5py


if os.name == 'nt':
    # Have Windows 7 consider this program to be a separate app, and not
    # group it with other Python programs in the taskbar:
    import ctypes
    myappid = 'monashbec.labscript.runmanager.1-0' # arbitrary string
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except:
        pass

funny_units = ['attoparsecs',
               'light-nanoseconds',
               'metric inches',
               'cars',
               'buses',
               'blocks',
               'barns',
               'nanoacres',
               'bags of sugar',
               'elephants',
               'Jupiters',
               'jiffies',
               'microfortninghts',
               'dog years',
               'galactic years',
               'binary radians',
               'tons of TNT',
               'Sagans',
               'nibbles',
               'proof',
               'banana equivalent doses',
               'on the Richter scale',
               'GigaTorr',
               'furlongs per fortnight',
               'beard seconds',
               'dozen',
               'o\'clock',
               'baud',
               'metabolic equivalents',
               'carats',
               'Gillettes',
               'horsepower',
               'man-months',
               'MegaFonzies', 
               'milliHelens',
               'smidgens',
               'tablespoons',
               'pieces of string',
               'barrels of monkeys']
               
class FileOps:
    
    def handle_error(self,e):
        # for the moment:
        raise e
        
    def new_file(self,filename):
        try:
            with h5py.File(filename,'w') as f:
                f.create_group('globals')
                return True
        except Exception as e:
            self.handle_error(e)
            return False
    
    def get_grouplist(self,filename):
        try:
            with h5py.File(filename,'r') as f:
                grouplist = f['globals']
                # File closes after this function call, so have to
                # convert the grouplist generator to a list of strings
                # before its file gets dereferenced:
                return list(grouplist), True
        except Exception as e:
            self.handle_error(e)
            return [], False
        
    def new_group(self,filename,groupname):
        try:
            with h5py.File(filename,'a') as f:
                group = f['globals'].create_group(groupname)
                units = group.create_group('units')
                return True
        except Exception as e:
            self.handle_error(e)
            return False
    
    def rename_group(self,filename,oldgroupname,newgroupname):
        if oldgroupname == newgroupname:
            # No rename!
            return
        try:
            with h5py.File(filename,'a') as f:
                f.copy(f['globals'][oldgroupname], '/globals/%s'%newgroupname)
                del f['globals'][oldgroupname]
                return True
        except Exception as e:
            self.handle_error(e)
            return False
    
    def delete_group(self,filename,groupname):
        try:
            with h5py.File(filename,'a') as f:
                del f['globals'][groupname]
                return True
        except Exception as e:
            self.handle_error(e)
            return False
    
    def get_globalslist(self,filename,groupname):
        try:
            with h5py.File(filename,'r') as f:
                group = f['globals'][groupname]
                # File closes after this function call, so have to convert
                # the attrs to a dict before its file gets dereferenced:
                return dict(group.attrs), True
        except Exception as e:
            self.handle_error(e)
            return {},{}, False
    
    def new_global(self,filename,groupname,globalname):
        try:
            with h5py.File(filename,'a') as f:
                group = f['globals'][groupname]
                group.attrs[globalname] = ''
                return True
        except Exception as e:
            self.handle_error(e)
            return False
    
    def rename_global(self,filename,groupname,oldglobalname,newglobalname):
        pass
    
    def get_value(self,filename,groupname,globalname):
        try:
            with h5py.File(filename,'r') as f:
                value = f['globals'][groupname].attrs[globalname]
                return value, True
        except Exception as e:
            self.handle_error(e)
            return None, False
    
    def set_value(self,filename,groupname,globalname, value):
        try:
            with h5py.File(filename,'a') as f:
                f['globals'][groupname].attrs[globalname] = value
                return True
        except Exception as e:
            self.handle_error(e)
            return False
    
    def get_units(self,filename,groupname,globalname):
        try:
            with h5py.File(filename,'r') as f:
                value = f['globals'][groupname]['units'].attrs[globalname]
                return value, True
        except Exception as e:
            self.handle_error(e)
            return None, False
    
    def set_units(self,filename,groupname,globalname, units):
        try:
            with h5py.File(filename,'a') as f:
                f['globals'][groupname]['units'].attrs[globalname] = units
                return True
        except Exception as e:
            self.handle_error(e)
            return False
    
    def delete_global(self,filename,groupname,globalname):
        try:
            with h5py.File(filename,'a') as f:
                group = f['globals'][groupname]
                del group.attrs[globalname]
                return True
        except Exception as e:
            self.handle_error(e)
            return False
    
    
class Global(object):
    def __init__(self, group, name):
        
        self.group = group
        self.table = self.group.global_table
        n_globals = len(self.group.globals)
        self.filepath = self.group.filepath
        
        self.builder = gtk.Builder()
        self.builder.add_from_file('global.glade')
        
        self.entry_name = self.builder.get_object('entry_name')
        self.entry_units = self.builder.get_object('entry_units')
        self.label_name = self.builder.get_object('label_name')
        self.label_units = self.builder.get_object('label_units')
        self.entry_value = self.builder.get_object('entry_value')
        self.vbox_value = self.builder.get_object('vbox_value')
        self.hbox_buttons = self.builder.get_object('hbox_buttons')
        self.vbox_buttons = self.builder.get_object('vbox_buttons')
        self.toggle_edit = self.builder.get_object('toggle_edit')
        self.button_remove = self.builder.get_object('button_remove')
        self.vbox_name = self.builder.get_object('vbox_name')
        self.vbox_units = self.builder.get_object('vbox_units')
        
        for widget in [self.entry_name,self.label_name,self.entry_value]:
            widget.modify_font(pango.FontDescription("monospace 10"))
        
        self.label_name.set_text(name)
        value, success = file_ops.get_value(self.filepath, self.group.name, name)
        units, success = file_ops.get_units(self.filepath, self.group.name, name)
        self.entry_value.set_text(str(value))
        self.label_units.set_text(units)
        self.toggle_edit.set_active(False)
                
        print n_globals + 1
        self.insert_at_position(n_globals + 1)
        
        self.builder.connect_signals(self)
        
        self.editing = False
        self.undo_backup = ''
        
    def insert_at_position(self,n):
        self.table.attach(self.vbox_name,0,1,n,n+1)
        self.table.attach(self.vbox_value,1,2,n,n+1)
        self.table.attach(self.vbox_units,2,3,n,n+1)
        self.table.attach(self.vbox_buttons,3,4,n,n+1)
        
        self.vbox_name.show()
        self.vbox_units.show()
        self.vbox_buttons.show()
        self.vbox_value.show()
    
    def focus_in(self, *args):
        self.undo_backup = self.entry_value.get_text()
    
    def value_changed(self, *args):
        success = file_ops.set_value(self.filepath,self.group.name,
                                     self.label_name.get_text(), 
                                     self.entry_value.get_text())
        
    def value_keypress(self, widget, event):
        if event.keyval == 65307: # escape
            self.entry_value.set_text(self.undo_backup)
            self.group.toggle_group_name_edit.grab_focus()
        elif event.keyval == 65293 or event.keyval == 65421: #enter
            self.group.toggle_group_name_edit.grab_focus()
        elif event.keyval == 122 and event.state & gtk.gdk.CONTROL_MASK:
            self.entry_value.set_text(self.undo_backup)
            
    def on_edit_toggled(self,widget):
        if widget.get_active():
            self.entry_units.set_text(self.label_units.get_text())
            self.entry_name.set_text(self.label_name.get_text())
            self.entry_name.show()
            self.entry_units.show()
            self.button_remove.show()
            self.label_name.hide()
            self.label_units.hide()
            self.entry_name.select_region(0, -1)
            self.entry_name.grab_focus()
        else:
            self.entry_name.hide()
            self.entry_units.hide()
            self.button_remove.hide()
            self.label_units.set_text(self.entry_units.get_text())
            self.label_name.set_text(self.entry_name.get_text())
            self.label_name.show()
            self.label_units.show()
    
       
    def on_entry_keypress(self,widget,event):
        if event.keyval == 65307: #escape
            self.entry_units.set_text(self.label_units.get_text())
            self.entry_name.set_text(self.label_name.get_text())
            self.toggle_edit.set_active(False)
        elif event.keyval == 65293 or event.keyval == 65421: #enter
            self.toggle_edit.set_active(False)
        elif event.keyval == 122 and event.state & gtk.gdk.CONTROL_MASK:
            if widget is self.entry_units:
                self.entry_units.set_text(self.label_units.get_text())
            elif widget is self.entry_name:
                self.entry_name.set_text(self.label_name.get_text())
    
    def focus_out(self, widget, event):
        print event
        for thing in dir(event):
            print thing
        
        print event.type
        print event.window
        print event.get_coords()
        print event.get_state()    
        print self.entry_units.is_focus()
        print self.entry_name.is_focus()
        print self.entry_units.has_focus()
        print self.entry_name.has_focus()
        
#        if not (self.entry_units.is_focus() or self.entry_name.is_focus()):
#            self.toggle_edit.set_active(False)
            
    def on_remove_clicked(self,widget):
        # TODO "Are you sure? This will remove the global from the h5
        # file and cannot be undone."
        success = file_ops.delete_global(self.filepath,self.group.name,
                                         self.entry_name.get_text())
        self.table.remove(self.vbox_name)
        self.table.remove(self.vbox_value)
        self.table.remove(self.vbox_units)
        self.table.remove(self.vbox_buttons)
        self.group.globals.remove(self)
        

                
class GroupTab(object):
    
    def __init__(self, runmanager, filepath, name):
        self.name = name
        self.filepath = filepath
        self.runmanager = runmanager
        self.notebook = self.runmanager.notebook
        self.vbox = runmanager.use_globals_vbox
        
        self.builder = gtk.Builder()
        self.builder.add_from_file('grouptab.glade')
        self.toplevel = self.builder.get_object('tab_toplevel')
        self.global_table = self.builder.get_object('global_table')
        self.scrolledwindow_globals = self.builder.get_object('scrolledwindow_globals')
        self.label_groupname = self.builder.get_object('label_groupname')
        self.entry_groupname = self.builder.get_object('entry_groupname')
        self.entry_new_global = self.builder.get_object('entry_new_global')
        self.label_h5_path = self.builder.get_object('label_h5_path')
        self.toggle_group_name_edit = self.builder.get_object('toggle_group_name_edit')
        self.adjustment = self.scrolledwindow_globals.get_vadjustment()
        self.tab = gtk.HBox()
        
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
                     
        self.checkbox = gtk.CheckButton(self.name)
        self.vbox.pack_start(self.checkbox,expand=False,fill=False)
        self.vbox.show_all()
        self.notebook.set_tab_reorderable(self.toplevel,True)
        
        self.label_groupname.set_text(self.name)
        self.entry_groupname.set_text(self.name)
        self.label_h5_path.set_text(self.filepath)
        
        self.notebook.show()

        #connect the close button
        btn.connect('clicked', self.on_closetab_button_clicked)

        self.builder.connect_signals(self)
        
        self.globals = []
        
        global_vars, success = file_ops.get_globalslist(self.filepath,self.name)
        for global_var in global_vars:
            self.globals.append(Global(self, global_var))
        
    def on_closetab_button_clicked(self, *args):
        # Get the page number of the tab we wanted to close
        pagenum = self.notebook.page_num(self.toplevel)
        # And close it
        self.notebook.remove_page(pagenum)
        self.checkbox.destroy()
        self.runmanager.opentabs.remove(self)
    
    def changename(self, newname):
        success = file_ops.rename_group(self.filepath, self.name, newname)
        if success:
            oldname = self.name
            self.name = newname
            self.label_groupname.set_text(self.name)
            self.tablabel.set_text(self.name)
            self.checkbox.get_children()[0].set_text(self.name) 
            # Is this global group open in the import tab?
            if self.runmanager.chooser_h5_file.get_filename() == self.filepath:
                # Better change the name there too:
                for entry in self.runmanager.grouplist:
                    if entry.name == oldname:
                        entry.name = self.name
                        entry.label_name.set_text(self.name)
                        entry.entry_name.set_text(self.name)
            
    def on_groupname_edit_toggle(self,widget):
        if widget.get_active():
            self.entry_groupname.set_text(self.name)
            self.entry_groupname.show()
            self.label_groupname.hide()
            self.entry_groupname.select_region(0, -1)
            self.entry_groupname.grab_focus()
        else:
            self.changename(self.entry_groupname.get_text())
            self.entry_groupname.hide()
            self.label_groupname.show() 
    
    def focus_out(self,widget):
        self.toggle_group_name_edit.set_active(False)
        
    def on_entry_keypress(self,widget,event):
        if event.keyval == 65307: #escape
            widget.set_text(self.label_groupname.get_text())
            self.toggle_group_name_edit.set_active(False)
        elif event.keyval == 65293 or event.keyval == 65421: #enter
            self.toggle_group_name_edit.set_active(False)
        elif event.keyval == 122 and event.state & gtk.gdk.CONTROL_MASK:
            widget.set_text(self.label_groupname.get_text())
        
    def on_new_global_clicked(self,button):
        print 'new global clicked!'
        name = self.entry_new_global.get_text()
        self.adjustment.value = self.adjustment.upper  
        success = file_ops.new_global(self.filepath, self.name, name)
        if success:
            success = file_ops.set_value(self.filepath, self.name, 
                                         name, int(1000*random.random() - 500))
        if success:
            success = file_ops.set_units(self.filepath, self.name, 
                                         name,random.choice(funny_units))
        if success:
            self.globals.append(Global(self,name)) 
            self.entry_new_global.set_text('')   


class GroupListEntry(object):
    def __init__(self,runmanager,filepath,name):
        self.runmanager = runmanager
        self.vbox = self.runmanager.grouplist_vbox
        n_groups = len(self.runmanager.grouplist)
        self.filepath = filepath
        self.name = name
        
        self.builder = gtk.Builder()
        self.builder.add_from_file('grouplistentry.glade')
        
        self.toplevel = self.builder.get_object('toplevel')
        self.entry_name = self.builder.get_object('entry_name')
        self.label_name = self.builder.get_object('label_name')
        self.hbox_buttons = self.builder.get_object('hbox_buttons')
        self.toggle_edit = self.builder.get_object('toggle_edit')
        self.button_import = self.builder.get_object('button_remove')
        self.button_remove = self.builder.get_object('button_remove')
        
        self.label_name.set_text(name)
        
        self.vbox.pack_start(self.toplevel)
        self.toplevel.show()
        
        self.builder.connect_signals(self)
        
    def on_edit_toggled(self,widget):
        if widget.get_active():
            self.entry_name.set_text(self.label_name.get_text())
            self.entry_name.show()
            self.label_name.hide()
            self.entry_name.select_region(0, -1)
            self.entry_name.grab_focus()
        else:
            self.entry_name.hide()
            self.changename(self.entry_name.get_text())
            self.label_name.show()
    
    def focus_out(self,*args):
        self.toggle_edit.set_active(False)
                
    def changename(self, newname):
        success = file_ops.rename_group(self.filepath, self.name, newname)
        if success:
            oldname = self.name
            self.name = newname
            self.label_name.set_text(self.name)
            # Is this global group open in a tab?
            if self.runmanager.chooser_h5_file.get_filename() == self.filepath:
                # Better change the name there too:
                for tab in self.runmanager.opentabs:
                    if tab.name == oldname and tab.filepath == self.filepath:
                        tab.name = self.name
                        tab.label_groupname.set_text(self.name)
                        tab.entry_groupname.set_text(self.name) 
                        tab.tablabel.set_text(self.name) 
                        tab.checkbox.get_children()[0].set_text(self.name) 
       
    def on_entry_keypress(self,widget,event):
        if event.keyval == 65307: #escape
            self.entry_name.set_text(self.label_name.get_text())
            self.toggle_edit.set_active(False)
        elif event.keyval == 65293 or event.keyval == 65421: #enter
            self.toggle_edit.set_active(False)
        elif event.keyval == 122 and event.state & gtk.gdk.CONTROL_MASK:
            self.entry_name.set_text(self.label_name.get_text())
        
    def on_remove_clicked(self,widget):
        # TODO "Are you sure? This will remove the group from the h5
        # file and cannot be undone."
        success = file_ops.delete_group(self.filepath,self.name)
        self.toplevel.destroy()
        self.runmanager.grouplist.remove(self)
        # is the tab open? If so, close it:
        for tab in self.runmanager.opentabs:
            if tab.name == self.name and tab.filepath == self.filepath:
                print 'closing etc!'
                tab.on_closetab_button_clicked()
        
    def on_import_clicked(self,widget):
        self.runmanager.opentabs.append(GroupTab(self.runmanager, self.filepath, self.name))
            
            
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
        self.chooser_h5_file = self.builder.get_object('chooser_h5_file')
        self.window.show_all()
        
        area=self.builder.get_object('drawingarea1')
        pixbuf=gtk.gdk.pixbuf_new_from_file(os.path.join('assets','grey.png'))
        pixmap, mask=pixbuf.render_pixmap_and_mask()
        area.window.set_back_pixmap(pixmap, False)
        self.output_view.modify_font(pango.FontDescription("monospace 10"))
        self.output_view.modify_base(gtk.STATE_NORMAL, gtk.gdk.color_parse('black'))
        self.output_view.modify_text(gtk.STATE_NORMAL, gtk.gdk.color_parse('white'))

        self.window.set_icon_from_file(os.path.join('assets','icon.png'))
        self.builder.get_object('filefilter1').add_pattern('*.h5')
        self.builder.get_object('filefilter2').add_pattern('*.py')
        
        self.builder.connect_signals(self)
        
        self.opentabs = []
        self.grouplist = []
    
        self.output('ready\n')
        
    def output(self,text):
        """Prints text to the output textbox and to stdout"""
        print text, 
        text_iter = self.output_buffer.get_end_iter()
        # Check if the scrollbar is at the bottom of the textview:
        scrolling = self.output_adjustment.value == self.output_adjustment.upper - self.output_adjustment.page_size
        # Insert the text at the end:
        self.output_buffer.insert(text_iter, text)
        # Automatically keep the textbox scrolled to the bottom, but
        # only if it was at the bottom to begin with. If the user has
        # scrolled up we won't jump them back to the bottom:
        if scrolling:
            self.output_adjustment.value = self.output_adjustment.upper

    def button_create_new_group(self,*args):
        entry_name = self.builder.get_object('entry_tabname')
        name = entry_name.get_text()
        filepath = self.chooser_h5_file.get_filenames()[0]
        success = file_ops.new_group(filepath, name)
        if success:
            self.grouplist.append(GroupListEntry(self, filepath, name))
            self.opentabs.append(GroupTab(self, filepath, name))
            entry_name.set_text('')
    
    
    def update_grouplist(self,chooser=None):
        if not chooser:
            chooser = self.chooser_h5_file
        filename = chooser.get_filename()
        for group in self.grouplist:
            group.toplevel.destroy()
        self.grouplist = []
        if not filename:
            self.no_file_opened.show()
        else:
            grouplist, success = file_ops.get_grouplist(filename) 
            if success:
                self.no_file_opened.hide()
                for name in grouplist:
                    self.grouplist.append(GroupListEntry(self, filename, name))
            else:
                chooser.unselect_all()
        return True
    
    
    def on_selection_changed(self,chooser):
        if not self.chooser_h5_file.get_filename():
            self.update_grouplist(chooser)
            
            
    def on_new_file_clicked(self,*args):
        chooser = gtk.FileChooserDialog(title='Save new HDF5 file',action=gtk.FILE_CHOOSER_ACTION_SAVE,
                                    buttons=(gtk.STOCK_CANCEL,gtk.RESPONSE_CANCEL,
                                               gtk.STOCK_SAVE,gtk.RESPONSE_OK))
        chooser.add_filter(self.builder.get_object('filefilter1'))
        chooser.set_default_response(gtk.RESPONSE_OK)
        chooser.set_do_overwrite_confirmation(True)
#        chooser.set_current_folder_uri('')
        chooser.set_current_name('.h5')
        response = chooser.run()
        
        if response == gtk.RESPONSE_OK:
            success = file_ops.new_file(chooser.get_filename())
            self.chooser_h5_file.unselect_all()
            self.chooser_h5_file.select_filename(chooser.get_filename())
            # We need self.chooser_h5_file to have its file set before
            # we can move on:
            while gtk.events_pending():
                gtk.main_iteration()
            self.update_grouplist()
        chooser.destroy()
               
    def do_it(self,*args):
        self.output('do it\n')
 
if __name__ == '__main__':        
    app = RunManager()
    file_ops = FileOps()
    gtk.main()
