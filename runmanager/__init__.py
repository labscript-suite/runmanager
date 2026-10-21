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

import labscript_utils.h5_lock # noqa: F401 (needed to ensure lock initializes properly)

from .__version__ import __version__

from runmanager.exceptions import ExpansionError
from runmanager.group_manager import (
    is_valid_python_identifier,
    is_valid_hdf5_group_name,
    new_globals_file,
    add_expansion_groups,
    get_grouplist,
    new_group,
    copy_group,
    rename_group,
    delete_group,
    get_globalslist,
    new_global,
    rename_global,
    get_value,
    set_value,
    get_units,
    set_units,
    get_expansion,
    set_expansion,
    delete_global,
    guess_expansion_type,
    get_all_groups,
    get_globals,
    get_shot_globals,
)
from runmanager.evaluator import iterator_to_tuple, evaluate_globals
from runmanager.expander import expand_globals, count_shots
from runmanager.compiler import (
    next_sequence_index,
    new_sequence_details,
    make_run_files,
    make_single_run_file,
    make_run_file_from_globals_files,
    compile_labscript_async,
    compile_multishot_async,
    compile_labscript_with_globals_files_async,
)
from runmanager.differ import flatten_globals, dict_diff, globals_diff_groups, globals_diff_shots
from runmanager.tokenizer import find_comments, remove_comments_and_tokenify

__all__ = [
    'ExpansionError',
    'is_valid_python_identifier',
    'is_valid_hdf5_group_name',
    'new_globals_file',
    'add_expansion_groups',
    'get_grouplist',
    'new_group',
    'copy_group',
    'rename_group',
    'delete_group',
    'get_globalslist',
    'new_global',
    'rename_global',
    'get_value',
    'set_value',
    'get_units',
    'set_units',
    'get_expansion',
    'set_expansion',
    'delete_global',
    'guess_expansion_type',
    'get_all_groups',
    'get_globals',
    'get_shot_globals',
    'iterator_to_tuple',
    'evaluate_globals',
    'expand_globals',
    'count_shots',
    'next_sequence_index',
    'new_sequence_details',
    'make_run_files',
    'make_single_run_file',
    'make_run_file_from_globals_files',
    'compile_labscript_async',
    'compile_multishot_async',
    'compile_labscript_with_globals_files_async',
    'flatten_globals',
    'dict_diff',
    'globals_diff_groups',
    'globals_diff_shots',
    'find_comments',
    'remove_comments_and_tokenify',
]
