import itertools
import os
import sys
import random
import time
import subprocess
import types
import threading
import traceback
import tokenize,token, StringIO

import h5_lock, h5py
import pylab

import subproc_utils
import mise

class ExpansionError(Exception):
    """An exception class so that error handling code can tell when a
    parsing exception was caused by a mismatch with the expansion mode"""
    pass
    
def new_globals_file(filename):
    with h5py.File(filename,'w') as f:
        f.create_group('globals')

def add_expansion_groups(filename):
    """backward compatability, for globals files which don't have
    expansion groups. Create them if they don't exist. Guess expansion
    settings based on datatypes, if possible."""
    modified = False
    # Don't open in write mode unless we have to:
    with h5py.File(filename,'r') as f:
        requires_expansion_group = []
        for groupname in f['globals']:
            group = f['globals'][groupname]
            if not 'expansion' in group:
                requires_expansion_group.append(groupname)
    if requires_expansion_group:
        group_globalslists = [get_globalslist(filename, groupname) for groupname in requires_expansion_group] 
        with h5py.File(filename,'a') as f:
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
    add_expansion_groups(filename)
    with h5py.File(filename,'r') as f:
        grouplist = f['globals']
        # File closes after this function call, so have to
        # convert the grouplist generator to a list of strings
        # before its file gets dereferenced:
        return list(grouplist)
        
def new_group(filename, groupname):
    with h5py.File(filename,'a') as f:
        group = f['globals'].create_group(groupname)
        group.create_group('units')
        group.create_group('expansion')
        
def rename_group(filename, oldgroupname, newgroupname):
    if oldgroupname == newgroupname:
        # No rename!
        return
    with h5py.File(filename,'a') as f:
        f.copy(f['globals'][oldgroupname], '/globals/%s'%newgroupname)
        del f['globals'][oldgroupname]
    
def delete_group(filename, groupname):
    with h5py.File(filename,'a') as f:
        del f['globals'][groupname]
    
def get_globalslist(filename, groupname):
    with h5py.File(filename,'r') as f:
        group = f['globals'][groupname]
        # File closes after this function call, so have to convert
        # the attrs to a dict before its file gets dereferenced:
        return dict(group.attrs)
    
def new_global(filename, groupname, globalname):
    with h5py.File(filename,'a') as f:
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
    value = get_value(filename, groupname, oldglobalname)
    units = get_units(filename, groupname, oldglobalname)
    expansion = get_expansion(filename, groupname, oldglobalname)
    with h5py.File(filename,'a') as f:
        group = f['globals'][groupname]
        if newglobalname in group.attrs:
            raise Exception('Can\'t rename: target name already exists.')
        group.attrs[newglobalname] = value
        group['units'].attrs[newglobalname] = units
        group['expansion'].attrs[newglobalname] = expansion
        del group.attrs[oldglobalname]
        del group['units'].attrs[oldglobalname]
        del group['expansion'].attrs[oldglobalname]
        
def get_value(filename, groupname, globalname):
    with h5py.File(filename,'r') as f:
        value = f['globals'][groupname].attrs[globalname]
        return value
                
def set_value(filename, groupname, globalname, value):
    with h5py.File(filename,'a') as f:
        f['globals'][groupname].attrs[globalname] = value
    
def get_units(filename, groupname, globalname):
    with h5py.File(filename,'r') as f:
        value = f['globals'][groupname]['units'].attrs[globalname]
        return value

def set_units(filename, groupname, globalname, units):
    with h5py.File(filename,'a') as f:
        f['globals'][groupname]['units'].attrs[globalname] = units

def get_expansion(filename, groupname, globalname):
    with h5py.File(filename,'r') as f:
        value = f['globals'][groupname]['expansion'].attrs[globalname]
        return value  
        
def set_expansion(filename, groupname, globalname, expansion):
    with h5py.File(filename,'a') as f:
        f['globals'][groupname]['expansion'].attrs[globalname] = expansion
                  
def delete_global(filename, groupname, globalname):
    with h5py.File(filename,'a') as f:
        group = f['globals'][groupname]
        del group.attrs[globalname]

def guess_expansion_type(value):
    if isinstance(value, pylab.ndarray) or  isinstance(value, list):
        return 'outer'
    else:
        return ''

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
                             'If you really want an iterator longer than %d, '%max_length +
                             'please modify runmanager.iterator_to_tuple and increase max_length.')
    return tuple(temp_list)
        
