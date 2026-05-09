"""Starlette middleware for MCP HTTP Bearer-token authentication (v1.5.0).

Per ``docs/CURATOR_MCP_HTTP_AUTH_DESIGN.md`` v0.2 RATIFIED \u00a74.3 / \u00a74.5,
this middleware extracts the ``Authorization`` header on every HTTP
request to the FastMCP ``streamable_http_app``, validates the bearer
token against ``~/.curator/mcp/api-keys.json``, emits audit events for
both successful and failed attempts, and either:

* Forwards the request to the FastMCP ASGI app (on success); OR
* Returns a 401 Unauthorized response with ``WWW-Authenticate: Bearer``
  (on failure).

Why custom middleware instead of FastMCP's ``token_verifier``?
FastMCP's built-in auth (the ``auth_server_provider`` / ``auth=`` /
``token_verifier=`` constructor params) is wired for OAuth 2.0 resource
server flows -- it expects scoped tokens issued by an external auth
server, RFC 9068-style. Our use case is single-user, single-trust-
domain bearer tokens managed locally. Wrapping the Starlette app with
plain middleware is much smaller in scope, easier to test, and
doesn't pull in OAuth ceremony we don't need.

Constitutional alignment:

* **Article II Principle 4 (No Silent Failures):** Every auth failure
  produces both a 401 response (visible to the caller) AND an audit
  log entry (visible to the user / future investigation). Audit
  emission failures are logged but never block the auth response --
  the auth outcome is the user-facing contract; audit is supplementary.
* **Aim 8 (Auditability):** Successful auth is also audited (throttled
  to 1/key/minute) so the ``who used my Curator and when`` introspection
  question has an answer.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from curator.mcp.auth import update_last_used, validate_key


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_BEARER_PREFIX = "Bearer "
"""HTTP Authorization scheme prefix per RFC 6750. Note trailing space."""

_KEY_PREFIX_AUDIT_LEN = 10
"""How many characters of the failed key to record in audit log details.
Long enough to be useful for forensics; short enough to never leak
recoverable key material (key total length is 44+ chars, so the first
10 don't reveal the rest)."""

_DEFAULT_THROTTLE_SECONDS = 60.0
"""Successful auth events are emitted at most once per key per N
seconds. Failed auth events are NEVER throttled."""


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Validate Bearer tokens against the Curator MCP keys file.

    Wrap the ASGI app returned by ``FastMCP.streamable_http_app()``
    with this middleware to enforce authentication on every request.

    Args:
        app: The downstream ASGI application (typically the Starlette
            app from FastMCP). Required by ``BaseHTTPMiddleware``.
        keys_file: Override the path to ``api-keys.json``. Defaults to
            ``~/.curator/mcp/api-keys.json`` via :func:`validate_key`.
        audit_emitter: Optional callable with signature
            ``(action: str, **details) -> None`` invoked on every auth
            attempt. The ``action`` is one of ``mcp.auth_success`` or
            ``mcp.auth_failure``. Details vary by event type
            (see :meth:`_emit_failure` / :meth:`_emit_success`).
            ``None`` disables audit emission entirely (the middleware
            still enforces auth, just doesn't write events).
        throttle_seconds: Successful-auth audit emission throttle.
            Default 60 seconds per key. Failed-auth emission is never
            throttled.
    """

    def __init__(
        self,
        app: Any,
        *,
        keys_file: Any = None,
        audit_emitter: Callable[..., None] | None = None,
        throttle_seconds: float = _DEFAULT_THROTTLE_SECONDS,
    ) -> None:
        super().__init__(app)
        self.keys_file = keys_file
        self.audit_emitter = audit_emitter
        self.throttle_seconds = throttle_seconds
        # In-memory throttling state. Resets on server restart, which
        # is acceptable -- restart is a natural audit boundary anyway.
        self._last_audit_at: dict[str, float] = {}

    async def dispatch(self, request: Request, call_next):
        auth_header = request.headers.get("Authorization", "")

        # Step 1: header presence + scheme check
        if not auth_header.startswith(_BEARER_PREFIX):
            self._emit_failure(
                request,
                reason=("missing_header" if not auth_header
                        else "malformed_header"),
                key_prefix=None,
            )
            return self._unauthorized(
                "Missing or malformed Authorization header. "
                "Expected 'Bearer <key>'."
            )

        token = auth_header[len(_BEARER_PREFIX):].strip()
        if not token:
            self._emit_failure(
                request, reason="empty_token", key_prefix=None,
            )
            return self._unauthorized(
                "Empty bearer token in Authorization header."
            )

        # Step 2: validate against the keys file
        stored = validate_key(token, path=self.keys_file)
        if stored is None:
            self._emit_failure(
                request,
                reason="invalid_key",
                key_prefix=token[:_KEY_PREFIX_AUDIT_LEN],
            )
            return self._unauthorized("Invalid API key.")

        # Step 3: success path -- update last_used (best-effort) +
        # emit (throttled), then forward.
        try:
            update_last_used(stored.name, path=self.keys_file)
        except Exception as e:  # noqa: BLE001 -- best-effort
            logger.warning(
                "BearerAuthMiddleware: update_last_used failed for {n}: {e}",
                n=stored.name, e=e,
            )
        self._emit_success(request, stored.name)

        return await call_next(request)

    # ------------------------------------------------------------------
    # Response builder
    # ------------------------------------------------------------------

    @staticmethod
    def _unauthorized(message: str) -> JSONResponse:
        """Build a 401 response with proper WWW-Authenticate header."""
        return JSONResponse(
            content={"error": "unauthorized", "message": message},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer realm="curator-mcp"'},
        )

    # ------------------------------------------------------------------
    # Audit emission (DM-5 RATIFIED: success + failure, success
    # throttled to 1/key/minute)
    # ------------------------------------------------------------------

    def _emit_failure(
        self,
        request: Request,
        *,
        reason: str,
        key_prefix: str | None,
    ) -> None:
        """Emit a ``mcp.auth_failure`` audit event. Never throttled.
        Best-effort: emission failures are logged but never raised."""
        if self.audit_emitter is None:
            return
        try:
            self.audit_emitter(
                "mcp.auth_failure",
                reason=reason,
                key_prefix=key_prefix,
                remote_addr=self._remote_addr(request),
                request_method=request.method,
                request_path=str(request.url.path),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "BearerAuthMiddleware: audit emission failed for failure: {e}",
                e=e,
            )

    def _emit_success(self, request: Request, key_name: str) -> None:
        """Emit a ``mcp.auth_success`` audit event. Throttled per key.
        Best-effort: emission failures are logged but never raised."""
        if self.audit_emitter is None:
            return

        # Throttle: emit at most once per key per throttle_seconds
        now = time.monotonic()
        last = self._last_audit_at.get(key_name, 0.0)
        if now - last < self.throttle_seconds:
            return
        self._last_audit_at[key_name] = now

        try:
            self.audit_emitter(
                "mcp.auth_success",
                key_name=key_name,
                remote_addr=self._remote_addr(request),
                request_method=request.method,
                request_path=str(request.url.path),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "BearerAuthMiddleware: audit emission failed for success: {e}",
                e=e,
            )

    @staticmethod
    def _remote_addr(request: Request) -> str | None:
        """Return the request's remote address, or ``None`` if unavailable
        (e.g., loopback connections may not have a meaningful client tuple)."""
        client = request.client
        if client is None:
            return None
        return client.host


# ---------------------------------------------------------------------------
# Audit-emitter factory
# ---------------------------------------------------------------------------


def make_audit_emitter(audit_repo: Any) -> Callable[..., None]:
    """Build a callable suitable for ``BearerAuthMiddleware(audit_emitter=...)``.

    Curator's :class:`AuditRepository` exposes ``.log(actor, action,
    entity_type, entity_id, details)``. The middleware doesn't know
    about Curator's audit schema; this factory bridges the two.

    Args:
        audit_repo: A Curator :class:`AuditRepository` instance, or any
            object exposing a compatible ``.log(actor, action,
            entity_type, entity_id, details)`` method.

    Returns:
        A function ``(action, **details) -> None`` that emits the event
        with ``actor='curator-mcp'`` and ``entity_type='mcp_auth'``.
    """
    def emit(action: str, **details: Any) -> None:
        # Strip None values from details so the audit log JSON stays clean
        clean_details = {k: v for k, v in details.items() if v is not None}
        try:
            audit_repo.log(
                actor="curator-mcp",
                action=action,
                entity_type="mcp_auth",
                entity_id=None,  # auth events are not about a specific entity
                details=clean_details,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "make_audit_emitter: audit_repo.log failed: {e}", e=e,
            )

    return emit
