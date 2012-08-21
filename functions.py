from __future__ import division
from pylab import *

def quadspace(t_min, t_max, n_points, randomise=False, repeats=1):
    times = sqrt(linspace(t_min**2, t_max**2, n_points))
    times = repeat(times, repeats)
    
    if randomise:
        return times[argsort(rand(n_points*repeats))]
    else:
        return times
        
drop_times = quadspace
