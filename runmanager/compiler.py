import os
import random
import subprocess
import threading
import traceback
import datetime
import errno
import json

import h5py
import numpy as np

from labscript_utils.ls_zprocess import ProcessTree, zmq_push_multipart
from labscript_utils.labconfig import LabConfig
process_tree = ProcessTree.instance()

from runmanager.expander import expand_globals

import runmanager.group_manager as group_manager
import runmanager.evaluator as evaluator
import runmanager.expander as expander

def next_sequence_index(shot_basedir, dt, increment=True):
    """Return the next sequence index for sequences in the given base directory (i.e.
    <experiment_shot_storage>/<script_basename>) and the date of the given datetime
    object, and increment the sequence index atomically on disk if increment=True. If
    not setting increment=True, then the result is indicative only and may be used by
    other code at any time. One must increment the sequence index prior to use."""
    from labscript_utils.ls_zprocess import Lock
    from labscript_utils.shared_drive import path_to_agnostic

    DATE_FORMAT = '%Y-%m-%d'
    # The file where we store the next sequence index on disk:
    sequence_index_file = os.path.join(shot_basedir, '.next_sequence_index')
    # Open with zlock to prevent race conditions with other code:
    with Lock(path_to_agnostic(sequence_index_file), read_only=not increment):
        try:
            with open(sequence_index_file) as f:
                datestr, sequence_index = json.load(f)
                if datestr != dt.strftime(DATE_FORMAT):
                    # New day, start from zero again:
                    sequence_index = 0
        except (OSError, IOError) as exc:
            if exc.errno != errno.ENOENT:
                raise
            # File doesn't exist yet, start from zero
            sequence_index = 0
        if increment:
            # Write the new file with the incremented sequence index
            os.makedirs(os.path.dirname(sequence_index_file), exist_ok=True)
            with open(sequence_index_file, 'w') as f:
                json.dump([dt.strftime(DATE_FORMAT), sequence_index + 1], f)
        return sequence_index


def new_sequence_details(script_path, config=None, increment_sequence_index=True):
    """Generate the details for a new sequence: the toplevel attrs sequence_date,
    sequence_index, sequence_id; and the the output directory and filename prefix for
    the shot files, according to labconfig settings. If increment_sequence_index=True,
    then we are claiming the resulting sequence index for use such that it cannot be
    used by anyone else. This should be done if the sequence details are immediately
    about to be used to compile a sequence. Otherwise, set increment_sequence_index to
    False, but in that case the results are indicative only and one should call this
    function again with increment_sequence_index=True before compiling the sequence, as
    otherwise the sequence_index may be used by other code in the meantime."""
    if config is None:
        config = LabConfig()
    script_basename = os.path.splitext(os.path.basename(script_path))[0]
    shot_storage = config.get('DEFAULT', 'experiment_shot_storage')
    shot_basedir = os.path.join(shot_storage, script_basename)
    now = datetime.datetime.now()
    sequence_timestamp = now.strftime('%Y%m%dT%H%M%S')

    # Toplevel attributes to be saved to the shot files:
    sequence_date = now.strftime('%Y-%m-%d')
    sequence_id = sequence_timestamp + '_' + script_basename
    sequence_index = next_sequence_index(shot_basedir, now, increment_sequence_index)

    sequence_attrs = {
        'script_basename': script_basename,
        'sequence_date': sequence_date,
        'sequence_index': sequence_index,
        'sequence_id': sequence_id,
    }

    # Compute the output directory based on labconfig settings:
    try:
        subdir_format = config.get('runmanager', 'output_folder_format')
    except (LabConfig.NoOptionError, LabConfig.NoSectionError):
        subdir_format = os.path.join('%Y', '%m', '%d', '{sequence_index:05d}')

    # Format the output directory according to the current timestamp, sequence index and
    # sequence_timestamp, if present in the format string:
    subdir = now.strftime(subdir_format).format(
        sequence_index=sequence_index, sequence_timestamp=sequence_timestamp
    )
    shot_output_dir = os.path.join(shot_basedir, subdir)

    # Compute the shot filename prefix according to labconfig settings:
    try:
        filename_prefix_format = config.get('runmanager', 'filename_prefix_format')
    except (LabConfig.NoOptionError, LabConfig.NoSectionError):
        # Default, for backward compatibility:
        filename_prefix_format = '{sequence_timestamp}_{script_basename}'
    # Format the filename prefix according to the current timestamp, sequence index,
    # sequence_timestamp, and script_basename, if present in the format string:
    filename_prefix = now.strftime(filename_prefix_format).format(
        sequence_index=sequence_index,
        sequence_timestamp=sequence_timestamp,
        script_basename=script_basename,
    )

    return sequence_attrs, shot_output_dir, filename_prefix


