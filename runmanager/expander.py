import itertools
import random

import numpy as np

def expand_globals(sequence_globals, evaled_globals, expansion_config = None, return_dimensions = False):
    """Expands iterable globals according to their expansion
    settings. Creates a number of 'axes' which are to be outer product'ed
    together. Some of these axes have only one element, these are globals
    that do not vary. Some have a set of globals being zipped together,
    iterating in lock-step. Others contain a single global varying
    across its values (the globals set to 'outer' expansion). Returns
    a list of shots, each element of which is a dictionary for that
    shot's globals."""

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
            expression, units, expansion = sequence_globals[group_name][global_name]
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
    dimensions = {}
    for zip_key in zip_keys:
        axis = []
        zip_global_names = []
        for global_name in expansions:
            if expansions[global_name] == zip_key:
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
        axis = list(zip(*axis))
        dimensions['zip '+zip_key] = len(axis)
        axes['zip '+zip_key] = axis
        global_names['zip '+zip_key] = zip_global_names

    # Give each global being outer-product'ed its own axis. It gets
    # wrapped up in a list and zipped with itself so that it is in the
    # same format as the zipped globals, ready for outer-producting
    # together:
    for global_name in expansions:
        if expansions[global_name] == 'outer':
            value = values[global_name]
            if isinstance(value, Exception):
                continue
            axis = [value]
            axis = list(zip(*axis))
            dimensions['outer '+global_name] = len(axis)
            axes['outer '+global_name] = axis
            global_names['outer '+global_name] = [global_name]

    # add any missing items to order and dimensions
    for key, value in axes.items():
        if key not in order:
            order[key] = -1
        if key not in shuffle:
            shuffle[key] = False
        if key not in dimensions:
            dimensions[key] = 1

    # shuffle relevant axes
    for axis_name, axis_values in axes.items():
        if shuffle[axis_name]:
            random.shuffle(axis_values)

    # sort axes and global names by order
    axes = [axes.get(key) for key in sorted(order, key=order.get)]
    global_names = [global_names.get(key) for key in sorted(order, key=order.get)]

    # flatten the global names
    global_names = [global_name for global_list in global_names for global_name in global_list]


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
