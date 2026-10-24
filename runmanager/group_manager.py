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
"""Functions for reading, writing, and modifying globals groups.
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import time
import tokenize
import warnings

import h5py
import labscript_utils.shot_utils
import numpy as np
from labscript_utils.ls_zprocess import Lock
from labscript_utils.shared_drive import path_to_agnostic

from runmanager.evaluator import evaluate_globals
from runmanager.exceptions import ExpansionError
from runmanager.tokenizer import remove_comments_and_tokenify

# The score of a value in the history of a global halves over this interval, in seconds:
HISTORY_HALF_LIFE = 7 * 24 * 3600
# The score of a value is increased at most once in this interval, in seconds:
HISTORY_SCORE_INTERVAL = 3600
HISTORY_MAX_ENTRIES = 20
# The maximum size of the JSON history of one global. HDF5 attributes stored in the
# object header must be smaller than 64 kB.
HISTORY_MAX_BYTES = 60000


# HDF5 stores variable-length strings in global heap collections of at least 4 kB, and
# allocates a new collection each time a globals file is opened and a string is written to
# it. The free space in earlier collections is not reused, so we repack a globals file
# once it has grown by more than this many bytes and to more than twice its size since it
# was last repacked:
_REPACK_GROWTH = 1 << 20

# Size in bytes of each globals file after it was last repacked by this process, keyed by
# absolute path
_repacked_sizes = {}


def _ensure_str(s):
    """convert bytestrings and numpy strings to python strings"""
    return s.decode() if isinstance(s, bytes) else str(s)


def is_valid_python_identifier(name):
    # No whitespace allowed. Do this check here because an actual newline in the source
    # is not easily distinguished from a NEWLINE token in the produced tokens, which is
    # produced even when there is no newline character in the string. So since we ignore
    # NEWLINE later, we must check for it now.
    if name != "".join(name.split()):
        return False
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(name).readline))
    except tokenize.TokenError:
        return False
    token_types = [
        t[0] for t in tokens if t[0] not in [tokenize.NEWLINE, tokenize.ENDMARKER]
    ]
    if len(token_types) == 1:
        return token_types[0] == tokenize.NAME
    return False


def is_valid_hdf5_group_name(name):
    """Ensure that a string is a valid name for an hdf5 group.

    The names of hdf5 groups may only contain ASCII characters. Furthermore, the
    characters "/" and "." are not allowed.

    Args:
        name (str): The potential name for an hdf5 group.

    Returns:
        bool: Whether or not `name` is a valid name for an hdf5 group. This will
            be `True` if it is a valid name or `False` otherwise.
    """    
    # Ensure only ASCII characters are used.
    for char in name:
        if ord(char) >= 128:
            return False
    
    # Ensure forbidden ASCII characters are not used.
    forbidden_characters = ['.', '/']
    for character in forbidden_characters:
        if character in name:
            return False
    return True


def _grown(size, repacked_size):
    return size > 2 * repacked_size and size > repacked_size + _REPACK_GROWTH


def _attributes(f):
    """The attributes of every object in an open h5 file, keyed by the path of the
    object"""
    objects = {'/': f}
    f.visititems(objects.__setitem__)
    return {path: {name: obj.attrs[name] for name in obj.attrs} for path, obj in objects.items()}


def _same_attributes(a, b):
    return a.keys() == b.keys() and all(
        a[path].keys() == b[path].keys()
        and all(np.array_equal(a[path][name], b[path][name]) for name in a[path])
        for path in a
    )


def _repack(path, size):
    """Copy the globals file at the absolute path, which is size bytes, to a new file.
    Replace the file with the copy if the file is too large compared with the copy, and
    record the size of the copy. The caller must hold the write lock of the file."""
    directory, name = os.path.split(path)
    try:
        fd, temp_path = tempfile.mkstemp(prefix=f'.{name}.', suffix='.repack', dir=directory)
    except OSError:
        return
    os.close(fd)
    try:
        with h5py.File(path, 'r') as source, h5py.File(temp_path, 'w') as dest:
            for attr_name in source.attrs:
                dest.attrs.create(
                    attr_name, source.attrs[attr_name], dtype=source.attrs.get_id(attr_name).dtype
                )
            for item_name in source:
                source.copy(source[item_name], dest, item_name)
            same = _same_attributes(_attributes(source), _attributes(dest))
        if not same:
            return
        repacked_size = os.path.getsize(temp_path)
        if _grown(size, repacked_size):
            shutil.copymode(path, temp_path)
            # Fails on Windows if another process has the file open, in which case we try
            # again after the next write:
            os.replace(temp_path, path)
        _repacked_sizes[path] = repacked_size
    except OSError:
        pass
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def _repack_if_grown(filename):
    """Repack the globals file if it has grown too much since it was last repacked by
    this process. The first call for each file repacks it to find its repacked size."""
    path = os.path.abspath(filename)
    if path in _repacked_sizes and not _grown(os.path.getsize(path), _repacked_sizes[path]):
        return
    with Lock(path_to_agnostic(path)):
        # Another thread may have repacked the file since the check above:
        size = os.path.getsize(path)
        if path in _repacked_sizes and not _grown(size, _repacked_sizes[path]):
            return
        _repack(path, size)


@contextlib.contextmanager
def _open_for_writing(filename):
    """Open a globals file in append mode, and repack it after closing it if required"""
    with h5py.File(filename, 'a') as f:
        yield f
    _repack_if_grown(filename)


def new_globals_file(filename):
    """Creates a new globals h5 file.
    
    Creates a 'globals' group at the top level.
    If file does not exist, a new h5 file is created.
    """
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
            if 'expansion' not in group:
                requires_expansion_group.append(groupname)
    if requires_expansion_group:
        group_globalslists = [get_globalslist(filename, groupname) for groupname in requires_expansion_group]
        with _open_for_writing(filename) as f:
            for groupname, globalslist in zip(requires_expansion_group, group_globalslists):
                group = f['globals'][groupname]
                subgroup = group.create_group('expansion')
                # Initialise all expansion settings to blank strings:
                for name in globalslist:
                    subgroup.attrs[name] = ''
        groups = {group_name: filename for group_name in get_grouplist(filename)}
        sequence_globals = get_globals(groups)
        evaled_globals, _, _ = evaluate_globals(sequence_globals, raise_exceptions=False)
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
    if not is_valid_hdf5_group_name(groupname):
        raise ValueError(
            'Invalid group name. Group names must contain only ASCII '
            'characters and cannot include "/" or ".".'
        )
    with _open_for_writing(filename) as f:
        if groupname in f['globals']:
            raise Exception('Can\'t create group: target name already exists.')
        group = f['globals'].create_group(groupname)
        group.create_group('units')
        group.create_group('expansion')
        group.create_group('expansion_custom')


def copy_group(source_globals_file, source_groupname, dest_globals_file, delete_source_group=False):
    """ This function copies the group source_groupname from source_globals_file
        to dest_globals_file and renames the new group so that there is no name
        collision. If delete_source_group is False the copyied files have
        a suffix '_copy'."""
    with contextlib.ExitStack() as stack:
        source_f = stack.enter_context(_open_for_writing(source_globals_file))
        # check if group exists
        if source_groupname not in source_f['globals']:
            raise Exception(f'Can\'t copy there is no group "{source_groupname}"!')

        # Are we coping from one file to another?
        if dest_globals_file is not None and source_globals_file != dest_globals_file:
            dest_f = stack.enter_context(_open_for_writing(dest_globals_file))
        else:
            dest_f = source_f  # no -> dest files is source file

        # Rename the group until there is no name collision: g, g_copy, g_copy(1),
        # g_copy(2), ... for a copy and g, g(1), g(2), ... for a move
        dest_groupname = source_groupname
        if dest_groupname in dest_f['globals'] and not delete_source_group:
            dest_groupname = f"{source_groupname}_copy"
        base_groupname = dest_groupname
        i = 1
        while dest_groupname in dest_f['globals']:
            dest_groupname = f"{base_groupname}({i})"
            i += 1

        # copy group
        dest_f.copy(source_f['globals'][source_groupname], f'/globals/{dest_groupname}')

    return dest_groupname


def rename_group(filename, oldgroupname, newgroupname):
    if oldgroupname == newgroupname:
        # No rename!
        return
    if not is_valid_hdf5_group_name(newgroupname):
        raise ValueError(
            'Invalid group name. Group names must contain only ASCII '
            'characters and cannot include "/" or ".".'
        )
    with _open_for_writing(filename) as f:
        if newgroupname in f['globals']:
            raise Exception('Can\'t rename group: target name already exists.')
        f.copy(f['globals'][oldgroupname], f'/globals/{newgroupname}')
        del f['globals'][oldgroupname]


def delete_group(filename, groupname):
    with _open_for_writing(filename) as f:
        del f['globals'][groupname]


def get_globalslist(filename, groupname):
    with h5py.File(filename, 'r') as f:
        group = f['globals'][groupname]
        # File closes after this function call, so have to convert
        # the attrs to a dict before its file gets dereferenced:
        return dict(group.attrs)


def new_global(filename, groupname, globalname):
    if not is_valid_python_identifier(globalname):
        raise ValueError(f'{globalname} is not a valid Python variable name')
    with _open_for_writing(filename) as f:
        group = f['globals'][groupname]
        if globalname in group.attrs:
            raise Exception('Can\'t create global: target name already exists.')
        group.attrs[globalname] = ''
        group['units'].attrs[globalname] = ''
        group['expansion'].attrs[globalname] = ''
        group.require_group('expansion_custom').attrs[globalname] = False


def rename_global(filename, groupname, oldglobalname, newglobalname):
    if oldglobalname == newglobalname:
        # No rename!
        return
    if not is_valid_python_identifier(newglobalname):
        raise ValueError(f'{newglobalname} is not a valid Python variable name')
    value = get_value(filename, groupname, oldglobalname)
    units = get_units(filename, groupname, oldglobalname)
    expansion = get_expansion(filename, groupname, oldglobalname)
    custom = get_expansion_custom(filename, groupname, oldglobalname)
    with _open_for_writing(filename) as f:
        group = f['globals'][groupname]
        if newglobalname in group.attrs:
            raise Exception('Can\'t rename global: target name already exists.')
        group.attrs[newglobalname] = value
        group['units'].attrs[newglobalname] = units
        group['expansion'].attrs[newglobalname] = expansion
        if custom is not None:
            group['expansion_custom'].attrs[newglobalname] = custom
        del group.attrs[oldglobalname]
        del group['units'].attrs[oldglobalname]
        del group['expansion'].attrs[oldglobalname]
        if custom is not None:
            del group['expansion_custom'].attrs[oldglobalname]
        if 'history' in group and oldglobalname in group['history'].attrs:
            group['history'].attrs[newglobalname] = group['history'].attrs[oldglobalname]
            del group['history'].attrs[oldglobalname]


def get_value(filename, groupname, globalname):
    with h5py.File(filename, 'r') as f:
        value = f['globals'][groupname].attrs[globalname]
        # Replace numpy strings with python unicode strings.
        # DEPRECATED, for backward compat with old files
        value = _ensure_str(value)
        return value


def set_value(filename, groupname, globalname, value):
    with _open_for_writing(filename) as f:
        f['globals'][groupname].attrs[globalname] = value


def get_units(filename, groupname, globalname):
    with h5py.File(filename, 'r') as f:
        value = f['globals'][groupname]['units'].attrs[globalname]
        # Replace numpy strings with python unicode strings.
        # DEPRECATED, for backward compat with old files
        value = _ensure_str(value)
        return value


def set_units(filename, groupname, globalname, units):
    with _open_for_writing(filename) as f:
        f['globals'][groupname]['units'].attrs[globalname] = units


def get_expansion(filename, groupname, globalname):
    with h5py.File(filename, 'r') as f:
        value = f['globals'][groupname]['expansion'].attrs[globalname]
        # Replace numpy strings with python unicode strings.
        # DEPRECATED, for backward compat with old files
        value = _ensure_str(value)
        return value


def get_expansion_custom(filename, groupname, globalname):
    """Whether the expansion setting of the global was chosen by the user rather than set
    automatically. None if the file does not record this for the global."""
    with h5py.File(filename, 'r') as f:
        group = f['globals'][groupname]
        if 'expansion_custom' in group and globalname in group['expansion_custom'].attrs:
            return bool(group['expansion_custom'].attrs[globalname])
        return None


def set_expansion(filename, groupname, globalname, expansion, custom=True):
    """Sets the expansion setting of the global, and records whether it was chosen by the
    user (custom=True) or set automatically."""
    with _open_for_writing(filename) as f:
        group = f['globals'][groupname]
        group['expansion'].attrs[globalname] = expansion
        group.require_group('expansion_custom').attrs[globalname] = bool(custom)


def delete_global(filename, groupname, globalname):
    with _open_for_writing(filename) as f:
        group = f['globals'][groupname]
        del group.attrs[globalname]
        for subgroup in ['units', 'expansion', 'expansion_custom', 'history']:
            if subgroup in group and globalname in group[subgroup].attrs:
                del group[subgroup].attrs[globalname]


def history_key(value):
    """The key that identifies a value in the history of a global: its tokens without
    comments or whitespace, so that values that differ only in those are the same."""
    _, tokens = remove_comments_and_tokenify(value)
    return ' '.join(token for token in tokens if token.strip())


def history_score(entry, now):
    """The score of a history entry at the time now, which is the number of times that
    the value was used, with each use weighted by its age."""
    return entry['score'] * 2 ** (-(now - entry['scored']) / HISTORY_HALF_LIFE)


def _read_history(history_group, globalname):
    if history_group is None or globalname not in history_group.attrs:
        return []
    return json.loads(_ensure_str(history_group.attrs[globalname]))


def _write_history(history_group, globalname, entries):
    # A fixed-length bytes attribute is overwritten in place. A variable-length string
    # would be written to the HDF5 global heap, which does not reuse the space of the
    # old value, and the file would grow every time the history changed.
    history_group.attrs[globalname] = np.bytes_(json.dumps(entries).encode('utf8'))


def get_history(filename, groupname, globalname):
    """Return the history of the values of a global, as a list of entries in no
    particular order. Each entry is a dictionary with the keys:

    'text': the text of the value the last time it was used, including comments
    'key': the history_key() of the value
    'last_used': the time at which the value was last used, in seconds since the epoch
    'score', 'scored': the score of the value and the time at which it was last
        updated, from which history_score() computes its current score
    'pinned': whether the user has pinned the entry
    'expansion': the expansion setting that the global had when the value was last
        used, if it was set by the user. Absent if the expansion setting was
        automatic."""
    with h5py.File(filename, 'r') as f:
        group = f['globals'][groupname]
        return _read_history(group.get('history'), globalname)


def record_history(groups, sequence_globals, custom_expansions, now=None):
    """Record that the globals in sequence_globals were used to compile shots.
    groups is a dictionary of group_name: h5_file pairs, and sequence_globals and
    custom_expansions are as returned by get_globals() and get_custom_expansions() for
    those groups. Groups and globals that no longer exist are ignored."""
    if now is None:
        now = time.time()
    filepaths = set(groups.values())
    for filepath in filepaths:
        groups_from_this_file = [g for g, f in groups.items() if f == filepath]
        with h5py.File(filepath, 'a') as f:
            for group_name in groups_from_this_file:
                if group_name not in f['globals']:
                    continue
                group = f['globals'][group_name]
                history_group = group.require_group('history')
                for global_name, (value, _, expansion) in sequence_globals[group_name].items():
                    if global_name not in group.attrs:
                        continue
                    entries = _read_history(history_group, global_name)
                    custom = custom_expansions[group_name].get(global_name)
                    if _update_history(entries, value, expansion if custom else None, now):
                        _write_history(history_group, global_name, entries)


def _update_history(entries, value, expansion, now):
    """Add a use of the value to the list of history entries, and remove the entries
    with the lowest scores if there are too many. expansion is the expansion setting if
    it was chosen by the user, otherwise None. Return whether the entries can be
    stored."""
    key = history_key(value)
    for entry in entries:
        if entry['key'] == key:
            if now - entry['scored'] >= HISTORY_SCORE_INTERVAL:
                entry['score'] = history_score(entry, now) + 1
                entry['scored'] = now
            break
    else:
        entry = {'key': key, 'score': 1, 'scored': now, 'pinned': False}
        entries.append(entry)
    entry['text'] = value
    entry['last_used'] = now
    if expansion is None:
        entry.pop('expansion', None)
    else:
        entry['expansion'] = expansion
    # Unpinned entries are removed first, lowest score first:
    removable = sorted(
        (e for e in entries if e is not entry),
        key=lambda e: (e['pinned'], history_score(e, now)),
    )
    while removable and (
        len(entries) > HISTORY_MAX_ENTRIES
        or len(json.dumps(entries).encode('utf8')) > HISTORY_MAX_BYTES
    ):
        entries.remove(removable.pop(0))
    return len(json.dumps(entries).encode('utf8')) <= HISTORY_MAX_BYTES


def _modify_history_entry(filename, groupname, globalname, key, modify):
    with h5py.File(filename, 'a') as f:
        group = f['globals'][groupname]
        history_group = group.get('history')
        entries = _read_history(history_group, globalname)
        for entry in entries:
            if entry['key'] == key:
                modify(entries, entry)
                _write_history(history_group, globalname, entries)
                return


def set_history_pinned(filename, groupname, globalname, key, pinned):
    """Pin or unpin the history entry of a global that has the given history_key()"""
    def modify(entries, entry):
        entry['pinned'] = bool(pinned)
    _modify_history_entry(filename, groupname, globalname, key, modify)


def delete_history_entry(filename, groupname, globalname, key):
    """Remove the history entry of a global that has the given history_key()"""
    def modify(entries, entry):
        entries.remove(entry)
    _modify_history_entry(filename, groupname, globalname, key, modify)


def previous_history_entry(entries, current_value):
    """Return the most recently used history entry that is not the current value, or
    None if there is none."""
    key = history_key(current_value)
    others = [e for e in entries if e['key'] != key]
    if not others:
        return None
    return max(others, key=lambda e: e['last_used'])


def ordered_history(entries, current_value, now=None):
    """Return the history entries in the order in which to show them: the entry for the
    current value, then the most recently used of the other entries, then the pinned
    entries, then the other entries. The pinned and other entries are in order of
    decreasing score."""
    if now is None:
        now = time.time()
    key = history_key(current_value)
    previous = previous_history_entry(entries, current_value)
    current = [e for e in entries if e['key'] == key]
    rest = sorted(
        (e for e in entries if e['key'] != key and e is not previous),
        key=lambda e: (not e['pinned'], -history_score(e, now)),
    )
    return current + ([previous] if previous is not None else []) + rest


def guess_expansion_type(value):
    if isinstance(value, (np.ndarray, list, range)):
        return 'outer'
    else:
        return ''


def automatic_expansions(evaled_globals, global_hierarchy, expansions, custom):
    """The expansion setting that each global gets automatically. evaled_globals maps
    global names to values, global_hierarchy maps the name of each global that refers to
    other globals to their names, expansions maps global names to their current settings
    and custom maps global names to whether their setting was chosen by the user.

    A global with a custom setting keeps it. A list, array or range is 'outer' and any
    other value is '', except that a global that fails to evaluate keeps its setting. The
    'outer' globals that are derived, through other 'outer' globals, from an 'outer'
    global that is not itself derived from an expanded global are zipped with it in a
    zip group named after it."""
    result = {}
    outer = set()
    for global_name, value in evaled_globals.items():
        if custom.get(global_name):
            result[global_name] = expansions[global_name]
        elif isinstance(value, ExpansionError):
            result[global_name] = ''
        elif isinstance(value, Exception):
            result[global_name] = expansions[global_name]
        else:
            result[global_name] = guess_expansion_type(value)
            if result[global_name] == 'outer':
                outer.add(global_name)

    def expanded(global_name):
        return global_name in outer or (custom.get(global_name) and expansions[global_name])

    for root in sorted(outer):
        if any(expanded(name) for name in global_hierarchy.get(root, [])):
            continue
        members = set()
        to_visit = [root]
        while to_visit:
            name = to_visit.pop()
            for other in outer - members - {root}:
                if name in global_hierarchy.get(other, []):
                    members.add(other)
                    to_visit.append(other)
        for global_name in members | ({root} if members else set()):
            if result[global_name] == 'outer':
                result[global_name] = root
    return result


def get_all_groups(h5_files):
    """returns a dictionary of group_name: h5_path pairs from a list of h5_files."""
    if isinstance(h5_files, (bytes, str)):
        h5_files = [h5_files]
    groups = {}
    for path in h5_files:
        for group_name in get_grouplist(path):
            if group_name in groups:
                raise ValueError(f'Error: group {group_name} is defined in both {groups[group_name]} and {path}. '
                                 'Only uniquely named groups can be used together '
                                 'to make a run file.')
            groups[group_name] = path
    return groups


def get_custom_expansions(groups):
    """Takes a dictionary of group_name: h5_file pairs and returns a dictionary of the
    same structure as get_globals, mapping each global name to whether its expansion
    setting was chosen by the user, or None if the file does not record this."""
    filepaths = set(groups.values())
    custom = {}
    for filepath in filepaths:
        groups_from_this_file = [g for g, f in groups.items() if f == filepath]
        with h5py.File(filepath, 'r') as f:
            for group_name in groups_from_this_file:
                globals_group = f['globals'][group_name]
                flags = dict(globals_group['expansion_custom'].attrs) if 'expansion_custom' in globals_group else {}
                custom[group_name] = {global_name: (bool(flags[global_name]) if global_name in flags else None)
                                      for global_name in globals_group.attrs}
    return custom


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

def get_shot_globals(filepath):
    """Returns the evaluated globals for a shot, for use by labscript or lyse.
    Simple dictionary access as in dict(h5py.File(filepath).attrs) would be fine
    except we want to apply some hacks, so it's best to do that in one place.
    
    Deprecated: use identical function `labscript_utils.shot_utils.get_shot_globals`
    """
    
    warnings.warn(
        FutureWarning("get_shot_globals has moved to labscript_utils.shot_utils. "
                      "Please update your code to import it from there."))

    return labscript_utils.shot_utils.get_shot_globals(filepath)
