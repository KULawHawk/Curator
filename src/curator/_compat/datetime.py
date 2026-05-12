"""Drop-in replacement for ``datetime.utcnow()`` (v1.7.47).

Background
----------

``datetime.datetime.utcnow()`` was deprecated in Python 3.12 (PEP 695) and
is scheduled for removal in Python 3.14. The deprecation emits a
``DeprecationWarning`` on every call, which adds up: a full Curator test
baseline produces 6000+ warnings, most from this single deprecation.

The stdlib's recommended replacement is ``datetime.now(timezone.utc)``,
which returns a *timezone-aware* datetime. That's the modern API and the
right long-term direction, but a wholesale audit-and-conversion of every
datetime in Curator is a separate, larger ship: there are
comparisons-to-naive-datetimes, serialization paths, SQLite TEXT-column
storage assumptions, and downstream consumers (e.g. the GUI's table
display) that would all need to be reviewed.

For now, this module provides a **drop-in replacement that preserves
the naive-output behavior** of the old API. ``utcnow_naive()`` returns a
timezone-aware ``datetime.now(timezone.utc)`` with its ``tzinfo``
stripped via ``.replace(tzinfo=None)``, producing a value bit-identical
to what ``datetime.utcnow()`` would have returned. No behavior change at
any call site; the only effect is silencing the deprecation warning.

When the project is ready for the timezone-aware migration, the work
is:

  1. Audit each call site for naive-vs-aware comparison hazards.
  2. Change ``utcnow_naive()`` to ``utc_now_aware()`` (or just inline
     ``datetime.now(timezone.utc)``) at the safe sites.
  3. Update the SQLite row factories to round-trip ``tzinfo`` correctly
     (currently they assume naive UTC).
  4. Update pydantic models to declare ``datetime`` fields as aware.

That's a v1.8.x-class ship; this module is the bridge until then.

API
---

* :func:`utcnow_naive` -- current UTC time as a naive datetime
"""

from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["utcnow_naive"]


def utcnow_naive() -> datetime:
    """Current UTC time as a *naive* (tzinfo-free) ``datetime``.

    Bit-identical replacement for the deprecated ``datetime.utcnow()``.

    Returns:
        A naive ``datetime`` representing the current UTC instant.
        Equivalent to the historical ``datetime.utcnow()`` return value
        but without the deprecation warning.

    Example:
        >>> from curator._compat.datetime import utcnow_naive
        >>> now = utcnow_naive()
        >>> now.tzinfo is None
        True
        >>> # Same usage patterns as the deprecated API:
        >>> from datetime import timedelta
        >>> one_hour_ago = utcnow_naive() - timedelta(hours=1)

    Notes:
        For new code where timezone-awareness is wanted, prefer the
        stdlib's ``datetime.now(timezone.utc)`` directly. This helper
        exists for the migration path from ``datetime.utcnow()``, not
        as a recommendation for new naive-datetime use.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
