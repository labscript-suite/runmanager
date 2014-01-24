import sys
import traceback

# Supress labscript's automatic initialisation (labscript looks in the __main__ module for this):
labscript_auto_init = False
import labscript
    
import labscript_utils.excepthook
from subproc_utils import setup_connection_with_parent
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
        labscript.labscript_init(run_file, labscript_file=labscript_file)
        try:
            with kill_lock:
                execfile(labscript_file,sandbox,sandbox)
            return True
        except:
            traceback_lines = traceback.format_exception(sys.exc_type, sys.exc_value, sys.exc_traceback)
            del traceback_lines[1:2]
            message = ''.join(traceback_lines)
            sys.stderr.write(message)
            return False
        finally:
            labscript.labscript_cleanup()
                   
if __name__ == '__main__':
    to_parent, from_parent, kill_lock = setup_connection_with_parent(lock = True)
    
    module_watcher = ModuleWatcher() # Make sure modified modules are reloaded
    batch_processor = BatchProcessor(to_parent,from_parent,kill_lock)
