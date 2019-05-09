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
        return self.get(self.port, self.host, data=[command, args, kwargs], timeout=5)

    def say_hello(self):
        """Ping the runmanager server for a response"""
        return self.request('hello')

    def get_version(self):
        """Return the version of runmanager the server is running in"""
        return self.request('__version__')

    def get_globals(self):
        """Return all active globals as a dict of the form: {'<global_name>': value}"""
        return self.request('get_globals')

    def get_globals_full(self):
        """Return all active globals and their details as a dict of the form:
        {'<group_name>': {'<global_name>': ('<global_str>', '<units>', '<expansion>')}},
        where global_str is the *unevaluated* string expression of the global."""
        return self.request('get_globals_full')

    def set_globals(self, globals):
        """For a dict of the form {'<global_name>': value}, set the given globals to the
        given values"""
        return self.request('set_globals', globals)

    def set_globals_full(self, globals_full):
        raise NotImplementedError

    def engage(self):
        """Trigger shot compilation/submission"""
        return self.request('engage')

_default_client = Client()

say_hello = _default_client.say_hello
get_version = _default_client.get_version
get_globals = _default_client.get_globals
get_globals_full = _default_client.get_globals_full
set_globals = _default_client.set_globals
set_globals_full = _default_client.set_globals_full
engage = _default_client.engage

if __name__ == '__main__':
    # Test
    import time
    current = get_globals()
    print("get globals:", current)
    print("get globals full:", get_globals_full())
    # Add 1 to test:
    print("set globals", set_globals({'test': current['test'] + '1'}))
    assert get_globals()['test'] == current['test'] + '1'