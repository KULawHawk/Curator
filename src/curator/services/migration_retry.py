"""Retry decorator for Tracer Phase 3 transient-error recovery.

See ``docs/TRACER_PHASE_3_DESIGN.md`` v0.2 RATIFIED §3 (DM-1, DM-2, DM-3) and §4.5.

v1.3.0+. Wraps :meth:`MigrationService._cross_source_transfer` with
exponential-backoff retry against a conservative error whitelist:

* HTTP 4xx/5xx for cloud sources (googleapiclient HttpError with status
  in {403, 429, 500, 502, 503, 504})
* Connection errors, timeouts, protocol errors for any I/O

Emits :pep:```migration.retry``` audit events on each retry attempt with
attempt count, error class, error message, backoff seconds, and the
parsed Retry-After header value (when present). Honors Retry-After
when the cloud explicitly tells us to wait longer than our default
exponential schedule.

The decorator is stateless and reads policy from the wrapped instance:

* ``self._max_retries`` (int, default 3) — number of retry ATTEMPTS;
  total attempts = ``_max_retries + 1``. ``0`` disables retry entirely.
* ``self._retry_backoff_cap`` (float, default 60.0) — maximum sleep
  duration between attempts, regardless of exponential formula or
  Retry-After header.
* ``self.audit`` (AuditRepository | None) — audit emission target;
  retry events are skipped if ``self.audit`` is ``None``.

The decorator does NOT decorate ``_execute_one_same_source``: same-source
local-FS errors are mostly permanent (disk full, permission denied,
file not found) and don't benefit from retry. Cloud-source transient
errors are where retry has real value. This is a deliberate simplification
from `TRACER_PHASE_3_DESIGN.md` v0.2 §4.4 — captured in the v0.3
IMPLEMENTED revision-log entry.
"""

from __future__ import annotations

import socket
import time
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from curator.services.migration import MigrationService

# Retryable HTTP status codes per DM-1.
_RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({403, 429, 500, 502, 503, 504})


def _is_retryable(exc: BaseException) -> tuple[bool, float | None]:
    """Determine if an exception is retryable + parse Retry-After if present.

    Returns:
        A tuple ``(is_retryable, retry_after_seconds_or_None)``. The
        Retry-After value, if present in the exception's response, is
        parsed and returned for the caller to incorporate into backoff.
    """
    # gdrive / generic googleapiclient HttpError
    try:
        from googleapiclient.errors import HttpError  # type: ignore

        if isinstance(exc, HttpError):
            status = getattr(exc.resp, "status", 0)
            if status in _RETRYABLE_HTTP_STATUSES:
                # Try to parse Retry-After header
                retry_after_raw = None
                if exc.resp:
                    try:
                        retry_after_raw = exc.resp.get("retry-after")
                    except (AttributeError, KeyError):
                        pass
                if retry_after_raw:
                    try:
                        return True, float(retry_after_raw)
                    except (ValueError, TypeError):
                        pass
                return True, None
            return False, None
    except ImportError:
        pass

    # requests.exceptions.ConnectionError / Timeout
    try:
        import requests

        if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
            return True, None
    except ImportError:
        pass

    # socket-level timeouts
    if isinstance(exc, socket.timeout):
        return True, None

    # urllib3 protocol errors (mid-connection drops)
    try:
        import urllib3.exceptions

        if isinstance(exc, urllib3.exceptions.ProtocolError):
            return True, None
    except ImportError:
        pass

    return False, None


def retry_transient_errors(fn: Callable) -> Callable:
    """Decorate a MigrationService method with transient-error retry.

    The decorator is stateless; policy comes from the instance:

    * ``self._max_retries``       — max retry attempts (default 3, max 10)
    * ``self._retry_backoff_cap`` — max sleep seconds (default 60.0)
    * ``self.audit``              — audit emission target (None disables)

    Backoff: ``min(_retry_backoff_cap, 1.0 * 2 ** attempt)`` with the
    Retry-After header value (if present) as a floor.

    Audit: on each retry attempt, emits ``migration.retry`` with
    ``details={attempt, max_retries, error_class, error_message,
    backoff_seconds, retry_after_header}``. Audit failures never
    block the retry path.
    """

    @wraps(fn)
    def wrapper(self: "MigrationService", *args: Any, **kwargs: Any) -> Any:
        max_retries: int = getattr(self, "_max_retries", 3)
        backoff_cap: float = getattr(self, "_retry_backoff_cap", 60.0)

        # Sanity-cap regardless of caller (defensive)
        if max_retries < 0:
            max_retries = 0
        if max_retries > 10:
            max_retries = 10

        last_exc: BaseException | None = None

        for attempt in range(max_retries + 1):  # pragma: no branch -- always exits via internal return/raise
            try:
                return fn(self, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                is_retryable, retry_after = _is_retryable(exc)
                if not is_retryable:
                    raise  # fail-fast for non-retryable errors

                last_exc = exc
                if attempt >= max_retries:
                    # Out of retries; re-raise to mark FAILED
                    raise

                # Compute backoff: exponential, capped, with Retry-After floor
                exponential = min(backoff_cap, 1.0 * (2 ** attempt))
                sleep_for = exponential
                if retry_after is not None and retry_after > sleep_for:
                    sleep_for = retry_after
                sleep_for = min(sleep_for, backoff_cap)

                # Emit audit event for this retry attempt (best-effort)
                audit = getattr(self, "audit", None)
                if audit is not None:
                    try:
                        audit.log(
                            actor="curator.migrate",
                            action="migration.retry",
                            details={
                                "attempt": attempt + 1,
                                "max_retries": max_retries,
                                "error_class": type(exc).__name__,
                                "error_message": str(exc)[:500],
                                "backoff_seconds": sleep_for,
                                "retry_after_header": retry_after,
                            },
                        )
                    except Exception:  # noqa: BLE001
                        pass  # never let audit failure block retry

                time.sleep(sleep_for)

        # The for-loop above always exits via `return` (success) or
        # `raise` (non-retryable error, OR retryable but attempt >=
        # max_retries). The previous "defensive end-of-loop" block
        # was provably unreachable and has been removed (v1.7.104).

    return wrapper
