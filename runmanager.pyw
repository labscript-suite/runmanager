#!/usr/bin/env python

import os
import sys
import time
import random
import itertools
import types
import subprocess
import threading
import urllib, urllib2

import gtk
import gobject
import pango

import h5py

import pylab



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
    myappid = 'monashbec.labscript.runmanager.1-0' # arbitrary string
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except:
        pass

# Feel free to add to this list!
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


class StreamWatcher(threading.Thread):
    def __init__(self, runmanager, stream, proc, red=False):
        threading.Thread.__init__(self)
        self.stream = stream
        self.runmanager = runmanager
        self.proc = proc
        self.red = red
        
    def run(self):
        while True:
            if self.stream.closed:
                break
            line = self.stream.readline()
            if line:
                gtk.gdk.threads_enter()
                self.runmanager.output(line,red=self.red)
                gtk.gdk.threads_leave()
            else:
                self.stream.close()
                break
            if self.runmanager.aborted:
                self.stream.close()
                break
                       
class FileOps:
    def __init__(self, runmanager):
        self.runmanager = runmanager
        
    def handle_error(self,e):
        if '-debug' in sys.argv:
            raise e
        else:
            print str(e)
            md = gtk.MessageDialog(self.runmanager.window, gtk.DIALOG_DESTROY_WITH_PARENT, gtk.MESSAGE_ERROR, 
                                   buttons =(gtk.BUTTONS_OK), message_format = str(e))
            md.run()
            md.destroy()
    
    def make_single_run_file(self, filename, sequenceglobals, runglobals, sequence_id, run_no, n_runs):
        with h5py.File(filename,'w') as f:
            f.attrs['sequence_id'] = sequence_id
            f.attrs['run_no'] = run_no
            f.attrs['n_runs'] = n_runs
            f.create_group('globals')
            for groupname, groupvars in sequenceglobals.items():
                group = f['globals'].create_group(groupname)
                unitsgroup = group.create_group('units')
                for name, (value, units) in groupvars.items():
                    group.attrs[name] = value
                    unitsgroup.attrs[name] = units
            for name, value in runglobals.items():
                f['globals'].attrs[name] = value
    
                    
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
            return True
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
            return {}, False
    
    def new_global(self,filename,groupname,globalname):
        try:
            with h5py.File(filename,'a') as f:
                group = f['globals'][groupname]
                if globalname in group.attrs:
                    raise Exception('Can\'t create global: target name already exists.')
                group.attrs[globalname] = ''
                return True
        except Exception as e:
            self.handle_error(e)
            return False
    
    def rename_global(self,filename,groupname,oldglobalname,newglobalname):
        if oldglobalname == newglobalname:
            # No rename!
            return True
        try:
            value, success = self.get_value(filename, groupname, oldglobalname)
            if success:
                units, success = self.get_units(filename, groupname, oldglobalname)
            if not success:
                return False
            with h5py.File(filename,'a') as f:
                group = f['globals'][groupname]
                if newglobalname in group.attrs:
                    raise Exception('Can\'t rename: target name already exists.')
                group.attrs[newglobalname] = value
                group['units'].attrs[newglobalname] = units
                del group.attrs[oldglobalname]
                del group['units'].attrs[oldglobalname]
                return True
        except Exception as e:
            self.handle_error(e)
            return False

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
        self.name = name
        self.group = group
        self.list_name = self.group.builder.get_object('vbox_name')
        self.list_value = self.group.builder.get_object('vbox_value')
        self.list_units = self.group.builder.get_object('vbox_units')
        self.list_buttons = self.group.builder.get_object('vbox_buttons')
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
        
        self.label_name.set_text(self.name)
        value, success = file_ops.get_value(self.filepath, self.group.name, name)
        units, success = file_ops.get_units(self.filepath, self.group.name, name)
        self.entry_value.set_text(str(value))
        self.label_units.set_text(units)
        
        self.list_name.pack_start(self.vbox_name)
        self.list_value.pack_start(self.vbox_value)
        self.list_units.pack_start(self.vbox_units)
        self.list_buttons.pack_start(self.vbox_buttons)
        
        self.vbox_name.show()
        self.vbox_units.show()
        self.vbox_buttons.show()
        self.vbox_value.show()
        
        self.builder.connect_signals(self)
        
        self.undo_backup = ''
        
    def focus_in(self, widget, event):
        """Called whenever one of the three text entries gains focus. If
        it's the value entry, then we want to store its existing value
        as a backup if the editing is cancelled via esc or ctrl-z. Also,
        the 'focus_out' callback adds a timeout to end the editing of
        the name and units. If one of the other text boxes is gaining
        focus immediately after, then we don't want this to occur. So
        we'll cancel that timeout."""
        self.undo_backup = self.entry_value.get_text()
        try:
            # Might not exist. If it doesn't, then there's no need to
            # remove it!
            gobject.source_remove(self.timeout)
        except:
            pass
        
    def focus_out(self, widget, event):
        """Called whenever either of the units entry box, the value
        box or the name entry box lose focus. When this happens, we
        want to end editing of that box. If one of these three entries
        subsequently gains focus (within 100ms), then the end-editing
        will not occur. If its just the window itself losing focus though
        (for example if you click and drag on the title bar), then we
        don't want to cancel editing. So we use widget.is_focus() to
        check if the widget still has focus within its toplevel. """
        if not widget.is_focus():
            self.timeout = gobject.timeout_add(100, self.toggle_edit.set_active, False)
            widget.select_region(0, 0)
        
    def value_changed(self, *args):
        """Saves the value to the h5 file every time it is modified."""
        success = file_ops.set_value(self.filepath,self.group.name,
                                     self.name, 
                                     self.entry_value.get_text())
        
    def value_keypress(self, widget, event):
        """Keyboard shortcuts for the value entry box. If you hit escape
        whilst editing the value, the previous value is restored and
        the entry box loses focus. If you hit control z, the same
        occurs except without losing focus. Enter simply causes the
        box to lose focus. No saving is required since the value is
        saved constantly."""
        if event.keyval == 65307: # escape
            self.entry_value.set_text(self.undo_backup)
            self.toggle_edit.grab_focus()
            self.group.entry_new_global.grab_focus()
        elif event.keyval == 65293 or event.keyval == 65421: #enter
            self.toggle_edit.grab_focus()
            self.group.entry_new_global.grab_focus()
        elif event.keyval == 122 and event.state & gtk.gdk.CONTROL_MASK: # control z
            self.entry_value.set_text(self.undo_backup)
            
    def on_edit_toggled(self,widget):
        """called when the edit toggle button is toggled, to enter or
        cancel editing of the global's name and or units. Name and units
        are not saved constantly like the value, they are only saved
        when editing is ended."""
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
            success = file_ops.rename_global(self.filepath, self.group.name, 
                                             self.name, 
                                             self.entry_name.get_text())
            if success:
                self.name = self.entry_name.get_text()
                self.label_name.set_text(self.name)
                success = file_ops.set_units(self.filepath, self.group.name, 
                                             self.name, 
                                             self.entry_units.get_text())
            else:
                self.entry_name.set_text(self.name)
            if success:
                self.label_units.set_text(self.entry_units.get_text())
            else:
                self.entry_units.set_text(self.label_units.get_text())
             
                
            if any([w.has_focus() for w in (self.entry_units, self.entry_name, self.entry_value)]):
                self.group.entry_new_global.grab_focus()
                
            self.entry_name.hide()
            self.entry_units.hide()
            self.button_remove.hide()
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
    
    def on_remove_clicked(self,widget):
        md = gtk.MessageDialog(self.group.runmanager.window, 
        gtk.DIALOG_DESTROY_WITH_PARENT, gtk.MESSAGE_WARNING,gtk.BUTTONS_OK_CANCEL,
        "Are you sure? This will remove the global variable from the h5 file and cannot be undone.")
        result = md.run()
        md.destroy()
        if result == gtk.RESPONSE_OK:
            success = file_ops.delete_global(self.filepath,self.group.name,
                                             self.entry_name.get_text())
            self.list_name.remove(self.vbox_name)
            self.list_value.remove(self.vbox_value)
            self.list_units.remove(self.vbox_units)
            self.list_buttons.remove(self.vbox_buttons)
            self.group.globals.remove(self)
            self.group.entry_new_global.grab_focus()
            

                
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
        # Is this global group open in the import tab?
        if self.runmanager.chooser_h5_file.get_filename() == self.filepath:
            for entry in self.runmanager.grouplist:
                if entry.name == self.name:
                    entry.button_import.show()
                    entry.button_close.hide()
                    entry.label_name.set_use_markup(False)
                    entry.label_name.set_markup('%s'%entry.name)
    
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
                        entry.label_name.set_markup('<b>%s</b>'%self.name)
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
    
    def focus_out(self,widget,event):
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
        name = self.entry_new_global.get_text()
        if not name:
            # Do nothing if the textbox is empty:
            return
        self.adjustment.value = self.adjustment.upper  
        success = file_ops.new_global(self.filepath, self.name, name)
        if success:
            success = file_ops.set_value(self.filepath, self.name, 
                                         name, str(int(1000*random.random() - 500)))
        if success:
            success = file_ops.set_units(self.filepath, self.name, 
                                         name,random.choice(funny_units))
        if success:
            newglobal = Global(self,name)
            self.globals.append(newglobal) 
            newglobal.toggle_edit.set_active(True)
            newglobal.entry_value.grab_focus()
            self.entry_new_global.set_text('')   


