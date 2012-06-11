import itertools
import os
import sys
import random
import time
import subprocess
import types
import threading

import h5py
import pylab

import subproc_utils

def new_globals_file(filename):
    with h5py.File(filename,'w') as f:
        f.create_group('globals')
    
def get_grouplist(filename):
    with h5py.File(filename,'r') as f:
        grouplist = f['globals']
        # File closes after this function call, so have to
        # convert the grouplist generator to a list of strings
        # before its file gets dereferenced:
        return list(grouplist)
        
def new_group(filename,groupname):
    with h5py.File(filename,'a') as f:
        group = f['globals'].create_group(groupname)
        units = group.create_group('units')
    
def rename_group(filename,oldgroupname,newgroupname):
    if oldgroupname == newgroupname:
        # No rename!
        return
    with h5py.File(filename,'a') as f:
        f.copy(f['globals'][oldgroupname], '/globals/%s'%newgroupname)
        del f['globals'][oldgroupname]
    
def delete_group(filename,groupname):
    with h5py.File(filename,'a') as f:
        del f['globals'][groupname]
    
def get_globalslist(filename,groupname):
    with h5py.File(filename,'r') as f:
        group = f['globals'][groupname]
        # File closes after this function call, so have to convert
        # the attrs to a dict before its file gets dereferenced:
        return dict(group.attrs)
    
def new_global(filename,groupname,globalname):
    with h5py.File(filename,'a') as f:
        group = f['globals'][groupname]
        if globalname in group.attrs:
            raise Exception('Can\'t create global: target name already exists.')
        group.attrs[globalname] = ''
        f['globals'][groupname]['units'].attrs[globalname] = ''
    
def rename_global(filename,groupname,oldglobalname,newglobalname):
    if oldglobalname == newglobalname:
        # No rename!
        return
    value = get_value(filename, groupname, oldglobalname)
    units = get_units(filename, groupname, oldglobalname)
    with h5py.File(filename,'a') as f:
        group = f['globals'][groupname]
        if newglobalname in group.attrs:
            raise Exception('Can\'t rename: target name already exists.')
        group.attrs[newglobalname] = value
        group['units'].attrs[newglobalname] = units
        del group.attrs[oldglobalname]
        del group['units'].attrs[oldglobalname]

def get_value(filename,groupname,globalname):
    with h5py.File(filename,'r') as f:
        value = f['globals'][groupname].attrs[globalname]
        return value
                
def set_value(filename,groupname,globalname, value):
    with h5py.File(filename,'a') as f:
        f['globals'][groupname].attrs[globalname] = value
    
def get_units(filename,groupname,globalname):
    with h5py.File(filename,'r') as f:
        value = f['globals'][groupname]['units'].attrs[globalname]
        return value

def set_units(filename,groupname,globalname, units):
    with h5py.File(filename,'a') as f:
        f['globals'][groupname]['units'].attrs[globalname] = units
    
def delete_global(filename,groupname,globalname):
    with h5py.File(filename,'a') as f:
        group = f['globals'][groupname]
        del group.attrs[globalname]
        
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
    
def get_sequence_globals(groups):
    """Takes a dictionary of group_name: h5_file pairs and pulls the
    globals out of the groups in their files.  The globals are strings
    storing python expressions at this point. All these globals are
    packed into a new dictionary, keyed by group_name, where the values
    are dictionaries which look like {global_name: (expression, units), ...}"""
    sequence_globals = {}
    for group_name in groups:
        sequence_globals[group_name] = {}
        filepath = groups[group_name]
        globals_list = get_globalslist(filepath,group_name)
        for global_name in globals_list:
            value = get_value(filepath,group_name,global_name)
            units = get_units(filepath,group_name,global_name)
            sequence_globals[group_name][global_name] = value, units
    return sequence_globals
    
def get_shot_globals(sequence_globals,full_output=False):
    """Takes a dictionary of globals as returned by get_sequence_globals,
    and first flattens it to just the globals themselves, without
    regard to what group they belong to. The expressions for the globals
    are then evaluated with eval. If the result is a numpy array or a
    list, then this value is used. Otherwise the result is put into a
    single-element list: [value]. These global lists are then ripe for
    feeding into itertools.product, which they then are. This function
    then returns a list of dictionaries, each dictionary representing
    one shot. These dictionaries are of the form {global_name: value}"""
    # Flatten all the groups into one dictionary of {global_name: expression} pairs:
    all_globals = {}
    for group_name in sequence_globals:
        for global_name in sequence_globals[group_name]:
            if global_name in all_globals:  
                raise ValueError('Error parsing %s from group %s. Global name is already defined in another group.'%(global_name,group_name))
            all_globals[global_name], units = sequence_globals[group_name][global_name]
    
    # Eval the expressions, storing them all as lists or numpy arrays:        
    evaled_globals = {}
    for global_name, expression in all_globals.items():
        try:
            sandbox = {}
            exec('from pylab import *',sandbox,sandbox)
            exec('from runmanager.functions import *',sandbox,sandbox)
            value = eval(expression,sandbox)
        except Exception as e:
            raise Exception('Error parsing global \'%s\': '%global_name + str(e))
        if isinstance(value,types.GeneratorType):
           evaled_globals[global_name] = [tuple(value)]
        elif isinstance(value, pylab.ndarray) or  isinstance(value, list):
            evaled_globals[global_name] = value
        else:
            evaled_globals[global_name] = [value]
    
    # Do a cartesian product over the resulting lists of values:
    global_names = evaled_globals.keys()
    shots = []
    for global_values in itertools.product(*evaled_globals.values()):
        shot_globals = dict(zip(global_names,global_values))
        shots.append(shot_globals)
        
    if full_output:
        return shots, all_globals, evaled_globals
    else:
        return shots

