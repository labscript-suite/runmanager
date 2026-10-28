#####################################################################
#                                                                   #
# /functions.py                                                     #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program runmanager, in the labscript     #
# suite (see http://labscriptsuite.org), and is licensed under the  #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################

import numpy as np

__all__ = [
    'np', 'ns', 'us', 'ms', 's', 'Hz', 'kHz', 'MHz', 'GHz', 'quadspace', 'drop_times', 'first',
]

ns = 1e-9
us = 1e-6
ms = 1e-3
s = 1
Hz = 1
kHz = 1e3
MHz = 1e6
GHz = 1e9

def quadspace(t_min, t_max, n_points, randomise=False, repeats=1):
    times = np.sqrt(np.linspace(t_min**2, t_max**2, n_points))
    times = np.repeat(times, repeats)
    
    if randomise:
        return times[np.argsort(np.random.rand(n_points*repeats))]
    else:
        return times
       
# For backward compatibility:
drop_times = quadspace

def first():
    """Infinite iterator. Its first return value is true, subsequent
    return values are False"""
    yield True
    while True:
        yield False
