<img src="https://raw.githubusercontent.com/labscript-suite/labscript-suite/master/art/runmanager_32nx32n.svg" height="64" alt="the labscript suite – runmanager" align="right">

# the _labscript suite_ » runmanager

### Graphical and remote interface to parameterized experiments

[![Actions Status](https://github.com/labscript-suite/runmanager/workflows/Build%20and%20Release/badge.svg?branch=maintenance%2F3.0.x)](https://github.com/labscript-suite/runmanager/actions)
[![License](https://img.shields.io/pypi/l/runmanager.svg)](https://github.com/labscript-suite/runmanager/raw/master/LICENSE.txt)
[![Python Version](https://img.shields.io/pypi/pyversions/runmanager.svg)](https://python.org)
[![PyPI](https://img.shields.io/pypi/v/runmanager.svg)](https://pypi.org/project/runmanager)
[![Conda Version](https://img.shields.io/conda/v/labscript-suite/runmanager)](https://anaconda.org/labscript-suite/runmanager)
[![Google Group](https://img.shields.io/badge/Google%20Group-labscriptsuite-blue.svg)](https://groups.google.com/forum/#!forum/labscriptsuite)
<!--[![DOI](http://img.shields.io/badge/DOI-10.1063%2F1.4817213-0F79D0.svg)](https://doi.org/10.1063/1.4817213)-->


**runmanager** is an intuitive graphical interface for controlling [*labscript suite*](https://github.com/labscript-suite/labscript-suite) experiments.

Experiment parameters are defined and modified in runmanager and referenced in the labscript experiment logic (Python scripts). runmanager provides a potent framework for parameter space exploration; parameters can be raw Python expressions, with multiple iterable parameters scanned over via an outer product and/or in unison.

runmanager can be run on any host with network access to the hardware supervisor [**blacs**](https://github.com/labscript-suite/blacs), and includes a remote programming interface for automation.


## Installation

runmanager is distributed as a Python package on [PyPI](https://pypi.org/user/labscript-suite) and [Anaconda Cloud](https://anaconda.org/labscript-suite), and should be installed with other components of the _labscript suite_. Please see the [installation guide](https://docs.labscriptsuite.org/en/latest/installation) for details.
