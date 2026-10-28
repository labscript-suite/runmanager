class ExpansionError(ValueError):

    """An exception class so that error handling code can tell when a
    parsing exception was caused by a mismatch with the expansion mode.
    The value attribute is the value of the global."""

    def __init__(self, message, value):
        super().__init__(message, value)

    @property
    def value(self):
        return self.args[1]

    def __str__(self):
        return str(self.args[0])
