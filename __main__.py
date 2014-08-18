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

import os
import sys
import labscript_utils.excepthook

import time
import itertools
import logging, logging.handlers
import subprocess
import threading
import Queue
import urllib, urllib2, socket

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

# Set working directory to runmanager folder, resolving symlinks
runmanager_dir = os.path.dirname(os.path.realpath(__file__))
os.chdir(runmanager_dir)

# Set a meaningful name for zprocess.locking's client id:
zprocess.locking.set_client_process_name('runmanager')
  
if os.name == 'nt':
    # Have Windows 7 consider this program to be a separate app, and not
    # group it with other Python programs in the taskbar:
    import ctypes
    myappid = 'monashbec.labscript.runmanager.2-0' # arbitrary string
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception:
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
    raise NotImplementedError

def error_dialog_from_thread(message):
    raise NotImplementedError
    
    
class FingerTabBarWidget(QtGui.QTabBar):
    """A TabBar with the tabs on the left and the text horizontal.
    Credit to @LegoStormtroopr, https://gist.github.com/LegoStormtroopr/5075267.
    We will promote the TabBar from the ui file to one of these."""
    def __init__(self, parent=None, *args, **kwargs):
        self.tabSize = QtCore.QSize(kwargs.pop('width',100), kwargs.pop('height',25))
        QtGui.QTabBar.__init__(self, parent, *args, **kwargs)
                 
    def paintEvent(self, event):
        painter = QtGui.QStylePainter(self)
        option = QtGui.QStyleOptionTab()
 
        for index in range(self.count()):
            self.initStyleOption(option, index)
            tabRect = self.tabRect(index)
            tabRect.moveLeft(10)
            painter.drawControl(QtGui.QStyle.CE_TabBarTabShape, option)
            painter.drawText(tabRect, QtCore.Qt.AlignVCenter |\
                             QtCore.Qt.TextDontClip, \
                             self.tabText(index));
        painter.end()
        
    def tabSizeHint(self,index):
        return self.tabSize
        
        
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


class ParameterSpaceOverview(object):
    def __init__(self, container):
        raise NotImplementedError

    def set_axes(self, axes):
        raise NotImplementedError
            
class RunManager(object):
    def __init__(self):
        loader = UiLoader()
        loader.registerCustomWidget(QueueTreeview)
        loader.registerCustomPromotion('BLACS',BLACSWindow)
        self.ui = loader.load(os.path.join(os.path.dirname(os.path.realpath(__file__)),'main.ui'))
        
    
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
        
    def output(self,text,red=False):
        self.output_box.output(text, red)
    
    def on_kill_child_clicked(self, *ignore):
        self.child.terminate()
        self.from_child.put(['done', False])
        self.to_child, self.from_child, self.child = zprocess.subprocess_with_queues('batch_compiler.py', self.output_box.port) 
        
    def pop_out_in(self,widget):
        raise NotImplementedError
    
    def on_scroll(self,*args):
        raise NotImplementedError
                
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
    qapplication = QApplication(sys.argv)
    app = RunManager()
    app = BLACS(qapplication)
    sys.exit(qapplication.exec_())
