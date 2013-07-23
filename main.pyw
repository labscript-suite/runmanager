import os
import sys
import excepthook

import time
import itertools
import logging, logging.handlers
import subprocess
import threading
import Queue
import urllib, urllib2, socket

import gtk
import gobject
import glib
import pango

import h5_lock, h5py
from zmq import ZMQError

import lyse
import pylab
from LabConfig import LabConfig, config_prefix
import shared_drive
import runmanager
import subproc_utils
from subproc_utils.gtk_components import OutputBox

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
        terminalhandler.setLevel(logging.DEBUG) # only display info or higher in the terminal
        logger.addHandler(terminalhandler)
    else:
        # Prevent bug on windows where writing to stdout without a command
        # window causes a crash:
        sys.stdout = sys.stderr = open(os.devnull,'w')
    logger.setLevel(logging.DEBUG)
    return logger

def error_dialog(message):
    dialog =  gtk.MessageDialog(app.window, gtk.DIALOG_DESTROY_WITH_PARENT, gtk.MESSAGE_WARNING, 
                                buttons=(gtk.BUTTONS_OK), message_format = message)
    result = dialog.run()
    dialog.destroy()

def error_dialog_from_thread(message):
    def f():
        with gtk.gdk.lock:
            error_dialog(message)
    gobject.idle_add(f)
    
class CellRendererClickablePixbuf(gtk.CellRendererPixbuf):
    __gsignals__    = { 'clicked' :
                        (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, (gobject.TYPE_STRING,)) , }
    def __init__(self):
        gtk.CellRendererPixbuf.__init__(self)
        self.set_property('mode', gtk.CELL_RENDERER_MODE_ACTIVATABLE)
    def do_activate(self, event, widget, path, background_area, cell_area, flags):
        self.emit('clicked', path)


