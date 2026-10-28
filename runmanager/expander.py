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


def axis_names(expansions):
    """Returns a dict mapping the name of each global that has an expansion setting to
    the name of its axis. expansions maps global names to their expansion settings. A
    global in zip group key is on the axis 'zip key'. An 'outer' global is on the axis
    'outer name', unless a zip group has the name of the global, in which case the
    global is on the axis of that zip group."""
    zip_keys = {expansion for expansion in expansions.values() if expansion not in ('', 'outer')}
    result = {}
    for global_name, expansion in expansions.items():
        if expansion == 'outer' and global_name not in zip_keys:
            result[global_name] = 'outer ' + global_name
        elif expansion == 'outer':
            result[global_name] = 'zip ' + global_name
        elif expansion:
            result[global_name] = 'zip ' + expansion
    return result


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

    # The globals of each zip group axis, including the axis 'zip ' of the globals
    # with no expansion:
    members = {}
    axis_of = axis_names(expansions)
    for global_name, expansion in expansions.items():
        axis_name = axis_of.get(global_name, 'zip ')
        if axis_name.startswith('zip '):
            members.setdefault(axis_name, []).append(global_name)

    axes = {}
    global_names = {}
    for axis_name, names in members.items():
        axis = []
        zip_global_names = []
        for global_name in names:
            value = values[global_name]
            if isinstance(value, Exception):
                continue
            if axis_name == 'zip ':
                # Wrap up non-iterating globals in a one-element list. When zipped and
                # then outer product'ed, this will give us the result we want:
                value = [value]
            axis.append(value)
            zip_global_names.append(global_name)
        axes[axis_name] = list(zip(*axis, strict=True))
        global_names[axis_name] = zip_global_names

    # Give each global being outer-product'ed its own axis. It gets
    # wrapped up in a list and zipped with itself so that it is in the
    # same format as the zipped globals, ready for outer-producting
    # together:
    for global_name, axis_name in axis_of.items():
        if axis_name.startswith('outer '):
            value = values[global_name]
            if isinstance(value, Exception):
                continue
            axes[axis_name] = list(zip(*[value]))
            global_names[axis_name] = [global_name]

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


def scanned_globals(sequence_globals, evaled_globals, global_hierarchy):
    """Returns a dict mapping the name of each global that is scanned over to the name
    of its axis. A global is scanned over if it is on an 'outer' axis or a zip group
    axis, of any length, and does not depend, directly or through other globals, on
    another global on the same axis. global_hierarchy is as returned by
    evaluate_globals."""
    _, global_names, _ = _make_axes(sequence_globals, evaled_globals)

    def dependencies(global_name):
        result = set()
        to_visit = [global_name]
        while to_visit:
            for name in global_hierarchy.get(to_visit.pop(), []):
                if name not in result:
                    result.add(name)
                    to_visit.append(name)
        return result

    scanned = {}
    for axis_name, names in global_names.items():
        if axis_name == 'zip ':
            # The globals with no expansion
            continue
        for global_name in names:
            if not dependencies(global_name) & (set(names) - {global_name}):
                scanned[global_name] = axis_name
    return scanned


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
