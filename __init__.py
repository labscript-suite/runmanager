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

from __future__ import division, unicode_literals, print_function, absolute_import
from labscript_utils import PY2
if PY2:
    str = unicode

import itertools
import os
import sys
import random
import time
import subprocess
import types
import threading
import traceback

import labscript_utils.h5_lock
import h5py
import numpy as np

import zprocess

__version__ = '2.2.0'


def _ensure_str(s):
    """convert bytestrings and numpy strings to python strings"""
    return s.decode() if isinstance(s, bytes) else str(s)


def is_valid_python_identifier(name):
    import tokenize
    if PY2:
        import StringIO as io
    else:
        import io
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(name).readline))
    except tokenize.TokenError:
        return False
    if len(tokens) == 2:
        (token_type, _, _, _, _), _ = tokens
        return token_type == tokenize.NAME
    return False


class ExpansionError(Exception):

    """An exception class so that error handling code can tell when a
    parsing exception was caused by a mismatch with the expansion mode"""
    pass


class TraceDictionary(dict):

    def __init__(self, *args, **kwargs):
        self.trace_data = None
        dict.__init__(self, *args, **kwargs)

    def start_trace(self):
        self.trace_data = []

    def __getitem__(self, key):
        if self.trace_data is not None:
            if key not in self.trace_data:
                self.trace_data.append(key)
        return dict.__getitem__(self, key)

    def stop_trace(self):
        trace_data = self.trace_data
        self.trace_data = None
        return trace_data


def new_globals_file(filename):
    with h5py.File(filename, 'w') as f:
        f.create_group('globals')


def add_expansion_groups(filename):
    """backward compatability, for globals files which don't have
    expansion groups. Create them if they don't exist. Guess expansion
    settings based on datatypes, if possible."""
    # DEPRECATED
    # Don't open in write mode unless we have to:
    with h5py.File(filename, 'r') as f:
        requires_expansion_group = []
        for groupname in f['globals']:
            group = f['globals'][groupname]
            if not 'expansion' in group:
                requires_expansion_group.append(groupname)
    if requires_expansion_group:
        group_globalslists = [get_globalslist(filename, groupname) for groupname in requires_expansion_group]
        with h5py.File(filename, 'a') as f:
            for groupname, globalslist in zip(requires_expansion_group, group_globalslists):
                group = f['globals'][groupname]
                subgroup = group.create_group('expansion')
                # Initialise all expansion settings to blank strings:
                for name in globalslist:
                    subgroup.attrs[name] = ''
        groups = {group_name: filename for group_name in get_grouplist(filename)}
        sequence_globals = get_globals(groups)
        evaled_globals, global_hierarchy, expansions = evaluate_globals(sequence_globals, raise_exceptions=False)
        for group_name in evaled_globals:
            for global_name in evaled_globals[group_name]:
                value = evaled_globals[group_name][global_name]
                expansion = guess_expansion_type(value)
                set_expansion(filename, group_name, global_name, expansion)


def get_grouplist(filename):
    # For backward compatability, add 'expansion' settings to this
    # globals file, if it doesn't contain any.  Guess expansion settings
    # if possible.
    # DEPRECATED
    add_expansion_groups(filename)
    with h5py.File(filename, 'r') as f:
        grouplist = f['globals']
        # File closes after this function call, so have to
        # convert the grouplist generator to a list of strings
        # before its file gets dereferenced:
        return list(grouplist)


def new_group(filename, groupname):
    with h5py.File(filename, 'a') as f:
        if groupname in f['globals']:
            raise Exception('Can\'t create group: target name already exists.')
        group = f['globals'].create_group(groupname)
        group.create_group('units')
        group.create_group('expansion')


