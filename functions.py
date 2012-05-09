from pylab import *

def drop_times(t_min, t_max, n_points, sample_square=True, randomise=True,repeats=1):
    if sample_square:
        times = sqrt(linspace(t_min**2,t_max**2,n_points))
    else:
        times = linspace(t_min,t_max,n_points)
    
    times=repeat(times,repeats)
    
    if randomise:
        return times[argsort(rand(n_points*repeats))]
    else:
        return times