#####################################################################
#                                                                   #
# __main__.py                                                        #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program runmanager, in the labscript     #
# suite (see http://labscriptsuite.org), and is licensed under the  #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################
from __future__ import print_function
import os
import errno
import sys
import labscript_utils.excepthook

import time
import itertools
import logging, logging.handlers
import subprocess
import threading
import Queue
import socket

import PyQt4.QtCore as QtCore
import PyQt4.QtGui as QtGui
    
import zprocess.locking, labscript_utils.h5_lock, h5py
from zmq import ZMQError

import pylab
from labscript_utils.labconfig import LabConfig, config_prefix
import labscript_utils.shared_drive as shared_drive
import runmanager
import zprocess
from qtutils.outputbox import OutputBox
from qtutils import inmain, inmain_later, inmain_decorator, UiLoader, inthread, DisconnectContextManager
import qtutils.icons

# Set working directory to runmanager folder, resolving symlinks
runmanager_dir = os.path.dirname(os.path.realpath(__file__))
os.chdir(runmanager_dir)

# Set a meaningful name for zprocess.locking's client id:
zprocess.locking.set_client_process_name('runmanager')
  
# if os.name == 'nt':
    # # Have Windows 7 consider this program to be a separate app, and not
    # # group it with other Python programs in the taskbar:
    # import ctypes
    # myappid = 'monashbec.labscript.runmanager.2-0' # arbitrary string
    # try:
        # ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    # except Exception:
        # pass

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

@inmain_decorator()
def error_dialog(parent, message):
    QtGui.QMessageBox.warning(parent, 'runmanager', message)

def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else: raise
        
def delete_folder_if_empty(folder):
    if folder is None:
        return
    try:
        os.rmdir(folder)
    except OSError:
        pass
            
@inmain_decorator()
def qstring_to_unicode(qstring):
    if sys.version > '3':
        return str(qstring.toUtf8(), encoding="UTF-8")
    else:
        return unicode(qstring.toUtf8(), encoding="UTF-8")
        
class FingerTabBarWidget(QtGui.QTabBar):
    """A TabBar with the tabs on the left and the text horizontal.
    Credit to @LegoStormtroopr, https://gist.github.com/LegoStormtroopr/5075267.
    We will promote the TabBar from the ui file to one of these."""
    def __init__(self, parent=None, width=150, height=32, **kwargs):
        QtGui.QTabBar.__init__(self, parent, **kwargs)
        self.tabSize = QtCore.QSize(width, height)
        self.iconPosition=kwargs.pop('iconPosition',QtGui.QTabWidget.West)
        self.tabSizes = []
  
    def paintEvent(self, event):
        painter = QtGui.QStylePainter(self)
        option = QtGui.QStyleOptionTab()
  
        self.tabSizes = range(self.count())
        #Check if there are any icons to align correctly
        hasIcon = False
        for index in range(self.count()):
            hasIcon |= not(self.tabIcon(index).isNull())
  
        for index in range(self.count()):
            self.initStyleOption(option, index)
            tabRect = self.tabRect(index)
            painter.drawControl(QtGui.QStyle.CE_TabBarTabShape, option)
            tabRect.moveLeft(5)
            icon = self.tabIcon(index).pixmap(self.iconSize())
            if hasIcon:
                alignment = QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter
                if self.iconPosition == QtGui.QTabWidget.West:
                    alignment = QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter
                #if self.iconPosition == QtGui.QTabWidget.East:
                #    alignment = QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter
                #if self.iconPosition == QtGui.QTabWidget.North:
                #    alignment = QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter
                #if self.iconPosition == QtGui.QTabWidget.South:
                #    alignment = QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter
                tabRect.moveLeft(10)
                painter.drawItemPixmap(tabRect,alignment,icon)
                tabRect.moveLeft(self.iconSize().width() + 15)
                tabRect.setWidth(tabRect.width() - self.iconSize().width())
            painter.drawText(tabRect, QtCore.Qt.AlignVCenter |\
                             QtCore.Qt.TextWordWrap, \
                             self.tabText(index));
            self.tabSizes[index] = tabRect.size()
        painter.end()
  
    def tabSizeHint(self,index):
        try:
            return self.tabSizes[index]
        except:
            size = QtGui.QTabBar.tabSizeHint(self,index)
            return QtCore.QSize(size.height(),size.width())
        
    def tabSizeHint(self,index):
        return self.tabSize
        
        
class FingerTabWidget(QtGui.QTabWidget):
    """A QTabWidget equivalent which uses our FingerTabBarWidget"""
    def __init__(self, parent, *args):
        QtGui.QTabWidget.__init__(self, parent, *args)
        self.setTabBar(FingerTabBarWidget(self))
        
        
