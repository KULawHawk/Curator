# Changelog

All notable changes to Curator are documented here. Format inspired by
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) with semver
versioning where reasonable.

## [1.1.2] — 2026-05-08 — `curator_plugin_init` hookspec (PLUGIN_INIT P1)

**Headline:** Patch release adding the `curator_plugin_init(pm)`
plugin lifecycle hookspec. Lets plugins receive a reference to the
plugin manager once at startup, so they can call OTHER plugins' hooks
from inside their own hookimpls. Strictly additive; existing plugins
work unchanged.

### Added

- **New hookspec** `curator_plugin_init(pm: pluggy.PluginManager) -> None`
  in `src/curator/plugins/hookspecs.py`. Fired exactly once per pm at
  the end of `_create_plugin_manager`, after all plugins (core +
  entry-point-discovered) are registered. Plugins typically save the
  pm reference as `self.pm` and use `self.pm.hook.<other>(...)` from
  inside subsequent hookimpls. See
  `docs/PLUGIN_INIT_HOOKSPEC_DESIGN.md` v0.2 for the full design and
  the four ratified DMs. The motivating consumer is
  `curatorplug-atrium-safety` v0.2.0+ which uses the pm to perform
  independent re-read verification of cross-source migration writes
  via `curator_source_read_bytes`; future plugins (`curatorplug-
  atrium-reversibility`, audit-aggregator, etc.) consume the same
  primitive.
- **Manager wiring** in `src/curator/plugins/manager.py`. The init
  hook fires as the LAST step of `_create_plugin_manager` (per DM-2
  so init hookimpls can see all sibling plugins). Wrapped in a
  defensive try/except so a plugin's init raising is logged at warn
  level but does NOT abort startup or de-register the misbehaving
  plugin (per DM-3, consistent with the existing
  `load_setuptools_entrypoints` failure handling and Atrium Principle
  1 Reversibility at the operational level).

### Tests (+6 new — 342 → 348 in the migration + GUI + plugin-manager slice)

- `tests/unit/test_plugin_manager.py` (NEW, 6 tests):
  - `TestPluginInitFiresOnce` (2): hook fires exactly once via
    `_create_plugin_manager` for entry-point-discovered plugins;
    plugins registered dynamically AFTER startup do NOT receive the
    hook (regression-guards DM-4).
  - `TestPluginInitTiming` (1): when the hook fires, the pm already
    has all core plugins registered — init hookimpls can list
    siblings and do setup work that depends on them (regression-
    guards DM-2).
  - `TestPluginInitFailureIsolation` (2): a plugin's init raising
    does NOT crash `_create_plugin_manager`; the misbehaving plugin
    remains registered AND other plugins' init hookimpls still fire
    (regression-guards DM-3).
  - `TestPluginInitNoOpForSilentPlugins` (1): existing core plugins
    that don't implement the new hookspec are completely unaffected
    (regression-guards the strictly-additive invariant from §2).

### Backward compatibility

- **Strictly additive.** Plugins that don't implement the new
  hookspec are not invoked. Existing source plugins (`local`,
  `gdrive`) and `curatorplug-atrium-safety` v0.1.0 work unchanged
  (verified: 348/348 in the migration + GUI slice; 53/53 in the
  atrium-safety plugin's full suite with auto-discovered registration
  firing the new hook on each Curator startup).
- **No new dependencies.** Uses pluggy's existing hook mechanism.
- **Schema unchanged.** No new tables, no new columns.

### Why a patch (1.1.1 → 1.1.2) and not a minor (1.1.1 → 1.2.0)