def copy_group(source_globals_file, source_groupname, dest_globals_file, delete_source_group=False):
    """ This function copies the group source_groupname from source_globals_file
        to dest_globals_file and renames the new group so that there is no name
        collision. If delete_source_group is False the copyied files have
        a suffix '_copy'."""
    with h5py.File(source_globals_file, 'a') as source_f:
        # check if group exists
        if source_groupname not in source_f['globals']:
            raise Exception('Can\'t copy there is no group "{}"!'.format(source_groupname))

        # Are we coping from one file to another?
        if dest_globals_file is not None and source_globals_file != dest_globals_file:
            dest_f = h5py.File(dest_globals_file, 'a')  # yes -> open dest_globals_file
        else:
            dest_f = source_f  # no -> dest files is source file

        # rename Group until there is no name collisions
        i = 0 if not delete_source_group else 1
        dest_groupname = source_groupname
        while dest_groupname in dest_f['globals']:
            dest_groupname = "{}({})".format(dest_groupname, i) if i > 0 else "{}_copy".format(dest_groupname)
            i += 1

        # copy group
        dest_f.copy(source_f['globals'][source_groupname], '/globals/%s' % dest_groupname)

        # close opend file
        if dest_f != source_f:
            dest_f.close()

    return dest_groupname


def rename_group(filename, oldgroupname, newgroupname):
    if oldgroupname == newgroupname:
        # No rename!
        return
    with h5py.File(filename, 'a') as f:
        if newgroupname in f['globals']:
            raise Exception('Can\'t rename group: target name already exists.')
        f.copy(f['globals'][oldgroupname], '/globals/%s' % newgroupname)
        del f['globals'][oldgroupname]


def delete_group(filename, groupname):
    with h5py.File(filename, 'a') as f:
        del f['globals'][groupname]


def get_globalslist(filename, groupname):
    with h5py.File(filename, 'r') as f:
        group = f['globals'][groupname]
        # File closes after this function call, so have to convert
        # the attrs to a dict before its file gets dereferenced:
        return dict(group.attrs)


def new_global(filename, groupname, globalname):
    if not is_valid_python_identifier(globalname):
        raise ValueError('%s is not a valid Python variable name'%globalname)
    with h5py.File(filename, 'a') as f:
        group = f['globals'][groupname]
        if globalname in group.attrs:
            raise Exception('Can\'t create global: target name already exists.')
        group.attrs[globalname] = ''
        f['globals'][groupname]['units'].attrs[globalname] = ''
        f['globals'][groupname]['expansion'].attrs[globalname] = ''


def rename_global(filename, groupname, oldglobalname, newglobalname):
    if oldglobalname == newglobalname:
        # No rename!
        return
    if not is_valid_python_identifier(newglobalname):
        raise ValueError('%s is not a valid Python variable name'%newglobalname)
    value = get_value(filename, groupname, oldglobalname)
    units = get_units(filename, groupname, oldglobalname)
    expansion = get_expansion(filename, groupname, oldglobalname)
    with h5py.File(filename, 'a') as f:
        group = f['globals'][groupname]
        if newglobalname in group.attrs:
            raise Exception('Can\'t rename global: target name already exists.')
        group.attrs[newglobalname] = value
        group['units'].attrs[newglobalname] = units
        group['expansion'].attrs[newglobalname] = expansion
        del group.attrs[oldglobalname]
        del group['units'].attrs[oldglobalname]
        del group['expansion'].attrs[oldglobalname]


def get_value(filename, groupname, globalname):
    with h5py.File(filename, 'r') as f:
        value = f['globals'][groupname].attrs[globalname]
        # Replace numpy strings with python unicode strings.
        # DEPRECATED, for backward compat with old files
        value = _ensure_str(value)
        return value


def set_value(filename, groupname, globalname, value):
    with h5py.File(filename, 'a') as f:
        f['globals'][groupname].attrs[globalname] = value


def get_units(filename, groupname, globalname):
    with h5py.File(filename, 'r') as f:
        value = f['globals'][groupname]['units'].attrs[globalname]
        # Replace numpy strings with python unicode strings.
        # DEPRECATED, for backward compat with old files
        value = _ensure_str(value)
        return value


def set_units(filename, groupname, globalname, units):
    with h5py.File(filename, 'a') as f:
        f['globals'][groupname]['units'].attrs[globalname] = units