class GroupTab(object):

    def __init__(self, app, filepath, name):
        raise NotImplementedError
        
    def on_closetab_button_clicked(self,*args):
        raise NotImplementedError
        
    def update_name(self,new_name):
        raise NotImplementedError
    
    def close_tab(self):
        raise NotImplementedError
    
    def focus_cell(self, column, name):
        raise NotImplementedError
            
    def on_edit_name(self, cellrenderer, path, new_text):
        raise NotImplementedError
        
    def on_edit_value(self, cellrenderer, path, new_text):
        raise NotImplementedError
        
    def apply_bool_settings(self, row):
        raise NotImplementedError
            
    def on_toggle_bool_toggled(self, cellrenderer, path):
        raise NotImplementedError
        
    def on_edit_units(self, cellrenderer, path, new_text):
        raise NotImplementedError
        
    def on_edit_expansion(self, cellrenderer, path, new_text):
        raise NotImplementedError
    
    def on_editing_cancelled_units(self, cellrenderer):
        raise NotImplementedError
            
    def on_editing_cancelled_value(self, cellrenderer):
        raise NotImplementedError
            
    def on_editing_started(self, cellrenderer, editable, path):
        raise NotImplementedError
            
    def on_delete_global(self,cellrenderer,path):
        raise NotImplementedError
        
    def update_parse_indication(self, sequence_globals, evaled_globals):
        raise NotImplementedError

        
