#####################################################################
#                                                                   #
# /__init__.py                                                      #
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
import warnings

import labscript_utils.h5_lock
import numpy as np

from .__version__ import __version__

# Used in BLACS
from runmanager.compiler import compile_labscript_with_globals_files_async

def iterator_to_tuple(iterator, max_length=1000000):
    # We want to prevent infinite length tuples, but we cannot know
    # whether they are infinite or not in advance. So we'll convert to
    # a tuple only if the length is less than max_length:
    temp_list = []
    for i, element in enumerate(iterator):
        temp_list.append(element)
        if i == max_length:
            raise ValueError('This iterator is very long, possibly infinite. ' +
                             'Runmanager cannot create an infinite number of shots. ' +
                             'If you really want an iterator longer than %d, ' % max_length +
                             'please modify runmanager.iterator_to_tuple and increase max_length.')
    return tuple(temp_list)


def dict_diff(dict1, dict2):
    """Return the difference between two dictionaries as a dictionary of key: [val1, val2] pairs.
    Keys unique to either dictionary are included as key: [val1, '-'] or key: ['-', val2]."""
    diff_keys = []
    common_keys = np.intersect1d(list(dict1.keys()), list(dict2.keys()))
    for key in common_keys:
        if np.iterable(dict1[key]) or np.iterable(dict2[key]):
            if not np.array_equal(dict1[key], dict2[key]):
                diff_keys.append(key)
        else:
            if dict1[key] != dict2[key]:
                diff_keys.append(key)

    dict1_unique = [key for key in dict1.keys() if key not in common_keys]
    dict2_unique = [key for key in dict2.keys() if key not in common_keys]

    diff = {}
    for key in diff_keys:
        diff[key] = [dict1[key], dict2[key]]

    for key in dict1_unique:
        diff[key] = [dict1[key], '-']

    for key in dict2_unique:
        diff[key] = ['-', dict2[key]]

    return diff