def get_expansion(filename, groupname, globalname):
    with h5py.File(filename, 'r') as f:
        value = f['globals'][groupname]['expansion'].attrs[globalname]
        # Replace numpy strings with python unicode strings.
        # DEPRECATED, for backward compat with old files
        value = _ensure_str(value)
        return value


def set_expansion(filename, groupname, globalname, expansion):
    with h5py.File(filename, 'a') as f:
        f['globals'][groupname]['expansion'].attrs[globalname] = expansion


def delete_global(filename, groupname, globalname):
    with h5py.File(filename, 'a') as f:
        group = f['globals'][groupname]
        del group.attrs[globalname]


def guess_expansion_type(value):
    if isinstance(value, np.ndarray) or isinstance(value, list):
        return u'outer'
    else:
        return u''


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


def get_all_groups(h5_files):
    """returns a dictionary of group_name: h5_path pairs from a list of h5_files."""
    if isinstance(h5_files, bytes) or isinstance(h5_files, str):
        h5_files = [h5_files]
    groups = {}
    for path in h5_files:
        for group_name in get_grouplist(path):
            if group_name in groups:
                raise ValueError('Error: group %s is defined in both %s and %s. ' % (group_name, groups[group_name], path) +
                                 'Only uniquely named groups can be used together '
                                 'to make a run file.')
            groups[group_name] = path
    return groups


def get_globals(groups):
    """Takes a dictionary of group_name: h5_file pairs and pulls the
    globals out of the groups in their files.  The globals are strings
    storing python expressions at this point. All these globals are
    packed into a new dictionary, keyed by group_name, where the values
    are dictionaries which look like {global_name: (expression, units, expansion), ...}"""
    # get a list of filepaths:
    filepaths = set(groups.values())
    sequence_globals = {}
    for filepath in filepaths:
        groups_from_this_file = [g for g, f in groups.items() if f == filepath]
        with h5py.File(filepath, 'r') as f:
            for group_name in groups_from_this_file:
                sequence_globals[group_name] = {}
                globals_group = f['globals'][group_name]
                values = dict(globals_group.attrs)
                units = dict(globals_group['units'].attrs)
                expansions = dict(globals_group['expansion'].attrs)
                for global_name, value in values.items():
                    unit = units[global_name]
                    expansion = expansions[global_name]
                    # Replace numpy strings with python unicode strings.
                    # DEPRECATED, for backward compat with old files
                    value = _ensure_str(value)
                    unit = _ensure_str(unit)
                    expansion = _ensure_str(expansion)
                    sequence_globals[group_name][global_name] = value, unit, expansion
    return sequence_globals


