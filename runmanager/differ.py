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

import tokenize

from evaluator import evaluate_globals

def find_comments(src):
    """Return a list of start and end indices for where comments are in given Python
    source. Comments on separate lines with only whitespace in between them are
    coalesced. Whitespace preceding a comment is counted as part of the comment."""
    line_start = 0
    comments = []
    tokens = tokenize.generate_tokens(io.StringIO(src).readline)
    try:
        for token_type, token_value, (_, start), (_, end), _ in tokens:
            if token_type == tokenize.COMMENT:
                comments.append((line_start + start, line_start + end))
            if token_value == '\n':
                line_start += end
    except tokenize.TokenError:
        pass
    # coalesce comments with only whitespace between them:
    to_merge = []
    for i, ((start1, end1), (start2, end2)) in enumerate(zip(comments, comments[1:])):
        if not src[end1:start2].strip():
            to_merge.append(i)
    # Reverse order so deletion doesn't change indices:
    for i in reversed(to_merge):
        start1, end1 = comments[i]
        start2, end2 = comments[i + 1]
        comments[i] = (start1, end2)
        del comments[i + 1]
    # Extend each comment block to the left to include whitespace:
    for i, (start, end) in enumerate(comments):
        n_whitespace_chars = len(src[:start]) - len(src[:start].rstrip())
        comments[i] = start - n_whitespace_chars, end
    # Extend the final comment to the right to include whitespace:
    if comments:
        start, end = comments[-1]
        n_whitespace_chars = len(src[end:]) - len(src[end:].rstrip())
        comments[-1] = (start, end + n_whitespace_chars)
    return comments


def remove_comments_and_tokenify(src):
    """Removes comments from source code, leaving it otherwise intact,
    and returns it. Also returns the raw tokens for the code, allowing
    comparisons between source to be made without being sensitive to
    whitespace."""
    # Remove comments
    for (start, end) in reversed(find_comments(src)):
        src = src[:start] + src[end:]
    # Tokenify:
    tokens = []
    tokens_iter = tokenize.generate_tokens(io.StringIO(src).readline)
    try:
        for _, token_value, _, _, _ in tokens_iter:
            if token_value:
                tokens.append(token_value)
    except tokenize.TokenError:
        pass
    return src, tokens


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
                value_expression, units, expansion = value
                flattened_sequence_globals[name] = value_expression
    return flattened_sequence_globals


def globals_diff_groups(active_groups, other_groups, max_cols=1000, return_string=True):
    """Given two sets of globals groups, perform a diff of the raw
    and evaluated globals."""
    our_sequence_globals = get_globals(active_groups)
    other_sequence_globals = get_globals(other_groups)

    # evaluate globals
    our_evaluated_sequence_globals, _, _ = evaluate_globals(our_sequence_globals, raise_exceptions=False)
    other_evaluated_sequence_globals, _, _ = evaluate_globals(other_sequence_globals, raise_exceptions=False)

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
        import pandas as pd
        df = pd.DataFrame.from_dict(filtered_differences, 'index')
        df = df.sort_index()
        df.columns = ['Prev (Eval)', 'Current (Eval)', 'Prev (Raw)', 'Current (Raw)']
        df_string = df.to_string(max_cols=max_cols)
        payload = df_string + '\n\n'
    else:
        payload = 'Evaluated globals are identical to those of selected file.\n'
    if return_string:
        return payload
    else:
        print(payload)
        return df


def globals_diff_shots(file1, file2, max_cols=100):
    # Get file's globals groups
    active_groups = get_all_groups(file1)

    # Get other file's globals groups
    other_groups = get_all_groups(file2)

    print('Globals diff between:\n%s\n%s\n\n' % (file1, file2))
    return globals_diff_groups(active_groups, other_groups, max_cols=max_cols, return_string=False)