def make_run_files(
    output_folder,
    sequence_globals,
    shots,
    sequence_attrs,
    filename_prefix,
    shuffle=False,
):
    """Does what it says. sequence_globals and shots are of the datatypes returned by
    get_globals and get_shots, one is a nested dictionary with string values, and the
    other a flat dictionary. sequence_attrs is a dict of the attributes pertaining to
    this sequence to be initially set at the top-level group of the h5 file, as returned
    by new_sequence_details. output_folder and filename_prefix determine the directory
    shot files will be output to, as well as their filenames (this function will
    generate filenames with the shot number and .h5 extension appended to
    filename_prefix). Sensible defaults for these are also returned by
    new_sequence_details(), so preferably these should be used.

    Shuffle will randomise the order that the run files are generated in with respect to
    which element of shots they come from. This function returns a *generator*. The run
    files are not actually created until you loop over this generator (which gives you
    the filepaths). This is useful for not having to clean up as many unused files in
    the event of failed compilation of labscripts. If you want all the run files to be
    created at some point, simply convert the returned generator to a list. The
    filenames the run files are given is simply the sequence_id with increasing integers
    appended."""
    basename = os.path.join(output_folder, filename_prefix)
    nruns = len(shots)
    ndigits = int(np.ceil(np.log10(nruns)))
    if shuffle:
        random.shuffle(shots)
    for i, shot_globals in enumerate(shots):
        runfilename = ('%s_%0' + str(ndigits) + 'd.h5') % (basename, i)
        make_single_run_file(
            runfilename, sequence_globals, shot_globals, sequence_attrs, i, nruns
        )
        yield runfilename


def make_single_run_file(filename, sequenceglobals, runglobals, sequence_attrs, run_no, n_runs):
    """Does what it says. runglobals is a dict of this run's globals, the format being
    the same as that of one element of the list returned by expand_globals.
    sequence_globals is a nested dictionary of the type returned by get_globals.
    sequence_attrs is a dict of attributes pertaining to this sequence, as returned by
    new_sequence_details. run_no and n_runs must be provided, if this run file is part
    of a sequence, then they should reflect how many run files are being generated in
    this sequence, all of which must have identical sequence_attrs."""
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with h5py.File(filename, 'w') as f:
        f.attrs.update(sequence_attrs)
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
                           '%s: %s' % (e.__class__.__name__, str(e)))
                raise ValueError(message)


def make_run_file_from_globals_files(labscript_file, globals_files, output_path, config=None):
    """Creates a run file output_path, using all the globals from globals_files. Uses
    labscript_file to determine the sequence_attrs only"""
    groups = group_manager.get_all_groups(globals_files)
    sequence_globals = group_manager.get_globals(groups)
    evaled_globals, global_hierarchy, expansions = evaluator.evaluate_globals(sequence_globals)
    shots = expander.expand_globals(sequence_globals, evaled_globals)
    if len(shots) > 1:
        scanning_globals = []
        for global_name in expansions:
            if expansions[global_name]:
                scanning_globals.append(global_name)
        raise ValueError('Cannot compile to a single run file: The following globals are a sequence: ' +
                         ', '.join(scanning_globals))

    sequence_attrs, _, _ = new_sequence_details(
        labscript_file, config=config, increment_sequence_index=True
    )
    make_single_run_file(output_path, sequence_globals, shots[0], sequence_attrs, 1, 1)


def compile_labscript_async(labscript_file, run_file,
                            stream_port=None, done_callback=None):
    """Compiles labscript_file with run_file.
    
    This function is designed to be called in a thread. 
    The stdout and stderr from the compilation will be shovelled into
    stream_port via zmq push as it spews forth, and when compilation is complete,
    done_callback will be called with a boolean argument indicating success. Note that
    the zmq communication will be encrypted, or not, according to security settings in
    labconfig. If you want to receive the data on a zmq socket, do so using a PULL
    socket created from a :class:`labscript_utils.ls_zprocess.Context`, or using a
    :class:`labscript_utils.ls_zprocess.ZMQServer`. These subclasses will also be configured
    with the appropriate security settings and will be able to receive the messages.

    Args:
        labscript_file (str): Path to labscript file to be compiled
        run_file (str): Path to h5 file where compilation output is stored.
            This file must already exist with proper globals initialization.
            See :func:`new_globals_file` for details.
        stream_port (zmq.socket, optional): ZMQ socket to push stdout and stderr.
            If None, defaults to calling process stdout/stderr. Default is None.
        done_callback (function, optional): Callback function run when compilation finishes.
            Takes a single boolean argument marking compilation success or failure.
            If None, callback is skipped. Default is None.
    """
    compiler_path = os.path.join(os.path.dirname(__file__), 'batch_compiler.py')
    to_child, from_child, child = process_tree.subprocess(
        compiler_path, output_redirection_port=stream_port
    )
    to_child.put(['compile', [labscript_file, run_file]])
    while True:
        signal, data = from_child.get()
        if signal == 'done':
            success = data
            to_child.put(['quit', None])
            child.communicate()
            if done_callback is not None:
                done_callback(success)
            break
        else:
            raise RuntimeError((signal, data))


