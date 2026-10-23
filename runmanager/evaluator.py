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
"""Functions for evaluating runmanager globals.

Evaluating converts strings into python expressions.
"""

import ast
import graphlib
import types

from runmanager.exceptions import ExpansionError


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
                             f'If you really want an iterator longer than {max_length}, ' +
                             'please modify runmanager.iterator_to_tuple and increase max_length.')
    return tuple(temp_list)

def evaluate_globals(sequence_globals, raise_exceptions=True):
    """Evaluates the expressions of the globals in sequence_globals and returns
    (results, global_hierarchy, expansions). results has the structure of
    sequence_globals with each expression replaced by its value. global_hierarchy maps
    the name of each global that refers to other globals to a sorted list of their
    names. expansions maps each global name to its expansion setting.

    Each expression is evaluated once, after the globals that it refers to. A global
    that fails to evaluate, that is defined in more than one group, or that is in a
    cycle of references holds an exception instead of a value, and the globals that
    refer to it fail with NameError. If raise_exceptions is True, one exception listing
    every failed global is raised instead."""

    # Flatten all the groups into one dictionary of {global_name:
    # expression} pairs. Also create the group structure of the results
    # dict, which has the same structure as sequence_globals:
    all_globals = {}
    results = {}
    expansions = {}
    # Pre-fill the results dictionary with groups, this is needed for
    # storing exceptions in the case of globals with the same name being
    # defined in multiple groups (all of them get the exception):
    for group_name in sequence_globals:
        results[group_name] = {}
    multiply_defined_globals = set()
    for group_name in sequence_globals:
        for global_name in sequence_globals[group_name]:
            if global_name in all_globals:
                # The same global is defined twice. Either raise an
                # exception, or store the exception for each place it is
                # defined, depending on whether raise_exceptions is True:
                groups_with_same_global = []
                for other_group_name in sequence_globals:
                    if global_name in sequence_globals[other_group_name]:
                        groups_with_same_global.append(other_group_name)
                exception = ValueError(f'Global named \'{global_name}\' is defined in multiple active groups:\n    ' +
                                       '\n    '.join(groups_with_same_global))
                if raise_exceptions:
                    raise exception
                for other_group_name in groups_with_same_global:
                    results[other_group_name][global_name] = exception
                multiply_defined_globals.add(global_name)
            all_globals[global_name], _, expansion = sequence_globals[group_name][global_name]
            expansions[global_name] = expansion

    # Do not attempt to evaluate globals which are multiply defined:
    for global_name in multiply_defined_globals:
        del all_globals[global_name]

    # Compile each expression and find the globals it refers to. Names bound within
    # the expression, by a comprehension or a lambda, are not references:
    errors = {}
    code = {}
    dependencies = {}
    for global_name, expression in all_globals.items():
        try:
            tree = ast.parse(expression, '<string>', 'eval')
        except SyntaxError as e:
            errors[global_name] = e
            dependencies[global_name] = set()
            continue
        code[global_name] = compile(tree, '<string>', 'eval')
        referenced = set()
        bound = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                (referenced if isinstance(node.ctx, ast.Load) else bound).add(node.id)
            elif isinstance(node, ast.arg):
                bound.add(node.arg)
        dependencies[global_name] = (referenced - bound) & all_globals.keys()
    global_hierarchy = {name: sorted(deps) for name, deps in dependencies.items() if deps}

    # Order the globals so that each comes after the globals it refers to. The globals
    # of a cycle of references get an error and are taken out of the ordering:
    while True:
        try:
            order = list(graphlib.TopologicalSorter(dependencies).static_order())
            break
        except graphlib.CycleError as e:
            cycle = e.args[1]
            if len(cycle) == 2:
                exception = ValueError(f'Global {cycle[0]} refers to itself')
            else:
                exception = ValueError('Globals refer to each other in a cycle: ' + ' -> '.join(cycle))
            for global_name in set(cycle):
                errors[global_name] = exception
                del dependencies[global_name]

    # Eval the expressions in the same namespace as each other:
    sandbox = {}
    exec('from pylab import *', sandbox, sandbox) # noqa: S102
    exec('from runmanager.functions import *', sandbox, sandbox) # noqa: S102
    evaled_globals = {}
    for global_name in order:
        if global_name in errors:
            continue
        try:
            value = eval(code[global_name], sandbox) # noqa: S307
            # Need to know the length of any generators, convert to tuple:
            if isinstance(value, types.GeneratorType):
                value = iterator_to_tuple(value)
            # Make sure if we're zipping or outer-producting this value, that it can
            # be iterated over:
            if expansions[global_name]:
                try:
                    iter(value)
                except Exception as e: # noqa: BLE001
                    # Any error here is an expansion error
                    raise ExpansionError(str(e))
        except ExpansionError as e:
            # The expression evaluated, so other globals can refer to the value:
            sandbox[global_name] = value
            errors[global_name] = e
        except Exception as e: # noqa: BLE001
            errors[global_name] = e
        else:
            # Put the global into the namespace so other globals can use it:
            sandbox[global_name] = value
            evaled_globals[global_name] = value

    if errors and raise_exceptions:
        message = 'Error parsing globals:\n'
        for global_name, exception in errors.items():
            message += f'{global_name}: {exception.__class__.__name__}: {exception!s}\n'
        raise Exception(message)
    evaled_globals.update(errors)

    # Globals in the same zip group must have equal lengths. Exceptions and
    # iterators without a length are skipped:
    zip_groups = {}
    for global_name, value in evaled_globals.items():
        if expansions[global_name] not in ('', 'outer') and hasattr(value, '__len__'):
            zip_groups.setdefault(expansions[global_name], {})[global_name] = len(value)
    for zip_key, lengths in zip_groups.items():
        if len(set(lengths.values())) > 1:
            exception = ValueError(
                f"Globals in zip group '{zip_key}' have unequal lengths: "
                + ', '.join(f'{name} ({length})' for name, length in lengths.items())
            )
            if raise_exceptions:
                raise exception
            evaled_globals.update(dict.fromkeys(lengths, exception))

    # Each global is evaluated once for the whole sequence, so a global in a zip group
    # cannot depend on globals expanded on other axes, directly or through globals that
    # are not expanded:
    def expanded_dependencies(global_name, visited):
        for name in global_hierarchy.get(global_name, []):
            if name not in visited:
                visited.add(name)
                if expansions[name]:
                    yield name
                else:
                    yield from expanded_dependencies(name, visited)

    for global_name, value in evaled_globals.items():
        zip_key = expansions[global_name]
        if zip_key in ('', 'outer') or isinstance(value, Exception):
            continue
        other_axes = [
            name for name in expanded_dependencies(global_name, {global_name})
            if expansions[name] != zip_key
        ]
        if other_axes:
            exception = ValueError(
                f"Global '{global_name}' in zip group '{zip_key}' depends on globals on "
                + 'other axes: '
                + ', '.join(
                    f"{name} (outer)" if expansions[name] == 'outer'
                    else f"{name} (zip group '{expansions[name]}')"
                    for name in other_axes
                )
                + '. Globals are evaluated once for the whole sequence, not once per '
                + 'shot, so a global in a zip group cannot vary with other axes'
            )
            if raise_exceptions:
                raise exception
            evaled_globals[global_name] = exception

    # Assemble results into a dictionary of the same format as sequence_globals:
    for group_name in sequence_globals:
        for global_name in sequence_globals[group_name]:
            # Do not attempt to override exception objects already stored
            # as the result of multiply defined globals:
            if global_name not in results[group_name]:
                results[group_name][global_name] = evaled_globals[global_name]

    return results, global_hierarchy, expansions

