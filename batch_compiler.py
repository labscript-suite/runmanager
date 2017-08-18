#####################################################################
#                                                                   #
# /batch_compiler.py                                                #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program runmanager, in the labscript     #
# suite (see http://labscriptsuite.org), and is licensed under the  #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################
from __future__ import division, unicode_literals, print_function, absolute_import

import sys
import traceback
from zprocess import setup_connection_with_parent
to_parent, from_parent, kill_lock = setup_connection_with_parent(lock = True)

import labscript
import labscript_utils.excepthook
from labscript_utils.modulewatcher import ModuleWatcher

class BatchProcessor(object):
    def __init__(self, to_parent, from_parent, kill_lock):
        self.to_parent = to_parent
        self.from_parent = from_parent
        self.kill_lock = kill_lock
        self.mainloop()
        
    def mainloop(self):
        while True:
            signal, data =  self.from_parent.get()
            if signal == 'compile':
                success = self.compile(*data)
                self.to_parent.put(['done',success])
            elif signal == 'quit':
                sys.exit(0)
            else:
                raise ValueError(signal)
                    
    def compile(self,labscript_file, run_file):
        # The namespace the labscript will run in:
        sandbox = {'__name__':'__main__'}
        try:
            # Do not let the modulewatcher unload any modules whilst we're working:
            with kill_lock, module_watcher.lock:
                labscript.labscript_init(run_file, labscript_file=labscript_file)
                execfile(labscript_file,sandbox,sandbox)
            return True
        except:
            traceback_lines = traceback.format_exception(*sys.exc_info())
            del traceback_lines[1:2]
            message = ''.join(traceback_lines)
            sys.stderr.write(message)
            return False
        finally:
            labscript.labscript_cleanup()
                   
if __name__ == '__main__':
    module_watcher = ModuleWatcher() # Make sure modified modules are reloaded
    batch_processor = BatchProcessor(to_parent,from_parent,kill_lock)