def get_all_groups(h5_files):
    """returns a dictionary of group_name: h5_path pairs from a list of h5_files."""
    if isinstance(h5_files,str):
        h5_files = [h5_files]
    groups = {}
    for path in h5_files:
        for group_name in get_grouplist(path):
            if group_name in groups:
                raise ValueError('Error: group %s is defined in both %s and %s. ' %(group_name,groups[group_name],path) +
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
        groups_from_this_file = [g for g, f in groups.items() if f==filepath] 
        with h5py.File(filepath,'r') as f:
            for group_name in groups_from_this_file:
                sequence_globals[group_name] = {}
                globals_group = f['globals'][group_name]
                for global_name in globals_group.attrs:
                    value = globals_group.attrs[global_name]
                    units = globals_group['units'].attrs[global_name]
                    expansion = globals_group['expansion'].attrs[global_name]
                    # Replace numpy empty strings with python empty strings.
                    # There is a bug where numpy empty strings can't be pickled.
                    # This is a problem since runmanager pickles these things to
                    # send them to mise:
                    if isinstance(value,str) and value == '':
                        value = ''
                    if isinstance(units,str) and units == '':
                        units = ''
                    if isinstance(expansion,str) and expansion == '':
                        expansion = ''
                    sequence_globals[group_name][global_name] = value, units, expansion
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
                exception = ValueError('Global named \'%s\' is defined in multiple active groups:\n    '%global_name + 
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

    # for each global, find the other globals it depends on, and record them as a dependency
    sandbox = {}
    exec('from pylab import *',sandbox,sandbox)
    exec('from runmanager.functions import *',sandbox,sandbox)
    exec('from mise import MiseParameter',sandbox,sandbox)    
    for global_name, expression in all_globals.items():
        try:
            eval(expression,sandbox)
        except NameError:
            tokens = tokenize.generate_tokens(StringIO.StringIO(expression).readline)
            for toknum, tokval, _, _, _ in tokens:
                if toknum == token.NAME and tokval in all_globals:
                    global_hierarchy.setdefault(global_name,[])
                    global_hierarchy[global_name].append(tokval)
        except Exception as e:
            pass
    
    #Eval the expressions in the same namespace as each other:
    evaled_globals = {}
    sandbox = {}
    exec('from pylab import *',sandbox,sandbox)
    exec('from runmanager.functions import *',sandbox,sandbox)
    exec('from mise import MiseParameter',sandbox,sandbox)
    globals_to_eval = all_globals.copy()
    previous_errors = -1
    while globals_to_eval:
        errors = []
        for global_name, expression in globals_to_eval.copy().items():
            try:
                value = eval(expression,sandbox)
                # Need to know the length of any generators, convert to tuple:
                if isinstance(value,types.GeneratorType):
                    value = iterator_to_tuple(value)
                # Make sure if we're zipping or outer-producting this value, that it can
                # be iterated over:
                if expansions[global_name] == 'outer':
                    try:
                        test = iter(value)
                    except Exception as e:
                        raise ExpansionError(str(e))
            except Exception as e:
                # Don't raise, just append the error to a list, we'll display them all later.
                errors.append((global_name,e))
                continue
            # Put the global into the namespace so other globals can use it:
            sandbox[global_name] = value
            del globals_to_eval[global_name]
            evaled_globals[global_name] = value
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
                    message += '%s: %s\n'%(global_name,str(exception))
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

def expand_globals(sequence_globals, evaled_globals):
    """Expands iterable globals according to their expansion
    settings. Creates a number of 'axes' which are to be outer product'ed
    together. Some of these axes have only one element, these are globals
    that do not vary. Some have a set of globals being zipped together,
    iterating in lock-step. Others contain a single global varying
    across its values (the globals set to 'outer' expansion). Returns
    a list of shots, each element of which is a dictionary for that
    shot's globals."""
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
    axes = [] 
    global_names = []
    for zip_key in zip_keys:
        axis = []
        for global_name in expansions:
            if expansions[global_name] == zip_key:
                value = values[global_name]
                if not zip_key:
                    # Wrap up non-iterating globals (with zip_key = '') in a
                    # one-element list. When zipped and then outer product'ed,
                    # this will give us the result we want:
                    value = [value]
                axis.append(value)
                global_names.append(global_name)
        axis = zip(*axis)
        axes.append(axis)
    
    # Give each global being outer-product'ed its own axis. It gets
    # wrapped up in a list and zipped with itself so that it is in the
    # same format as the zipped globals, ready for outer-producting
    # together:
    for global_name in expansions:
        if expansions[global_name] == 'outer':
            value = values[global_name]
            axis = [value]
            axis = zip(*axis)
            axes.append(axis)
            global_names.append(global_name)

    shots = []
    for axis_values in itertools.product(*axes):
        # values here is a tuple of tuples, with the outer list being over
        # the axes. We need to flatten it to get our individual values out
        # for each global, since we no longer care what axis they are on:
        global_values = [value for axis in axis_values for value in axis]
        shot_globals = dict(zip(global_names,global_values))
        shots.append(shot_globals)
    return shots
    
def generate_sequence_id(scriptname):
    """Our convention for generating sequence ids. Just a timestamp and
    the name of the labscript that the run file is to be compiled with."""
    timestamp = time.strftime('%Y%m%dT%H%M%S',time.localtime())
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
    basename = os.path.join(output_folder,sequence_id)
    nruns = len(shots)
    ndigits = int(pylab.ceil(pylab.log10(nruns)))
    if shuffle:
        random.shuffle(shots)
    for i, shot_globals in enumerate(shots):
        runfilename = ('%s_%0'+str(ndigits)+'d.h5')%(basename,i) 
        make_single_run_file(runfilename,sequence_globals,shot_globals, sequence_id, i, nruns)
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
    with h5py.File(filename,'w') as f:
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
            try:
                f['globals'].attrs[name] = value
            except Exception:
                message = ('Global %s cannot be saved as an hdf5 attribute. '%name +
                                     'Globals can only have relatively simple datatypes, with no nested structures. ' +
                                     'Original error was:\n' +
                                     '%s: %s'%(sys.exc_type.__name__,sys.exc_value.message))
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
        for global_name in evaled_globals:
            if len(evaled_globals[global_name]) > 1:
                scanning_globals.append(global_name)
        raise ValueError('Cannot compile to a single run file: The following globals are a sequence: ' +
                         ' '.join(scanning_globals))
    sequence_id = generate_sequence_id(labscript_file)
    make_single_run_file(output_path,sequence_globals,shots[0],sequence_id,1,1)

def compile_labscript(labscript_file, run_file):
    """Compiles labscript_file with the run file, returning
    the processes return code, stdout and stderr."""
    proc = subprocess.Popen([sys.executable, labscript_file, run_file],stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
    to_child, from_child, child = subproc_utils.subprocess_with_queues(compiler_path, stream_port)
    to_child.put(['compile',[labscript_file, run_file]])
    while True:
        signal, data = from_child.get()
        if signal == 'done':
            success = data
            to_child.put(['quit',None])
            retcode = child.communicate()
            done_callback(data)
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
    to_child, from_child, child = subproc_utils.subprocess_with_queues(compiler_path, stream_port)
    try:
        for run_file in run_files:
            to_child.put(['compile',[labscript_file, run_file]])
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
        subproc_utils.zmq_push_multipart(stream_port, data=['stderr', error])
        to_child.put(['quit',None])
        retcode = child.communicate()
        raise
    to_child.put(['quit',None])
    retcode = child.communicate()
    
def compile_labscript_with_globals_files_async(labscript_file, globals_files, output_path, stream_port, done_callback):   
    """Same as compile_labscript_with_globals_files, except it launches
    a thread to do the work and does not return anything. Instead,
    stderr and stdout will be put to stream_port via zmq push in
    the multipart message format ['stdout','hello, world\n'] etc. When
    compilation is finished, the function done_callback will be called
    a boolean argument indicating success or failure."""
    try:
        make_run_file_from_globals_files(labscript_file, globals_files, output_path)
        thread = threading.Thread(target=compile_labscript_async, args=[labscript_file, output_path, stream_port, done_callback])
        thread.daemon = True
        thread.start()
    except Exception:
        error = traceback.format_exc()
        subproc_utils.zmq_push_multipart(stream_port, data=['stderr', error])
        t = threading.Thread(target=done_callback,args=(False,))
        t.daemon=True
        t.start()

