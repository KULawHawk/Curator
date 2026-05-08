"""Pluggy hookspecs — the contract that plugins implement.

DESIGN.md §5.1.

This module defines the entire surface area of Curator's plugin system.
Plugins implement these hooks via ``@hookimpl``; hosting code calls them
via ``plugin_manager.hook.<hook_name>(...)``.

Conventions:
    * Hooks return ``None`` to mean "I have no opinion / this isn't me".
    * "Compute" hooks (classify, validate, lineage) collect non-None results
      from all matching plugins.
    * "Source" hooks check ``source_id`` and return ``None`` if not theirs;
      only ONE plugin should match any given ``source_id``.
    * "Veto" hooks (``pre_trash``, ``pre_restore``) block the operation if
      ANY plugin returns ``ConfirmationResult(allow=False)``.

Type imports are guarded with TYPE_CHECKING to avoid circular imports
at runtime (these are entity classes that ultimately import from us).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterator

import pluggy

# These markers are what plugin authors use:
#   from curator.plugins import hookimpl
#   class MyPlugin:
#       @hookimpl
#       def curator_classify_file(self, file): ...
hookspec = pluggy.HookspecMarker("curator")
hookimpl = pluggy.HookimplMarker("curator")


if TYPE_CHECKING:  # pragma: no cover
    # Forward references only — never imported at runtime.
    from curator.models import (
        BundleProposal,
        ConfirmationResult,
        FileClassification,
        FileEntity,
        FileInfo,
        FileStat,
        LineageEdge,
        SourcePluginInfo,
        TrashRecord,
        ValidationResult,
    )


# ============================================================================
# File classification
# ============================================================================

@hookspec
def curator_classify_file(file: "FileEntity") -> "FileClassification | None":
    """Classify a file's type.

    Plugins return :class:`FileClassification` when they have an opinion,
    or ``None`` to abstain. The classification service collects all
    non-None results and selects by confidence.
    """


# ============================================================================
# File validation / detection
# ============================================================================

@hookspec
def curator_validate_file(file: "FileEntity") -> "ValidationResult | None":
    """Validate a file's integrity.

    Plugins inspect the file (e.g. open it, parse headers) and return a
    :class:`ValidationResult`. Used for broken-file detection, signature
    matching, etc. Plugins that don't apply to this file return ``None``.
    """


# ============================================================================
# Lineage detection
# ============================================================================

@hookspec
def curator_compute_lineage(
    file_a: "FileEntity",
    file_b: "FileEntity",
) -> "LineageEdge | None":
    """Detect a relationship between two files.

    Each plugin gets every candidate pair and decides whether to emit an
    edge. The lineage service filters by confidence threshold per edge
    kind before persisting.

    The pair is unordered from a semantics perspective but ordered as
    passed: ``file_a`` is "this file", ``file_b`` is "the other". Plugins
    decide directionality of the returned edge.
    """


# ============================================================================
# Bundle proposal
# ============================================================================

@hookspec
def curator_propose_bundle(
    files: list["FileEntity"],
) -> "BundleProposal | None":
    """Propose a bundle from a set of files.

    Run on a candidate set (e.g. all files in a folder, or all assessment
    files for a client). Plugins that recognize a pattern return a
    :class:`BundleProposal`. The user (or auto-confirmation) decides
    whether to materialize.
    """


# ============================================================================
# Source plugin contract
# ============================================================================

@hookspec
def curator_source_register() -> "SourcePluginInfo":
    """Register a source plugin.

    Called once at plugin-manager init time. Returns metadata that
    Curator uses to validate source configs and to know whether the
    source supports watching, requires auth, etc.
    """


@hookspec
def curator_source_enumerate(
    source_id: str,
    root: str,
    options: dict[str, Any],
) -> Iterator["FileInfo"]:
    """Enumerate files under a root in a source.

    Plugins check whether ``source_id`` matches their source_type and
    return ``None`` (or yield nothing) if not. Yields :class:`FileInfo`
    for every file (not directory) reachable from ``root`` per the
    plugin's filtering rules.
    """


@hookspec
def curator_source_read_bytes(
    source_id: str,
    file_id: str,
    offset: int,
    length: int,
) -> bytes | None:
    """Read a byte range from a file in a source.

    The hash pipeline calls this in 64KB chunks (typically) to compute
    fingerprints without buffering whole files. Cloud sources should
    use range requests where the API supports them.

    Plugins that don't own this ``source_id`` return ``None``.
    """


@hookspec
def curator_source_stat(
    source_id: str,
    file_id: str,
) -> "FileStat | None":
    """Return current metadata for a file.

    Used to detect whether a file's mtime/size has changed since the
    last scan, to decide whether the hash cache entry is still valid,
    and to verify presence before destructive operations.
    """


@hookspec
def curator_source_move(
    source_id: str,
    file_id: str,
    new_path: str,
) -> "FileInfo | None":
    """Move a file within a source.

    Used by the rules engine and manual reorganization. Plugins ensure
    the parent directory exists, perform the move atomically where
    possible, and return updated :class:`FileInfo` for the new location.
    """


@hookspec
def curator_source_delete(
    source_id: str,
    file_id: str,
    to_trash: bool,
) -> bool | None:
    """Delete a file (or send it to the OS trash).

    Phase Alpha: only ``to_trash=True`` is used (via TrashService). Hard
    delete (``to_trash=False``) is reserved for Phase Beta+ purges.
    """


@hookspec
def curator_source_write(
    source_id: str,
    parent_id: str,
    name: str,
    data: bytes,
    *,
    mtime: "datetime | None" = None,
    overwrite: bool = False,
) -> "FileInfo | None":
    """Create a new file in a source.

    Phase Beta gate 5 (v0.40). Required for cross-source migration
    (DESIGN_PHASE_DELTA.md §2) and cloud sync (§3). Plugins that don't
    own this ``source_id`` return ``None``.

    Args:
        source_id: which source to write to.
        parent_id: destination folder identifier. For local: an absolute
            directory path. For gdrive: a Drive folder ID (or ``"root"``
            for My Drive root).
        name: new file's display name (basename for local; title for
            gdrive).
        data: bytes to write. Whole-file in-memory; large files
            (>500 MB) should be staged via temp paths once a streaming
            variant ships in Phase γ.
        mtime: optional mtime to set on the new file. Sources that don't
            support setting mtime ignore this.
        overwrite: if False (default), raise/return None when a file with
            the same ``name`` already exists in ``parent_id``. If True,
            atomically replace.

    Returns:
        :class:`FileInfo` for the newly-created file, or ``None`` if
        this plugin doesn't own the source.

    Raises:
        FileExistsError: if ``overwrite=False`` and a file with this
            name already exists at the destination.
        OSError, RuntimeError: source-specific failures.
    """


@hookspec
def curator_source_write_post(
    source_id: str,
    file_id: str,
    src_xxhash: str | None,
    written_bytes_len: int,
) -> None:
    """Post-write notification hook (v1.1.1+).

    Fired AFTER a successful ``curator_source_write`` call (and after
    the caller's own verify step, if any). Plugins can use this to:

    * Perform an independent post-write verification (the
      ``curatorplug-atrium-safety`` plugin re-reads the destination
      via ``curator_source_read_bytes`` and verifies the hash against
      ``src_xxhash``).
    * Record the successful write in an out-of-band ledger or audit
      channel.
    * Cross-check the written bytes against an external policy.

    Hook semantics:

    * **Multi-plugin:** all plugins implementing this hook are invoked.
      Pluggy's default ``firstresult=False`` applies; results (typically
      ``None``) are collected but not consumed.
    * **Exception propagation:** a plugin raising from this hook
      propagates the exception to the caller of
      ``curator_source_write``. Safety / compliance plugins use this to
      *refuse* a write that violates policy (e.g., raise
      ``ComplianceError``). The originating caller's outer
      exception-boundary turns this into the appropriate failure outcome
      (``MigrationOutcome.FAILED`` for migrations).
    * **No-op for plugins that don't care:** plugins that don't
      implement this hook are simply not invoked; this hook is
      *strictly additive* and existing source plugins do not need to
      be modified.

    Args:
        source_id: the source the write went to (passes the same value
            ``curator_source_write`` was called with).
        file_id: the destination's identifier as returned by the write
            hook (e.g. for local: an absolute path; for gdrive: a Drive
            file ID).
        src_xxhash: the source's xxhash3_128 hex digest, IF the caller
            performed verify and has a value to share. ``None`` if
            verification was skipped (e.g., ``verify_hash=False`` was
            passed to the migration). Plugins must handle the ``None``
            case gracefully.
        written_bytes_len: the length in bytes of the data that was
            written, as observed by the caller. Useful as a sanity
            check against the post-write file size a plugin might read
            back.

    See ``curatorplug-atrium-safety/DESIGN.md`` §5 for the design that
    motivated this hookspec.
    """


@hookspec
async def curator_source_watch(
    source_id: str,
    root: str,
):  # pragma: no cover — Phase Beta
    """Async-iterate change events for a source root.

    Phase Beta. Returns an async iterator of :class:`ChangeEvent`
    objects. Plugins that don't implement watching return ``None``.
    """


# ============================================================================
# Computed attributes (the third tier of CuratorEntity attrs)
# ============================================================================

@hookspec
def curator_compute_attr(entity: Any, key: str) -> Any:
    """Compute a derived attribute on an entity.

    The first plugin returning a non-None value wins; entity calls this
    via ``entity.get_computed(key)``. Plugins should be quick — these
    are expected to be cheap.
    """


# ============================================================================
# Trash / restore lifecycle
# ============================================================================

@hookspec
def curator_pre_trash(
    file: "FileEntity",
    reason: str,
) -> "ConfirmationResult | None":
    """Veto-style hook called before a file is trashed.

    Plugins can prevent the trash by returning
    ``ConfirmationResult(allow=False, reason=...)``. Used to protect
    files that match certain rules (e.g. an APEX clinical file that's
    part of an active assessment).
    """


@hookspec
def curator_post_trash(trash_record: "TrashRecord") -> None:
    """Notification hook after a file has been trashed.

    Plugins use this for cleanup, notification, derived-state updates.
    Return value is ignored.
    """


@hookspec
def curator_pre_restore(
    trash_record: "TrashRecord",
    target_path: str,
) -> "ConfirmationResult | None":
    """Veto-style hook called before a file is restored from trash."""


@hookspec
def curator_post_restore(file: "FileEntity") -> None:
    """Notification hook after a file has been restored."""


# ============================================================================
# Rules-engine extensions
# ============================================================================

@hookspec
def curator_rule_types() -> dict[str, type]:
    """Register custom rule types for the rules engine.

    Returns a dict mapping rule_type identifiers to RuleBase subclasses.
    Phase Beta+. See DESIGN.md §13.3.
    """


# ============================================================================
# CLI / API extensions
# ============================================================================

@hookspec
def curator_cli_commands() -> list[Any]:
    """Register additional CLI subcommands.

    Returns a list of ``typer.Typer`` instances to add to the main app.
    """


@hookspec
def curator_api_routers() -> list[Any]:
    """Register additional REST API routers.

    Returns a list of ``fastapi.APIRouter`` instances. Phase Gamma.
    """


# ============================================================================
# Plugin lifecycle (v1.1.2+)
# ============================================================================

@hookspec
def curator_plugin_init(pm: "pluggy.PluginManager") -> None:
    """One-time initialization notification for plugins.

    Fired exactly once per ``pm``, after ALL plugins (core +
    entry-point-discovered) have been registered. Plugins that need to
    call OTHER plugins' hooks from inside their own hookimpls implement
    this hookspec to receive a reference to the plugin manager and save
    it for later use.

    Hook semantics:

    * **One-shot:** fired once per pm at the end of
      ``_create_plugin_manager``. Plugins registered dynamically after
      startup do NOT receive this hook (per DM-4 of
      ``docs/PLUGIN_INIT_HOOKSPEC_DESIGN.md`` v0.2).
    * **Multi-plugin:** all plugins implementing this hookspec are
      invoked. Pluggy's default ``firstresult=False`` applies; results
      (typically ``None``) are not consumed.
    * **Failure isolation:** a plugin's init hookimpl raising an
      exception is caught and logged but does NOT abort startup or
      de-register the plugin (per DM-3). Subsequent hookimpls of the
      misbehaving plugin may behave oddly; that's the plugin author's
      problem to surface.
    * **Strictly additive:** plugins that don't implement this hookspec
      are unaffected.

    Args:
        pm: the plugin manager that holds this plugin and all its
            siblings. Plugins typically save it as ``self.pm`` and
            use ``self.pm.hook.<other_hook>(...)`` from inside other
            hookimpls.

    See ``docs/PLUGIN_INIT_HOOKSPEC_DESIGN.md`` v0.2 for the design
    that motivated this hookspec, and ``curatorplug-atrium-safety``
    v0.2.0+ for the canonical consumer (independent re-read
    verification of cross-source migration writes).
    """
