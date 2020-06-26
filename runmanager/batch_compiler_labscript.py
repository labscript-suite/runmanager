#####################################################################
#                                                                   #
# /batch_compiler_labscript.py                                      #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program runmanager, in the labscript     #
# suite (see http://labscriptsuite.org), and is licensed under the  #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################

try:
    from labscript_utils import check_version
except ImportError:
    raise ImportError('Require labscript_utils > 2.1.0')

check_version('labscript_utils', '2', '3')
check_version('labscript', '2.4', '3')
check_version('zprocess', '1.1.5', '3')

import labscript
from labscript_utils.modulewatcher import ModuleWatcher
from labscript_utils.ls_zprocess import ProcessTree
process_tree = ProcessTree.connect_to_parent()
to_parent = process_tree.to_parent
from_parent = process_tree.from_parent
kill_lock = process_tree.kill_lock

# Set a meaningful name for zprocess.locking's client id:
process_tree.zlock_client.set_process_name('runmanager.batch_compiler')

from runmanager.batch_compiler import BatchProcessorBase


class BatchProcessor(BatchProcessorBase):
    module_name = "labscript"

    def module_init(self, labscript_file, run_file):
        labscript.labscript_init(run_file, labscript_file=labscript_file, load_globals_values=False)

    def module_cleanup(self, labscript_file, run_file):
        labscript.labscript_cleanup()

    def module_protected_global_names(self):
        return labscript.__dict__.keys()


if __name__ == '__main__':
    module_watcher = ModuleWatcher()  # Make sure modified modules are reloaded
    batch_processor = BatchProcessor()
    batch_processor.mainloop(to_parent, from_parent, kill_lock, module_watcher)
