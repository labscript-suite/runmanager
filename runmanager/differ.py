#####################################################################
#                                                                   #
# __main__.py                                                       #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program runmanager, in the labscript     #
# suite (see http://labscriptsuite.org), and is licensed under the  #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################
"""Functions for comparing two globals groups.
"""

import numpy as np

from runmanager.evaluator import _evaluate_globals
from runmanager.group_manager import get_all_groups, get_globals
from runmanager.tokenizer import remove_comments_and_tokenify


def flatten_globals(sequence_globals, evaluated=False):
    """Flattens the data structure of the globals. If evaluated=False,
    saves only the value expression string of the global, not the
    units or expansion."""
    flattened_sequence_globals = {}
    for globals_group in sequence_globals.values():
        for name, value in globals_group.items():
            if evaluated:
                flattened_sequence_globals[name] = value
            else:
                value_expression, _, _ = value
                flattened_sequence_globals[name] = value_expression
    return flattened_sequence_globals


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

    dict1_unique = [key for key in dict1 if key not in common_keys]
    dict2_unique = [key for key in dict2 if key not in common_keys]

    diff = {}
    for key in diff_keys:
        diff[key] = [dict1[key], dict2[key]]

    for key in dict1_unique:
        diff[key] = [dict1[key], '-']

    for key in dict2_unique:
        diff[key] = ['-', dict2[key]]

    return diff


DIFF_COLUMNS = ['Prev (Eval)', 'Current (Eval)', 'Prev (Raw)', 'Current (Raw)']

# Maximum length of a value or expression in the text output of a diff
MAX_TEXT_LENGTH = 200


def _shorten(text):
    """Replace each run of whitespace in text with a single space, and replace the
    middle of the result with '...' if it is longer than MAX_TEXT_LENGTH"""
    text = ' '.join(text.split())
    if len(text) > MAX_TEXT_LENGTH:
        n_end = (MAX_TEXT_LENGTH - 5) // 2
        n_start = MAX_TEXT_LENGTH - 5 - n_end
        text = text[:n_start] + ' ... ' + text[-n_end:]
    return text


def _diff_text(differences):
    """Format the differences, a dict of global name: list of a string for each of
    DIFF_COLUMNS, as a block of lines per global"""
    width = max(len(column) for column in DIFF_COLUMNS) + 2
    blocks = []
    for name in sorted(differences):
        lines = [name]
        for column, text in zip(DIFF_COLUMNS, differences[name]):
            lines.append(f'    {column + ":":<{width}}{_shorten(text)}')
        blocks.append('\n'.join(lines))
    return '\n\n'.join(blocks)


def globals_diff_groups(active_groups, other_groups, return_string=True):
    """Given two sets of globals groups, perform a diff of the raw
    and evaluated globals. If return_string is True, returns the diff as a
    string. Otherwise prints it and returns it as a pandas DataFrame, which is
    empty if there are no differences. In the string, values and expressions longer
    than MAX_TEXT_LENGTH characters are shortened; the DataFrame has them in full."""
    our_sequence_globals = get_globals(active_groups)
    other_sequence_globals = get_globals(other_groups)

    # evaluate globals
    our_evaluated_sequence_globals, _, _, _ = _evaluate_globals(our_sequence_globals, raise_exceptions=False)
    other_evaluated_sequence_globals, _, _, _ = _evaluate_globals(other_sequence_globals, raise_exceptions=False)

    # flatten globals dictionaries
    our_globals = flatten_globals(our_sequence_globals, evaluated=False)
    other_globals = flatten_globals(other_sequence_globals, evaluated=False)
    our_evaluated_globals = flatten_globals(our_evaluated_sequence_globals, evaluated=True)
    other_evaluated_globals = flatten_globals(other_evaluated_sequence_globals, evaluated=True)

    # diff the *evaluated* globals
    value_differences = dict_diff(other_evaluated_globals, our_evaluated_globals)

    # We are interested only in displaying globals where *both* the
    # evaluated global *and* its unevaluated expression (ignoring comments
    # and whitespace) differ. This will minimise false positives where a
    # slight change in an expression still leads to the same value, or
    # where an object has a poorly defined equality operator that returns
    # False even when the two objects are identical.
    filtered_differences = {}
    for name, (other_value, our_value) in value_differences.items():
        our_expression = our_globals.get(name, '-')
        other_expression = other_globals.get(name, '-')
        # Strip comments, get tokens so we can diff without being sensitive to comments or whitespace:
        our_expression, our_tokens = remove_comments_and_tokenify(our_expression)
        other_expression, other_tokens = remove_comments_and_tokenify(other_expression)
        if our_tokens != other_tokens:
            filtered_differences[name] = [repr(other_value), repr(our_value), other_expression, our_expression]
    if filtered_differences:
        payload = _diff_text(filtered_differences) + '\n\n'
    else:
        payload = 'Evaluated globals are identical to those of selected file.\n'
    if return_string:
        return payload
    print(payload)
    import pandas as pd
    if filtered_differences:
        df = pd.DataFrame.from_dict(filtered_differences, 'index')
        df = df.sort_index()
        df.columns = DIFF_COLUMNS
    else:
        df = pd.DataFrame(columns=DIFF_COLUMNS)
    return df


def globals_diff_shots(file1, file2):
    # Get file's globals groups
    active_groups = get_all_groups(file1)

    # Get other file's globals groups
    other_groups = get_all_groups(file2)

    print(f'Globals diff between:\n{file1}\n{file2}\n\n')
    return globals_diff_groups(active_groups, other_groups, return_string=False)

