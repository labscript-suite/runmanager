from pylab import *

def drop_times(t_min, t_max, n_points, sample_square=True, randomise=True):
    if sample_square:
        times = sqrt(linspace(t_min**2,t_max**2,n_points))
    else:
        times = linspace(t_min,t_max,n_points)
    if randomise:
        return times[argsort(randn(n_points))]
    else:
        return times