class GroupListEntry(object):
    def __init__(self,runmanager,filepath,name):
        self.runmanager = runmanager
        self.vbox = self.runmanager.grouplist_vbox
        self.filepath = filepath
        self.name = name
        
        self.builder = gtk.Builder()
        self.builder.add_from_file('grouplistentry.glade')
        
        self.toplevel = self.builder.get_object('toplevel')
        self.entry_name = self.builder.get_object('entry_name')
        self.label_name = self.builder.get_object('label_name')
        self.hbox_buttons = self.builder.get_object('hbox_buttons')
        self.toggle_edit = self.builder.get_object('toggle_edit')
        self.button_import = self.builder.get_object('button_import')
        self.button_close = self.builder.get_object('button_close')
        self.button_remove = self.builder.get_object('button_remove')
        
        self.label_name.set_text(name)
        
        self.vbox.pack_start(self.toplevel)
        self.toplevel.show()
        
        self.builder.connect_signals(self)
        
        # Is this group open in a tab?
        for tab in self.runmanager.opentabs:
            if tab.name == self.name and tab.filepath == self.filepath:
                # If so, better make the GUI look like it:
                self.button_import.hide()
                self.button_close.show()
                self.label_name.set_use_markup(True)
                self.label_name.set_markup('<b>%s</b>'%self.name)
                
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
        md = gtk.MessageDialog(self.runmanager.window, 
        gtk.DIALOG_DESTROY_WITH_PARENT, gtk.MESSAGE_WARNING, 
        buttons =(gtk.BUTTONS_OK_CANCEL),
        message_format = "Are you sure? This will remove the group from the h5 file and cannot be undone.")
        result = md.run()
        md.destroy()
        if result == gtk.RESPONSE_OK:
            success = file_ops.delete_group(self.filepath,self.name)
            self.toplevel.destroy()
            self.runmanager.grouplist.remove(self)
            # is the tab open? If so, close it:
            for tab in self.runmanager.opentabs:
                if tab.name == self.name and tab.filepath == self.filepath:
                    tab.on_closetab_button_clicked()
        
    def on_import_clicked(self,widget):
        self.runmanager.opentabs.append(GroupTab(self.runmanager, self.filepath, self.name))
        self.button_import.hide()
        self.button_close.show()
        self.label_name.set_use_markup(True)
        self.label_name.set_markup('<b>%s</b>'%self.name)
        
    def on_close_clicked(self,widget):
        for tab in self.runmanager.opentabs:
            if tab.name == self.name and tab.filepath == self.filepath:
                tab.on_closetab_button_clicked()
        self.label_name.set_use_markup(False)
        self.label_name.set_markup('%s'%self.name)
        self.button_import.show()
        self.button_close.hide()
            
            
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

        self.window.show()
        
        area=self.builder.get_object('drawingarea1')
        pixbuf=gtk.gdk.pixbuf_new_from_file(os.path.join('assets','grey.png'))
        pixmap, mask=pixbuf.render_pixmap_and_mask()
        area.window.set_back_pixmap(pixmap, False)
        self.output_view.modify_font(pango.FontDescription("monospace 11"))
        self.output_view.modify_base(gtk.STATE_NORMAL, gtk.gdk.color_parse('black'))
        self.output_view.modify_text(gtk.STATE_NORMAL, gtk.gdk.color_parse('white'))
        
        self.window.set_icon_from_file(os.path.join('assets','icon.png'))
        self.builder.get_object('filefilter1').add_pattern('*.h5')
        self.builder.get_object('filefilter2').add_pattern('*.py')
        
        self.builder.connect_signals(self)
        self.outputscrollbar.connect_after('value-changed', self.on_scroll)
        
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
        
        self.text_mark = self.output_buffer.create_mark(None, self.output_buffer.get_end_iter())

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

    def on_scroll(self,*args):
        """Queue a redraw of the output on Windows, to prevent visual artifacts
           when the window isn't focused"""
        if os.name == 'nt':
            parent = self.scrolledwindow_output.get_parent()
            if isinstance(parent,gtk.Window):
                parent.queue_draw()
                                
    def button_create_new_group(self,*args):
        entry_name = self.builder.get_object('entry_tabname')
        name = entry_name.get_text()
        filepath = self.chooser_h5_file.get_filenames()[0]
        success = file_ops.new_group(filepath, name)
        if success:
            self.opentabs.append(GroupTab(self, filepath, name))
            self.grouplist.append(GroupListEntry(self, filepath, name))
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
        if not self.chooser_h5_file.get_filename() and not self.making_new_file:
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
        self.chooser_h5_file.unselect_all()
        while gtk.events_pending():
            gtk.main_iteration()
        response = chooser.run()
        f = chooser.get_filename()
        d = chooser.get_current_folder()
        chooser.destroy()
        if response == gtk.RESPONSE_OK:
            # Make sure that we don't accidentally trigger more callbacks
            # in the gtk.main_iteration calls in this block:
            self.making_new_file = True
            success = file_ops.new_file(f)
            self.chooser_h5_file.set_current_folder(d)
            # Have to make sure the above changes occur before proceeding:
            while gtk.events_pending():
                gtk.main_iteration()
            self.chooser_h5_file.select_filename(f)
            # Have to make sure the file gets set before
            # update_grouplist() happens:
            while gtk.events_pending():
                gtk.main_iteration()
            self.update_grouplist()
            self.making_new_file = False
            
            
            
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
            
    def parse_globals(self):
        self.output('Parsing globals...\n')
        sequenceglobals = {}
        for grouptab in self.opentabs:
            if not grouptab.checkbox.get_active():
                continue
            globalsdict = {}
            for globalvar in grouptab.globals:
                value, success1 = file_ops.get_value(grouptab.filepath,grouptab.name,globalvar.name)
                units, success2 = file_ops.get_units(grouptab.filepath,grouptab.name,globalvar.name)
                if not(success1 and success2):
                    return {}, False
                globalsdict[globalvar.name] = value, units
            sequenceglobals[grouptab.name] = globalsdict
           
        names = []  
        vals = []
        allglobals = {}
         
        for groupname, groupglobals in sequenceglobals.items():
            for globalname in groupglobals:
                if globalname in allglobals:
                    raise Exception('Error parsing \'%s\' from group \'%s\'. Global name is already defined in another group.'%(globalname,groupname))
                allglobals[globalname], units = groupglobals[globalname]
        for key in allglobals:
            try:
                value = eval(allglobals[key],pylab.__dict__)
            except Exception as e:
                raise Exception('Error parsing global \'%s\': '%key + str(e))
                
            if isinstance(value,types.GeneratorType):
               result = [tuple(value)]
            elif isinstance(value, pylab.ndarray) or  isinstance(value, list):
                result = value
            else:
                result = [value]
            names.append(key)
            vals.append(result)
        
        return sequenceglobals, names, vals

    def generate_sequence_number(self):
        timestamp = str(int(time.time()))
        scriptname = self.chooser_labscript_file.get_filename()
        if not scriptname:
            raise Exception('Error: No labscript file selected')
        scriptbase = os.path.basename(scriptname).split('.py')[0]
        return timestamp + scriptbase        
        
    def make_sequence(self, sequenceglobals, names, vals):
        """makes a sequence of hdf5 run files given a set of global
        variables. This function takes the unevaluated globals --
        ie lists and arrays haven't yet been expanded. A run file is
        then made for every combination. 'sequenceglobals' should be a
        dictionary with keys being the name of each group, and values
        being a dictionary of globalname: (value, units)"""

        self.output('Generating run files...\n')
        outfolder = self.chooser_output_directory.get_filename()
        labscript_file = self.chooser_labscript_file.get_filename()
        sequence_id = self.generate_sequence_number()
        basename = os.path.join(outfolder,sequence_id)
        
        nruns = 1
        for lst in vals:
            nruns *= len(lst)
        ndigits = int(pylab.ceil(pylab.log10(nruns)))
        for i, values in enumerate(itertools.product(*vals)):
            runfilename = ('%s%0'+str(ndigits)+'d.h5')%(basename,i)
            self.run_files.append(runfilename)
            self.output('Creating run file %s/%s : %s\n'%(str(i+1),str(nruns),runfilename))
            runglobals = {} 
            for name,val in zip(names,values):
                runglobals[name] = val
            file_ops.make_single_run_file(runfilename,sequenceglobals,runglobals, sequence_id, i, nruns)
        return labscript_file

    def compile_labscript(self, labscript_file):
        for run_file in self.run_files:
            print 'compiling!!!'
            proc = subprocess.Popen(['python','-u',labscript_file,run_file],stderr=subprocess.PIPE,stdout=subprocess.PIPE)
            stdout = StreamWatcher(self,proc.stdout,proc)
            stderr = StreamWatcher(self,proc.stderr,proc,red=True)
            stdout.start()
            stderr.start()
            proc.wait()
            if proc.returncode:
                while not (proc.stdout.closed and proc.stderr.closed):
                    continue
                if not self.aborted:
                    raise Exception('Error: this labscript would not compile.')
                else:
                    raise Exception('Complilation interrupted.')
                    
    
    def submit_jobs(self, run_files):
        server = self.builder.get_object('entry_server').get_text()
        if not server.startswith('http://'):
            server = 'http://'+server
        port = 42517
        for run_file in run_files:
            if self.aborted:
                raise Exception('Job submission interrupted.')
            gtk.gdk.threads_enter()
            self.output('Submitting run file %s.\n'%os.path.basename(run_file))
            gtk.gdk.threads_leave()
            params = urllib.urlencode({'filepath': run_file})
            try:
                response = urllib2.urlopen('%s:%d'%(server,port), params, 2).read()
                print response
            except Exception as e:
                raise Exception('Couldn\'t submit job to control server. Check network connectivity, and server address.%s'%str(e))
        
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
    
    def ask_delete_run_files(self):
        md = gtk.MessageDialog(self.window, 
        gtk.DIALOG_DESTROY_WITH_PARENT, gtk.MESSAGE_QUESTION, 
        buttons =(gtk.BUTTONS_OK_CANCEL),
        message_format = "Run aborted. Would you like to delete the hdf5 files that were created?")
        result = md.run()
        md.destroy()
        if result == gtk.RESPONSE_OK:
            for run_file in self.run_files:
                os.remove(run_file)

    def do_it(self, *args):
        self.builder.get_object('button_run').set_visible(False)
        self.builder.get_object('button_abort').set_visible(True)
        gtk.gdk.threads_leave()
        threading.Thread(target = self._do_it).start()
        gtk.gdk.threads_enter()
        
    def on_abort_clicked(self, *args):
        self.aborted = True
        
    def _do_it(self):
        try:
            try:
                gtk.gdk.threads_enter()
                if self.parse:
                    sequenceglobals, names, vals = self.parse_globals()
                if self.make:
                    labscript_file = self.make_sequence(sequenceglobals, names, vals)
                gtk.gdk.threads_leave()
            except:
                raise
            finally:
                gtk.gdk.threads_leave()
            if self.compile:
                self.compile_labscript(labscript_file)
            if self.run:
                self.submit_jobs(self.run_files)
        except Exception as e:
            gtk.gdk.threads_enter()
            self.output(str(e)+'\n',red=True)
            self.output('Run aborted\n',red=True)
            #if self.run_files:
                #self.ask_delete_run_files()
            gtk.gdk.threads_leave()

        self.run_files = []
        gtk.gdk.threads_enter()
        self.output('Ready\n')
        self.builder.get_object('button_run').set_visible(True)
        self.builder.get_object('button_abort').set_visible(False)
        gtk.gdk.threads_leave()
        self.aborted = False
        
if __name__ == '__main__':    
    gtk.gdk.threads_init()    
    run_manager = RunManager()
    file_ops = FileOps(run_manager)
    gtk.gdk.threads_enter()
    gtk.main()
    gtk.gdk.threads_leave()
    
    
    
    
    
    
