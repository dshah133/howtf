"""symsplit -- a split-state linking diagnostic (binding simulator).

The contribution is not "does a duplicate symbol exist" (that is
``nm | sort | uniq -d``). It is "will two modules in this process image
resolve the same strong symbol name to DIFFERENT definitions" -- the silent
split-state failure class. See README.md for the model.
"""

__version__ = "0.1.0"