def generate_sequence_id(scriptname):
    """Our convention for generating sequence ids. Just a timestamp and
    the name of the labscript that the run file is to be compiled with."""
    timestamp = time.strftime('%Y%m%dT%H%M%S',time.localtime())
    scriptbase = os.path.basename(scriptname).split('.py')[0]
    return timestamp + '_' + scriptbase      
        
def make_run_files(output_folder, sequence_globals, shots, sequence_id, shuffle=False):
    """Does what it says. sequence_globals and shots are of the
    datatypes returned by get_sequence_globals and get_shots, one
    is a nested dictionary with string values, and the other a flat
    dictionary. sequence_id should be some identifier unique to this
    sequence, use generate_sequence_id to follow convention. shuffle will
    randomise the order that the run files are generated in with respect
    to which element of shots they come from. This function returns a
    *generator*. The run files are not actually created until you loop
    over this generator (which gives you the filepaths). This is useful
    for not having to clean up as many unused files in the event of
    failed compilation of labscripts. If you want all the run files to
    be created at some point, simply convert the returned generator to a
    list. The filenames the run files are given is simply the sequence_id
    with increasing integers appended."""
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
    by get_shot_globals.  sequence_globals is a nested dictionary of the
    type returned by get_sequence_globals. Every run file needs a sequence
    ID, generate one with generate_sequence_id. This doesn't have to match
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
                for name, (value, units) in groupvars.items():
                    group.attrs[name] = value
                    unitsgroup.attrs[name] = units
        for name, value in runglobals.items():
            f['globals'].attrs[name] = value
                    
def make_run_file_from_globals_files(labscript_file, globals_files, output_path):
    """Creates a run file output_path, using all the globals from
    globals_files. Uses labscript_file only to generate a sequence ID"""
    groups = get_all_groups(globals_files)
    sequence_globals = get_sequence_globals(groups)
    shots,all_globals,evaled_globals = get_shot_globals(sequence_globals,full_output=True)
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
    
def compile_labscript_async(labscript_file, run_file, stream_queue, done_callback):
    """Compiles labscript_file with run_file. This function is designed to
    be called in a thread.  The stdout and stderr from the compilation
    will be shoveled into stream_queue as it spews forth, and when
    compilation is complete, done_callback will be called with a boolean
    argument indicating success."""
    compiler_path = os.path.join(os.path.dirname(__file__), 'batch_compiler.py')
    to_child, from_child, child = subproc_utils.subprocess_with_queues(compiler_path)
    to_child.put(['compile',[labscript_file, run_file]])
    while True:
        signal, data = from_child.get()
        if signal in ['stdout', 'stderr']:
            stream_queue.put([signal,data])
        elif signal == 'done':
            success = data
            to_child.put(['quit',None])
            done_callback(data)
            break
            
def compile_multishot_async(labscript_file, run_files, stream_queue, done_callback):
    """Compiles labscript_file with run_files. This function is designed to
    be called in a thread.  The stdout and stderr from the compilation
    will be shoveled into stream_queue as it spews forth, and when each
    compilation is complete, done_callback will be called with a boolean
    argument indicating success. Compilation will stop after the first failure."""
    compiler_path = os.path.join(os.path.dirname(__file__), 'batch_compiler.py')
    to_child, from_child, child = subproc_utils.subprocess_with_queues(compiler_path)
    to_child.put(['compile',[labscript_file, run_file]])
    for run_file in run_files:
        while True:
            signal, data = from_child.get()
            if signal in ['stdout', 'stderr']:
                stream_queue.put([signal,data])
            elif signal == 'done':
                success = data
                done_callback(data)
                break
        if not success:
            break
    to_child.put(['quit',None])
            
def compile_labscript_with_globals_files_async(labscript_file, globals_files, output_path, stream_queue, done_callback):   
    """Same as compile_labscript_with_globals_files, except it launches a
    thread to do the work and does not return anything. Instead, stderr
    and stdout will be put to the queue stream_queue in the format
    ['stdout','hello, world\n'] etc. When compilation is finished, the
    function done_callback will be called a boolean argument indicating
    success or failure."""
    make_run_file_from_globals_files(labscript_file, globals_files, output_path)
    thread = threading.Thread(target=compile_labscript_async, args=[labscript_file, output_path, stream_queue, done_callback])
    thread.daemon = True
    thread.start()
    

    
