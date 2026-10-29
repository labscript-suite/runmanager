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
"""Functions for parsing global expressions, especially for comments.
"""

import functools
import io
import tokenize


def find_comments(src):
    """Return a list of start and end indices for where comments are in given Python
    source. Comments on separate lines with only whitespace in between them are
    coalesced. Whitespace preceding a comment is counted as part of the comment."""
    # Offset of the start of each line in src, so that token positions, which are
    # a line number and a column, can be converted to offsets:
    line_offsets = [0]
    for line in io.StringIO(src):
        line_offsets.append(line_offsets[-1] + len(line))
    comments = []
    tokens = tokenize.generate_tokens(io.StringIO(src).readline)
    try:
        for token_type, _, (row, start), (_, end), _ in tokens:
            if token_type == tokenize.COMMENT:
                line_start = line_offsets[row - 1]
                comments.append((line_start + start, line_start + end))
    except (tokenize.TokenError, SyntaxError):
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
    except (tokenize.TokenError, SyntaxError):
        pass
    return src, tokens


@functools.lru_cache(maxsize=4096)
def bool_literal(src):
    """Return True or False if the given Python source is the literal True or False,
    with only whitespace and comments around it, otherwise None"""
    code, _ = remove_comments_and_tokenify(src)
    return {'True': True, 'False': False}.get(code.strip())


def set_bool_literal(src, value):
    """Return the given Python source, for which bool_literal() is not None, with its
    literal replaced by the literal of the given bool. Whitespace and comments are
    unchanged."""
    literal = str(bool_literal(src))
    start = src.index(literal)
    return src[:start] + str(bool(value)) + src[start + len(literal):]