User-facing functionality didn't change. The new hookspec is
preparatory infrastructure for `curatorplug-atrium-safety` v0.2.0
(which doesn't ship in this release). Plugin authors building against
Curator can pin `>= 1.1.2` to require the hookspec; users who don't
install such plugins see no difference.

### Cross-references

- `docs/PLUGIN_INIT_HOOKSPEC_DESIGN.md` v0.2 (RATIFIED 2026-05-08) —
  the design doc this commit implements P1 of.
- `curatorplug-atrium-safety/DESIGN.md` v0.2 §5 — the deferred
  re-read verification capability whose plumbing this commit
  unblocks. P2 (plugin v0.2.0) and P3 (regression sweep + docs) of
  PLUGIN_INIT_HOOKSPEC_DESIGN's plan land separately.
- `Atrium\CONSTITUTION.md` Principle 2 — the invariant whose
  third-party-plugin enforcement gains a defense-in-depth layer once
  P2 ships in plugin v0.2.0.

## [1.1.1] — 2026-05-08 — `curator_source_write_post` hookspec (Tracer P1)

**Headline:** Patch release adding the `curator_source_write_post`
plugin hookspec as a prerequisite for the
[`curatorplug-atrium-safety`](https://github.com/KULawHawk/curatorplug-atrium-safety)
plugin (P1 of its 3-session implementation plan; design ratified
2026-05-08 in that package's `DESIGN.md` v0.2). User-visible behavior
is unchanged for users who don't install third-party plugins consuming
the new hook.

### Added

- **New hookspec** `curator_source_write_post(source_id, file_id,
  src_xxhash, written_bytes_len)` in `src/curator/plugins/hookspecs.py`.
  Fired AFTER a successful `curator_source_write` (and after
  Curator's own verify step, if any). Plugins use this for independent
  post-write verification, out-of-band ledger writes, or to *refuse*
  a write by raising (which propagates through the caller's
  exception-boundary and turns the operation into the appropriate
  failure outcome). Multi-plugin: all registered hookimpls fire;
  exception propagation is intentional. `src_xxhash` is `None` when
  the caller skipped its own verify (e.g., `--no-verify-hash`); plugins
  must handle that case gracefully. Strictly additive — existing
  source plugins do not need to be modified.
- **MigrationService wiring** (`_invoke_post_write_hook` helper in
  `src/curator/services/migration.py`). Called from the cross-source
  path (`_cross_source_transfer`) after the bytes are written and
  hash-verified, just before the success return. Same-source
  (`shutil.copy2`) path does not fire the hook — it never goes through
  `curator_source_write` so the hookspec does not apply there.
  Runtime-wise: if `MigrationService` is constructed with `pm=None`
  (as in many test fixtures), the helper is a silent no-op, preserving
  backward compatibility.

### Tests (+5 new — 1150 → 1155 in the migration + GUI slice)

- `tests/unit/test_migration_cross_source.py::TestCuratorSourceWritePostHook`
  (5 tests): hook fires once per successful cross-source migration
  with the expected arguments populated; hook does NOT fire when
  `curator_source_write` raises `FileExistsError` (collision); hook
  does NOT fire when verify reads back mismatched bytes (HASH_MISMATCH
  — dst is deleted, write didn't survive); hook receives
  `src_xxhash=None` when `verify_hash=False`; a plugin raising from
  the hook turns the move into `MigrationOutcome.FAILED` with the
  exception's message in `MigrationMove.error` (the soft-enforcement
  UX that DM-1 of `curatorplug-atrium-safety` ratified).

### Backward compatibility

- **Strictly additive.** Plugins that don't implement the new hookspec
  are not invoked. Existing source plugins (`local`, `gdrive`) need no
  changes. Existing CLI invocations behave identically. Existing test
  suites pass without modification (verified: 342/342 in the migration
  + GUI slice, 0 failures).
- **No new dependencies.** Uses pluggy's existing hook mechanism.
- **Schema unchanged.** No new tables, no new columns.

### Why a patch (1.1.0 → 1.1.1) and not a minor (1.1.0 → 1.2.0)

User-facing functionality didn't change. The new hook is preparatory
infrastructure for an *external* plugin (`curatorplug-atrium-safety`)
that doesn't ship in this release. Plugin authors building against
Curator can pin `>= 1.1.1` to require the hook; users who don't
install such plugins see no difference. Patch bump is honest;
`v1.2.0` is reserved for a more substantial feature release later.

### Cross-references

- `docs/TRACER_PHASE_2_DESIGN.md` v0.3 — the v1.1.0 release whose
  `_cross_source_transfer` is the call site for the new hook.
- `Atrium\CONSTITUTION.md` Principle 2 (Hash-Verify-Before-Move) —
  the invariant the future safety plugin will defend across
  third-party source plugins.
- `curatorplug-atrium-safety/DESIGN.md` v0.2 (separate repo, not yet
  pushed) — Session P1 (this release) closes the prerequisite; P2
  (plugin scaffolding + verifier + enforcer) and P3 (integration
  tests + v0.1.0 release) land in that package.

## [1.1.0] — 2026-05-08 — Migration tool Phase 2 (stable)

**Headline:** Tracer (the Curator brand for migration capabilities)
Phase 2 ships stable. Every item the v1.1.0a1 entry listed under "Phase 2
deferred" is now done: persistent + resumable jobs, worker-pool concurrency,
cross-source migration via the ``curator_source_write`` plugin hook,
full CLI flag surface (``--list``, ``--status``, ``--abort``, ``--resume``,
``--workers``, ``--include``, ``--exclude``, ``--path-prefix``,
``--dst-source-id``, ``--keep-source``, ``--include-caution``), and a
PySide6 "Migrate" tab with read-only job/progress views, right-click
Abort/Resume mutations, and live cross-thread progress signals from the
worker pool to the GUI thread. Seven implementation sessions
(A1+A2+A3+B+C1+C2+C2b) shipped over a single day's work, plus
~130 net new tests on top of the v1.1.0a1 baseline.

### Added — storage + models (Session A1)

- **Schema migration_002** (``src/curator/storage/migrations.py``):
  introduces ``migration_jobs`` (one row per CLI invocation; tracks
  src/dst routing + rollup counters + status + options blob) and
  ``migration_progress`` (one row per planned file move; tracks
  per-file outcome + verified hash + size + safety_level). Both keyed
  by ``job_id`` UUID. Foreign key from progress -> jobs with cascade
  delete; indexed on ``(job_id, status)`` for the worker claim path.
- **Domain models** (``src/curator/models/migration.py``):
  :class:`MigrationJob` (Pydantic) with ``is_terminal`` /
  ``duration_seconds`` properties; :class:`MigrationProgress` mirroring
  the per-file row; status literal types pinned
  (``Literal['queued', 'running', 'completed', 'partial', 'failed', 'cancelled']``
  for jobs, ``Literal['pending', 'in_progress', 'completed', 'failed', 'skipped']``
  for progress).
- **Repository** (``src/curator/storage/repositories/migration_job_repo.py``
  ~360 LOC): :class:`MigrationJobRepository` with the full job + progress
  lifecycle: ``insert_job``, ``update_job_status``,
  ``increment_job_counts``, ``set_files_total``, ``delete_job``,
  ``get_job``, ``list_jobs(status=None, limit=50)``,
  ``seed_progress_rows`` (bulk insert for plan-time fan-out),
  **``next_pending_progress`` (atomic claim via SQLite
  ``BEGIN IMMEDIATE`` + ``UPDATE … RETURNING``)** — the worker pool's
  central ordering primitive, ``update_progress``,
  ``reset_in_progress_to_pending`` (resume safety net),
  ``get_progress``, ``query_progress``, ``count_progress_by_status``.

### Added — service layer (Session A2)

- **MigrationService Phase 2 API** (``src/curator/services/migration.py``):
  - ``create_job(plan, *, options=None, db_path_guard=None,
    include_caution=False)`` — persists a Phase-1 plan as a
    ``migration_jobs`` row + N ``migration_progress`` rows. Pre-skips
    REFUSE / DB-guarded files at seed time; CAUTION rows are pre-skipped
    UNLESS ``include_caution=True``.
  - ``run_job(job_id, *, workers=4, verify_hash=True, keep_source=False,
    on_progress=None)`` — ThreadPoolExecutor with N workers (clamped to
    >=1). Workers loop on ``next_pending_progress`` until empty or
    ``abort_event.is_set()``. Final job status determined from terminal
    histogram: ``cancelled`` if aborted, ``partial`` if any failed,
    ``completed`` otherwise.
  - ``abort_job(job_id)`` — sets a per-job ``threading.Event`` (instant;
    no I/O). Workers finish the current file (per-file atomicity is
    preserved — no mid-file abort) and exit on the next loop iteration.
  - ``list_jobs(*, status=None, limit=50)`` and
    ``get_job_status(job_id)`` — read-only enumeration / detail.
- **Resume semantics:** rows left as ``status='in_progress'`` from a
  previous interrupted run are reset to ``'pending'`` before workers
  start. Safe per design — progress rows transition to ``'completed'``
  AFTER the FileEntity index update but BEFORE the trash step, so an
  ``in_progress`` row never has the index-update side effect.
- **Worker discipline:** every per-file move still follows the Atrium
  Constitution Principle 2 (Hash-Verify-Before-Move) protocol.
  Persistent path additionally records each per-file outcome to
  ``migration_progress`` and bumps the job-level rollup counters
  atomically.
- **Runtime wiring:** ``CuratorRuntime`` constructs ``MigrationService``
  with ``migration_jobs=migration_job_repo`` and ``pm=pm`` (the latter
  is used by Session B's cross-source dispatch).

### Added — CLI extensions (Session A3)

- **Filter flags:** ``--include <glob>`` / ``--exclude <glob>``
  (repeatable; matched against path-relative-to-src_root with
  forward-slash normalization for cross-platform glob portability),
  ``--path-prefix <subpath>`` (narrows selection without changing
  src_root semantics).
- **Routing flags:** ``--dst-source-id <id>`` (required for Session B's
  cross-source case; defaults to ``src_source_id`` for same-source).
- **Worker / parallelism flags:** ``--workers N`` / ``-w N``
  (default 1 for backwards compat; ``> 1`` automatically routes through
  the Phase 2 persistent path).
- **Source-action flags:** ``--keep-source`` (creates dst, leaves src,
  index NOT updated; outcome is :class:`MigrationOutcome.COPIED`,
  audit action ``migration.copy`` distinct from ``migration.move``)
  vs ``--trash-source`` (Phase 1 default semantics).
- **Safety opt-in:** ``--include-caution`` (eligible CAUTION-level
  files migrate alongside SAFE; REFUSE is always skipped regardless).
- **Job lifecycle flags:** ``--list`` (recent jobs, optional ``--status``
  filter), ``--status <job_id>`` (rich detail for one job),
  ``--abort <job_id>`` (signals the running pool to stop),
  ``--resume <job_id>`` (re-runs ``run_job`` on an interrupted
  cancelled/partial/failed job).
- **Auto-routing:** the CLI inspects ``--workers`` and routes to
  Phase 2 (persistent + parallel) when ``> 1``, Phase 1 (in-memory +
  serial) otherwise. Single transparent surface.
- **New outcome:** :class:`MigrationOutcome.COPIED` for keep-source
  semantics. ``apply()`` and ``run_job()`` both honor it.
- **``_audit_copy`` helper:** distinct audit action ``migration.copy``
  for keep-source moves so audit log queries can differentiate from
  ``migration.move``.

### Added — cross-source migration (Session B)

- **Cross-source dispatcher** in ``MigrationService._execute_one``:
  routes to ``_execute_one_same_source`` (the Phase 1 ``shutil.copy2``
  fast path) or ``_execute_one_cross_source`` (hook-mediated bytes
  transfer) based on ``src_source_id != dst_source_id``. Same
  dispatcher applied to both in-memory ``apply()`` and persistent
  ``run_job()`` paths.
- **5 cross-source helpers:** ``_is_cross_source``, ``_can_write_to_source``
  (reads ``SourcePluginInfo.supports_write`` from
  ``curator_source_register``), ``_hook_first_result`` (collapses
  pluggy result lists; preserves ``FileExistsError`` for collision
  signaling), ``_read_bytes_via_hook`` (chunks 64KB through
  ``curator_source_read_bytes``), ``_cross_source_transfer``
  (read src → write dst via hook → re-read dst via hook → verify hash
  → return outcome+verified_hash).
- **Pre-existing plugin hook leveraged:** ``curator_source_write``
  (whole-file in-memory bytes API:
  ``(source_id, parent_id, name, data, *, mtime=None, overwrite=False) → FileInfo | None``)
  was already specced in ``hookspecs.py`` and implemented production-grade
  by both source plugins — ``LocalSourcePlugin`` (atomic via
  ``tempfile`` + ``os.replace``) and ``GoogleDriveSourcePlugin`` (PyDrive2
  ``CreateFile`` + ``BytesIO`` + ``Upload``). No new plugin work was
  required to enable cross-source migration; the service layer just
  wired itself to the hook surface that already existed.
- **Cross-source per-file discipline:** identical Hash-Verify-Before-Move
  protocol — read src bytes, optionally compute src hash, write dst
  via hook, re-read dst via hook, recompute and verify hash. On mismatch:
  delete dst via ``curator_source_delete(to_trash=False)``, mark
  HASH_MISMATCH, src untouched. On success: update FileEntity's
  **both** ``source_id`` AND ``source_path`` (cross-source moves
  legitimately transit a source-id boundary), then trash src via
  ``curator_source_delete(to_trash=True)``.
- **CLI capability check:** the CLI now invokes
  ``rt.migration._can_write_to_source(dst_source_id)`` and refuses with
  a clear error if the destination plugin does not advertise
  ``supports_write``. Replaces the v1.0.0rc1 "cross-source not yet
  supported" hard-coded refusal.
- **Persistent audit entries** for cross-source moves include
  ``src_source_id``, ``dst_source_id``, and a ``cross_source: True``
  marker so audit-log queries can partition cross-source from
  same-source operations.
- **Streaming chunked transfer is NOT in this release.** Per the v0.40
  hookspec, ``curator_source_write`` is whole-file-in-memory only;
  streaming is "Phase γ+" future work. For typical music / document
  / spreadsheet corpora, RAM is not the bottleneck.

### Added — PySide6 Migrate tab (Sessions C1, C2, C2b)

- **New tab in ``CuratorMainWindow``** (``src/curator/gui/main_window.py``):
  Migrate tab inserted at index 4, between Trash and Audit Log. Final
  tab order: Inbox(0) / Browser(1) / Bundles(2) / Trash(3) /
  **Migrate(4)** / Audit Log(5) / Settings(6) / Lineage Graph(7).
- **Master/detail layout** (Session C1, ``QSplitter``): jobs table on
  top (status / src→dst / files / copied / failed / bytes / started /
  duration), per-job progress table below (status / outcome / src path /
  size / verified hash, hash truncated to 12 chars + ellipsis).
  Selection-driven: clicking a job populates the progress table.
  ``selectionChanged`` slot preserves the selected ``job_id`` across
  refreshes.
- **Two new Qt models** (``src/curator/gui/models.py``, ~290 LOC):
  :class:`MigrationJobTableModel` wrapping ``MigrationJobRepository.list_jobs()``;
  :class:`MigrationProgressTableModel` with settable ``job_id`` via
  ``set_job_id()``. ``_format_duration`` helper handles the
  ``"H:MM:SS"`` / ``"MM:SS"`` / ``"—"`` cases consistently.
- **Right-click context menu on jobs** (Session C2):
  - **Abort job…** — enabled only for ``running``. Synchronous
    (``abort_job`` is fast; just sets a thread Event).
  - **Resume job (background)…** — enabled for
    ``{queued, cancelled, partial, failed}`` (excluded:
    ``running`` and ``completed``). Spawns a daemon
    ``threading.Thread`` running ``run_job`` so the GUI stays
    responsive; the perform method returns immediately.
  Each action has a tooltip explaining what it does, modal
  confirmation dialog before, and a result dialog after. Class
  constant ``_MIGRATE_RESUMABLE_STATUSES = frozenset(...)`` codifies
  the resume eligibility rule.
- **Live progress signals** (Session C2b,
  ``src/curator/gui/migrate_signals.py`` ~50 LOC):
  :class:`MigrationProgressBridge(QObject)` exposes a single
  ``progress_updated = Signal(object)``. The window constructs one
  bridge per Migrate tab and passes
  ``bridge.progress_updated.emit`` as the ``on_progress`` callback to
  ``run_job``. ThreadPoolExecutor workers calling on_progress per file
  fire the signal; Qt routes the cross-thread emission via
  ``Qt::QueuedConnection`` so the connected slot
  (``_slot_migrate_apply_progress_update``) runs on the GUI thread
  — the only safe place to touch ``QAbstractTableModel`` — and
  refreshes the affected models. The user sees progress tick up live
  with no manual Refresh.
- **Refresh strategy:** jobs model refreshes on every progress signal
  (cheap; <=50 rows). Progress model refreshes only when the
  in-flight job_id matches the displayed one (avoids redundant DB
  reads for unrelated jobs the user may be viewing instead).
  ``hasattr`` guards make the slot a silent no-op during window
  tear-down.
- **Tab-index regression fixes** in 4 prior GUI test files
  (``test_gui_audit``, ``test_gui_inbox``, ``test_gui_lineage``,
  ``test_gui_settings``) for the new tab ordering.

### Tests (~130 net new since v1.1.0a1)

- ``tests/unit/test_migration_phase2.py`` (~95 tests, Sessions A2 + A3):
  worker-pool semantics (``next_pending_progress`` atomicity, abort
  signaling latency, partial vs completed final-status logic, resume
  recovery from in_progress, keep_source COPIED outcome,
  include_caution gating).
- ``tests/unit/test_migration_cross_source.py`` (17 tests, Session B):
  uses a TWO-local-source-IDs strategy (``local`` and ``local:vault``,
  both owned by ``LocalPlugin`` via ``_owns()`` prefix matching) to
  exercise the cross-source code path hermetically without needing a
  real GDrive auth. Covers: capability check (refusal when dst plugin
  lacks ``supports_write``), full local→local-vault transfer with
  hash verification, dst-side collision (``FileExistsError`` from
  ``curator_source_write`` → SKIPPED_COLLISION), hash mismatch
  re-read fallback, lineage edge survival across
  ``source_id`` change (one-line ``get_edges_for(...)`` fix during
  test build to align with the actual repo method name).
- ``tests/integration/test_cli_migrate.py`` (+5 cross-source CLI
  tests via ``cross_source_seeded_db`` fixture, Session B).
- ``tests/gui/test_gui_migrate.py`` (NEW — 50 tests across 7 test
  classes, Sessions C1+C2+C2b):
  - C1: ``TestFormatDuration`` (9 parametrized),
    ``TestMigrationJobTableModel`` (10),
    ``TestMigrationProgressTableModel`` (7),
    ``TestMigrateTabWiring`` (6).
  - C2: ``TestPerformMigrateAbort`` (2),
    ``TestPerformMigrateResume`` (5 — includes a threading test that
    mocks ``run_job``, joins the spawned thread with a 5s timeout,
    asserts mock invocation from the background thread),
    ``TestMigrateContextMenuEnabling`` (3).
  - C2b: ``TestMigrationProgressBridge`` (3 — includes the headline
    cross-thread test: emit from a ``threading.Thread``,
    ``qapp.processEvents()``, slot fires on GUI thread),
    ``TestMigrateApplyProgressUpdateSlot`` (3),
    ``TestMigrateBridgeIntegration`` (2 — full pipe
    thread→emit→slot→refresh).
- **Final test count in the migration + GUI slice: 1150 passing,
  0 failures, 47s wall-clock.**

### Schema

- migration_002 adds ``migration_jobs`` + ``migration_progress``.
  No changes to existing tables (additive only). No data migration
  required — existing v1.1.0a1 / v1.0.0rc1 databases pick up the new
  tables on first run.

### Backward compatibility

- **Phase 1 in-memory API preserved.** ``MigrationService.plan()`` /
  ``apply()`` continue to work exactly as in v1.1.0a1 for users who
  prefer the simple one-shot path. ``apply()`` even gained
  ``keep_source`` and ``include_caution`` parameters that mirror the
  Phase 2 flags, so callers can opt into those semantics without
  routing through ``create_job`` + ``run_job``.
- **Phase 1 CLI surface preserved.** ``curator migrate <src> <root> <dst>``
  with no flags or only Phase 1 flags (``--ext``, ``--verify-hash``,
  ``--apply``, ``--json``) behaves identically. Users only opt into
  Phase 2 by passing one of the new flags (``--workers > 1``,
  ``--list``, ``--status``, ``--abort``, ``--resume``, etc.).
- **No new dependencies.** PySide6, pluggy, xxhash, loguru, send2trash,
  PyDrive2 — all already required.

### Atrium constitutional compliance

- **Principle 2 (Hash-Verify-Before-Move):** preserved per file in
  ALL paths — same-source in-memory, same-source persistent,
  cross-source in-memory, cross-source persistent. Verify happens via
  filesystem re-read for same-source and via hook re-read for
  cross-source, but the discipline (hash src, write dst, re-hash
  dst, compare, only THEN trash src) is identical in all four.
- **Audit log action distinction:** ``migration.move`` (index
  re-pointed, src trashed) vs ``migration.copy`` (keep-source: dst
  created, src + index untouched). Audit-log queries can partition
  on this. Cross-source entries also include
  ``src_source_id`` + ``dst_source_id`` + ``cross_source: True``.

### What's NOT in this release

- **Streaming chunked transfer for cross-source** — whole-file
  in-memory only; "Phase γ+" future work. Not a blocker for typical
  corpora.
- **Per-row progress updates in the GUI** — full-refresh on each file
  is fine for typical job sizes (dozens to low hundreds of files).
  Future polish if perf becomes an issue with thousand-file jobs.
- **Selection preservation across progress refreshes** — the user's
  row selection in the progress table is reset on each ``beginResetModel``.
  ~10 LOC of stash + restore-by-curator_id; deferred polish.
- **Live progress bar widget** — status text + counters only. Nothing
  prevents adding a ``QProgressBar`` next to the progress label in a
  future point release.
- **Real-world local→gdrive demo log** — the cross-source code path is
  fully tested via the two-local-source-IDs strategy in
  ``test_migration_cross_source.py``, but a curated end-to-end demo
  document analogous to v1.1.0a1's ``v100a1_migration_demo.txt`` is
  pending Jake's hands-on session against his real gdrive auth.

### Manual release steps remaining (Jake)

At the time of this commit, the v1.1.0 tag exists locally but has
not been pushed anywhere — Curator's git remote is not yet
configured. To complete the release:

1. ``git remote add origin <github-url>``
2. ``git push -u origin main``
3. ``git push origin v1.1.0``
4. (Optional) Publish a GitHub Release pointing at the tag and pasting
   this changelog entry as the release body.

Until step 1–3 happen, the entire ``v1.0.0rc1…v1.1.0`` work surface
(13 commits, ~2700 LOC of production code + ~1900 LOC of tests +
~130 new tests) lives on a single disk. Atrium GATE-PM-013 (git/backup
risk) remains the highest-leverage available action.

## [1.1.0a1] — 2026-05-08 — Migration tool Phase 1 (alpha)

**Headline:** Feature M (Migration tool) Phase 1 ships. Same-source
local→local file relocation with hash-verify-before-move discipline,
``curator_id`` constancy proven by lineage-edge + bundle-membership
preservation, audit log integration, and a real-world end-to-end demo
(5 files / 14,265 bytes / 0.31s, 5/5 verified, all index rows updated
in place). **Alpha:** Phase 2 (cross-source via gdrive write hook,
resume tables, worker concurrency, GUI Migrate tab) is needed before
v1.1.0 stable.

### Added

- **`MigrationService` (Phase 1):** ``src/curator/services/migration.py``
  (~430 LOC). Public API:
  - ``MigrationService.plan(src_source_id, src_root, dst_root, *, dst_source_id=None, extensions=None)``
    — walks every file under ``src_root`` via FileQuery, runs each through
    SafetyService, partitions into SAFE/CAUTION/REFUSE buckets, computes
    per-file ``dst_path`` preserving subpath. Refuses if ``dst_root`` is
    inside ``src_root`` (loop guard). Optional case-insensitive extension
    filter.
  - ``MigrationService.apply(plan, *, verify_hash=True, db_path_guard=None)``
    — per-file Atrium Constitution Hash-Verify-Before-Move discipline:
    (1) hash src (cached if available), (2) make dst parent dirs,
    (3) ``shutil.copy2``, (4) hash dst, (5) verify match — on mismatch
    unlink dst and mark HASH_MISMATCH leaving src intact, (6) update
    ``FileEntity.source_path`` (curator_id stays constant), (7) trash src
    via vendored send2trash (best-effort). Skips CAUTION/REFUSE files,
    pre-existing collisions, and the file at ``db_path_guard``.
- **Types:** ``MigrationOutcome`` enum (MOVED / SKIPPED_NOT_SAFE /
  SKIPPED_COLLISION / SKIPPED_DB_GUARD / HASH_MISMATCH / FAILED),
  ``MigrationMove``, ``MigrationPlan`` (with ``total_count`` /
  ``safe_count`` / ``caution_count`` / ``refuse_count`` / ``planned_bytes``),
  ``MigrationReport`` (with ``moved_count`` / ``skipped_count`` /
  ``failed_count`` / ``bytes_moved`` / ``duration_seconds``).
- **CLI command** ``curator migrate <src_source_id> <src_root> <dst_root>``:
  - Plan-only by default (no mutations). ``--apply`` runs moves.
  - ``--ext .mp3,.flac`` extension filter (comma-separated, case-insensitive).
  - ``--verify-hash / --no-verify-hash`` (default ON — Constitutional discipline).
  - JSON output via top-level ``--json`` flag for both plan + apply.
  - Auto DB-guard: passes ``rt.db.db_path`` to ``apply()`` so Curator's
    own DB file can never migrate out from under itself.
- **Runtime wiring:** ``CuratorRuntime.migration: MigrationService`` field;
  constructed in ``build_runtime`` after safety + audit are ready.
- **Service exports:** ``MigrationService``, ``MigrationPlan``,
  ``MigrationReport``, ``MigrationMove``, ``MigrationOutcome`` available
  from ``curator.services``.

### Tests (+33 new — 1002 default passing total, was 969)

- ``tests/unit/test_migration.py`` — 25 tests covering
  ``_compute_dst_path`` (3), ``_xxhash3_128_of_file`` (3),
  ``MigrationPlan`` dataclass (1), ``plan()`` (7), ``apply()`` (8),
  lineage/bundle preservation (2), error handling (1).
- ``tests/integration/test_cli_migrate.py`` — 8 tests covering CLI help,
  plan-only no-mutation, plan JSON shape, apply moves files end-to-end,
  no-SAFE returns moved=0, dst-inside-src exits 2, extension filter,
  ``--apply`` gate.
- **Headline invariants proven:** curator_id constancy (lineage edges +
  bundle memberships persist after move); hash mismatch leaves source
  intact and removes destination; DB-guard skip; collision skip;
  audit entries with ``actor='curator.migrate'`` /
  ``action='migration.move'`` per move; copy failure preserves source.
- **Real-world end-to-end demo** at ``docs/v100a1_migration_demo.txt``
  (4,098 bytes): Desktop-rooted demo (5 files / 14,265 bytes), plan
  via ``curator --json migrate`` produces 5 SAFE / 0 CAUTION / 0 REFUSE,
  apply moves all 5 in 0.31s with hash verification, sources trashed to
  Recycle Bin, FileEntity rows re-pointed at new paths, 5 audit entries
  written.

### Phase 2 deferred (required for v1.1.0 stable)

- Cross-source migration (local↔gdrive) via the v0.40
  ``curator_source_write`` plugin hook.
- Resume tables (``migration_jobs`` + ``migration_progress`` per
  DESIGN_PHASE_DELTA.md §M.4) so interrupted migrations can pick up
  where they left off.
- Worker pool for concurrent file copies (``--workers N`` flag).
- ``curator migrate --resume <job_id>`` / ``--list`` / ``--abort``.
- GUI "Migrate" tab.
- ``--keep-source`` and ``--delete-source`` flags (Phase 1 hardcodes ``trash``).
- Opt-in CAUTION migration via ``--include-caution``.

### Migration semver note

v1.1.0a1 is alpha. Bumped from v1.0.0rc1 to v1.1.0a1 (NOT v1.0.0a1 —
that would regress per PEP 440 since alpha < rc). Migration tool was
always post-1.0 work per ``DESIGN_PHASE_DELTA.md`` Phase Δ+ Roadmap, so
it ships as the first feature of the v1.1 minor cycle. v1.0.0rc1
remains the stability anchor; the v1.0.0rc1 git tag is unchanged.

## [1.0.0rc1] — 2026-05-08 — First release candidate 🎉

**Curator's first release candidate.** Phase α + Phase β are 100% complete.
This is the first version Curator considers itself feature-stable for the
use cases the project was designed to serve. The version bump from 0.43.0
to 1.0.0rc1 marks the milestone; semver-major changes after this point
require deliberation, not just a patch bump.

### What v1.0 IS

A standalone, cross-platform, file-knowledge-graph tool that:

- **Indexes files** from local + Google Drive sources via a pluggable
  source-plugin contract (read + write + delete + stat + enumerate).
- **Hashes** with xxh3_128 (fast) + ssdeep fuzzy + MD5 (compatibility),
  with single-pass file reads.
- **Detects lineage** between files: exact duplicates (xxhash match),
  near-duplicates (ssdeep + MinHash-LSH at scale; 196.7x speedup at
  10k files), version-of and renamed-from heuristics.
- **Bundles** — logical groupings of related files. Manual creation +
  editing via GUI; plugin-driven proposals via the rule engine.
- **Trash + restore** with cross-platform send2trash (Windows Recycle
  Bin v1+v2 metadata parsing; macOS via AppleScript; Linux via
  freedesktop.org Trash spec).
- **Watch + incremental scan** — long-running file watcher with
  debounced events that flow into incremental scans, keeping the
  index live.
- **Safety primitives** — four concern types (open handles, project
  files via VCS markers, app-data prefixes, OS-managed paths) with
  three-level verdicts (SAFE / CAUTION / REFUSE). Foundation of every
  destructive operation.
- **Organize** — four type-specific pipelines (music via mutagen +
  MusicBrainz fallback / photos via EXIF / documents via PDF+OOXML
  metadata / code projects via VCS detection) with plan / stage /
  apply / revert flows. Manifest-based reverts work bidirectionally.
- **Cleanup** — five detectors (junk files / empty directories / broken
  symlinks / exact duplicates / fuzzy near-duplicates) with full
  index-sync on every destructive operation (no phantom-file gap).
- **GUI** — native PySide6 desktop app, 7 tabs (Inbox / Browser /
  Bundles / Trash / Audit Log / Settings / Lineage Graph), 5
  mutations (Trash / Restore / Dissolve / Bundle create / Bundle
  edit), per-file inspect dialog with metadata + lineage edges +
  bundle memberships.
- **CLI** — full Typer-based CLI with `--json` mode for piping; sources
  add/list/show/enable/disable/remove; scan with watch mode; trash +
  restore; bundles list/show/create/dissolve; organize plan/stage/
  apply/revert; cleanup empty-dirs/broken-symlinks/junk/duplicates;
  doctor health check; audit log query; gdrive paths/status/auth.
- **Audit log** — append-only JSONL, every destructive operation
  recorded with timestamp + actor + action + entity + details. Full
  cross-tool compatibility per SIP v0.1.
- **Plugin system** — pluggy-based hookspecs for source plugins,
  classifier plugins, lineage detectors, bundle proposers,
  pre-trash veto. External code can extend Curator without modifying
  the core.

### What v1.0 IS NOT (deferred to v1.x)

- **Migration tool (Feature M)** — unblocked by v0.40 source write hook
  but not yet implemented. ~6-10h. Same-machine local→local first;
  cross-source local↔gdrive after.
- **Sync (Feature S)** — bidirectional source synchronization. Larger;
  design pass needed.
- **Update protocol (Feature U)** — version-and-upgrade ceremony.
- **MCP server** — read APIs as MCP tools. Unblocks Synergy Phase 1 +
  Conclave Phase 1.
- **APEX safety plugin** (`curatorplug-atrium-safety`) —
  operationalizes the MORTAL SIN Constitutional principle as a
  cross-product safety net. ~3-4h.
- **Bundle creation in CLI** — GUI ships in v0.43; CLI parity for
  bundle creation is a v1.x polish item.
- **OneDrive + Dropbox source plugins** — the source-plugin contract
  exists and gdrive uses it; the additional cloud sources are v1.x.

### Test status at v1.0.0rc1

**Default suite: 969 passing, 8 correctly-skipped, 0 failures, ~54s.**
Opt-in suite (slow + perf): 978 total passing.

### Documentation-only release marker

Curator does not yet have a `.git` directory at the project root. The
"tag" at v1.0.0rc1 is therefore documentation-only — the version bump
in `pyproject.toml` + `src/curator/__init__.py` plus this CHANGELOG
entry plus the corresponding BUILD_TRACKER entry collectively mark
the cut point. To make the tag a real Git tag, the user must:

```bash
cd C:/Users/jmlee/Desktop/AL/Curator
git init
# (decisions: .gitignore, squash strategy, remote)
git add -A
git commit -m "Release 1.0.0rc1"
git tag -a v1.0.0rc1 -m "First release candidate"
```

The long-deferred git_init decision is now the highest-priority
outstanding item per BUILD_TRACKER and the Atrium logic-gate inventory's
GATE-PM-013 (surface git/backup risk).

## [0.43.0] — 2026-05-08 — Phase Beta gate 4 polish: bundle creation + editing UI

**Phase β closes at 100%.** Bundle creation and editing now ship in the
GUI — the lone remaining DESIGN.md §15.2 surface from v0.42 is now
complete.

### Added

- New `BundleEditorDialog` in `src/curator/gui/dialogs.py` (~410 lines).
  Modal dialog used for both Create and Edit modes, distinguished by
  the optional `existing_bundle` parameter. Layout: Name + Description
  text fields at top; horizontal splitter with Available files (left)
  | Add→ / ←Remove / Set as ★ Primary buttons (middle) | In bundle
  (right). Each list has a search filter; double-click moves an item.
  The primary member is marked with a `★` prefix; defaults to first
  member if none explicitly chosen. Validation rejects empty name +
  zero-member bundles before the dialog accepts.
- New `BundleEditorResult` dataclass exposing `name`, `description`,
  `member_ids`, `primary_id`, `existing_bundle_id`,
  `initial_member_ids`. `added_member_ids` and `removed_member_ids`
  properties compute the set diff for edit-mode dispatchers.
- Three new GUI flow paths in `src/curator/gui/main_window.py`:
  * `_slot_bundle_new` (Edit menu "&New bundle..." Ctrl+N OR Bundles
    tab right-click "New bundle...") opens the editor in Create mode
    and dispatches to `_perform_bundle_create` on accept.
  * `_slot_bundle_edit_at_row` (Edit menu "&Edit selected bundle..."
    Ctrl+E OR Bundles tab right-click "Edit bundle...") opens the
    editor pre-populated with the bundle's current state and
    dispatches to `_perform_bundle_apply_edits` on accept.
  * `_open_bundle_editor` is the testable seam — tests patch it on
    the window instance to inject a synthetic `BundleEditorResult`
    (or `None` for cancel) without booting the Qt event loop.
- Bundles tab context menu now offers "New bundle..." even when
  right-clicking on empty space. When a row IS selected, the menu
  also offers "Edit bundle..." and "Dissolve bundle...".
- About dialog mentions the new v0.43 capability.

### Tests

- 32 new at `tests/gui/test_gui_bundle_editor.py` covering: dataclass
  set-diff properties (3), `_perform_bundle_create` (4),
  `_perform_bundle_apply_edits` (6), slot wiring with mocked
  `_open_bundle_editor` (4), real dialog construction + validation +
  interaction (12), context menu + Edit menu wiring (3).

### Regression

**Default suite: 937 → 969 passing, 8 correctly-skipped, 0 failures, 60.2s.**
Full GUI suite: 145 → 177 passing.

### Phase status

- Phase β gate 4 (GUI): **100%** — all 7 DESIGN.md §15.2 views shipped
  (Inbox / Browser / Bundles / Trash / Audit Log / Settings / Lineage
  Graph) plus all relevant mutations (Trash / Restore / Dissolve /
  Bundle create + edit) plus per-file inspect dialog plus bundle editor.
- Phase β gate 5 (gdrive): **100%** since v0.42.
- **Phase Beta: 100%.** Eligible for v1.0-rc1 tag at user discretion.
- Curator transitions to Phase Gamma polish + Phase Delta substantive
  features (Migration tool already unblocked by v0.40 write hook).

### Real-world demo

Seeded 12 audio files across 3 albums, pre-created one bundle
("Pink Floyd - The Wall" with 4 members), opened `BundleEditorDialog`
in Create mode, pre-filled name "Radiohead - OK Computer", added the
3 Radiohead tracks, marked "Paranoid Android" as primary, applied a
filter on Available list to "Pink Floyd". Captured a 1000x600
screenshot at `docs/v043_bundle_editor.png` (42KB) showing the
dialog state.

## [Unreleased] — 2026-05-08 (later) — Atrium governance suite + Conclave Lenses v2

Major design milestone: created the Atrium constellation governance
suite (5 documents) and expanded Conclave's Lens roster from 9 to 12
based on 2024-2025 state of art. **No code shipped** — still v0.41.0;
897 tests passing.

### Added (constellation governance)

New directory `C:\Users\jmlee\Desktop\AL\Atrium\` (peer to Curator,
Apex, future Conclave/Umbrella/Nestegg):

- `Atrium/CONSTITUTION.md` (~3000 words) — binding governance with
  Six Aims (Accuracy, Reversibility, Self-sufficiency, Auditability,
  Composability, Portability), Five Non-Negotiable Principles
  (MORTAL SIN, Hash-Verify-Before-Move, Citation Chain, No Silent
  Failures, Atomic Operations), six Articles. Awaiting Jake's
  ratification.
- `Atrium/CHARTER.md` (~2000 words) — operational elaboration of
  Constitution Articles III + IV. Constellation pattern, membership
  criteria, retirement criteria, cross-product authority, APEX peer
  relationship.
- `Atrium/CONTRIBUTOR_PROTOCOL.md` (~2500 words) — codifies all 11
  operating rules from this conversation, mid-session repair
  patterns, things to never do, standard restart prompt.
- `Atrium/GLOSSARY.md` (~1500 words) — ~50 terms with cross-references
  and APEX attribution markers.
- `Atrium/ONBOARDING.md` (~2000 words) — fresh-session ramp-up guide,
  reading order, current state snapshot, common tasks, success
  criteria.

### Added (Conclave roster)

- `docs/CONCLAVE_LENSES_v2.md` (~3000 words) — Lens roster expanded
  from 9 to 12 with explicit distinctness criterion (distinct method
  + distinct failure mode + distinct cost profile). Four genuine
  additions: GotOcrUnified (Stepfun GOT-OCR 2.0), NougatScience (Meta
  scientific paper specialist), MinerU (Shanghai AI Lab comprehensive
  pipeline, AGPL-3 license watch item), ColPaliVerify (late-interaction
  retrieval verifier in verification role, not extraction). Updated
  configurable presets: Triage 3 / Cheap-and-fast 5 / Balanced 7 /
  Full 12. Lens evaluation methodology added with quantitative
  acceptance criteria. Honest exclusions documented (Mistral OCR API,
  Reducto, Aryn, Surya, Donut, LayoutLMv3, EasyOCR, generic VLMs).
  Re-validation calendar specified. Four new open questions OQ-9
  through OQ-12.

### What's now waiting on Jake

- Ratification of Atrium Constitution
- Selection of Constitutional amendment codeword (proposed: `Keystone`)
- Confirmation of "Atrium" as constellation name (alternatives in
  `Charter`)
- OQ-9 through OQ-12 in CONCLAVE_LENSES_v2.md
- DE-1 through DE-13 in `ECOSYSTEM_DESIGN.md` (still open from
  earlier today)
- OQ-1 through OQ-8 in CONCLAVE_PROPOSAL.md


## [Unreleased] — 2026-05-08 (mid) — Conclave proposal + Synergy phased recommendation


### Added

- `docs/CONCLAVE_PROPOSAL.md` (~30 KB, ~3000 words) — full proposal
  for **Conclave**, a multi-Lens ensemble indexer for assessment
  knowledge bases. 5-9 independent extractors run on the same source,
  produce candidate KB outputs, then collectively vote section-by-
  section. Mathematical premise: ensemble voting with uncorrelated
  errors collapses error rates multiplicatively past 99% (matches
  APEX Constitution §1's ≥99.5% accuracy aim).
  - Nine proposed Lenses: PdfText (pdfplumber), OcrFlow (Tesseract),
    OcrPaddle (PaddleOCR), MarkerPdf (Marker), TableSurgeon (Camelot
    + table-transformer), VisionClaude (Claude API), VisionLocal
    (Qwen-VL or Llama-3.2-Vision), StructuredHeuristic (regex
    patterns), CitationGraph (anystyle/GROBID).
  - Configurable subset presets: cheap-and-fast (3) / balanced (5) /
    full (9) / custom.
  - Five-stage pipeline: source prep → parallel Lens execution →
    alignment → voting → synthesis emitting APEX KB format.
  - Logic gates and decision trees as the organizing primitive.
  - Standalone constellation product; integrates with Curator (MCP),
    APEX (KB format), Umbrella (monitoring), Nestegg (model bundling).
  - Phased rollout proposal: ~120h to v1.0, best built parallel with
    Curator Phase Δ work, not before.
  - 8 open questions (OQ-1 through OQ-8); 10 Conclave-specific ideas;
  - 3 explicit anti-patterns.

### Changed

- `ECOSYSTEM_DESIGN.md` §1: Synergy resolution updated from "Option B
  forever" to **"phased B → A"**. Per Jake's framing that Synergy is
  effectively Curator's alpha, the optimal path is opt-in Curator MCP
  consumption (Phase 1) → organic scope narrowing (Phase 2) →
  Constitutional retirement via Master Scroll edit (Phase 3). Trigger
  is event-driven, not calendar-driven. Honors offline-first via
  fallback paths; honors APEX authority structure via explicit
  Constitutional moment.
- `ECOSYSTEM_DESIGN.md` §4: Per-product responsibility matrix gains
  **Conclave** row as fifth constellation product (proposed).
- `ECOSYSTEM_DESIGN.md` §8: Ideas log gains `[IDEA-00] Conclave`
  reference pointing to `docs/CONCLAVE_PROPOSAL.md`.

### What this enables

- Concrete forward-looking proposal for the assessment-indexing
  bottleneck: Vampire's single-method approach is the current
  ceiling; Conclave is the architectural answer.
- Clean Synergy phase-out path that doesn't force a Master Scroll
  edit until the moment is genuinely warranted.

## [Unreleased] — 2026-05-08 (earlier) — Ecosystem-design milestone

Received and integrated APEX architecture inventory response. Synthesized
into a forward-looking ecosystem design document.

### Added

- `docs/APEX_INFO_RESPONSE.md` (~30 KB) — the canonical APEX architecture
  inventory verbatim from the APEX session, with full citations and
  `[NOT IN PROJECT KNOWLEDGE]` markers per APEX's Standing Rule 11.
  Complete subsystem roster (9 codenamed: Synergy / Succubus / Vampire /
  Opus / Locker / Inkblot / Id / Latent / Sketch).
- `ECOSYSTEM_DESIGN.md` (~36 KB, ~1000 lines) — full integration design
  in 8 sections covering: Synergy/Curator overlap (4 resolution options +
  recommendation), hard constraints from APEX Constitution translated to
  Curator requirements, Suite Integration Protocol (SIP) v0.1, per-product
  responsibility matrix, 3 first-integration milestone candidates
  (recommended: APEX safety plugin, ~3-4h), 13 enumerated open decisions,
  ideas log with 14 IDEA items + 3 NOT-IDEA anti-patterns.

### Changed

- Banner added to `DESIGN_PHASE_DELTA.md` realigning per ecosystem
  understanding: Feature A (asset monitor) → Umbrella standalone project,
  Feature I (installer) → Nestegg standalone project, Feature M
  (migration) framing realigned to Synergy/Curator (not Vampire/Curator),
  Features S + U stay in Curator.

### Critical finding

The original Phase Δ framing assumed APEX's `subAPEX2` (Vampire) was the
file inventory subsystem. **It isn't.** Vampire is a PDF-to-KB content
extractor. The actual file-inventory subsystem is **Synergy (subAPEX12)**
— the canonical state-of-disk authority per APEX's Master Scroll v0.4
(built v0.2.2, shipped 2026-04-30). Synergy directly overlaps with
Curator's role; recommended resolution is Option B: Synergy becomes a
Curator client (preserves APEX interfaces, no Master Scroll edit needed).

### Hard constraints surfaced

- **MORTAL SIN rule** (Standing Rule 9): never delete assessment-derived
  artifacts. Maps to required `curator_pre_trash` veto plugin.
- **Self-sufficiency**: APEX must work offline. Any Curator dependency
  must have graceful fallback.
- **Citation chain** (Constitution §3, NON-NEGOTIABLE): Curator data is
  enrichment, never Scribe authority.
- **Standing Rule 3** (No new memory systems): formally rules out
  side-by-side Curator+Synergy as long-term state.
- **SHA256 universal hash**: APEX uses SHA256 throughout; recommend
  adding SHA256 as Curator secondary hash.
- **Hash-verify-before-move discipline**: triple-check (source-absent +
  destination-present + hash-match SHA256) before declaring move
  successful.

### Recommended first integration milestone

APEX safety plugin for Curator — separately distributable
`curatorplug-apex-safety` package registering a `curator_pre_trash`
veto hook. Checks paths against APEX assessment-derivation patterns;
blocks trash with reason citing Standing Rule 9. ~3-4h. Validates
Curator's plugin system works for external code, validates APEX
governance can be enforced by Curator hooks, prevents real data loss.

## [0.41.0] — 2026-05-08 — "Phase Beta gate 4 complete: Lineage Graph view"

Seventh and final GUI tab from DESIGN.md §15.2. Renders the full
lineage edge graph as a 2D node+edge visualization with type-colored
edges and confidence labels. **Phase β gate 4 (GUI) is now 100%.**
Combined with gate 5 (gdrive plugin) also complete, **Phase β is
~98%.** Remaining items are bundle creation/editing UI and
`curator gdrive auth` helper — Phase γ polish.
**897 default-suite tests passing in ~53s, 0 failures.**

### Added

- New module `src/curator/gui/lineage_view.py` (~360 lines):
  `LineageGraphBuilder` (pure-Python facade over file_repo +
  lineage_repo) + `LineageGraphView` (`QGraphicsView` with networkx
  layout). Build modes: full graph (all files with edges) or
  focus-graph (BFS from a curator_id outward to N hops).
- New 7th tab "Lineage Graph" with edge-kind legend bar.
- Color-coded edges: magenta (duplicate), orange (near_duplicate),
  blue (version_of), green (derived_from), yellow (renamed_from);
  unknown kinds get neutral gray.
- 26 unit tests at `tests/gui/test_gui_lineage.py`.
- Real-world screenshot at `docs/v041_lineage_graph.png`.
- Companion design doc at `docs/APEX_INFO_REQUEST.md` — a prompt to
  paste into the APEX project chat for ecosystem integration design.

### Changed

- New dependency in `[gui]` extras: `networkx>=3.0`. MIT license,
  ~5MB pure Python; provides graph algorithms (spring/kamada_kawai/
  circular/shell layouts).
- Tab order is now: Inbox / Browser / Bundles / Trash / Audit Log /
  Settings / Lineage Graph (was: ... without Lineage Graph).
- `refresh_all()` (F5) now also refreshes the lineage view when present.

### Fixed

- `test_gui_inbox.py::test_inbox_tab_at_index_0` updated for new
  tab count (6 -> 7).
- `test_gui_settings.py::test_settings_tab_exists_at_index_4`
  updated similarly.

### What this closes

- Phase β gate 4 (GUI) is **100% complete**: all 7 DESIGN.md §15.2
  canonical views shipped (Inbox, Browser, Bundles, Trash, Audit Log,
  Settings, Lineage Graph) plus the per-file inspect dialog.
- Phase β overall: **~98%.** Remaining is polish, not architecture.

### Not yet (deferred)

- Focus-mode: select a file in the graph to filter to its N-hop
  neighborhood. Builder supports this; the picker UI is a v0.42 item.
- Bundle creation + membership editing UI (Phase γ polish).
- `curator gdrive auth <alias>` interactive helper (Phase γ polish).

## [0.40.0] — 2026-05-08 — "Phase Beta gate 5: source write hook"

Source plugin contract extended with `curator_source_write` for
create-new-file operations. Both `local_source` and `gdrive_source`
implement it. **This is the foundational primitive for cross-source
migration (Phase Δ Feature M) and cloud sync (Feature S)** — without
it, the source contract was read-only-plus-delete.
**871 default-suite tests passing in ~49s, 0 failures.**

### Added

- New hookspec `curator_source_write(source_id, parent_id, name,
  data: bytes, *, mtime, overwrite) -> FileInfo | None` in
  `src/curator/plugins/hookspecs.py`.
- New `SourcePluginInfo.supports_write: bool = False` field; both core
  plugins now advertise `supports_write=True`.
- Local implementation: atomic write via `tempfile.mkstemp` in same
  directory + `os.replace`. Auto-creates parent directories. Optional
  mtime preservation. Tempfile cleanup on any exception.
- Google Drive implementation: PyDrive2 CreateFile + content as BytesIO
  + Upload + FetchMetadata. Pre-flight existence check (Drive permits
  duplicate titles, so overwrite=False must check explicitly). When
  overwrite=True, trashes existing files with the same title first.
- 24 unit tests at `tests/unit/test_source_write_hook.py`.

### Changed

- `gdrive_source.py` docstring updated: status changed from
  "scaffolding" to "v0.40 implements register / enumerate / stat /
  read_bytes / write / delete".
- Phase β gate 5 status: COMPLETE for the core read+write+delete
  contract. Move and watch remain explicit Phase γ items.

### What this unblocks

- **Feature M (Migration tool)** can now be designed against a
  complete source contract — migration TO local OR TO gdrive works
  through the same code path.
- **Feature S (Cloud sync)** v1 can wrap rclone for sync while using
  `curator_source_write` for any non-rclone-handled cases.
- Phase β is now ~95% complete; only the Lineage Graph view (gate 4)
  remains.

### Not yet (deferred)

- `curator gdrive auth` interactive CLI helper (Phase γ).
- Streaming write variant for >500 MB files (Phase γ).
- `curator_source_move` for gdrive (Phase γ — Drive moves are
  parent-id swaps, need higher-level API).
- `curator_source_watch` for gdrive (Phase γ — push notifications).

## [0.39.0] — 2026-05-07 — "Inbox view"

Sixth GUI tab landing the canonical landing-tab of DESIGN.md §15.2.
Three-section dashboard: Recent scans / Pending review / Recent trash.
**847 default-suite tests passing in ~49s, 0 failures.**

### Added

- New `ScanJobTableModel` over `ScanJobRepository.list_recent` (~70
  lines). Columns: Status / Source / Root / Files / Started / Completed.
- New `PendingReviewTableModel` over lineage edges with confidence
  in the `[escalate, auto_confirm)` ambiguous middle band (~110
  lines). Resolves file paths via `file_repo.get` with a per-instance
  cache; falls back to `(<uuid>)` when a file row is missing.
- New 1st tab "Inbox" composing three QGroupBox sections, each with
  row count in the title and an empty-state hint label below when 0.
  Inbox is the canonical landing tab per DESIGN.md §15.2 ordering.
- New public method on `LineageRepository`: `query_by_confidence(*,
  min_confidence, max_confidence, limit)` returns edges in `[min, max)`.
  Replaces an earlier draft that crossed the public/private boundary
  by reaching into `_row_to_edge` from the model.
- `TrashTableModel` extended to accept an optional `limit` kwarg.
- 25 unit tests at `tests/gui/test_gui_inbox.py`.
- Real-world screenshot at `docs/v039_inbox.png`.

### Changed

- Tab order is now: Inbox / Browser / Bundles / Trash / Audit Log /
  Settings (was: Browser / Bundles / Trash / Audit Log / Settings).
  This matches DESIGN.md §15.2.
- `_make_inbox_section(title, view, model, *, empty_hint)` factored
  out so each section's QGroupBox composition is consistent.
- `_build_inbox_tab` reads `lineage.escalate_threshold` /
  `auto_confirm_threshold` from the runtime Config, so the band is
  configurable per-deployment via curator.toml. The active band
  appears in the section title (e.g. `[0.70, 0.95)`).
- `refresh_all()` (F5) now also refreshes the three Inbox models.

### Fixed

- `test_audit_tab_exists_with_title` updated for the new tab order:
  count >= 5, tabText(4) == "Audit Log".
- `test_settings_tab_exists_at_index_4` updated: count == 6,
  tabText(5) == "Settings".

### Not yet (deferred to v0.40+)

- Lineage Graph view (1 of 7 DESIGN.md views remaining).
- Bundle creation + membership editing UI.

## [0.38.0] — 2026-05-07 — "Settings view"

Fifth GUI tab landing the second of the remaining DESIGN.md §15.2
core views. Read-only display of the active `curator.toml` config
plus a Reload-from-disk button for verifying TOML edits.
**822 default-suite tests passing in ~45s, 0 failures.**

### Added

- New `ConfigTableModel` in `src/curator/gui/models.py` (~135 lines).
  Two columns: Setting (dotted path) / Value. Lists JSON-formatted;
  primitives stringified; tooltip on Value column shows untruncated.
- New 5th tab "Settings" with header label showing source TOML path,
  table view, Reload button, and help text below.
- Reload button re-parses the source TOML and updates the *display*
  only; the live runtime keeps using its original config until
  Curator is restarted.
- 26 unit tests at `tests/gui/test_gui_settings.py`.
- Real-world screenshot at `docs/v038_settings.png`.

### Architecture

- The Settings tab is the first GUI view that's NOT just a wrapped
  table — it composes a header label + table + button row + help text
  in a vertical layout.
- Reload logic factored into `_perform_settings_reload()` returning
  `(success, message, fresh_config | None)`, never raises. Slot calls
  it then either shows QMessageBox.warning on failure or updates the
  model + header + status bar on success.
- `refresh_all()` (F5) does NOT refresh Settings; the explicit Reload
  button is the only path for that. Comment in the code explains why.

### Fixed

- v0.37 audit tab test was asserting `count() == 4`; updated to
  `count() >= 4` so adding tabs in subsequent versions doesn't break it.

### Not yet (deferred to v0.39+)

- Inbox view, Lineage Graph view (2 of 7 DESIGN.md views).
- Bundle creation + membership editing UI.

## [0.37.0] — 2026-05-07 — "Audit Log view"

Fourth GUI tab landing the first of the remaining DESIGN.md §15.2
core views. Read-only tabular view over the append-only audit log.
**796 default-suite tests passing in ~44s, 0 failures.**

### Added

- New `AuditLogTableModel` in `src/curator/gui/models.py` (~155 lines).
  Five columns: When / Actor / Action / Entity / Details (JSON-truncated).
  ToolTipRole on the Details column shows the full untruncated JSON.
- New 4th tab "Audit Log" in the main window. Read-only by design —
  the audit log is intentionally append-only at the storage layer.
- Status bar gains an "Audit: N" count alongside the existing
  Files / Bundles / Trash counts.
- `refresh_all()` (F5) now refreshes the audit model too.
- 23 unit tests at `tests/gui/test_gui_audit.py`.
- Real-world screenshot at `docs/v037_audit_log.png`.

### Architecture

- The `AuditLogTableModel` caps results at 1000 rows newest-first
  (matches `AuditRepository.query()` default). For larger forensic
  histories, users should filter via the CLI — the GUI is for
  at-a-glance "what just happened".
- UUID-shaped entity IDs are truncated to 8 chars + "..." in the
  Entity column for table compactness; full IDs are still in the
  underlying AuditEntry rows accessible via `entry_at(row)`.

### Not yet (deferred to v0.38+)

- Inbox / Lineage Graph / Settings views (3 of 7 DESIGN.md views).
- Bundle creation + membership editing UI.

## [0.36.0] — 2026-05-07 — "Per-file inspect dialog"

Double-click any row in the Browser tab to open a modal showing
everything Curator knows about that file. **773 default-suite tests
passing in ~43s, 0 failures.**

### Added

- New `FileInspectDialog` in `src/curator/gui/dialogs.py` (~210 lines).
- Three tabs: Metadata (every fixed-schema field + flex attrs), Lineage
  Edges (every edge with the other-file path resolved + direction
  arrow), Bundle Memberships (every bundle with role + confidence).
- Browser tab gains a "Inspect..." action above "Send to Trash..." in
  the right-click context menu.
- Header label shows path bolded with size / mtime / source; if the
  file is deleted (`deleted_at` set), shows a red "DELETED (timestamp)"
  segment.
- 14 unit tests at `tests/gui/test_gui_inspect.py`.
- Real-world screenshot at `docs/v036_inspect_dialog.png`.

### Architecture

- The dialog construction is factored as a testable seam:
  `_open_inspect_dialog(file)` on the main window can be patched in
  tests to capture the call without entering `dlg.exec()`.

## [0.35.0] — 2026-05-07 — "GUI mutations: Trash / Restore / Dissolve"

The v0.34 read-only GUI gains its first three destructive operations.
Each is gated through a confirmation dialog with Cancel as default.
**759 default-suite tests passing in ~44s, 0 failures.**

### Added

- Browser tab right-click → "Send to Trash..." → confirm →
  `TrashService.send_to_trash`. The file moves to the OS Recycle Bin
  via `send2trash`, the FileEntity is soft-deleted, and a TrashRecord
  is created.
- Trash tab right-click → "Restore..." → confirm →
  `TrashService.restore`. On Windows this typically returns a
  friendly "please restore manually" message because `send2trash`
  doesn't record the OS trash location.
- Bundles tab right-click → "Dissolve bundle..." → confirm →
  `BundleService.dissolve`. Member files are preserved.
- New Edit menu with the same three actions (Ctrl+T trash, Ctrl+R
  restore, Ctrl+D dissolve) for keyboard users.
- 11 unit tests for the mutation paths (`tests/gui/test_gui_mutations.py`).
  All use real `build_runtime` against a temp DB; none requires pytest-qt
  event loop driving.

### Architecture

- Mutation logic factored into `_perform_*` methods that NEVER raise
  and return `(success, message)`. Slots show the message in a dialog.
  This makes the methods unit-testable without booting Qt.
- Slot dispatch from Edit menu vs context menu shares the same
  `_slot_*_at_row(row)` core; only the row-resolution path differs.

### Not yet (deferred to v0.36+)

- Per-file inspect dialog (Browser double-click).
- Bundle creation + membership editing.
- Lineage Graph / Inbox / Audit Log / Settings views.

## [0.34.0] — 2026-05-07 — "PySide6 desktop GUI, read-only first ship"

First visual interface. Native PySide6 Qt window with three read-only
tabs (Browser, Bundles, Trash) over the existing Curator runtime.
Launched via `curator gui`. **748 default-suite tests passing in ~70s,
0 failures.**

### Added

- New package `src/curator/gui/` with three Qt table models, main
  window, and launcher (~550 lines total).
- New CLI subcommand `curator gui` with friendly install hint when
  PySide6 isn't available.
- New `[gui]` extra in `pyproject.toml` (`PySide6>=6.6`).
- `pytest-qt>=4.2` added to the `[dev]` extra.
- 19 unit tests for the Qt table models (`tests/gui/test_gui_models.py`)
  + 1 slow-marked smoke test for the actual QMainWindow.
- Real-world screenshot at `docs/v034_gui_screenshot.png`.

### Fixed

- `cli/runtime.py:build_runtime` was missing the `code: CodeProjectService`
  collaborator that v0.31 added to OrganizeService. Fixed; the runtime
  now passes `code=CodeProjectService()` explicitly.

### Not yet in v0.34 (deferred to v0.35+)

- Mutations from the GUI (trash a file, dissolve a bundle, restore from
  trash). The visual layer ships first; HITL escalation comes next.
- The remaining 4 of DESIGN.md §15.2's 7 core views: Inbox, Lineage
  Graph, Audit Log, Settings.
- Per-file inspect dialog (double-click on a Browser row).

## [0.33.0] — 2026-05-07 — "Twelve-feature Phase Gamma block"

The first stable release. Every type-specific organize pipeline, every
cleanup detector, and full bidirectional index sync are feature-complete
and tested. **728 default-suite tests passing in 39.5s, 0 failures, 0
warnings.** No manual rescan is needed after any destructive operation —
the index always reflects on-disk truth.

### Highlights

- **Four organize pipelines feature-complete**: music + photo + document
  + code (VCS-marked projects). Each has its own metadata reader,
  destination templating, and CLI integration.
- **Five cleanup detectors feature-complete**: junk files, empty dirs,
  broken symlinks, exact duplicates (xxhash3_128), fuzzy near-duplicates
  (LSH MinHash + ssdeep). All routed through `send2trash` with full
  audit logging.
- **Bidirectional index sync** on every destructive operation: cleanup
  deletes mark `FileEntity.deleted_at`; organize moves update
  `FileEntity.source_path` to follow the file. No phantom files.
- **Optional MusicBrainz enrichment** for music files where the filename
  heuristic produced only artist + title — fills missing album / year /
  track from canonical MB data without overwriting any existing real
  tags.

### Phase Gamma versions rolled into this release

| Version | What landed |
|---|---|
| v0.20 | Phase Gamma kickoff: SafetyService + OrganizeService.plan |
| v0.21 | F2 Music: MusicService (mutagen-backed) + plan integration |
| v0.22 | Stage / revert mode for organize |
| v0.23 | Apply mode + collision handling |
| v0.24 | F3 Photos: PhotoService (EXIF date hierarchy) |
| v0.25 | F6 Cleanup: junk + empty_dirs + broken_symlinks |
| v0.26 | F4 Documents: DocumentService (PDF + DOCX dates) |
| v0.27 | Music filename heuristic + MusicBrainz client (un-wired) |
| v0.28 | F7 Dedup-aware cleanup (exact, via xxhash3_128) |
| v0.29 | F8 Index sync for cleanup (mark_deleted on apply) |
| v0.30 | F9 Fuzzy near-duplicate detection via LSH |
| v0.31 | F5 Code project organization (VCS markers + language inference) |
| v0.32 | MB auto-enrichment wiring (`--enrich-mb`) |
| v0.33 | Organize index sync for stage / apply / revert |

### Test counts

- Phase Alpha closed at **149 passing**
- Phase Beta gates 1, 2, 3 (LSH + cross-platform send2trash + watchfiles) added ~150
- Phase Gamma block added the rest
- **Total: 728 default-suite + 8 opt-in passing, 7 correctly skipped, 0 failures**
- Plus 4 LSH perf benchmarks available via `pytest tests/perf -m slow`

### Known gaps (intentionally deferred)

- **Phase Beta gate 4 (GUI)**: 0% — Windows app shell still pending
- **Phase Beta gate 5 (Google Drive)**: scaffolded — OAuth flow + remote
  source plugin not yet completed
- **MB enrichment stretch goals**: `--enrich-mb-limit N`, progress
  counter, audit-table caching
- **Code organize stretch goals**: non-VCS project detection
  (pyproject.toml / package.json / Cargo.toml), submodule recognition
- **Pillow 12 deprecation**: a quiet warning in pytest output

---

## [0.1.0a1] — Phase Alpha (closed)

Foundational release. Storage layer (CuratorDB + repositories), scan
service with xxhash3_128 indexing, source plugin scaffolding,
classification service, audit log, full CLI shell, 149 passing tests.
See `BUILD_TRACKER.md` for the chronological log.
