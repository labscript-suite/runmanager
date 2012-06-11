import sys
import traceback
import excepthook
from subproc_utils import setup_connection_with_parent

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
                break
            else:
                raise ValueError(signal)
                    
    def compile(self,labscript_file, run_file):
        # The namespace the labscript will run in:
        sandbox = {}
        old_sys_argv = sys.argv
        old_builtins = __builtins__.__dict__.copy()
        sys.argv = [sys.executable, labscript_file, run_file]
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
            sys.argv = old_sys_argv
            for name in dir(__builtins__):
                if name not in old_builtins:
                    del __builtins__.__dict__[name]
                else:
                    __builtins__.__dict__[name] = old_builtins[name]
            to_delete = []
            for name in sys.modules.copy():
                if 'labscript' in name:
                    del sys.modules[name]  
                   
if __name__ == '__main__':
    to_parent, from_parent, kill_lock = setup_connection_with_parent(lock = True, redirect_output=True)
    batch_processor = BatchProcessor(to_parent,from_parent,kill_lock)
