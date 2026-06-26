import h5py
import numpy as np
import tokenize

import labscript_utils.shot_utils

from runmanager.evaluator import evaluate_globals

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
    if not is_valid_hdf5_group_name(groupname):
        raise ValueError(
            'Invalid group name. Group names must contain only ASCII '
            'characters and cannot include "/" or ".".'
        )
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
    if not is_valid_hdf5_group_name(newgroupname):
        raise ValueError(
            'Invalid group name. Group names must contain only ASCII '
            'characters and cannot include "/" or ".".'
        )
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
