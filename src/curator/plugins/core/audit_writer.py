"""Core audit-event writer plugin.

Implements the ``curator_audit_event`` hookspec that lets plugins
write structured audit entries via Curator's ``AuditRepository``.

Wiring two-step pattern (necessary because ``audit_repo`` doesn't
exist until ``build_runtime`` constructs it, but ``register_core_plugins``
must register this plugin BEFORE ``curator_plugin_init`` fires so other
plugins can call the hook from their init hookimpls if they want to):

1. ``register_core_plugins`` instantiates ``AuditWriterPlugin()`` with
   ``audit_repo=None`` (placeholder) and registers it on the pm.
2. ``build_runtime`` constructs ``audit_repo``, then calls
   ``audit_writer.set_audit_repo(audit_repo)`` to inject the repo.

Between step 1 and step 2 (i.e., during ``curator_plugin_init``
firing), any plugin that calls ``pm.hook.curator_audit_event(...)``
will hit the placeholder hookimpl. The placeholder logs the event at
debug level and drops it. This is consistent with DM-4 of
``docs/CURATOR_AUDIT_EVENT_HOOKSPEC_DESIGN.md`` v0.2 (best-effort
persistence) and with the design's recommendation that plugins NOT
fire audit events from init in the first place (init events are
typically attributable only to core, not to specific events).

After step 2, all subsequent ``curator_audit_event`` calls persist
normally to the audit log.

See ``docs/CURATOR_AUDIT_EVENT_HOOKSPEC_DESIGN.md`` v0.2 for the full
design that motivated this plugin.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from curator.models.audit import AuditEntry
from curator.plugins import hookimpl
from curator.storage.repositories.audit_repo import AuditRepository


class AuditWriterPlugin:
    """Persists plugin-emitted ``curator_audit_event`` events to repo.

    Constructor takes ``audit_repo=None`` so it can be registered before
    the runtime's ``AuditRepository`` is constructed; ``set_audit_repo``
    injects it later. See module docstring for the wiring pattern.
    """

    def __init__(self, audit_repo: AuditRepository | None = None) -> None:
        self.audit_repo = audit_repo

    def set_audit_repo(self, audit_repo: AuditRepository) -> None:
        """Inject the audit repo after construction.

        Called by ``build_runtime`` once the repo is available. After
        this point, ``curator_audit_event`` hookimpls persist to the
        repo; before this point, they log+drop.
        """
        self.audit_repo = audit_repo
        logger.debug(
            "AuditWriterPlugin: audit_repo injected; events now persist"
        )

    @hookimpl
    def curator_audit_event(
        self,
        actor: str,
        action: str,
        entity_type: str | None,
        entity_id: str | None,
        details: dict[str, Any],
    ) -> None:
        """Construct an :class:`AuditEntry` and insert via the repo.

        Best-effort per DM-4: failures are logged and swallowed. The
        originating plugin's hookimpl proceeds regardless.

        If ``self.audit_repo`` is None (called before
        ``set_audit_repo``), logs at debug level and returns. Plugins
        that fire audit events from their ``curator_plugin_init``
        hookimpl will hit this path; the design recommends against
        doing so.
        """
        if self.audit_repo is None:
            logger.debug(
                "AuditWriterPlugin: audit event dropped (no repo yet) "
                "actor={a}, action={ac}, entity_id={eid}",
                a=actor, ac=action, eid=entity_id,
            )
            return

        try:
            entry = AuditEntry(
                actor=actor,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                details=details,
            )
            self.audit_repo.insert(entry)
        except Exception as e:  # noqa: BLE001 -- best-effort write per DM-4
            logger.warning(
                "AuditWriterPlugin: failed to persist audit event "
                "actor={a}, action={ac}: {err}",
                a=actor, ac=action, err=e,
            )
