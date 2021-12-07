"""Script that runs :meth:`runmanager.globals_diff_shots` between two shot files.

It is run from the command prompt::

$ python runmanager.global_diffs(shot1,shot2)


"""
import sys
from runmanager import globals_diff_shots

if __name__ == '__main__':

    df = globals_diff_shots(sys.argv[1], sys.argv[2])
