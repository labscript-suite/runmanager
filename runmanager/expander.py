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
"""Functions for expanding iterable globals into a list of shots.
"""

import itertools
import math
import random


def _make_axes(sequence_globals, evaled_globals, expansion_config=None):
    """Constructs the expansion axes. Returns (axes, global_names, shuffle). axes maps
    each axis name to its list of value tuples, in the order given by the 'order'
    entries of expansion_config, with axes that have no order entry first. global_names
    maps each axis name to the names of the globals on that axis. shuffle maps each axis
    name to the 'shuffle' entry of expansion_config, or False if there is none."""

    if expansion_config is None:
        order = {}
        shuffle = {}
    else:
        order = {k:v['order'] for k,v in expansion_config.items() if 'order' in v}
        shuffle = {k:v['shuffle'] for k,v in expansion_config.items() if 'shuffle' in v}

    values = {}
    expansions = {}
    for group_name in sequence_globals:
        for global_name in sequence_globals[group_name]:
            _, _, expansion = sequence_globals[group_name][global_name]
            value = evaled_globals[group_name][global_name]
            values[global_name] = value
            expansions[global_name] = expansion

    # Get a list of the zip keys in use:
    zip_keys = set(expansions.values())
    try:
        zip_keys.remove('outer')
    except KeyError:
        pass

    axes = {}
    global_names = {}
    for zip_key in zip_keys:
        axis = []
        zip_global_names = []
        for global_name, expansion in expansions.items():
            if expansion == zip_key:
                value = values[global_name]
                if isinstance(value, Exception):
                    continue
                if not zip_key:
                    # Wrap up non-iterating globals (with zip_key = '') in a
                    # one-element list. When zipped and then outer product'ed,
                    # this will give us the result we want:
                    value = [value]
                axis.append(value)
                zip_global_names.append(global_name)
        axes['zip '+zip_key] = list(zip(*axis, strict=True))
        global_names['zip '+zip_key] = zip_global_names

    # Give each global being outer-product'ed its own axis. It gets
    # wrapped up in a list and zipped with itself so that it is in the
    # same format as the zipped globals, ready for outer-producting
    # together:
    for global_name, expansion in expansions.items():
        if expansion == 'outer':
            value = values[global_name]
            if isinstance(value, Exception):
                continue
            axes['outer '+global_name] = list(zip(*[value]))
            global_names['outer '+global_name] = [global_name]

    # add any missing items to order and shuffle
    for key in axes:
        if key not in order:
            order[key] = -1
        if key not in shuffle:
            shuffle[key] = False

    # sort axes and global names by order
    axes = {key: axes[key] for key in sorted(axes, key=order.get)}
    global_names = {key: global_names[key] for key in axes}
    shuffle = {key: shuffle[key] for key in axes}

    return axes, global_names, shuffle


def count_shots(sequence_globals, evaled_globals, expansion_config=None, return_dimensions=False):
    """Returns the number of shots that expand_globals would return for the same
    arguments, without constructing them. If return_dimensions is True, also returns the
    dict mapping each axis name to its length."""
    axes, _, _ = _make_axes(sequence_globals, evaled_globals, expansion_config)
    dimensions = {key: len(axis) for key, axis in axes.items()}
    n_shots = math.prod(dimensions.values())
    if return_dimensions:
        return n_shots, dimensions
    else:
        return n_shots


def expand_globals(sequence_globals, evaled_globals, expansion_config = None, return_dimensions = False):
    """Expands iterable globals according to their expansion
    settings. Creates a number of 'axes' which are to be outer product'ed
    together. Some of these axes have only one element, these are globals
    that do not vary. Some have a set of globals being zipped together,
    iterating in lock-step. Others contain a single global varying
    across its values (the globals set to 'outer' expansion). Returns
    a list of shots, each element of which is a dictionary for that
    shot's globals."""

    axes, global_names, shuffle = _make_axes(sequence_globals, evaled_globals, expansion_config)
    dimensions = {key: len(axis) for key, axis in axes.items()}

    # shuffle relevant axes
    for axis_name, axis_values in axes.items():
        if shuffle[axis_name]:
            random.shuffle(axis_values)

    axes = list(axes.values())
    # flatten the global names
    global_names = [global_name for global_list in global_names.values() for global_name in global_list]

    shots = []
    for axis_values in itertools.product(*axes):
        # values here is a tuple of tuples, with the outer list being over
        # the axes. We need to flatten it to get our individual values out
        # for each global, since we no longer care what axis they are on:
        global_values = [value for axis in axis_values for value in axis]
        shot_globals = dict(zip(global_names, global_values))
        shots.append(shot_globals)

    if return_dimensions:
        return shots, dimensions
    else:
        return shots
