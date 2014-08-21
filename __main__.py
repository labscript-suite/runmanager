#####################################################################
#                                                                   #
# /main.pyw                                                         #
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
from qtutils import inmain, inmain_later, UiLoader, inthread
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

def error_dialog(message):
    raise NotImplementedError

def error_dialog_from_thread(message):
    raise NotImplementedError
    
def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else: raise
        
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
    def __init__(self):
    
    
        loader = UiLoader()
        loader.registerCustomWidget(FingerTabWidget)
        self.ui = loader.load('main.ui')
        self.output_box = OutputBox(self.ui.verticalLayout_output_tab)
        
        self.setup_config()
        self.setup_axes_tab()
        self.setup_groups_tab()
        
        # The last location from which a labscript file was selected, defaults to labscriptlib:
        self.last_opened_labscript_folder = self.exp_config.get('paths','labscriptlib')
        # The last location from which a globals file was selected, defaults to experiment_shot_storage:
        self.last_opened_globals_folder = self.exp_config.get('paths', 'experiment_shot_storage')
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
        self.axes_model.setHorizontalHeaderLabels(['Name','Length','Shuffle'])
        self.ui.treeView_axes.setModel(self.axes_model)
    
    def setup_groups_tab(self):
        self.groups_model = QtGui.QStandardItemModel()
        self.groups_model.setHorizontalHeaderLabels(['Open/Close','Delete','Active','File/groups'])
        self.ui.treeView_groups.setModel(self.groups_model)
    
    def get_default_output_folder(self):
        """Returns what the default output folder would be right now,
        based on the current date and selected labscript file.
        Returns None if no labscript file is selected or if the file does not exist.
        Does not create the default output folder, does not check if it exists."""
        sep = os.path.sep
        current_day_folder_suffix = time.strftime('%Y'+sep+'%m'+sep+'%d')
        current_labscript_file = inmain(self.ui.lineEdit_labscript_file.text)
        if not os.path.exists(current_labscript_file):
            return None
        current_labscript_basename = os.path.splitext(os.path.basename(current_labscript_file))[0]
        default_output_folder = os.path.join(self.experiment_shot_storage, labscript_basename, self.current_day_dir_suffix)
        return default_output_folder
    
    def delete_folder_if_empty(self, folder):
        if os.path.isdir(folder) and not os.listdir(folder):
            os.unlink(folder)
                    
    def rollover_shot_output_folder(self):
        """Runs in a thread, checking once a second if it is a new day or the 
        labscript file has changed. If it is or has, creates a new folder in 
        which compiled shots will be put. Deletes the previous folder if it is empty,
        so that leaving runmanager open all the time doesn't create tons of empty folders.
        Will immediately without waiting a full second if the threading.Event() 
        self.output_folder_update_required is set() from anywhere.
        Also clears the selected folder if it does not exist"""
        previous_default_output_folder = self.get_default_output_folder()
        while True:
            # Wait up to one second, shorter if the Event() gets set() by someone:
            self.output_folder_update_required.wait(1)
            self.output_folder_update_required.clear()
            current_default_output_folder = self.get_default_output_folder()
            if current_default_output_folder is None:
                # No labscript file selected, or does not exist:
                continue
            currently_selected_output_folder = inmain(self.ui.lineEdit_shot_output_folder.text)
            # If the currently selected output folder does not exist, go back to default:
            if not os.path.isdir(currently_selected_output_folder):
                inmain(self.ui.lineEdit_shot_output_folder.setText, current_default_output_folder)
                # Ensure the default does exist:
                mdir_p(current_default_output_folder)
            if current_default_output_folder != previous_default_output_folder:
                # It's a new day, or a new labscript file!
                delete_folder_if_empty(previous_default_output_folder)
                # Is the user even using default folders?
                if currently_selected_output_folder == previous_default_output_folder:
                    # Yes they are! In that case, update to use the new folder:
                    mdir_p(current_default_output_folder)
                    inmain(self.ui.lineEdit_shot_output_folder.setText,current_default_output_folder)
                    
    def on_window_destroy(self,widget):
        raise NotImplementedError
    
    def on_save_configuration(self,widget):
        raise NotImplementedError
    
    def save_configuration(self,filename=None):
        raise NotImplementedError
        
    def on_load_configuration(self,filename):
        raise NotImplementedError        

    def load_configuration(self,filename=None):
        raise NotImplementedError
         
    def update_parent_checkbox_by_file(self,filepath):
        raise NotImplementedError
        
    def on_kill_child_clicked(self, *ignore):
        self.child.terminate()
        self.from_child.put(['done', False])
        self.to_child, self.from_child, self.child = zprocess.subprocess_with_queues('batch_compiler.py', self.output_box.port) 
        
    def toggle_compile_or_mise(self, widget):
        raise NotImplementedError
            
    def toggle_view(self,widget):
        raise NotImplementedError
            
    def toggle_run(self,widget):
        raise NotImplementedError
    
    def on_new_file_clicked(self,*args):
        raise NotImplementedError
            
    def on_diff_file(self,*args):
        raise NotImplementedError
            
    def on_global_toggle(self, cellrenderer_toggle, path):
        raise NotImplementedError
        
    def update_parent_checkbox(self,iter,child_state):
        raise NotImplementedError
    
    def on_open_h5_file(self, *args):      
        raise NotImplementedError

    def on_add_group(self,cellrenderer,path,new_text):
        raise NotImplementedError

    def on_delete_group(self,cellrenderer,path):
        raise NotImplementedError
        
    def on_toggle_group(self,cellrenderer,path):
        raise NotImplementedError
        
    # This function is poorly named. It actually only updates the +/x icon in the group list!   
    # This function is called by the GroupTab class to clean up the state of the group treeview
    def close_tab(self,filepath,group_name):
        raise NotImplementedError
        
    def labscript_file_selected(self,chooser):
        raise NotImplementedError
    
    def on_reset_dir(self,widget):
        raise NotImplementedError
    
    def on_edit_script(self,widget):
        raise NotImplementedError
    
    def update_output_dir(self):
        raise NotImplementedError
    
    def mk_output_dir(self,filename,force_set=False):
        raise NotImplementedError
    
    def labscript_selection_changed(self, chooser):
        # Probably not required anymore, because Qt probably won't 
        # unselect a deleted and recreated file like GTK does:
        raise NotImplementedError
        
    def on_keypress(self, widget, event):
        raise NotImplementedError
    
    def engage(self, *args):
        raise NotImplementedError
        
    def compile_loop(self):
        raise NotImplementedError
        
    def on_abort_clicked(self, *args):
        raise NotImplementedError
    
    def update_active_groups(self):
        raise NotImplementedError
                    
    def parse_globals(self, raise_exceptions=True,expand_globals=True):
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
