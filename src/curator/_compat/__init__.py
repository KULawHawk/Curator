"""Compatibility shims for stdlib changes across Python versions.

Each module here addresses a specific compatibility concern. Currently:

* :mod:`curator._compat.datetime` -- replacement for ``datetime.utcnow()``
  which was deprecated in 3.12 and is scheduled for removal in 3.14.

When the minimum supported Python version rises past the breakage point,
the corresponding module here can be removed and its callers updated to
use the new stdlib API directly.
"""