class GroupTab(object):

    # Useful constants for liststore operations:
    N_COLUMNS = 14
    NAME = 0
    VALUE = 1
    UNITS = 2
    EXPANSION = 3
    DELETE_ICON = 4
    EDITABLE = 5
    VALUE_BG_COLOR = 6
    VALUE_ERROR_ICON = 7
    VALUE_IS_BOOL = 8
    VALUE_BOOL_STATE = 9
    VALUE_BOOL_BG_COLOR = 10
    UNITS_EDITABLE = 11
    TOOLTIP = 12
    EXPANSION_ICON = 13
    
    NEW_GLOBAL_STRING = '<Click to add new global>'
    DELETE_ICON_STRING = 'gtk-remove'
    ERROR_ICON_STRING = 'gtk-dialog-warning'
    COLOR_ERROR = '#FF9999' # light red
    COLOR_OK = '#AAFFCC' # light green
    COLOR_BOOL_ON = '#66FF33' # bright green
    COLOR_BOOL_OFF = '#608060' # dark green
    ICON_OUTER = gtk.gdk.pixbuf_new_from_file('outer.png')
    ICON_ZIP = gtk.gdk.pixbuf_new_from_file('zip.png')
    
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
        
        self.global_treeview.set_hover_selection(True)
        self.tab = gtk.HBox()
        
        self.column_delete = self.builder.get_object('column_delete')
        self.column_name = self.builder.get_object('column_name')
        self.column_value = self.builder.get_object('column_value')
        self.column_units = self.builder.get_object('column_units')
        self.column_expansion = self.builder.get_object('column_expansion')
        delete_cell_renderer = CellRendererClickablePixbuf()
        self.column_delete.pack_end(delete_cell_renderer)
        self.column_delete.add_attribute(delete_cell_renderer,"stock-id",self.DELETE_ICON)
        delete_cell_renderer.connect("clicked",self.on_delete_global)
        
        # get a stock error image:
        self.tab_error_icon = gtk.image_new_from_stock(gtk.STOCK_DIALOG_WARNING, gtk.ICON_SIZE_MENU)
        
        # get a stock close button image
        close_image = gtk.image_new_from_stock(gtk.STOCK_CLOSE, gtk.ICON_SIZE_MENU)
        
        # make the close button
        btn = gtk.Button()
        btn.set_relief(gtk.RELIEF_NONE)
        btn.set_focus_on_click(False)
        btn.add(close_image)
        
        # this reduces the size of the button
        style = gtk.RcStyle()
        style.xthickness = 0
        style.ythickness = 0
        btn.modify_style(style)
        
        self.tablabel = gtk.Label(self.name)
        self.tablabel.set_ellipsize(pango.ELLIPSIZE_END)
        self.tablabel.set_tooltip_text(self.name)
        self.tab.pack_start(self.tablabel)
        self.tab.pack_start(self.tab_error_icon,False,False)
        self.tab.pack_start(btn, False, False)
        self.tab.show_all()
        self.tab_error_icon.hide()
        self.notebook.append_page(self.toplevel, tab_label = self.tab)                     
        
        self.notebook.set_tab_reorderable(self.toplevel,True)
        
        self.label_groupname.set_text(self.name)
        self.label_h5_path.set_text(self.filepath)
        
        self.notebook.show()

        # connect the close button
        btn.connect('clicked', self.on_closetab_button_clicked)

        self.builder.connect_signals(self)
        
        self.globals = []
        
        global_vars = runmanager.get_globalslist(self.filepath,self.name)
        
        for global_var in global_vars:
            value = runmanager.get_value(self.filepath, self.name, global_var)
            units = runmanager.get_units(self.filepath, self.name, global_var)
            expansion = runmanager.get_expansion(self.filepath, self.name, global_var)
            row = [None]*self.N_COLUMNS
            row[self.NAME] = global_var
            row[self.VALUE] = value
            row[self.UNITS] = units
            row[self.EXPANSION] = expansion
            if expansion == 'outer':
                row[self.EXPANSION_ICON] = self.ICON_OUTER
            elif expansion:
                row[self.EXPANSION_ICON] = self.ICON_ZIP
            row[self.DELETE_ICON] = self.DELETE_ICON_STRING
            row[self.EDITABLE] = True
            row[self.UNITS_EDITABLE] = True
            # Check if the row has a boolean value, update its settings accordingly:
            self.apply_bool_settings(row)
            self.global_liststore.append(row)
        
        # Add line to add a new global
        row = [None]*self.N_COLUMNS
        row[self.NAME] = self.NEW_GLOBAL_STRING
        self.global_liststore.append(row)
        
        # Variable required to fix bug with GTK that passes the incorrect cellrenderer to
        # the editing-cancelled callback when rows are reordered
        self.editing_started_name = None
        
        # Sort by name
        self.global_liststore.set_sort_column_id(self.NAME, gtk.SORT_ASCENDING)
        
        app.preparse_globals_required.set()
        
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
        # Get the page number of the tab we wanted to close:
        pagenum = self.notebook.page_num(self.toplevel)
        # And close it:
        self.notebook.remove_page(pagenum)
    
    def focus_cell(self, column, name):
        # Focus the target cell for editing. Do this asynchronously, as a gobject.idle:
        def focus_value_cell():
            # gobject.idles aren't threadsafe, must acquire the gtk lock:
            with gtk.gdk.lock:
                for path, row in enumerate(self.global_liststore):
                    if row[self.NAME] == name:
                        self.global_treeview.set_cursor(path, column, True)
        gobject.idle_add(focus_value_cell)
            
    def on_edit_name(self, cellrenderer, path, new_text):
        icon_name = self.global_liststore[path][self.DELETE_ICON]
        # if the icon_name is blank, then the user is editing the 'add
        # new global' row (which does not have a delete button). So in
        # this case we create a new global:
        if not icon_name:
            if new_text == self.NEW_GLOBAL_STRING:
                # The user quit the editing without entering a name for
                # their new global. Do not create a new global:
                return
            try:    
                runmanager.new_global(self.filepath,self.name,new_text)
            except Exception as e:
                error_dialog(str(e))
                return
            # Set the properties of this row to that of a new global:
            row = self.global_liststore[path]
            row[self.NAME] = new_text
            row[self.DELETE_ICON] = self.DELETE_ICON_STRING
            row[self.EDITABLE] = True
            row[self.UNITS_EDITABLE] = True
            
           
            # Focus the value cell for editing next:
            self.focus_cell(self.column_value, new_text)
            
            # Re-add a row to the bottom for adding a new global.
            # This must be after the above focus call, otherwise
            # sorting of the liststore may make the indices wrong.
            row = [None]*self.N_COLUMNS
            row[self.NAME] = self.NEW_GLOBAL_STRING
            self.global_liststore.append(row)
            

                            
        # Otherwise, we are editing an existing global:
        else:    
            name = self.global_liststore[path][self.NAME]
            if new_text == name:
                # Do nothing if there was no change:
                return
            try:
                runmanager.rename_global(self.filepath, self.name, name, new_text)
            except Exception as e:
                error_dialog(str(e))
                return
            self.global_liststore[path][self.NAME] = new_text
            # Clear its highlight and tooltip until it is re-evaluated by the preparser:
            self.global_liststore[path][self.VALUE_BG_COLOR] = None
            self.global_liststore[path][self.TOOLTIP] = 'group inactive, or expression still being evaluated'
            app.preparse_globals_required.set()
        
    def on_edit_value(self, cellrenderer, path, new_text):
        name = self.global_liststore[path][self.NAME]
        existing_value = self.global_liststore[path][self.VALUE]
        if new_text == existing_value:
            # Do nothing if there was no change:
            return
        try:
            runmanager.set_value(self.filepath, self.name, name, new_text)
        except Exception as e:
            error_dialog(str(e))
            return
            
        # Clear its highlight and tooltip until it is re-evaluated by the preparser:
        self.global_liststore[path][self.VALUE] = new_text
        # Get the new path for the instance when the rows are reodered (i.e. sorted by Value)
        for path, row in enumerate(self.global_liststore):
            if row[self.NAME] == name:
                break # this updates path
                
        self.global_liststore[path][self.VALUE_BG_COLOR] = None
        self.global_liststore[path][self.TOOLTIP] = 'Expression still being evaluated...'
        # Check for Boolean values:
        self.apply_bool_settings(self.global_liststore[path])
        
        # If the units box is empty, make it focussed and editable to encourage people to set units!
        units = self.global_liststore[path][self.UNITS]
        if not units:
            self.focus_cell(self.column_units, name)
        else:
            app.preparse_globals_required.set()
        
    def apply_bool_settings(self, row):
        value = row[self.VALUE]
        if value == 'True':
            row[self.UNITS] = 'Bool'
            row[self.UNITS_EDITABLE] = False
            row[self.VALUE_IS_BOOL] = True
            row[self.VALUE_BOOL_STATE] = True
            row[self.VALUE_BOOL_BG_COLOR] = self.COLOR_BOOL_ON
        elif value == 'False':
            row[self.UNITS] = 'Bool'
            row[self.UNITS_EDITABLE] = False
            row[self.VALUE_IS_BOOL] = True
            row[self.VALUE_BOOL_STATE] = False
            row[self.VALUE_BOOL_BG_COLOR] = self.COLOR_BOOL_OFF
        else:
            row[self.UNITS_EDITABLE] = True
            row[self.VALUE_IS_BOOL] = False
            row[self.VALUE_BOOL_BG_COLOR] = None
            
    def on_toggle_bool_toggled(self, cellrenderer, path):
        row = self.global_liststore[path]
        name = row[self.NAME]
        current_state = row[self.VALUE_BOOL_STATE]
        new_state = not current_state
        try:
            runmanager.set_value(self.filepath, self.name, name, 'True' if new_state else 'False')
        except Exception as e:
            error_dialog(str(e))
            return
        row[self.VALUE_BOOL_STATE] = new_state
        # Clear its highlight and tooltip until it is re-evaluated by the preparser:
        row[self.VALUE_BG_COLOR] = None
        row[self.TOOLTIP] = 'Expression still being evaluated...'
        if new_state:
            row[self.VALUE_BOOL_BG_COLOR] = self.COLOR_BOOL_ON
            row[self.VALUE] = 'True'
        else:
            row[self.VALUE_BOOL_BG_COLOR] = self.COLOR_BOOL_OFF
            row[self.VALUE] = 'False'
        app.preparse_globals_required.set()
        
    def on_edit_units(self, cellrenderer, path, new_text):
        name = self.global_liststore[path][self.NAME]
        try:
            runmanager.set_units(self.filepath, self.name, name, new_text)
        except Exception as e:
            error_dialog(str(e))
            return
        self.global_liststore[path][self.UNITS] = new_text
        app.preparse_globals_required.set()
        
    def on_edit_expansion(self, cellrenderer, path, new_text):
        name = self.global_liststore[path][self.NAME]
        try:
            runmanager.set_expansion(self.filepath, self.name, name, new_text)
        except Exception as e:
            error_dialog(str(e))
            return
        self.global_liststore[path][self.EXPANSION] = new_text
        # Clear its highlight and tooltip until it is re-evaluated by the preparser:
        self.global_liststore[path][self.VALUE_BG_COLOR] = None
        self.global_liststore[path][self.TOOLTIP] = 'Expression still being evaluated...'
        if new_text == 'outer':
            self.global_liststore[path][self.EXPANSION_ICON] = self.ICON_OUTER
        elif new_text:
            self.global_liststore[path][self.EXPANSION_ICON] = self.ICON_ZIP
        else:
            self.global_liststore[path][self.EXPANSION_ICON] = None
        app.preparse_globals_required.set()
    
    def on_editing_cancelled_units(self, cellrenderer):
        # Note: cellrenderer is not always correct when rows are reordered
        # Get the path from the row name
        for path, row in enumerate(self.global_liststore):
            if row[self.NAME] == self.editing_started_name:
                break # this sets path
        
        units = self.global_liststore[path][self.UNITS]
        
        # If the user has left a field blank, the other fields still
        # need parsing. This needs to be done after the cursor has been
        # moved automatically to the next cell (preparsing is not done
        # when this happens, so we'd better do preparsing now):
        if not units:
            app.preparse_globals_required.set()
            
    def on_editing_cancelled_value(self, cellrenderer):
        # Note: cellrenderer is not always correct when rows are reordered
        # Get the path from the row name
        for path, row in enumerate(self.global_liststore):
            if row[self.NAME] == self.editing_started_name:
                break # this sets path
        
        value = self.global_liststore[path][self.VALUE]
        
        # If the user has left a field blank, the other fields still
        # need parsing. This needs to be done after the cursor has been
        # moved automatically to the next cell (preparsing is not done
        # when this happens, so we'd better do preparsing now):
        if not value:
            app.preparse_globals_required.set()
            
    def on_editing_started(self, cellrenderer, editable, path):
        # Get the name of the row being edited for later use by on_editing_cancelled
        # This is required due to editing-cancelled callback returning wrong cellrenderer
        self.editing_started_name = self.global_liststore[path][self.NAME]
            
    def on_delete_global(self,cellrenderer,path):
        name = self.global_liststore[path][self.NAME] 
        icon_name = self.global_liststore[path][self.DELETE_ICON]
        # if the icon_name is blank, then the user clicking the space next
        # to the the 'add new global' row, which is not to be deleted:
        if not icon_name:
            return
        
        md = gtk.MessageDialog(self.app.window, 
                                gtk.DIALOG_DESTROY_WITH_PARENT, gtk.MESSAGE_WARNING,gtk.BUTTONS_OK_CANCEL,
                                "Are you sure? \n\nThis will remove the global variable ("+name+") from the h5 file ("+self.filepath+") and cannot be undone.")
        md.set_default_response(gtk.RESPONSE_CANCEL)
        result = md.run()
        md.destroy()
        if result == gtk.RESPONSE_OK:   
            try:
                runmanager.delete_global(self.filepath,self.name,name)
            except Exception as e:
                error_dialog(str(e))
                return
            # Remove from the liststore:
            del self.global_liststore[path]
        app.preparse_globals_required.set()
        
    def update_parse_indication(self, sequence_globals, evaled_globals):
        if self.name in evaled_globals:
            tab_contains_errors = False
            for row in self.global_liststore:
                name = row[self.NAME]
                if name == self.NEW_GLOBAL_STRING:
                    continue
                value = evaled_globals[self.name][name]
                ignore, ignore, expansion = sequence_globals[self.name][name]
                row[self.EXPANSION] = expansion
                if expansion == 'outer':
                    row[self.EXPANSION_ICON] = self.ICON_OUTER
                elif expansion:
                    row[self.EXPANSION_ICON] = self.ICON_ZIP
                else:
                    row[self.EXPANSION_ICON] = None
                if isinstance(value, Exception):
                    row[self.VALUE_BG_COLOR] = self.COLOR_ERROR
                    row[self.VALUE_ERROR_ICON] = self.ERROR_ICON_STRING
                    tooltip = '%s: %s'%(value.__class__.__name__, value.message)
                    tab_contains_errors = True
                else:
                    row[self.VALUE_BG_COLOR] = self.COLOR_OK
                    row[self.VALUE_ERROR_ICON] = None
                    tooltip = repr(value)
                row[self.TOOLTIP] = glib.markup_escape_text(tooltip)
            if tab_contains_errors:
                self.tab_error_icon.show()
            else:
                self.tab_error_icon.hide()
        else:
            # Clear everything:
            self.tab_error_icon.hide()
            for row in self.global_liststore:
                row[self.VALUE_ERROR_ICON] = None
                row[self.VALUE_BG_COLOR] = None
                row[self.TOOLTIP] = 'Group inactive'