class RunManager(object):
    
    # Constants for the model in the axes tab:
    AXES_COL_NAME = 0
    AXES_COL_LENGTH = 1
    AXES_COL_SHUFFLE = 2
    
    # Constants for the model in the groups tab:
    GROUPS_COL_NAME = 0
    GROUPS_COL_ACTIVE = 1
    GROUPS_COL_DELETE = 2
    GROUPS_COL_OPENCLOSE = 3
    GROUPS_ROLE_IS_DUMMY_ROW = QtCore.Qt.UserRole + 1
    GROUPS_ROLE_PREVIOUS_NAME = QtCore.Qt.UserRole + 2
    GROUPS_ROLE_SORT_DATA = QtCore.Qt.UserRole + 3
    GROUPS_ROLE_GROUP_IS_OPEN = QtCore.Qt.UserRole + 4
    GROUPS_DUMMY_ROW_TEXT = '<Click to add group>'

    def __init__(self):
    
        loader = UiLoader()
        loader.registerCustomWidget(FingerTabWidget)
        self.ui = loader.load('main.ui')
        self.output_box = OutputBox(self.ui.verticalLayout_output_tab)
        
        self.setup_config()
        self.setup_axes_tab()
        self.setup_groups_tab()
        self.connect_signals()
        
        # The last location from which a labscript file was selected, defaults to labscriptlib:
        self.last_opened_labscript_folder = self.exp_config.get('paths','labscriptlib')
        # The last location from which a globals file was selected, defaults to experiment_shot_storage:
        self.last_opened_globals_folder = self.exp_config.get('paths', 'experiment_shot_storage')
        # The last manually selected shot output folder, defaults to experiment_shot_storage:
        self.last_selected_shot_output_folder = self.exp_config.get('paths', 'experiment_shot_storage')
        self.shared_drive_prefix = self.exp_config.get('paths', 'shared_drive')
        self.experiment_shot_storage = self.exp_config.get('paths','experiment_shot_storage')
        
        # Start the compiler subprocess:
        self.to_child, self.from_child, self.child = zprocess.subprocess_with_queues('batch_compiler.py', self.output_box.port)
        
        # Start a thread to monitor the time of day and create new shot output folders for each day:
        self.output_folder_update_required = threading.Event()
        inthread(self.rollover_shot_output_folder)
        self.ui.show()
    
    def setup_config(self):
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
        
    def setup_axes_tab(self):
        self.axes_model = QtGui.QStandardItemModel()
        
        # Setup the model columns and link to the treeview
        name_header_item = QtGui.QStandardItem('Name')
        name_header_item.setToolTip('The name of the global or zip group being iterated over')
        self.axes_model.setHorizontalHeaderItem(self.AXES_COL_NAME, name_header_item)
        
        length_header_item = QtGui.QStandardItem('Length')
        length_header_item.setToolTip('The number of elements in the axis of the parameter space')
        self.axes_model.setHorizontalHeaderItem(self.AXES_COL_LENGTH, length_header_item)
        
        shuffle_header_item = QtGui.QStandardItem('Shuffle')
        shuffle_header_item.setToolTip('Whether or not the order of the axis should be randomised')
        shuffle_header_item.setIcon(QtGui.QIcon(':qtutils/fugue/arrow-switch'))
        self.axes_model.setHorizontalHeaderItem(self.AXES_COL_SHUFFLE, shuffle_header_item)
        
        self.ui.treeView_axes.setModel(self.axes_model)
        
        # Setup stuff for a custom context menu:
        self.ui.treeView_axes.setContextMenuPolicy(QtCore.Qt.CustomContextMenu);
        
        # Make the actions for the context menu:
        self.action_axes_check_selected = QtGui.QAction(QtGui.QIcon(':qtutils/fugue/ui-check-box'),
                                                        'Check selected', self.ui)
        self.action_axes_uncheck_selected = QtGui.QAction(QtGui.QIcon(':qtutils/fugue/ui-check-box-uncheck'),
                                                          'Uncheck selected', self.ui)
            
    def setup_groups_tab(self):
        self.groups_model = QtGui.QStandardItemModel()
        self.groups_model.setHorizontalHeaderLabels(['File/group name','Active','Delete','Open/Close'])

        self.groups_model.setSortRole(self.GROUPS_ROLE_SORT_DATA)
        self.ui.treeView_groups.setModel(self.groups_model)
        self.ui.treeView_groups.setSelectionMode(QtGui.QTreeView.ExtendedSelection)
        self.ui.treeView_groups.setSortingEnabled(True)
        # Set column widths:
        self.ui.treeView_groups.setColumnWidth(self.GROUPS_COL_NAME, 400)
        # Ensure the clickable region of the open/close button doesn't extend forever:
        self.ui.treeView_groups.header().setStretchLastSection(False)
        # Shrink columns other than the 'name' column to the size of their headers:
        for column in range(self.groups_model.columnCount()):
            if column != self.GROUPS_COL_NAME:
                self.ui.treeView_groups.resizeColumnToContents(column)

        self.ui.treeView_groups.setTextElideMode(QtCore.Qt.ElideMiddle)
        # Setup stuff for a custom context menu:
        self.ui.treeView_groups.setContextMenuPolicy(QtCore.Qt.CustomContextMenu);
        
        # Make the actions for the context menu:
        self.action_groups_set_selection_active = QtGui.QAction(QtGui.QIcon(':qtutils/fugue/ui-check-box'), 'Set selected group(s) active', self.ui)
        self.action_groups_set_selection_inactive = QtGui.QAction(QtGui.QIcon(':qtutils/fugue/ui-check-box-uncheck'), 'Set selected group(s) inactive', self.ui)
        self.action_groups_delete_selection = QtGui.QAction(QtGui.QIcon(':qtutils/fugue/minus'), 'Delete selected group(s)', self.ui)
        self.action_groups_open_selection = QtGui.QAction(QtGui.QIcon(':/qtutils/fugue/plus'), 'Open selected group(s)', self.ui)
        self.action_groups_close_selection = QtGui.QAction(QtGui.QIcon(':/qtutils/fugue/cross'), 'Close selected group(s)', self.ui)
        
    def connect_signals(self):
        # labscript file and folder selection stuff:
        self.ui.toolButton_select_labscript_file.clicked.connect(self.on_select_labscript_file_clicked)
        self.ui.toolButton_select_shot_output_folder.clicked.connect(self.on_select_shot_output_folder_clicked)
        self.ui.toolButton_edit_labscript_file.clicked.connect(self.on_edit_labscript_file_clicked)
        self.ui.toolButton_reset_shot_output_folder.clicked.connect(self.on_reset_shot_output_folder_clicked)
        self.ui.lineEdit_labscript_file.textChanged.connect(self.on_labscript_file_text_changed)
        self.ui.lineEdit_shot_output_folder.textChanged.connect(self.on_shot_output_folder_text_changed)
        
        # compile/send to mise toggling:
        self.ui.radioButton_compile.toggled.connect(self.on_compile_toggled)
        self.ui.radioButton_send_to_mise.toggled.connect(self.on_send_to_mise_toggled)
        
        # Control buttons; engage, abort, restart subprocess:
        self.ui.pushButton_engage.clicked.connect(self.on_engage_clicked)
        self.ui.pushButton_abort.clicked.connect(self.on_abort_clicked)
        self.ui.pushButton_restart_subprocess.clicked.connect(self.on_restart_subprocess_clicked)
        
        # Axes tab; right click menu, menu actions, reordering
        self.ui.treeView_axes.customContextMenuRequested.connect(self.on_treeView_axes_context_menu_requested)
        self.action_axes_check_selected.triggered.connect(self.on_axes_check_selected_triggered)
        self.action_axes_uncheck_selected.triggered.connect(self.on_axes_uncheck_selected_triggered)
        self.ui.toolButton_axis_to_top.clicked.connect(self.on_axis_to_top_clicked)
        self.ui.toolButton_axis_up.clicked.connect(self.on_axis_up_clicked)
        self.ui.toolButton_axis_down.clicked.connect(self.on_axis_down_clicked)
        self.ui.toolButton_axis_to_bottom.clicked.connect(self.on_axis_to_bottom_clicked)
        
        # Groups tab; right click menu, menu actions, open globals file, new globals file, diff globals file, 
        # (TODO add comment for remaining)
        self.ui.treeView_groups.customContextMenuRequested.connect(self.on_treeView_groups_context_menu_requested)
        self.action_groups_check_selected.triggered.connect(self.on_groups_check_selected_triggered)
        self.action_groups_uncheck_selected.triggered.connect(self.on_groups_uncheck_selected_triggered)
        self.ui.pushButton_open_globals_file.clicked.connect(self.on_open_globals_file_clicked)
        self.ui.pushButton_new_globals_file.clicked.connect(self.on_new_globals_file_clicked)
        self.ui.pushButton_diff_globals_file.clicked.connect(self.on_diff_globals_file_clicked)
        self.ui.treeView_groups.pressed.connect(self.on_treeView_groups_pressed)
        self.groups_model.itemChanged.connect(self.on_groups_model_item_changed)
        # A context manager with which we can temporarily disconnect the above connection.
        self.groups_model_item_changed_disconnected = DisconnectContextManager(self.groups_model.itemChanged, self.on_groups_model_item_changed)
        # Todo add remaining
        
    def on_select_labscript_file_clicked(self, checked):
        labscript_file = QtGui.QFileDialog.getOpenFileName(self.ui,
                                                     'Select labscript file',
                                                     self.last_opened_labscript_folder,
                                                     "Python files (*.py)")
        if not labscript_file:
            # User cancelled selection
            return 
        # Convert to standard platform specific path, otherwise Qt likes forward slashes:
        labscript_file = qstring_to_unicode(labscript_file)
        labscript_file = os.path.abspath(labscript_file)
        if not os.path.isfile(labscript_file):
            error_dialog(self.ui, "No such file %s."%labscript_file)
            return
        # Save the containing folder for use next time we open the dialog box:
        self.last_opened_labscript_folder = os.path.dirname(labscript_file)
        # Write the file to the lineEdit:
        self.ui.lineEdit_labscript_file.setText(labscript_file)
        # Tell the output folder thread that the output folder might need updating:
        self.output_folder_update_required.set()
    
    def on_edit_labscript_file_clicked(self, checked):
        # get path to text editor
        editor_path = self.exp_config.get('programs','text_editor')
        editor_args = self.exp_config.get('programs','text_editor_arguments')
        # Get the current labscript file:
        current_labscript_file = self.ui.lineEdit_labscript_file.text()
        current_labscript_file = qstring_to_unicode(current_labscript_file)
        # Ignore if no file selected
        if not current_labscript_file:
            return
        if not editor_path:
            error_dialog(self.ui, "No editor specified in the labconfig.")
        if '{file}' in editor_args:
            # Split the args on spaces into a list, replacing {file} with the labscript file
            editor_args = [arg if arg != '{file}' else current_labscript_file for arg in editor_args.split()]
        else:
            # Otherwise if {file} isn't already in there, append it to the other args:
            editor_args = [current_labscript_file] + editor_args.split()
        try:
            subprocess.Popen([editor_path] + editor_args)
        except Exception as e:
            error_dialog(self.ui, "Unable to launch text editor specified in %s. Error was: %s"%(self.exp_config.config_path, str(e)))
        
    def on_select_shot_output_folder_clicked(self, checked):
        shot_output_folder = QtGui.QFileDialog.getExistingDirectory(self.ui,
                                                     'Select shot output folder',
                                                     self.last_selected_shot_output_folder)
        if not shot_output_folder:
            # User cancelled selection
            return
        # Convert to standard platform specific path, otherwise Qt likes forward slashes:
        shot_output_folder = qstring_to_unicode(shot_output_folder)
        shot_output_folder = os.path.abspath(shot_output_folder)
        # Save the containing folder for use next time we open the dialog box:
        self.last_selected_shot_output_folder = os.path.dirname(shot_output_folder)
        # Write the file to the lineEdit:
        self.ui.lineEdit_shot_output_folder.setText(shot_output_folder)
        # Tell the output folder rollover thread to run an iteration,
        # so that it notices this change (even though it won't do anything now - this is so
        # it can respond correctly if anything else interesting happens within the next second):
        self.output_folder_update_required.set()
    
    def on_reset_shot_output_folder_clicked(self, checked):
        current_default_output_folder = self.get_default_output_folder()
        if current_default_output_folder is None:
            return
        mkdir_p(current_default_output_folder)
        self.ui.lineEdit_shot_output_folder.setText(current_default_output_folder)
        # Tell the output folder rollover thread to run an iteration,
        # so that it notices this change (even though it won't do anything now - this is so
        # it can respond correctly if anything else interesting happens within the next second):
        self.output_folder_update_required.set()
    
    def on_labscript_file_text_changed(self, text):
        # Blank out the 'edit labscript file' button if no labscript file is selected
        enabled = bool(text)
        self.ui.toolButton_edit_labscript_file.setEnabled(enabled)
        # Blank out the 'select shot output folder' button if no labscript file is selected:
        self.ui.toolButton_select_shot_output_folder.setEnabled(enabled)
            
    def on_shot_output_folder_text_changed(self, text):
        # Blank out the 'reset default output folder' button
        # if the user is already using the default output folder
        if qstring_to_unicode(text) == self.get_default_output_folder():
            enabled = False
        else:
            enabled = True
        self.ui.toolButton_reset_shot_output_folder.setEnabled(enabled)
    
    def on_compile_toggled(self, checked):
        if checked:
            # Show the corresponding page of the stackedWidget:
            page = self.ui.stackedWidgetPage_compile
            self.ui.stackedWidget_compile_or_mise.setCurrentWidget(page)
            
    def on_send_to_mise_toggled(self, checked):
        if checked:
            # Show the corresponding page of the stackedWidget:
            page = self.ui.stackedWidgetPage_send_to_mise
            self.ui.stackedWidget_compile_or_mise.setCurrentWidget(page)
    
    def on_engage_clicked(self):
        raise NotImplementedError
        
    def on_abort_clicked(self):
        raise NotImplementedError
        
    def on_restart_subprocess_clicked(self):
        # Kill and restart the compilation subprocess
        self.child.terminate()
        self.from_child.put(['done', False])
        self.to_child, self.from_child, self.child = zprocess.subprocess_with_queues('batch_compiler.py', self.output_box.port)
    
    def on_treeView_axes_context_menu_requested(self, point):
        index = self.ui.treeView_axes.indexAt(point)
        if index.isValid():
            print('yes, context menu! index is', index)
            # if column is 'shuffle':
                # menu = QtGui.QMenu(self.ui)
                # menu.addAction(self.action_axes_check_selected)
                # menu.addAction(self.action_axes_uncheck_selected)
                # menu.exec_(QtGui.QCursor.pos())
    
    def on_axes_check_selected_triggered(self, *args):
        raise NotImplementedError
    
    def on_axes_uncheck_selected_triggered(self, *args):
        raise NotImplementedError
        
    def on_axis_to_top_clicked(self, checked):
        raise NotImplementedError
        
    def on_axis_up_clicked(self, checked):
        raise NotImplementedError
        
    def on_axis_down_clicked(self, checked):
        raise NotImplementedError
        
    def on_axis_to_bottom_clicked(self, checked):
        raise NotImplementedError
    
    def on_treeView_groups_context_menu_requested(self, point):
        index = self.ui.treeView_axes.indexAt(point)
        if 1: # index.isValid():
            print('yes, context menu! index is', index)
            menu = QtGui.QMenu(self.ui)
            menu.addAction(self.action_groups_check_selected)
            menu.addAction(self.action_groups_uncheck_selected)
            menu.exec_(QtGui.QCursor.pos())
        
    def on_groups_check_selected_triggered(self):
        raise NotImplementedError
        
    def on_groups_uncheck_selected_triggered(self):
        raise NotImplementedError
        
    def on_open_globals_file_clicked(self):
        globals_file = QtGui.QFileDialog.getOpenFileName(self.ui,
                                                         'Select globals file',
                                                         self.last_opened_globals_folder,
                                                         "HDF5 files (*.h5)")
        if not globals_file:
            # User cancelled selection
            return
        # Convert to standard platform specific path, otherwise Qt likes forward slashes:
        globals_file = qstring_to_unicode(globals_file)
        globals_file = os.path.abspath(globals_file)
        if not os.path.isfile(globals_file):
            error_dialog(self.ui, "No such file %s."%globals_file)
            return
        # Save the containing folder for use next time we open the dialog box:
        self.last_opened_globals_folder = os.path.dirname(globals_file)
        # Open the file:
        self.open_globals_file(globals_file)
        
    def on_new_globals_file_clicked(self):
        globals_file = QtGui.QFileDialog.getSaveFileName(self.ui,
                                                         'Create new globals file',
                                                         self.last_opened_globals_folder,
                                                         "HDF5 files (*.h5)")
        if not globals_file:
            # User cancelled
            return
        # Convert to standard platform specific path, otherwise Qt likes forward slashes:
        globals_file = qstring_to_unicode(globals_file)
        globals_file = os.path.abspath(globals_file)
            
        # Create the new file and open it:
        runmanager.new_globals_file(globals_file)
        self.open_globals_file(globals_file)
            
            
    def on_diff_globals_file_clicked(self):
        raise NotImplementedError
    
    def on_treeView_groups_pressed(self, index):
        """Here we respond to user clicks on the treeview. We do the following:
        - If the user clicks on the <click to create new group> dummy row, we go into edit mode on it
          so they can enter the name of the new group they want.
        - If the user clicks on the icon to open or close a globals file or a group, we call the appropriate
          open and close methods and update the open/close data role on the model.
        - If the user clicks the 'active' checkbox of a group, we update the check state of the parent
          globals file row, to show whether all, none, or some of its children are checked.
        - If the user clicks the 'active' checkbox of a globals file, we check or uncheck all its
          child groups to match.
        - If the user clicks delete on a globals group, we delete it (and close it if open) after 
          a confirmation dialog.
          
        The checkbox thing - setting children and parent checkboxes to match - would seem
        to belong better in self.on_groups_model_item_changed where most of the data
        consistency stuff lives, but it would be difficult to put it there and not have it recurse
        indefinitely, whilst still ensuring all the changes we want to trigger get done.
        So we're doing it here where we won't recurse - though the changes we make here will still
        trigger self.on_groups_model_item_changed to make the other required changes."""
        
        # If not a left click, return:
        if not qapplication.mouseButtons() == QtCore.Qt.LeftButton:
            return
        item = self.groups_model.itemFromIndex(index)
        # The 'name' item in the same row:
        name_index = index.sibling(index.row(), self.GROUPS_COL_NAME)
        name_item = self.groups_model.itemFromIndex(name_index)
        # The parent item, None if there is no parent:
        parent_item = item.parent()
        # What kind of row did the user click on?
        # A globals file, a group, or a 'click to add new group' row?
        if name_item.data(self.GROUPS_ROLE_IS_DUMMY_ROW).toBool():
            # They clicked on an 'add new group' row. Enter editing
            # mode on the name item so they can enter a name for 
            # the new group:
            self.ui.treeView_groups.setCurrentIndex(name_index)
            self.ui.treeView_groups.edit(name_index)
        elif parent_item is None:
            # They clicked on a globals file row.
            globals_file = name_item.text()
            # What column did they click on?
            if item.column() == self.GROUPS_COL_OPENCLOSE:
                # They clicked the close button. Close the file:
                self.close_globals_file(globals_file)
        else:
            # They clicked on a globals group row.
            globals_file = parent_item.text()
            group_name = name_item.text()
            # What column did they click on?
            if item.column() == self.GROUPS_COL_DELETE:
                # They clicked the delete button. Delete the group:
                self.delete_globals_group(globals_file, group_name, confirm=True)
            elif item.column() == self.GROUPS_COL_OPENCLOSE:
                # They clicked the open/close button. Which is it, open or close?
                group_is_open = item.data(self.GROUPS_ROLE_GROUP_IS_OPEN).toBool()
                # Invert the open/close state. itemChanged will be emitted and 
                # self.on_groups_model_item_changed will handle updating the 
                # other data roles, icons etc:
                item.setData(not group_is_open, self.GROUPS_ROLE_GROUP_IS_OPEN)
                if group_is_open:
                    self.close_group(globals_file, group_name)
                else:
                    self.open_group(globals_file, group_name)
                    
    def on_groups_model_item_changed(self, item):
        """This function is mainly about responding to data changes by making other data changes
        for model consistency. It also handles new group creation.
        When we change things elsewhere, we prefer to only change one thing,
        and the rest of the changes are triggered here. So here we do the following:
        - When GROUPS_ROLE_GROUP_IS_OPEN changes on the open/close column, we set the appropriate icon,
          tooltip, and sort data.
        - When the check state in the 'active' column changes, we update the sort data.
        - When the text of the name column for a group changes, we call self.rename_group()
        - When the text of the <click to add new group> dummy row changes, we call self.new_group()
          
        Be careful not to recurse unsafely in this method - changing something that itself triggers
        further changes is fine so long as they peter out and don't get stuck in a loop. That's what the 
        'if new_group_name != previous_group_name' checks are for, and why we set GROUPS_ROLE_PREVIOUS_NAME
        before changing other data, so that the change we detect won't trigger twice. If recursion needs
        to be stopped, one can disconnect the signal temporarily with the context manager
        self.groups_model_item_changed_disconnected. But use this sparingly, as otherwise there's the risk
        that some data updates that need to happen in response to changes here won't.
        """
        # The parent item, None if there is no parent:
        parent_item = item.parent()
        if item.column() == self.GROUPS_COL_OPENCLOSE:
            # The open/close state of a globals group changed. It is definitely a group,
            # not a file, as the open/close state of a file shouldn't be changing.
            assert parent_item is not None # Just to be sure.
            # Ensure the sort data matches the open/close state:
            group_is_open = item.data(self.GROUPS_ROLE_GROUP_IS_OPEN).toBool()
            item.setData(group_is_open, self.GROUPS_ROLE_SORT_DATA)
            # Set the appropriate icon and tooltip. Changing the icon causes itemChanged
            # to be emitted, even if it the same icon, and even if we were to use the same 
            # QIcon instance. So to avoid infinite recursion we temporarily disconnect 
            # the signal whilst we set the icons.
            with self.groups_model_item_changed_disconnected:
                if group_is_open:
                    item.setIcon(QtGui.QIcon(':qtutils/fugue/minus'))
                    item.setToolTip('Close globals group.')
                else:
                    item.setIcon(QtGui.QIcon(':qtutils/fugue/plus'))
                    item.setToolTip('Load globals group into runmanager.')
        elif item.column() == self.GROUPS_COL_ACTIVE:
            check_state = item.checkState()
            # Ensure sort data matches active state:
            item.setData(check_state, self.GROUPS_ROLE_SORT_DATA)
        elif item.data(self.GROUPS_ROLE_IS_DUMMY_ROW).toBool():
            item_text = qstring_to_unicode(item.text())
            if item_text != self.GROUPS_DUMMY_ROW_TEXT:
                # The user has made a new globals group.
                globals_file = qstring_to_unicode(parent_item.text())
                self.new_group(item, parent_item, globals_file, group_name = item_text)   
        elif item.column() == self.GROUPS_COL_NAME:
            # User has renamed a globals group.
            new_group_name = qstring_to_unicode(item.text())
            previous_group_name = qstring_to_unicode(item.data(self.GROUPS_ROLE_PREVIOUS_NAME).toString())
            globals_file = qstring_to_unicode(parent_item.text())
            if new_group_name != previous_group_name:
                self.rename_group(item, globals_file, previous_group_name, new_group_name)
                
        
    @inmain_decorator()    
    def get_default_output_folder(self):
        """Returns what the default output folder would be right now,
        based on the current date and selected labscript file.
        Returns empty string if no labscript file is selected. Does not create 
        the default output folder, does not check if it exists."""
        sep = os.path.sep
        current_day_folder_suffix = time.strftime('%Y'+sep+'%m'+sep+'%d')
        current_labscript_file = self.ui.lineEdit_labscript_file.text()
        current_labscript_file = qstring_to_unicode(current_labscript_file)
        if not current_labscript_file:
            return ''
        current_labscript_basename = os.path.splitext(os.path.basename(current_labscript_file))[0]
        default_output_folder = os.path.join(self.experiment_shot_storage, 
                                    current_labscript_basename, current_day_folder_suffix)
        return default_output_folder
    
    def rollover_shot_output_folder(self):
        """Runs in a thread, checking once a second if it is a new day or the 
        labscript file has changed. If it is or has, sets the default folder in 
        which compiled shots will be put. Does not create the folder if it does
        not already exists, this will be done at compile-time.
        Will run immediately without waiting a full second if the threading.Event 
        self.output_folder_update_required is set() from anywhere."""
        previous_default_output_folder = self.get_default_output_folder()
        while True:
            # Wait up to one second, shorter if the Event() gets set() by someone:
            self.output_folder_update_required.wait(1)
            self.output_folder_update_required.clear()
            previous_default_output_folder = self.check_output_folder_update(previous_default_output_folder)
            
    @inmain_decorator()
    def check_output_folder_update(self, previous_default_output_folder):
        """Do a single check of whether the output folder needs updating.
        This is implemented as a separate function to the above loop so that
        the whole check happens at once in the Qt main thread and hence is atomic
        and can't be interfered with by other Qt calls in the program."""
        current_default_output_folder = self.get_default_output_folder()
        if current_default_output_folder is None:
            # No labscript file selected:
            return previous_default_output_folder
        currently_selected_output_folder = self.ui.lineEdit_shot_output_folder.text()
        currently_selected_output_folder = qstring_to_unicode(currently_selected_output_folder)
        if current_default_output_folder != previous_default_output_folder:
            # It's a new day, or a new labscript file.
            # Is the user using default folders?
            if currently_selected_output_folder == previous_default_output_folder:
                # Yes they are. In that case, update to use the new folder:
                self.ui.lineEdit_shot_output_folder.setText(current_default_output_folder)
            return current_default_output_folder
        return previous_default_output_folder
    
    def open_globals_file(self, globals_file):
        # Do nothing if this file is already open:
        if self.groups_model.findItems(globals_file, column=self.GROUPS_COL_NAME):
            return
        
        # Get the groups:       
        groups = runmanager.get_grouplist(globals_file)
        # Add the parent row:
        file_name_item = QtGui.QStandardItem(globals_file)
        file_name_item.setEditable(False)
        file_name_item.setToolTip(globals_file)
        # Sort column by name:
        file_name_item.setData(globals_file, self.GROUPS_ROLE_SORT_DATA)
        
        file_active_item = QtGui.QStandardItem()
        file_active_item.setCheckable(True)
        file_active_item.setCheckState(QtCore.Qt.Checked)
        # Sort column by CheckState - must keep this updated when checkstate changes:
        file_active_item.setData(QtCore.Qt.Checked, self.GROUPS_ROLE_SORT_DATA)
        file_active_item.setEditable(False)
        file_active_item.setToolTip('Check to set all the file\'s groups as active.')
        
        file_delete_item = QtGui.QStandardItem() # Blank, only groups have a delete button
        file_delete_item.setEditable(False)
        
        file_close_item = QtGui.QStandardItem()
        file_close_item.setIcon(QtGui.QIcon(':qtutils/fugue/cross'))
        file_close_item.setEditable(False)
        file_close_item.setToolTip('Close globals file.')
        
        self.groups_model.appendRow([file_name_item, file_active_item, file_delete_item, file_close_item])
        
        # Add the groups as children:
        for group_name in groups:
            row = self.make_group_row(group_name)
            file_name_item.appendRow(row)
            
        # Finally, add the <Click to add group> row at the bottom:
        dummy_name_item = QtGui.QStandardItem(self.GROUPS_DUMMY_ROW_TEXT)
        dummy_name_item.setToolTip('Click to add group')
        # This lets later code know that this row does
        # not correspond to an actual globals group:
        dummy_name_item.setData(True, self.GROUPS_ROLE_IS_DUMMY_ROW)
        dummy_name_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsEditable) # Clears the 'selectable' flag
        
        dummy_active_item = QtGui.QStandardItem()
        dummy_active_item.setEditable(False)
        dummy_active_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsEditable)
        
        dummy_delete_item = QtGui.QStandardItem()
        dummy_delete_item.setEditable(False)
        dummy_delete_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsEditable)
        
        dummy_open_close_item = QtGui.QStandardItem()
        dummy_open_close_item.setEditable(False)
        dummy_open_close_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsEditable)
        
        # Not setting anything as the above items' sort role has the effect of ensuring
        # this row is always sorted to the end of the list, without us having to implement
        # any custom sorting methods or subclassing anything, yay.
        
        file_name_item.appendRow([dummy_name_item, dummy_active_item, dummy_delete_item, dummy_open_close_item])
        # Expand the child items to be visible:
        self.ui.treeView_groups.setExpanded(file_name_item.index(), True)
    
    def make_group_row(self, group_name):
        """Returns a new row representing one group in the groups tab, ready to be
        inserted into the model."""
        group_name_item = QtGui.QStandardItem(group_name)
        # We keep the previous name around so that we can detect what changed:
        group_name_item.setData(group_name, self.GROUPS_ROLE_PREVIOUS_NAME)
        # Sort column by name:
        group_name_item.setData(group_name, self.GROUPS_ROLE_SORT_DATA)
        
        group_active_item = QtGui.QStandardItem()
        group_active_item.setCheckable(True)
        group_active_item.setCheckState(QtCore.Qt.Checked)
        # Sort column by CheckState - must keep this updated whenever the checkstate changes:
        group_active_item.setData(QtCore.Qt.Checked, self.GROUPS_ROLE_SORT_DATA)
        group_active_item.setEditable(False)
        group_active_item.setToolTip('Whether or not the globals within this group should be used by runmanager for compilation.')
        
        group_delete_item = QtGui.QStandardItem()
        group_delete_item.setIcon(QtGui.QIcon(':qtutils/fugue/minus'))
        # Must be set to something so that the dummy row doesn't get sorted first:
        group_delete_item.setData(False, self.GROUPS_ROLE_SORT_DATA)
        group_delete_item.setEditable(False)
        group_delete_item.setToolTip('Delete globals group from file.')
        
        group_open_close_item = QtGui.QStandardItem()
        group_open_close_item.setIcon(QtGui.QIcon(':qtutils/fugue/plus'))
        group_open_close_item.setData(False, self.GROUPS_ROLE_GROUP_IS_OPEN)
        # Sort column by whether group is open - must keep this manually updated when the state changes:
        group_open_close_item.setData(False, self.GROUPS_ROLE_SORT_DATA)
        group_open_close_item.setEditable(False)
        group_open_close_item.setToolTip('Load globals group into runmananger.')
        
        row = [group_name_item, group_active_item, group_delete_item, group_open_close_item]
        return row
    
    def close_globals_file(self, globals_file):
        raise NotImplementedError('close globals file')
    
    def new_group(self, item, parent_item, globals_file, group_name):
        """In response to the user changing the text in the <click to add new group> item
        in the groups tab, attempt to create a new group and add it to the model."""
        try:
            runmanager.new_group(globals_file, group_name)
        except Exception as e:
            error_dialog(self.ui, str(e))
        else:
            # Insert the newly created globals group into the model,
            # as a child row of the globals file it belong to.
            group_row = self.make_group_row(group_name)
            last_index = parent_item.rowCount()
            # Insert it as the row before the last (dummy) row: 
            parent_item.insertRow(last_index-1, group_row)
        finally:
            # Set the dummy row's text back ready for another group to be created:
            item.setText(self.GROUPS_DUMMY_ROW_TEXT)
            
    def open_group(self, globals_file, group_name):
        raise NotImplementedError('open group')
    
    def close_group(self, globals_file, group_name):
        raise NotImplementedError('close group')
    
    def rename_group(self, item, globals_file, previous_group_name, new_group_name):
        """In response to the user changing the text in the 'name' item of a group
        in the groups tab, attempt a rename of that group and set the item's text
        and GROUPS_ROLE_PREVIOUS_NAME data role accordingly, conditional on success."""
        try:
            runmanager.rename_group(globals_file, previous_group_name, new_group_name)
        except Exception as e:
            error_dialog(self.ui, str(e))
            # Set the item text back to the old name, since the rename failed:
            item.setText(previous_group_name)
        else:
            item.setData(new_group_name, self.GROUPS_ROLE_PREVIOUS_NAME) 
            
    def delete_group(self, globals_file, group_name, confirm=True):
        raise NotImplementedError('delete group')
        
    def on_window_destroy(self, widget):
        # What do we need to do here again? Check the gtk code. Also move this up
        # To where the other 'on_such_and_such' methods are, if we end up needing to
        # implement it.
        raise NotImplementedError('on window destroy')
    
    def on_save_configuration(self, widget):
        raise NotImplementedError
    
    def save_configuration(self, filename=None):
        raise NotImplementedError
        
    def on_load_configuration(self, filename):
        raise NotImplementedError        

    def load_configuration(self, filename=None):
        raise NotImplementedError
         
    def on_keypress(self, widget, event):
        raise NotImplementedError
    
    def compile_loop(self):
        raise NotImplementedError
        
    def update_active_groups(self):
        raise NotImplementedError
                    
    def parse_globals(self, raise_exceptions=True, expand_globals=True):
        raise NotImplementedError
    
    def guess_expansion_modes(self, evaled_globals, global_hierarchy, expansions):
        raise NotImplementedError
        
    def preparse_globals(self):
        raise NotImplementedError
    
    def make_h5_files(self, sequence_globals, shots):
        raise NotImplementedError

    def compile_labscript(self, labscript_file, run_files):
        raise NotImplementedError
    
    def submit_job(self, run_file):
        raise NotImplementedError
    
    def submit_to_mise(self, sequenceglobals, shots):
        raise NotImplementedError
        

if __name__ == "__main__":
    logger = setup_logging()
    labscript_utils.excepthook.set_logger(logger)
    logger.info('\n\n===============starting===============\n')
    qapplication = QtGui.QApplication(sys.argv)
    app = RunManager()
    sys.exit(qapplication.exec_())
