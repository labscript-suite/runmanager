"""Script that runs :meth:`runmanager.differ.globals_diff_shots` between two shot files.

It is run from the command prompt::

$ python runmanager.differ.global_diffs(shot1,shot2)


"""
import sys
from runmanager.differ import globals_diff_shots

if __name__ == '__main__':

    df = globals_diff_shots(sys.argv[1], sys.argv[2])