def evaluate_globals(sequence_globals, raise_exceptions=True):
    """Takes a dictionary of globals as returned by get_globals. These
    globals are unevaluated strings.  Evaluates them all in the same
    namespace so that the expressions can refer to each other. Iterates
    to allow for NameErrors to be resolved by subsequently defined
    globals. Throws an exception if this does not result in all errors
    going away. The exception contains the messages of all exceptions
    which failed to be resolved. If raise_exceptions is False, any
    evaluations resulting in an exception will instead return the
    exception object in the results dictionary"""

    # Flatten all the groups into one dictionary of {global_name:
    # expression} pairs. Also create the group structure of the results
    # dict, which has the same structure as sequence_globals:
    all_globals = {}
    results = {}
    expansions = {}
    global_hierarchy = {}
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
                exception = ValueError('Global named \'%s\' is defined in multiple active groups:\n    ' % global_name +
                                       '\n    '.join(groups_with_same_global))
                if raise_exceptions:
                    raise exception
                for other_group_name in groups_with_same_global:
                    results[other_group_name][global_name] = exception
                multiply_defined_globals.add(global_name)
            all_globals[global_name], units, expansion = sequence_globals[group_name][global_name]
            expansions[global_name] = expansion

    # Do not attempt to evaluate globals which are multiply defined:
    for global_name in multiply_defined_globals:
        del all_globals[global_name]

    # Eval the expressions in the same namespace as each other:
    evaled_globals = {}
    # we use a "TraceDictionary" to track which globals another global depends on
    sandbox = TraceDictionary()
    exec('from pylab import *', sandbox, sandbox)
    exec('from runmanager.functions import *', sandbox, sandbox)
    globals_to_eval = all_globals.copy()
    previous_errors = -1
    while globals_to_eval:
        errors = []
        for global_name, expression in globals_to_eval.copy().items():
            # start the trace to determine which globals this global depends on
            sandbox.start_trace()
            try:
                code = compile(expression, '<string>', 'eval')
                value = eval(code, sandbox)
                # Need to know the length of any generators, convert to tuple:
                if isinstance(value, types.GeneratorType):
                    value = iterator_to_tuple(value)
                # Make sure if we're zipping or outer-producting this value, that it can
                # be iterated over:
                if expansions[global_name] == 'outer':
                    try:
                        iter(value)
                    except Exception as e:
                        raise ExpansionError(str(e))
            except Exception as e:
                # Don't raise, just append the error to a list, we'll display them all later.
                errors.append((global_name, e))
                sandbox.stop_trace()
                continue
            # Put the global into the namespace so other globals can use it:
            sandbox[global_name] = value
            del globals_to_eval[global_name]
            evaled_globals[global_name] = value

            # get the results from the global trace
            trace_data = sandbox.stop_trace()
            # Only store names of globals (not other functions)
            for key in list(trace_data):  # copy the list before iterating over it
                if key not in all_globals:
                    trace_data.remove(key)
            if trace_data:
                global_hierarchy[global_name] = trace_data

        if len(errors) == previous_errors:
            # Since some globals may refer to others, we expect maybe
            # some NameErrors to have occured.  There should be fewer
            # NameErrors each iteration of this while loop, as globals
            # that are required become defined. If there are not fewer
            # errors, then there is something else wrong and we should
            # raise it.
            if raise_exceptions:
                message = 'Error parsing globals:\n'
                for global_name, exception in errors:
                    message += '%s: %s: %s\n' % (global_name, exception.__class__.__name__, exception.message if PY2 else str(exception))
                raise Exception(message)
            else:
                for global_name, exception in errors:
                    evaled_globals[global_name] = exception
                break
        previous_errors = len(errors)

    # Assemble results into a dictionary of the same format as sequence_globals:
    for group_name in sequence_globals:
        for global_name in sequence_globals[group_name]:
            # Do not attempt to override exception objects already stored
            # as the result of multiply defined globals:
            if not global_name in results[group_name]:
                results[group_name][global_name] = evaled_globals[global_name]

    return results, global_hierarchy, expansions


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

def generate_sequence_id(scriptname):
    """Our convention for generating sequence ids. Just a timestamp and
    the name of the labscript that the run file is to be compiled with."""
    timestamp = time.strftime('%Y%m%dT%H%M%S', time.localtime())
    scriptbase = os.path.basename(scriptname).split('.py')[0]
    return timestamp + '_' + scriptbase


def make_run_files(output_folder, sequence_globals, shots, sequence_id, shuffle=False):
    """Does what it says. sequence_globals and shots are of the datatypes
    returned by get_globals and get_shots, one is a nested dictionary with
    string values, and the other a flat dictionary. sequence_id should
    be some identifier unique to this sequence, use generate_sequence_id
    to follow convention. shuffle will randomise the order that the run
    files are generated in with respect to which element of shots they
    come from. This function returns a *generator*. The run files are
    not actually created until you loop over this generator (which gives
    you the filepaths). This is useful for not having to clean up as many
    unused files in the event of failed compilation of labscripts. If you
    want all the run files to be created at some point, simply convert
    the returned generator to a list. The filenames the run files are
    given is simply the sequence_id with increasing integers appended."""
    basename = os.path.join(output_folder, sequence_id)
    nruns = len(shots)
    ndigits = int(np.ceil(np.log10(nruns)))
    if shuffle:
        random.shuffle(shots)
    for i, shot_globals in enumerate(shots):
        runfilename = ('%s_%0' + str(ndigits) + 'd.h5') % (basename, i)
        make_single_run_file(runfilename, sequence_globals, shot_globals, sequence_id, i, nruns)
        yield runfilename


