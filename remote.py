DEFAULT_PORT = 42523

from labscript_utils.ls_zprocess import ZMQClient
from labscript_utils.labconfig import LabConfig


class Client(ZMQClient):
    """A ZMQClient for communication with runmanager"""

    def __init__(self, host=None, port=None):
        ZMQClient.__init__(self)
        if host is None:
            host = LabConfig().get('servers', 'runmanager', fallback='localhost')
        if port is None:
            port = LabConfig().getint('ports', 'runmanager', fallback=DEFAULT_PORT)
        self.host = host
        self.port = port

    def request(self, command, *args, **kwargs):
        return self.get(self.port, self.host, data=[command, args, kwargs], timeout=15)

    def say_hello(self):
        """Ping the runmanager server for a response"""
        return self.request('hello')

    def get_version(self):
        """Return the version of runmanager the server is running in"""
        return self.request('__version__')

    def get_globals(self, raw=False):
        """Return all active globals as a dict of the form: {'<global_name>': value}. If
        raw=True, then the global values are returned as their string representations,
        as stored in the runmanager GUI and globals HDF5 file, otherwise they are
        evaluated as python objects and then returned."""
        return self.request('get_globals', raw=raw)

    def set_globals(self, globals, raw=False):
        """For a dict of the form {'<global_name>': value}, set the given globals to the
        given values. If raw=True, then global values will be treated as the string
        representations of Python objects rather than the objects themselves, and
        written directly to the HDF5 file and runmanager GUI without calling repr() on
        them first."""
        return self.request('set_globals', globals, raw=raw)

    def engage(self):
        """Trigger shot compilation/submission"""
        return self.request('engage')

    def abort(self):
        """Trigger abort compilation/submission"""
        return self.request('abort')

    def get_run_shots(self):
        """Get boolean state of 'Run shot(s)' checkbox"""
        return self.request('get_run_shots')

    def set_run_shots(self, value):
        """Set boolean state of 'Run shot(s)' checkbox"""
        return self.request('set_run_shots', value)

    def get_view_shots(self):
        """Get boolean state of 'View shot(s)' checkbox"""
        return self.request('get_view_shots')

    def set_view_shots(self, value):
        """Set boolean state of 'View shot(s)' checkbox"""
        return self.request('set_view_shots', value)

    def get_shuffle(self):
        """Get boolean state of 'Shuffle' checkbox"""
        return self.request('get_shuffle')

    def set_shuffle(self, value):
        """Set boolean state of 'Shuffle' checkbox"""
        return self.request('set_shuffle', value)

    def n_shots(self):
        """Get the number of prospective shots from pressing 'Engage'"""
        return self.request('n_shots')

    def get_labscript_file(self):
        """Get the path of the current experiment script"""
        return self.request('get_labscript_file')

    def set_labscript_file(self, value):
        """Set the current experiment script"""
        return self.request('set_labscript_file', value)

    def get_shot_output_folder(self):
        """Get the current shot output folder"""
        return self.request('get_shot_output_folder')

    def set_shot_output_folder(self, value):
        """Set the shot output folder"""
        return self.request('set_shot_output_folder', value)

    def error_in_globals(self):
        """True if any tab of an active group contains error(s)"""
        return self.request('error_in_globals')

    def is_output_folder_default(self):
        """True if shot output folder is not the default path"""
        return self.request('is_output_folder_default')

    def reset_shot_output_folder(self):
        """Reset the shot output folder to the default path"""
        return self.request('reset_shot_output_folder')

_default_client = Client()

say_hello = _default_client.say_hello
get_version = _default_client.get_version
get_globals = _default_client.get_globals
# get_globals_full = _default_client.get_globals_full
set_globals = _default_client.set_globals
# set_globals_full = _default_client.set_globals_full
engage = _default_client.engage
abort = _default_client.abort
get_run_shots = _default_client.get_run_shots
set_run_shots = _default_client.set_run_shots
get_view_shots = _default_client.get_view_shots
set_view_shots = _default_client.set_view_shots
get_shuffle = _default_client.get_shuffle
set_shuffle = _default_client.set_shuffle
n_shots = _default_client.n_shots
get_labscript_file = _default_client.get_labscript_file
set_labscript_file = _default_client.set_labscript_file
get_shot_output_folder = _default_client.get_shot_output_folder
set_shot_output_folder = _default_client.set_shot_output_folder
error_in_globals = _default_client.error_in_globals
is_output_folder_default = _default_client.is_output_folder_default
reset_shot_output_folder = _default_client.reset_shot_output_folder

if __name__ == '__main__':
    # Test
    import time

    current = get_globals()
    print("get globals:", current)
    print("set globals", set_globals({'test': current['test'] + 1}))
    assert get_globals()['test'] == current['test'] + 1
    engage()
