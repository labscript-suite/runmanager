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

from labscript_utils.ls_zprocess import ProcessTree
process_tree = ProcessTree.connect_to_parent()
to_parent = process_tree.to_parent
from_parent = process_tree.from_parent
kill_lock = process_tree.kill_lock

# Set a meaningful name for zprocess.locking's client id:
process_tree.zlock_client.set_process_name('runmanager.batch_compiler')

import os
import sys
import traceback
from types import ModuleType

import labscript
from labscript_utils.modulewatcher import ModuleWatcher

class BatchProcessor(object):
    def __init__(self, to_parent, from_parent, kill_lock):
        self.to_parent = to_parent
        self.from_parent = from_parent
        self.kill_lock = kill_lock
        # Create a module object in which we execute the user's script. From its
        # perspective it will be the __main__ module:
        self.script_module = ModuleType('__main__')
        # Save the dict so we can reset the module to a clean state later:
        self.script_module_clean_dict = self.script_module.__dict__.copy()
        sys.modules[self.script_module.__name__] = self.script_module

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
                    
    def compile(self, labscript_file, run_file):
        self.script_module.__file__ = labscript_file

        # Save the current working directory before changing it to the location of the
        # user's script:
        cwd = os.getcwd()
        os.chdir(os.path.dirname(labscript_file))

        try:
            # Do not let the modulewatcher unload any modules whilst we're working:
            with kill_lock, module_watcher.lock:
                labscript.labscript_init(run_file, labscript_file=labscript_file)
                with open(labscript_file) as f:
                    code = compile(
                        f.read(), self.script_module.__file__, 'exec', dont_inherit=True
                    )
                    exec(code, self.script_module.__dict__)
            return True
        except Exception:
            traceback_lines = traceback.format_exception(*sys.exc_info())
            del traceback_lines[1:2]
            message = ''.join(traceback_lines)
            sys.stderr.write(message)
            return False
        finally:
            labscript.labscript_cleanup()
            os.chdir(cwd)
            # Reset the script module's namespace:
            self.script_module.__dict__.clear()
            self.script_module.__dict__.update(self.script_module_clean_dict)
                   
if __name__ == '__main__':
    module_watcher = ModuleWatcher() # Make sure modified modules are reloaded
    # Rename this module to '_runmanager_batch_compiler' and put it in sys.modules under
    # that name. The user's script will become the __main__ module:
    __name__ = '_runmanager_batch_compiler'
    sys.modules[__name__] = sys.modules['__main__']
    batch_processor = BatchProcessor(to_parent,from_parent,kill_lock)