def make_single_run_file(filename, sequenceglobals, runglobals, sequence_id, run_no, n_runs):
    """Does what it says. runglobals is a dict of this run's globals,
    the format being the same as that of one element of the list returned
    by expand_globals.  sequence_globals is a nested dictionary of the
    type returned by get_globals. Every run file needs a sequence ID,
    generate one with generate_sequence_id. This doesn't have to match
    the filename of the run file you end up using, though is usually does
    (exceptions being things like connection tables). run_no and n_runs
    must be provided, if this run file is part of a sequence, then they
    should reflect how many run files are being generated which share
    this sequence_id."""
    with h5py.File(filename, 'w') as f:
        f.attrs['sequence_id'] = sequence_id
        f.attrs['run number'] = run_no
        f.attrs['n_runs'] = n_runs
        f.create_group('globals')
        if sequenceglobals is not None:
            for groupname, groupvars in sequenceglobals.items():
                group = f['globals'].create_group(groupname)
                unitsgroup = group.create_group('units')
                expansiongroup = group.create_group('expansion')
                for name, (value, units, expansion) in groupvars.items():
                    group.attrs[name] = value
                    unitsgroup.attrs[name] = units
                    expansiongroup.attrs[name] = expansion
        for name, value in runglobals.items():
            if value is None:
                # Store it as a null object reference:
                value = h5py.Reference()
            try:
                f['globals'].attrs[name] = value
            except Exception as e:
                message = ('Global %s cannot be saved as an hdf5 attribute. ' % name +
                           'Globals can only have relatively simple datatypes, with no nested structures. ' +
                           'Original error was:\n' +
                           '%s: %s' % (e.__class__.__name__, e.message if PY2 else str(e)))
                raise ValueError(message)


def make_run_file_from_globals_files(labscript_file, globals_files, output_path):
    """Creates a run file output_path, using all the globals from
    globals_files. Uses labscript_file only to generate a sequence ID"""
    groups = get_all_groups(globals_files)
    sequence_globals = get_globals(groups)
    evaled_globals, global_hierarchy, expansions = evaluate_globals(sequence_globals)
    shots = expand_globals(sequence_globals, evaled_globals)
    if len(shots) > 1:
        scanning_globals = []
        for global_name in expansions:
            if expansions[global_name]:
                scanning_globals.append(global_name)
        raise ValueError('Cannot compile to a single run file: The following globals are a sequence: ' +
                         ', '.join(scanning_globals))
    sequence_id = generate_sequence_id(labscript_file)
    make_single_run_file(output_path, sequence_globals, shots[0], sequence_id, 1, 1)