class ParameterSpaceOverview(object):
    def __init__(self, container):
        self.builder = gtk.Builder()
        self.builder.add_from_file('parameter_space_overview.glade')
        toplevel = self.builder.get_object('toplevel')
        container.add(toplevel)

    def set_axes(self, axes):
        for axis in axes:
            pass
            
class RunManager(object):
    def __init__(self):
        config_path = os.path.join(config_prefix,'%s.ini'%socket.gethostname())
        required_config_params = {"DEFAULT":["experiment_name"],
                                  "programs":["text_editor",
                                              "text_editor_arguments",
                                             ],
                                  "paths":["shared_drive",
                                           "experiment_shot_storage",
                                           "labscriptlib",
                                          ],
                                 }
        self.exp_config = LabConfig(config_path, required_config_params)
        
        
        self.builder = gtk.Builder()
        self.builder.add_from_file('interface.glade')
        
        self.window = self.builder.get_object('window1')
        self.notebook = self.builder.get_object('notebook1')

        self.use_globals_vbox = self.builder.get_object('use_globals_vbox')
        self.grouplist_vbox = self.builder.get_object('grouplist_vbox')
        self.no_file_opened = self.builder.get_object('label_no_file_opened')
        self.chooser_labscript_file = self.builder.get_object('chooser_labscript_file')
        self.output_page = self.builder.get_object('output_page')
        self.scrolledwindow_output = self.builder.get_object('scrolledwindow_output')
        self.chooser_output_directory = self.builder.get_object('chooser_output_directory')
        self.radiobutton_compile = self.builder.get_object('radiobutton_compile')
        self.radiobutton_mise = self.builder.get_object('radiobutton_mise')
        self.checkbutton_view = self.builder.get_object('checkbutton_view')
        self.checkbutton_run = self.builder.get_object('checkbutton_run')
        self.checkbuttons_box = self.builder.get_object('checkbuttons_box')
        self.mise_server_box = self.builder.get_object('mise_server_box')
        self.toggle_shuffle = self.builder.get_object('toggle_shuffle')
        self.button_abort = self.builder.get_object('button_abort')
        self.outputscrollbar = self.scrolledwindow_output.get_vadjustment()
        self.group_store = self.builder.get_object('group_store')
        self.group_treeview = self.builder.get_object('group_treeview')
        self.current_h5_store = self.builder.get_object('current_h5_store')
        self.current_h5_file = self.builder.get_object('current_h5_file')
        self.column_add = self.builder.get_object('column_add')
        overview_page = self.builder.get_object('overview_page')
        add_cell_renderer = CellRendererClickablePixbuf()
        self.column_add.pack_end(add_cell_renderer)
        self.column_add.add_attribute(add_cell_renderer,"stock-id",2)
        add_cell_renderer.connect("clicked",self.on_toggle_group)
        
        self.column_delete = self.builder.get_object('column_delete')
        delete_cell_renderer = CellRendererClickablePixbuf()
        self.column_delete.pack_end(delete_cell_renderer)
        self.column_delete.add_attribute(delete_cell_renderer,"stock-id",3)
        delete_cell_renderer.connect("clicked",self.on_delete_group)
        
        self.window.show()
        
        self.output_box = OutputBox(self.scrolledwindow_output)
        self.parameter_space_overview = ParameterSpaceOverview(overview_page)
        self.window.set_icon_from_file(os.path.join('runmanager.svg'))
        self.builder.get_object('filefilter1').add_pattern('*.h5')
        self.builder.get_object('filefilter2').add_pattern('*.py')
        self.chooser_labscript_file.set_current_folder(self.exp_config.get('paths','labscriptlib')) # Will only happen if folder exists
        
        self.builder.connect_signals(self)
        
        self.opentabs = []
        self.grouplist = []
        self.previous_evaled_globals = {}
        self.popped_out = False
        self.making_new_file = False
        self.compile = True
        self.view = False
        self.run = False
        self.run_files = []
        self.aborted = False
        self.current_labscript_file = None
        
        self.shared_drive_prefix = self.exp_config.get('paths', 'shared_drive')
        
        self.globals_path = self.exp_config.get('paths', 'experiment_shot_storage')
        # Add timeout to watch for output folder changes when the day rolls over
        gobject.timeout_add(1000, self.update_output_dir)
        self.current_day_dir_suffix = os.path.join(time.strftime('%Y-%b'),time.strftime('%d'))
        # Start the compiler subprocess:
        self.to_child, self.from_child, self.child = subproc_utils.subprocess_with_queues('batch_compiler.py', self.output_box.port)
        
        # Start the loop that allows compilations to be queued up:
        self.compile_queue = Queue.Queue()
        self.compile_queue_thread = threading.Thread(target=self.compile_loop)
        self.compile_queue_thread.daemon = True
        self.compile_queue_thread.start()
        
        # Start the thread which preparses globals and updates the GUI in the background:
        self.preparse_globals_thread = threading.Thread(target=self.preparse_globals)
        self.preparse_globals_thread.daemon = True
        # An Event for tabs to let the thread know when there are new values needing parsing:
        self.preparse_globals_required = threading.Event()
        self.preparse_globals_thread.start()
        
        # Load default files and groups
        try:
            default_globals = eval(self.exp_config.get('runmanager', 'default_global_files'))
            self.output('Loading default files and groups:\n')
            for globals_file, global_groups in default_globals.items():
                # open the file
                self.output('   ' + globals_file + '\n')
                grouplist = runmanager.get_grouplist(globals_file) 
                # reorder the grouplist if a list of groups is specified
                if type(global_groups) is list:
                    grouplist_new = []
                    for g in global_groups:
                        try:
                            i = grouplist.index(g)
                            grouplist_new.append(grouplist[i])
                            grouplist.remove(grouplist[i])
                        except ValueError:
                            self.output('        Could not find group \'%s\' in file \'%s\'\n\n'%(g,globals_file))
                    grouplist = grouplist_new + grouplist
                # Append to Tree View
                parent = self.group_store.prepend(None, (globals_file, False, 'gtk-close', None, 0, 0, 1))            
                for name in grouplist:
                    self.group_store.append(parent, (name, False, 'gtk-add', 'gtk-remove', 0, 1, 1))                 
                self.group_treeview.expand_row(self.group_store.get_path(parent), True) 
                # Add editable option for adding groups
                add = self.group_store.append(parent,('<Click to add group>', False, None, None, 0, 1, 0)) 
                self.group_treeview.set_cursor(self.group_store.get_path(add), self.group_treeview.get_column(2), True)
                # Recurse Tree View and create tab for groups as required 
                if self.group_store.iter_has_child(parent):
                    child_iter = self.group_store.iter_children(parent)
                    while child_iter:
                        if type(global_groups) == str:
                            if global_groups.lower() == 'all':
                                self.on_toggle_group(None, self.group_store.get_path(child_iter))
                        elif type(global_groups) == list:
                            child_name = self.group_store.get_value(child_iter, 0)
                            if child_name in global_groups:
                                self.on_toggle_group(None, self.group_store.get_path(child_iter))
                        child_iter = self.group_store.iter_next(child_iter)    
                
                # Mark all groups in the file as active
                class FakeCellRendererToggle(object):
                    def get_active(self):
                        return False
                self.on_global_toggle(FakeCellRendererToggle(),self.group_store.get_path(parent))
            self.output('\n')
        except (LabConfig.NoSectionError, LabConfig.NoOptionError):
            self.output('No default h5 files listed in ' + config_path + '\n\n')
        
        # All set!
        self.output('Ready\n')
    
    def on_window_destroy(self,widget):
        self.to_child.put(['quit',None])
        gtk.main_quit()
    
    def output(self,text,red=False):
        self.output_box.output(text, red)
    
    def on_kill_child_clicked(self, *ignore):
        self.child.terminate()
        self.from_child.put(['done', False])
        self.to_child, self.from_child, self.child = subproc_utils.subprocess_with_queues('batch_compiler.py', self.output_box.port) 
        
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
    
    def on_scroll(self,*args):
        """Queue a redraw of the output on Windows, to prevent visual artifacts
           when the window isn't focused"""
        if os.name == 'nt':
            parent = self.scrolledwindow_output.get_parent()
            if isinstance(parent,gtk.Window):
                parent.queue_draw()
                
    def toggle_compile_or_mise(self, widget):
        self.compile = widget.get_active()
        if self.compile:
            self.checkbuttons_box.set_visible(True)
            self.mise_server_box.set_visible(False)
        else:
            self.checkbuttons_box.set_visible(False)
            self.mise_server_box.set_visible(True)
            
    def toggle_view(self,widget):
        self.view = widget.get_active()
            
    def toggle_run(self,widget):
        self.run = widget.get_active()
    
    def on_new_file_clicked(self,*args):
        chooser = gtk.FileChooserDialog(title='Save new HDF5 file',action=gtk.FILE_CHOOSER_ACTION_SAVE,
                                    buttons=(gtk.STOCK_CANCEL,gtk.RESPONSE_CANCEL,
                                               gtk.STOCK_SAVE,gtk.RESPONSE_OK))
        chooser.add_filter(self.builder.get_object('filefilter1'))
        chooser.set_default_response(gtk.RESPONSE_OK)
        chooser.set_do_overwrite_confirmation(True)
        chooser.set_local_only(False)
        
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
            
    def on_diff_file(self,*args):
        chooser = gtk.FileChooserDialog(title='Diff current globals with HDF file',action=gtk.FILE_CHOOSER_ACTION_OPEN,
                                    buttons=(gtk.STOCK_CANCEL,gtk.RESPONSE_CANCEL,
                                               gtk.STOCK_SAVE,gtk.RESPONSE_OK))
        chooser.add_filter(self.builder.get_object('filefilter1'))
        chooser.set_default_response(gtk.RESPONSE_OK)
        chooser.set_local_only(False)
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
            # instantiate a lyse Run object based on the file to diff
            run = lyse.Run(f)
            # get a dictionary of the sequence_globals for the file to diff
            sequence_globals_1 = run.get_globals_raw()
            # get a dictionary of the sequence globals based on the current globals
            sequence_globals, shots, evaled_globals = self.parse_globals()
            sequence_globals_2 = {}
            for globals_group in sequence_globals.values():
                for key, val in globals_group.items():
                    sequence_globals_2[key] = val[0]
            # do a diff of the two dictionaries
            diff_globals = lyse.dict_diff(sequence_globals_1, sequence_globals_2)
            if len(diff_globals):
                self.output('\nGlobals diff with:\n%s\n' % f)
                diff_keys = diff_globals.keys()
                diff_keys.sort()
                for key in diff_keys:
                    self.output('%s : %s\n' % (key, diff_globals[key]))
            else:
                self.output('Sequence globals are identical to those of:\n%s\n' % f)
            self.output('Ready\n')
            
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
        self.preparse_globals_required.set()
        
    def update_parent_checkbox(self,iter,child_state):
        # Get iter for top level
        parent_iter = self.group_store.iter_parent(iter)
        
        # Are the children in an inconsitent state?
        child_iter = self.group_store.iter_children(parent_iter)
        if child_iter:
            inconsistent = False
            first_state = self.group_store.get(child_iter,1)[0]
            while child_iter:
                # If state doesn't match the first one and the options are visible (not the child used to add a new group)
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
        chooser.set_local_only(False)
        # set this to the current location of the h5_chooser
        if self.globals_path:     
            chooser.set_current_folder(self.globals_path)
        elif self.chooser_labscript_file.get_current_folder() is not None:
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
            self.group_store.insert_before(parent,iter,(new_text,True,"gtk-close","gtk-remove",0,1,1))
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
        self.preparse_globals_required.set()
        
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
        self.preparse_globals_required.set()
        
    def labscript_file_selected(self,chooser):
        filename = chooser.get_filename()
        self.mk_output_dir(filename)
        self.current_labscript_file = filename
    
    def on_reset_dir(self,widget):
        # Ignore of no file selected
        if not self.current_labscript_file:
            return
        self.mk_output_dir(self.current_labscript_file,force_set=True)
    
    def on_edit_script(self,widget):
        # get path to text editor
        editor_path = self.exp_config.get('programs','text_editor')
        editor_args = self.exp_config.get('programs','text_editor_arguments')
        
        # Ignore of no file selected
        if not self.current_labscript_file:
            return
        
        if editor_path:  
            if '{file}' in editor_args:
                editor_args = editor_args.replace('{file}', self.current_labscript_file)
            else:
                editor_args = self.current_labscript_file + " " + editor_args
            
            try:
                subprocess.Popen([editor_path,editor_args])
            except Exception:
                raise Exception("Unable to launch text editor. Check the path is valid in the experiment config file (%s)"%(self.exp_config.config_path))
        else:
            raise Exception("No editor path was specified in the lab config file")
    
    def update_output_dir(self):
        # gtk idles are not threadsafe, must acquire gtk lock:
        with gtk.gdk.lock:
            current_day_dir_suffix = os.path.join(time.strftime('%Y-%b'),time.strftime('%d'))
            if current_day_dir_suffix != self.current_day_dir_suffix:        
                self.current_day_dir_suffix = current_day_dir_suffix
                if self.chooser_labscript_file.get_filename():
                    self.mk_output_dir(self.chooser_labscript_file.get_filename())
            return True
    
    # Makes the output dir for a labscript file
    # If force set is true, we force the output directory back to what it should be
    def mk_output_dir(self,filename,force_set=False):
        # If the output dir has been changed since we last did this, then just pass!
        if not force_set and hasattr(self,'todays_experiment_dir') and self.todays_experiment_dir != self.chooser_output_directory.get_filename():
            print 'mk_output_dir: ignoring request to make new output dir on the share drive'
            print self.chooser_output_directory.get_filename()
            if hasattr(self,'new_path'):
                print self.todays_experiment_dir
            print time.asctime(time.localtime())
            self.globals_path = None
            return
    
        try:
            experiment_prefix = self.exp_config.get('paths','experiment_shot_storage')
            labscript_basename = os.path.basename(filename).strip('.py')
            experiment_dir = os.path.join(experiment_prefix, labscript_basename)
            todays_experiment_dir = os.path.join(experiment_dir, self.current_day_dir_suffix)
            os.makedirs(todays_experiment_dir)
        except OSError, e:  
            print 'mk_output_dir: ignoring exception, folder probably already exists'
            print self.chooser_output_directory.get_filename()
            if hasattr(self,'todays_experiment_dir'):
                print self.todays_experiment_dir
            print time.asctime(time.localtime())
            print e.message
            self.globals_path = None
        except Exception, e:
            print type(e)
            self.globals_path = None
            raise
        if os.path.exists(todays_experiment_dir):     
            print 'mk_output_dir: updating output chooser'
            self.chooser_output_directory.set_current_folder(todays_experiment_dir)

            # Update storage of the path so we can check each time we
            # hit engage, whether we should check to see if the output
            # dir needs to be advanced to todays folder (if run manager
            # is left on overnight)
            #
            # This folder is only stored *IF* we have updated the out
            # dir via this function. Thus function only updates the out
            # dir if the outdir has not been changed since the last time
            # this function ran, and if the share drive is mapped and
            # accessible on windows.

            self.todays_experiment_dir = todays_experiment_dir
            self.globals_path = experiment_dir
        else:
            self.globals_path = None
    
    def labscript_selection_changed(self, chooser):
        """A hack to allow a file which is deleted and quickly recreated to not
        be unselected by the file chooser widget. This is the case when Vim saves a file,
        so this saves Vim users from reselecting the labscript file constantly."""
        if not chooser.get_filename():
            def keep_current_filename(filename):
                with gtk.gdk.lock:
                    chooser.select_filename(filename)
            if self.current_labscript_file:
                gobject.timeout_add(100, keep_current_filename,self.current_labscript_file)
                              
    def on_keypress(self, widget, event):
        if gtk.gdk.keyval_name(event.keyval) == 'F5':
            self.engage()
    
    def engage(self, *args):
        logger.info('engage')
        logger.info(str(self.compile))
        try:
            logger.info('in try statement')
            logger.info('about to parse_globals')
            sequenceglobals, shots, evaled_globals = self.parse_globals()
            if self.compile:
                logger.info('about to make_h5_files globals')
                labscript_file, run_files = self.make_h5_files(sequenceglobals, shots)
                self.compile_queue.put([labscript_file,run_files])
            else:
                threading.Thread(target=self.submit_to_mise,args=(sequenceglobals, shots)).start()
            logger.info('finishing try statement')
        except Exception as e:
            self.output(str(e)+'\n',red=True)
        logger.info('end engage')
        
    def compile_loop(self):
        work_was_done = False
        while True:
            try:
                labscript_file, run_files = self.compile_queue.get(timeout=0.5)
                logger.info('compile_loop: got a job')
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
                logger.info('compile_loop: doing compilation')
                last_run = self.compile_labscript(labscript_file, run_files)
                logger.info('compile_loop: did compilation')
                work_was_done = True
                                
    def on_abort_clicked(self, *args):
        self.aborted = True
    
    def update_active_groups(self):
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
        self.active_groups = active_groups
                    
    def parse_globals(self, raise_exceptions=True):
        if raise_exceptions:
            # Since the following might raise an exception, callers
            # requiring an update of active groups must call it themselves
            # before calling this function. Also this function requires the gtk lock:
            self.update_active_groups()
        sequence_globals = runmanager.get_globals(self.active_groups)
        evaled_globals = runmanager.evaluate_globals(sequence_globals,raise_exceptions)
        shots = runmanager.expand_globals(sequence_globals,evaled_globals)
        return sequence_globals, shots, evaled_globals
    
    def guess_expansion_modes(self, evaled_globals):
        # Do nothing if there were exceptions:
        for group_name in evaled_globals:
            for global_name in evaled_globals[group_name]:
                value = evaled_globals[group_name][global_name]
                if isinstance(value, Exception):
                    # Let ExpansionErrors through through, as they occur
                    # when the user has changed the value without changing
                    # the expansion type:
                    if isinstance(value, runmanager.ExpansionError):
                        continue
                    return False
        # Did the guessed expansion type for any of the globals change?
        expansion_types_changed = False
        for group_name in evaled_globals:
            for global_name in evaled_globals[group_name]:
                new_value = evaled_globals[group_name][global_name]
                try:
                    previous_value = self.previous_evaled_globals[group_name][global_name]
                except KeyError:
                    continue
                new_guess = runmanager.guess_expansion_type(new_value)
                previous_guess = runmanager.guess_expansion_type(previous_value)
                if new_guess != previous_guess:
                    filename = self.active_groups[group_name]
                    runmanager.set_expansion(filename, group_name, global_name, new_guess)
                    expansion_types_changed = True
        self.previous_evaled_globals = evaled_globals
        return expansion_types_changed
        
    def preparse_globals(self):
        # Silence spurious HDF5 errors:
        h5py._errors.silence_errors()
        while True:
            # Wait until we're needed:
            self.preparse_globals_required.wait()
            self.preparse_globals_required.clear()
            # Do some work:
            with gtk.gdk.lock:
                try:
                    self.update_active_groups()
                except Exception as e:
                    error_dialog_from_thread(str(e))
                    continue
            # Expansion mode is automatically updated when the global's type changes. If this occurs,
            # we will have to parse again to include the change: 
            expansions_changed = True
            while expansions_changed:
                sequence_globals, shots, evaled_globals = self.parse_globals(raise_exceptions = False)
                expansions_changed = self.guess_expansion_modes(evaled_globals)
            for tab in self.opentabs:
                with gtk.gdk.lock:
                    tab.update_parse_indication(sequence_globals, evaled_globals)
    
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
        logger.debug(run_files)
        return labscript_file, run_files

    def compile_labscript(self, labscript_file, run_files):
        logger.debug('in compile_labscript')
        try:
            for run_file in run_files:
                self.to_child.put(['compile',[labscript_file,run_file]])
                signal, data = self.from_child.get()
                assert signal == 'done'
                success = data
                if self.aborted or not success:
                    break
                if self.run:
                    self.submit_job(run_file)
                if self.aborted:
                    break
            return run_file
        except Exception as e :
            self.output(str(e)+'\n',red=True)
            self.aborted = True
    
    def submit_job(self, run_file):
        host = self.builder.get_object('entry_server').get_text()
        port = int(self.exp_config.get('ports','BLACS'))
        agnostic_path = shared_drive.path_to_agnostic(run_file)
        self.output('Submitting run file %s.\n'%os.path.basename(run_file))
        try:
            response = subproc_utils.zmq_get(port, host, data=agnostic_path)
            if 'added successfully' in response:
                self.output(response)
            else:
                raise Exception(response)
        except Exception as e:
            self.output('Couldn\'t submit job to control server: %s\n'%str(e),red=True)
            self.aborted = True
    
    def submit_to_mise(self, sequenceglobals, shots):
        port = int(self.exp_config.get('ports','mise'))
        host = self.builder.get_object('entry_mise_server').get_text()
        if self.current_labscript_file is None:
            self.output('no labscript file selected\n', red = True)
            return
        self.output('submitting labscript and parameter space to mise\n')
        with gtk.gdk.lock:
            output_folder = self.chooser_output_directory.get_filename()
            shuffle = self.toggle_shuffle.get_active()
            BLACS_server = self.builder.get_object('entry_server').get_text()
            BLACS_port = int(self.exp_config.get('ports','BLACS'))
        data = ('from runmanager', self.current_labscript_file, 
                sequenceglobals, shots, output_folder, shuffle, BLACS_server, BLACS_port, self.shared_drive_prefix)
        try:
            success, message = subproc_utils.zmq_get(port, host=host, data=data, timeout=2)
        except ZMQError as e:
            success, message = False, 'Could not send to mise: %s\n'%str(e)
        self.output(message, red = not success)
        
logger = setup_logging()
excepthook.set_logger(logger)
if __name__ == "__main__":
    logger.info('\n\n===============starting===============\n')
    gtk.gdk.threads_init()
    
    app = RunManager()
    
    ##########
#    import tracelog
#    tracelog.log('runmanager_trace.log',['__main__','runmanager','h5_lock','zlock'])
#    ##########
    
    with gtk.gdk.lock:
        gtk.main()