def compile_multishot_async(labscript_file, run_files,
                            stream_port=None, done_callback=None):
    """Compiles labscript_file with multiple run_files (ie globals).
    
    This function is designed to be called in a thread.
    The stdout and stderr from the compilation will be shovelled into
    stream_port via zmq push as it spews forth, and when each compilation is complete,
    done_callback will be called with a boolean argument indicating success. Compilation
    will stop after the first failure.  If you want to receive the data on a zmq socket,
    do so using a PULL socket created from a :class:`labscript_utils.ls_zprocess.Context`, or
    using a :class:`labscript_utils.ls_zprocess.ZMQServer`. These subclasses will also be
    configured with the appropriate security settings and will be able to receive the
    messages.
    
    Args:
        labscript_file (str): Path to labscript file to be compiled
        run_files (list of str): Paths to h5 file where compilation output is stored.
            These files must already exist with proper globals initialization.
        stream_port (zmq.socket, optional): ZMQ socket to push stdout and stderr.
            If None, defaults to calling process stdout/stderr. Default is None.
        done_callback (function, optional): Callback function run when compilation finishes.
            Takes a single boolean argument marking compilation success or failure.
            If None, callback is skipped. Default is None.
    """
    compiler_path = os.path.join(os.path.dirname(__file__), 'batch_compiler.py')
    to_child, from_child, child = process_tree.subprocess(
        compiler_path, output_redirection_port=stream_port
    )
    try:
        for run_file in run_files:
            to_child.put(['compile', [labscript_file, run_file]])
            while True:
                signal, data = from_child.get()
                if signal == 'done':
                    success = data
                    if done_callback is not None:
                        done_callback(data)
                    break
            if not success:
                break
    except Exception:
        error = traceback.format_exc()
        zmq_push_multipart(stream_port, data=[b'stderr', error.encode('utf-8')])
        to_child.put(['quit', None])
        child.communicate()
        raise
    to_child.put(['quit', None])
    child.communicate()


def compile_labscript_with_globals_files_async(labscript_file, globals_files, output_path,
                                               stream_port, done_callback):
    """Compiles labscript_file with multiple globals files into a directory.
    
    Instead, stderr and stdout will be put to
    stream_port via zmq push in the multipart message format `['stdout','hello, world\\n']`
    etc. When compilation is finished, the function done_callback will be called a
    boolean argument indicating success or failure.  If you want to receive the data on
    a zmq socket, do so using a PULL socket created from a
    :external:class:`labscript_utils.ls_zprocess.Context`, or using a
    :external:class:`labscript_utils.ls_zprocess.ZMQServer`. These subclasses will also be configured with
    the appropriate security settings and will be able to receive the messages.
    
    Args:
        labscript_file (str): Path to labscript file to be compiled
        globals_files (list of str): Paths to h5 file where globals values to be used are stored.
            See :func:`make_run_file_from_globals_files` for details.
        output_path (str): Folder to save compiled h5 files to.
        stream_port (zmq.socket): ZMQ socket to push stdout and stderr.
        done_callback (function): Callback function run when compilation finishes.
            Takes a single boolean argument marking compilation success or failure.
    """
    try:
        make_run_file_from_globals_files(labscript_file, globals_files, output_path)
        thread = threading.Thread(
            target=compile_labscript_async, args=[labscript_file, output_path, stream_port, done_callback])
        thread.daemon = True
        thread.start()
    except Exception:
        error = traceback.format_exc()
        zmq_push_multipart(stream_port, data=[b'stderr', error.encode('utf-8')])
        t = threading.Thread(target=done_callback, args=(False,))
        t.daemon = True
        t.start()