def compile_labscript(labscript_file, run_file):
    """Compiles labscript_file with the run file, returning
    the processes return code, stdout and stderr."""
    proc = subprocess.Popen([sys.executable, labscript_file, run_file], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = proc.communicate()
    return proc.returncode, stdout, stderr


def compile_labscript_with_globals_files(labscript_file, globals_files, output_path):
    """Creates a run file output_path, using all the globals from
    globals_files. Compiles labscript_file with the run file, returning
    the processes return code, stdout and stderr."""
    make_run_file_from_globals_files(labscript_file, globals_files, output_path)
    returncode, stdout, stderr = compile_labscript(labscript_file, output_path)
    return returncode, stdout, stderr


def compile_labscript_async(labscript_file, run_file, stream_port, done_callback):
    """Compiles labscript_file with run_file. This function is designed
    to be called in a thread.  The stdout and stderr from the compilation
    will be shoveled into stream_port via zmq push as it spews forth, and
    when compilation is complete, done_callback will be called with a
    boolean argument indicating success."""
    compiler_path = os.path.join(os.path.dirname(__file__), 'batch_compiler.py')
    to_child, from_child, child = zprocess.subprocess_with_queues(compiler_path, stream_port)
    to_child.put(['compile', [labscript_file, run_file]])
    while True:
        signal, data = from_child.get()
        if signal == 'done':
            success = data
            to_child.put(['quit', None])
            child.communicate()
            done_callback(success)
            break
        else:
            raise RuntimeError((signal, data))


def compile_multishot_async(labscript_file, run_files, stream_port, done_callback):
    """Compiles labscript_file with run_files. This function is designed
    to be called in a thread.  The stdout and stderr from the compilation
    will be shoveled into stream_port via zmq push as it spews forth,
    and when each compilation is complete, done_callback will be called
    with a boolean argument indicating success. Compilation will stop
    after the first failure."""
    compiler_path = os.path.join(os.path.dirname(__file__), 'batch_compiler.py')
    to_child, from_child, child = zprocess.subprocess_with_queues(compiler_path, stream_port)
    try:
        for run_file in run_files:
            to_child.put(['compile', [labscript_file, run_file]])
            while True:
                signal, data = from_child.get()
                if signal == 'done':
                    success = data
                    done_callback(data)
                    break
            if not success:
                break
    except Exception:
        error = traceback.format_exc()
        zprocess.zmq_push_multipart(stream_port, data=[b'stderr', error.encode('utf-8')])
        to_child.put(['quit', None])
        child.communicate()
        raise
    to_child.put(['quit', None])
    child.communicate()


def compile_labscript_with_globals_files_async(labscript_file, globals_files, output_path, stream_port, done_callback):
    """Same as compile_labscript_with_globals_files, except it launches
    a thread to do the work and does not return anything. Instead,
    stderr and stdout will be put to stream_port via zmq push in
    the multipart message format ['stdout','hello, world\n'] etc. When
    compilation is finished, the function done_callback will be called
    a boolean argument indicating success or failure."""
    try:
        make_run_file_from_globals_files(labscript_file, globals_files, output_path)
        thread = threading.Thread(
            target=compile_labscript_async, args=[labscript_file, output_path, stream_port, done_callback])
        thread.daemon = True
        thread.start()
    except Exception:
        error = traceback.format_exc()
        zprocess.zmq_push_multipart(stream_port, data=[b'stderr', error.encode('utf-8')])
        t = threading.Thread(target=done_callback, args=(False,))
        t.daemon = True
        t.start()


def get_shot_globals(filepath):
    """Returns the evaluated globals for a shot, for use by labscript or lyse.
    Simple dictionary access as in dict(h5py.File(filepath).attrs) would be fine
    except we want to apply some hacks, so it's best to do that in one place."""
    params = {}
    with h5py.File(filepath, 'r') as f:
        for name, value in f['globals'].attrs.items():
            # Convert numpy bools to normal bools:
            if isinstance(value, np.bool_):
                value = bool(value)
            # Convert null HDF references to None:
            if isinstance(value, h5py.Reference) and not value:
                value = None
            # Convert numpy strings to Python ones.
            # DEPRECATED, for backward compat with old files.
            if isinstance(value, np.str_):
                value = str(value)
            if isinstance(value, bytes):
                value = value.decode()
            params[name] = value
    return params


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


def remove_comments_and_tokenify(line):
    """Removed EOL comments from a line, leaving it otherwise intact,
    and returns it. Also returns the raw tokens for the line, allowing
    comparisons between lines to be made without being sensitive to
    whitespace."""
    import tokenize
    if PY2:
        import StringIO as io
    else:
        import io
    result_expression = ''
    result_tokens = []
    error_encountered = False
    # This never fails because it produces a generator, syntax errors
    # come out when looping over it:
    tokens = tokenize.generate_tokens(io.StringIO(line).readline)
    try:
        for token_type, token_value, (_, start), (_, end), _ in tokens:
            if token_type == tokenize.COMMENT and not error_encountered:
                break
            if token_type == tokenize.ERRORTOKEN:
                error_encountered = True
            result_expression = result_expression.ljust(start)
            result_expression += token_value
            if token_value:
                result_tokens.append(token_value)
    except tokenize.TokenError:
        # Means EOF was reached without closing brackets or something.
        # We don't care, return what we've got.
        pass
    return result_expression, result_tokens


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
