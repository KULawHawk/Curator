# Changelog

All notable changes to Curator are documented here. Format inspired by
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) with semver
versioning where reasonable.

## [1.6.0] — 2026-05-09 — `curator sources config`: native CLI for per-source plugin config

**Headline:** New `curator sources config <id> [--set / --unset / --clear]` subcommand closes the v1.5.0 CLI gap that `scripts/setup_gdrive_source.py` worked around. Cloud-source registration is now a pure CLI workflow with no helper-script dependency. The helper script is kept for backwards compatibility but is no longer required for new setups.

### Why this matters

In v1.5.0, `curator sources add --type gdrive` registered a source's metadata (id, type, name, enabled flag) but exposed no flag for the per-plugin `config` dict that cloud plugins need (client_secrets_path, credentials_path, root_folder_id, include_shared, etc.). The Session B (2026-05-09) workflow had to use a Python helper script to build a `SourceConfig` directly and call `source_repo.upsert()`. That worked but was awkward, didn't generalize cleanly to OneDrive/Dropbox, and meant every future cross-source plugin would need a similar helper.

This release adds a single subcommand that handles config for any source type, present and future:

```
curator sources config gdrive:src_drive --set root_folder_id=1abc... \
    --set client_secrets_path=/path/to/cs.json \
    --set credentials_path=/path/to/creds.json
```

### Added

* **`curator sources config <source_id>` subcommand** in `src/curator/cli/main.py` (~180 LOC). Operations within a single invocation apply in order: `--unset` first, then `--clear` if given, then `--set`. This lets you reset-and-rewrite atomically:
  ```
  curator sources config gdrive:src_drive --clear \
      --set client_secrets_path=/new/cs.json \
      --set credentials_path=/new/creds.json \
      --set root_folder_id=1xyz...
  ```
  With no flags, prints the current config (read-only; equivalent to the `config:` section of `sources show`).

  Flags:
  * `--set KEY=VALUE` (repeatable). Values are parsed as JSON first (so `true` -> `True`, `42` -> `42`, `[1,2]` -> `[1, 2]`), falling back to literal string when JSON parsing fails. Path strings, folder IDs, and other non-JSON values pass through as-is.
  * `--unset KEY` (repeatable). Silently no-ops if the key isn't present (the audit log records nothing for no-op invocations).
  * `--clear`. Removes ALL config keys.

* **`source.config` audit event.** Every successful mutation emits an audit-log entry under `actor='cli.sources'`, `action='source.config'`, `entity_type='source'`, `entity_id=<source_id>`, with `details={"changes": [{"op": ..., "key": ...}], "config_keys_after": [...]}` for traceability. No event is emitted for pure no-ops (e.g., `--unset` on a key that wasn't there).

* **18 new integration tests** in `tests/integration/test_cli_sources.py` (`TestSourcesConfig` class). Covers: read-only mode, --set with strings/booleans/ints/JSON, --set with `=` in value, --unset removing existing key, --unset missing key noop, --clear removing all, atomic --clear+--set replace, audit emission on mutation, no audit on noop, preservation of other source fields (display_name, source_type, enabled), error paths for malformed --set values.

### Changed

* **`docs/TRACER_SESSION_B_RUNBOOK.md`** v4 — Step 2 now shows the native CLI as the preferred path; setup_gdrive_source.py kept as compat fallback. New users follow the CLI path; existing scripts/automation using the helper continue working.
* **Version bump:** 1.5.1 → 1.6.0 in `pyproject.toml` and `src/curator/__init__.py`.

### Compatibility

* **No breaking changes.** Existing `sources add / list / show / enable / disable / remove` subcommands are unchanged. The new `config` subcommand is additive.
* **`scripts/setup_gdrive_source.py` continues to work.** It uses the same underlying `source_repo.upsert()` mechanism the new CLI uses, so the two paths are equivalent. Existing automation (Session B v3 runbook, future runbooks) doesn't need to change.
* **41/41 existing sources CLI tests pass.** No regressions.

### What's still pending for v1.7.0+

* `--get KEY` for reading a single config value (current `config` with no flags prints all).
* Plugin-side `config_schema` declaration so the CLI can validate `--set` values against expected types/required-keys for each plugin (today the CLI accepts any `--set KEY=VALUE` regardless of whether the plugin actually uses that key).
* Long-form documentation for cloud-source registration in a tutorial doc (currently only the Session B runbook covers it).

## [1.5.1] — 2026-05-09 — gdrive plugin: SourceConfig injection + parent_id translation (production-validated cross-source)

**Headline:** Two architectural bugs in the gdrive_source plugin made cross-source `local → gdrive:*` migration impossible in v1.5.0 and earlier. Both bugs were masked by the existing test suite (which used `set_drive_client()` mock injection to bypass the affected code paths). This patch fixes both bugs and validates the fix end-to-end against real Google Drive.

### Production validation

Session B (Tracer Phase 2 cross-source local→gdrive demo) ran end-to-end against a real Google Drive account 2026-05-09 02:54 CDT:

* 10 test files (~60 bytes each) migrated from `C:\Users\jmlee\Desktop\session_b_src\` to a Drive folder.
* `MOVED: 10, SKIPPED: 0, FAILED: 0` in 18.59s (real PyDrive2 round-trip latency).
* All 10 files verified present in Drive with correct sizes, parent folder, content, owner, and timestamps matching the audit log.
* 10 fresh `migration.move` audit entries with hashes recorded.

This is the first end-to-end production validation of the v1.4.0+ cross-source migration surface against real Drive. v1.4.0 / v1.4.1 / v1.5.0 cross-source code paths are now considered production-validated retroactively; the v1.5.1 patch is what actually unblocked them.

### Fixed

#### Bug 1: gdrive plugin couldn't resolve its own SourceConfig

**Symptom:** Calling `curator migrate local <src> "/" --dst-source-id "gdrive:src_drive" --apply` failed with:

```
gdrive client build failed for gdrive:src_drive: gdrive source config
requires both 'client_secrets_path' and 'credentials_path'.
```

**Cause:** `Plugin.curator_source_write/read_bytes/stat/delete/rename` all called `self._get_or_build_client(source_id, options={})` with hardcoded empty options. The plugin then read `client_secrets_path` from those empty options and failed. The hookspec for these methods doesn't carry options through its signature (only `curator_source_enumerate` does), so the plugin had no path to discover SourceConfig at hook-call time.

**Fix:** New `Plugin.set_source_repo(source_repo)` injection method, mirroring the existing `AuditWriterPlugin.set_audit_repo()` pattern. `build_runtime` calls it after constructing `source_repo`. The plugin's new `_resolve_config(source_id, options)` method walks four sources in priority order:

1. `options['source_config']` (scan path; preferred when present).
2. `self._config_cache[source_id]` (memo of prior resolution).
3. `self._source_repo.get(source_id).config` (production path — reads from SQLite `sources` table).
4. `source_config_for_alias(alias)` (disk-conventional fallback under `~/.curator/gdrive/<alias>/`; loses any custom `root_folder_id`).

#### Bug 2: parent_id `"/"` not translated to Drive folder ID

**Symptom:** Even with bug 1 fixed, the migration would hit a Drive API error because `target_parent = "/"` is not a valid Drive folder ID.

**Cause:** `MigrationService._cross_source_transfer` builds `parent_id` from path semantics: `parent_id = str(Path(dst_path).parent)`. For `dst_path="/session_b_test_1.txt"`, this yields `parent_id="\\"` on Windows or `"/"` on POSIX. The gdrive plugin's `curator_source_write` previously passed this through as `target_parent = parent_id or "root"`, which is truthy and gets sent to Drive as `{"parents": [{"id": "/"}]}` — invalid.

**Fix:** New `Plugin._resolve_parent_id(source_id, parent_id)` method. Maps a small set of well-known root sentinels (`/`, `\\`, `""`, `.`, `None`) to the configured `root_folder_id` from the resolved SourceConfig (falls back to `"root"` for the user's My Drive root if not configured). Real Drive folder IDs (alphanumeric, ~28 chars) pass through unchanged.

### Changed

* **`src/curator/plugins/core/gdrive_source.py`** — `Plugin` gained `set_source_repo()`, `_resolve_config()`, `_resolve_parent_id()`. `_get_or_build_client()` now calls `_resolve_config()` instead of reading from options directly. `curator_source_write()` now calls `_resolve_parent_id()` instead of `parent_id or "root"`. ~150 LOC added; no methods removed; no public API broken.
* **`src/curator/cli/runtime.py`** — `build_runtime()` calls `gdrive_plugin.set_source_repo(source_repo)` after constructing `source_repo`, mirroring the existing `audit_writer.set_audit_repo()` injection pattern.
* **`scripts/setup_gdrive_source.py`** (was added in v1.5.0 hot-fix territory but documented here for completeness) — new helper script bridging the v1.5.0 CLI gap where `curator sources add --type gdrive` registers the source's metadata but doesn't expose a way to set per-source config. Defaults `source_id` to `gdrive:<alias>` (with the prefix — the gdrive plugin's `_owns()` requires this for ownership). Idempotent: existing source is updated rather than failed.
* **`docs/TRACER_SESSION_B_RUNBOOK.md`** v3 — corrected CLI syntax, prefixed source_id, single-block format throughout. Now reproducible end-to-end.
* **Version bump:** 1.5.0 → 1.5.1 in `pyproject.toml` and `src/curator/__init__.py`.

### Compatibility

* **No public API changes.** `Plugin.set_drive_client()` (test injection) still works; `set_source_repo()` is new and additive. The `audit_writer` injection pattern is unchanged.
* **No breaking config changes.** Existing `~/.curator/gdrive/<alias>/` layouts work without modification.
* **244/244 existing unit tests pass.** No regressions. Test additions for the new resolution path are deferred to a follow-up commit (existing tests use `set_drive_client()` mock injection which bypasses `_resolve_config()` entirely; a dedicated integration test is the proper coverage).

### Why this slipped to v1.5.0

The affected hooks (`curator_source_write/read_bytes/stat/delete/rename`) were tested via mock-client injection from day one (Phase Beta v0.40). Mocked tests passed all migration paths because they bypassed `_get_or_build_client()` entirely, and the parent_id translation was never exercised because the mocks accepted any parent value. The bug only surfaced when an end-to-end Session B run attempted real Drive writes — which had never been done in CI or unit tests. Future cross-source plugins (OneDrive, Dropbox) should either follow this v1.5.1 pattern from the start OR have an end-to-end integration smoke test gating the release.

## [1.5.0] — 2026-05-08 — MCP HTTP-auth (Bearer-token authentication for `curator-mcp --http`)

**Headline:** Closes the v1.5.0 candidate item per Tracer Phase 4 v0.2 RATIFIED DM-6 plus the `docs/CURATOR_MCP_HTTP_AUTH_DESIGN.md` v0.2 RATIFIED design. Adds Bearer-token authentication to the HTTP transport so it can safely be exposed beyond loopback. Three-phase implementation (P1 auth.py module, P2 `curator mcp keys` CLI, P3 server middleware + integration tests) shipped over a single session per the design plan. **stdio transport (the default; used by Claude Desktop / Claude Code) is unchanged.** Existing v1.2.0 stdio integrations require zero modifications.

### Added

- **`src/curator/mcp/auth.py`** — Key generation (`secrets.token_urlsafe(30)` with `curm_` format prefix per DM-3 RATIFIED), SHA-256 storage (no plaintext persisted), atomic file I/O via `tempfile.mkstemp` + `os.replace`, validation (returns `StoredKey` or `None`), and `update_last_used` for the audit trail. Honors `CURATOR_HOME` env var like `gdrive_auth`.
- **`curator mcp keys generate <name> [--description TEXT]`** — Generate a new API key. Prints the plaintext to stdout once; subsequent operations only see the hash.
- **`curator mcp keys list`** — Show registered keys (name, created, last used, description). No hashes or plaintext shown. Honors `--json`.
- **`curator mcp keys revoke <name> [--yes]`** — Revoke a key. Prompts for confirmation unless `--yes`. Other keys preserved.
- **`curator mcp keys show <name>`** — Show metadata for one key. No hashes or plaintext shown.
- **`src/curator/mcp/middleware.py`** — `BearerAuthMiddleware` (Starlette `BaseHTTPMiddleware`) extracts `Authorization: Bearer <key>`, validates against the keys file, returns 401 + `WWW-Authenticate: Bearer` on failure, forwards on success. `make_audit_emitter(audit_repo)` factory bridges middleware events to Curator's audit log under `actor='curator-mcp'`.
- **`src/curator/mcp/server.py` `--no-auth` flag** — Opt out of authentication. Only legal with loopback `--host`. Default behavior is now auth-required.
- **Audit emission for auth events** — `mcp.auth_success` (throttled to 1/key/minute per DM-5 RATIFIED) and `mcp.auth_failure` (never throttled — security signal). Failed events record only the first 10 chars of the rejected key for forensics; full key never appears in audit.
- **Non-loopback HTTP binding now allowed when auth is configured.** Previously v1.2.0 hard-refused any `--host` other than loopback. v1.5.0 allows non-loopback iff at least one key is configured AND `--no-auth` is not passed.

### Per-DM ratification trace (all DMs RATIFIED 2026-05-08)

| DM | Decision | Implemented as |
|---|---|---|
| DM-1 | `Authorization: Bearer <key>` (RFC 6750) | `BearerAuthMiddleware.dispatch` extracts header, returns 401 + `WWW-Authenticate: Bearer` on fail |
| DM-2 | JSON file at `~/.curator/mcp/api-keys.json` with 0600 (Unix) | `default_keys_file()` + `_set_secure_permissions()` in auth.py |
| DM-3 | `curm_<40-char-random>` format-prefixed | `generate_key()` returns `f"curm_{secrets.token_urlsafe(30)}"` |
| DM-4 | Multiple named keys with `name`/`created_at`/`last_used_at`/`description` | `StoredKey` dataclass + `add_key`/`remove_key` operations |
| DM-5 | Both successful + failed; successful throttled to 1/key/minute | `BearerAuthMiddleware._emit_success` (throttled) + `_emit_failure` (never throttled) |
| DM-6 | Auth required by default; `--no-auth` opts out (loopback-only) | `_run_http()` in server.py: requires keys unless `--no-auth`; refuses `--no-auth` + non-loopback |

### Test coverage

* **`tests/unit/test_mcp_auth.py`** — 42 tests covering key generation, hashing, file I/O round-trip, atomic write, validation, last_used updates, default-paths, dataclass serialization. 1 Unix-only skip (0600 permission test).
* **`tests/unit/test_mcp_keys_cli.py`** — 24 tests covering all four CLI subcommands' happy paths + error paths + JSON output, including no-secrets-leaked verification.
* **`tests/unit/test_mcp_http_auth.py`** — 23 integration tests covering header rejections (5 variants), successful auth (3 variants), audit emission (7 variants including throttling for both success and failure), `make_audit_emitter` factory (3 variants), and `_run_http` arg validation (5 variants including the non-loopback + `--no-auth` refusal).
* **MCP-auth subsystem total: 89 passed, 1 skipped.**
* **Migration regression: 144/144 passing** (no impact on Tracer).

### Compatibility

* **stdio transport: zero changes.** Claude Desktop / Claude Code integrations using `curator-mcp` (no flags) are byte-for-byte identical to v1.2.0–1.4.1.
* **HTTP transport without `--no-auth` previously had no auth.** Existing v1.2.0–1.4.1 callers using `curator-mcp --http` now need to either generate a key (`curator mcp keys generate <name>`) and present it as `Authorization: Bearer <key>`, OR pass `--no-auth` to keep the old (loopback-only) unauthenticated behavior.
* **HTTP transport with non-loopback host previously exited 2.** Now allowed if a key is configured.

### New optional dependencies

* `mcp>=1.20` — already present from v1.2.0 (the `[mcp]` extra). v1.5.0 uses FastMCP's `streamable_http_app()` plus Starlette/uvicorn (transitively pulled by `mcp`).

### Why minor (1.4.1 → 1.5.0) not patch

The HTTP transport's auth requirement is a real public-API change — callers using `curator-mcp --http` without auth need to update. Patch versioning would be dishonest. Minor bump is appropriate per semver: backward-incompatible behavior change in a feature that was explicitly documented as beta-status (the v1.2.0 `"v1.2.0 has NO authentication for HTTP"` warning made clear auth was coming). stdio behavior is fully preserved.

## [1.4.1] — 2026-05-08 — API hardening: sentinel-default for `apply()` + `run_job()` policy kwargs

**Headline:** Patch fix for an undocumented footgun in `MigrationService.apply()` and `MigrationService.run_job()`. Calling `service.set_max_retries(N)` or `service.set_on_conflict_mode(M)` before `apply()` / `run_job()` previously did NOT stick — both methods unconditionally called the setters at entry with their hard-coded defaults (3 / 'skip'), silently overwriting any prior configuration. v1.4.1 changes the kwarg defaults to a `_UNCHANGED` sentinel and only invokes the setters when the caller explicitly passes a value. Sticky setters now stick.

### Fixed

- **`MigrationService.apply(plan, max_retries=..., on_conflict=...)`** — default values changed from `3` / `"skip"` to a module-level `_UNCHANGED` sentinel. Setters only invoked when the caller explicitly passes a value. Bare `apply(plan)` after `set_max_retries(7)` now actually uses 7 retries.
- **`MigrationService.run_job(job_id, max_retries=..., on_conflict=...)`** — same sentinel default change. Three-tier resolution preserved: explicit kwarg > persisted `job.options` > current `self._max_retries`/`self._on_conflict_mode`. The previous behavior of using `max_retries == 3` as a magic-default proxy for "caller didn't pass anything" is replaced with explicit sentinel comparison, which correctly distinguishes "caller passed nothing" from "caller explicitly passed 3".
- **Docstrings on `set_max_retries()` and `set_on_conflict_mode()`** — the v1.4.0 warning paragraphs about "calling this BEFORE apply() does NOT stick" are removed and replaced with positive guidance describing the two equivalent patterns: explicit kwarg per call, or sticky setter once.

### Added

- `_UNCHANGED: Any = object()` sentinel constant in `src/curator/services/migration.py`. Documented as "keyword arguments whose default is 'keep current setting' rather than 'reset to a hard-coded value.'" Annotated `Any` so type checkers don't complain about `int | _UNCHANGED` impossibilities; runtime validation by `set_max_retries()` / `set_on_conflict_mode()` is preserved.
- `tests/unit/test_migration_v141_sentinel_defaults.py` — 15 new unit tests covering:
  - `__init__` defaults unchanged (3 / 'skip').
  - Sticky setters persist through bare `apply()` / `run_job()`.
  - Explicit kwargs still override sticky setters.
  - Mixed kwargs (one explicit, one omitted) preserve the omitted-arg's sticky value.
  - Clamping behavior preserved on explicit kwargs.
  - `run_job()` three-tier resolution: explicit kwarg > persisted options > sticky setter > __init__ default.
  - Invalid persisted `on_conflict` falls back to 'skip' (not crash).
  - Invalid persisted `max_retries` (unparseable) silently preserves current `self._max_retries`.
  - Explicit invalid `on_conflict` still raises `ValueError`.

### Changed

- Version bump `1.4.0` → `1.4.1` in `pyproject.toml` and `src/curator/__init__.py`.

### Compatibility

- **No API surface changes.** Method signatures still accept `max_retries` and `on_conflict` keyword arguments. Existing callers passing explicit values get identical behavior. Existing callers passing nothing get NEAR-identical behavior — the only observable difference is when `set_max_retries()` or `set_on_conflict_mode()` was called previously: those calls now stick instead of being silently overwritten. This is a *bug fix*, not a behavior break.
- The previous BUILD_TRACKER `[v1.5.0 candidate]` entry for this work is closed and moved to the released list.

### Test totals after v1.4.1

- Migration regression slice: **150/150 passing** (was 135/135 in v1.4.0; +15 new sentinel tests). 4 skipped (preexisting; googleapiclient not installed in dev venv).
- Full unit-test run: 737 passed, 22 preexisting failures in `test_photo.py` (PIL/Pillow not installed in dev venv), 10 skipped, 2 deselected. The PIL failures are environment-only and predate v1.4.0.

## [1.4.0] — 2026-05-08 — Tracer Phase 4 (cross-source overwrite-with-backup + rename-with-suffix)

**Headline:** v1.3.0 → v1.4.0 (minor bump). Closes the cross-source simplification documented in Tracer Phase 3 v0.3 §12 P2 entry: cross-source `--on-conflict=overwrite-with-backup` and `--on-conflict=rename-with-suffix` no longer degrade to skip-with-warning. They now ship as full implementations using the new `curator_source_rename` hookspec (rename path) and the FileExistsError retry-write pattern (suffix path). Strictly additive at the user-facing surface; defaults preserve v1.3.0 behavior exactly. Plugins not implementing the new hook automatically retain the v1.3.0 degrade-to-skip behavior. See `docs/TRACER_PHASE_4_DESIGN.md` v0.3 IMPLEMENTED for the full design and per-DM implementation evidence.

### Added

- **New hookspec `curator_source_rename`** (P1 — commit `4a4c65e`). Signature: `curator_source_rename(source_id, file_id, new_name, *, overwrite=False) -> FileInfo | None`. Strict same-parent rename semantic, distinct from the existing `curator_source_move` whose path-vs-parent-id ambiguity made it unsafe to retrofit (per design DM-1). FileExistsError raise contract on `overwrite=False` mirrors `curator_source_write` (DM-2). Strictly additive: plugins that don't implement return None (pluggy default), `MigrationService` falls back to v1.3.0 degrade-to-skip behavior. No plugin contract version bump (DM-4).
- **Local source plugin implementation** (~30 LOC). `Path.rename` for default (atomic on same filesystem per POSIX); `Path.replace` for `overwrite=True`. Returns `FileInfo` with new path's stat including inode in `extras`.
- **Gdrive source plugin implementation** (~108 LOC). PyDrive2 title-only patch via `f['title'] = new_name; f.Upload()`; no bytes re-upload. Sibling-collision check via Drive query `'{parent_id}' in parents and title='{escaped}' and trashed=false`. Excludes self from collision check (handles eventual-consistency races). `overwrite=True` trashes colliders before rename; per-collider failures logged at warning rather than aborting.
- **Cross-source dispatch wiring** (P2 — commit `4aa4085`). Both `_execute_one_cross_source` (apply path) and `_execute_one_persistent_cross_source` (worker path) replace v1.3.0 degrade-to-skip in their SKIPPED_COLLISION blocks with full mode-dispatch on `self._on_conflict_mode`. Successful retry produces `MOVED_OVERWROTE_WITH_BACKUP` or `MOVED_RENAMED_WITH_SUFFIX`; degrade paths retain v1.3.0 SKIPPED_COLLISION behavior.
- **8 new helper methods** on `MigrationService` (~520 LOC total in `migration.py`):
  - `_compute_suffix_name(dst_p, n)` — sister of `_find_available_suffix` for the cross-source retry-write loop where existence is probed implicitly via FileExistsError (DM-3, no exists-probe hookspec needed).
  - `_find_existing_dst_file_id_for_overwrite(dst_source_id, dst_path)` — two-strategy resolver: (1) try `curator_source_stat` with dst_path as file_id (works for local-style sources where `file_id == path`); (2) fall back to `curator_source_enumerate(parent_id)` and match by display name (works for cloud sources). No source-type hardcoding.
  - `_attempt_cross_source_backup_rename(dst_source_id, file_id, backup_name)` — calls `pm.hook.curator_source_rename`. Returns `(success, error)`. Plugin-not-implementing OR FileExistsError OR other exception maps to `(False, reason)`; caller degrades to skip.
  - `_cross_source_overwrite_with_backup(move, ...)` (in-memory) + `_cross_source_overwrite_with_backup_for_progress(progress, ...)` (worker) — full rename + retry flow. Per DM-5: if retry fails (transfer exception OR HASH_MISMATCH OR retry-time SKIPPED_COLLISION), the renamed backup is preserved and the error message advertises `[backup at <name> preserved per DM-5]`.
  - `_cross_source_rename_with_suffix(move, ...)` (in-memory) + `_cross_source_rename_with_suffix_for_progress(progress, ...)` (worker) — retry-write loop n=1..9999 using DM-3 implicit FileExistsError existence probe. On rename-with-suffix success in the worker path, `progress.dst_path` is mutated to the suffix variant so the post-transfer entity update + audit_move reference the correct path.
  - `_emit_progress_audit_conflict(progress, mode, details_extra)` — sister of `_audit_conflict` adding `job_id` to audit details for the persistent worker path.
- **`migration.conflict_resolved` audit details extended.** Success paths now emit `mode='overwrite-with-backup'` (with `backup_name` + `existing_file_id` + `cross_source: True`) or `mode='rename-with-suffix'` (with `original_dst` + `renamed_dst` + `suffix_n` + `cross_source: True`). Degrade paths emit `mode='<m>-degraded-cross-source'` with `reason` + `fallback: 'skipped'` + `cross_source: True`. Existing v1.3.0 details (size, src_path, dst_path, mode) preserved.

### Changed

- **`_execute_one_cross_source` (apply path)** — replaced v1.3.0 degrade-to-skip block at L853-895 with mode-dispatch. The four conflict modes now branch as: `skip` keeps SKIPPED_COLLISION (v1.2.0 behavior); `fail` marks FAILED_DUE_TO_CONFLICT (existing v1.3.0 logic); `overwrite-with-backup` calls the new helper and finalizes as MOVED_OVERWROTE_WITH_BACKUP on retry success; `rename-with-suffix` calls the new helper and finalizes as MOVED_RENAMED_WITH_SUFFIX on retry success. The trailing `move.outcome = MigrationOutcome.MOVED` is replaced with `move.outcome = final_outcome` to honor the variant outcome.
- **`_execute_one_persistent_cross_source` (worker path)** — same dispatch shape using `_for_progress` sister helpers. The trailing `return (MigrationOutcome.MOVED, verified_hash)` is replaced with `return (final_outcome, verified_hash)`.

### Tests (+24 new — 444 → 468 in regression slice)

- **`tests/unit/test_curator_source_rename.py`** (NEW, 10 tests — P1):
  - `TestLocalRename` (5): rename to new name same parent; FileInfo includes inode + stat fields; FileExistsError on collision without overwrite; overwrite=True replaces atomically; None returned for non-local source_id (gdrive/onedrive).
  - `TestGdriveRename` (5): title patch via `f['title']=new_name; f.Upload()`; collision raises FileExistsError; self-in-ListFile-results not counted as collision; overwrite=True trashes collider then renames; None returned for non-gdrive source_id. Mocking via `SimpleNamespace` + `_FakeDriveFile(dict)` class tracking FetchMetadata/Upload/Trash via outer dicts.
- **`tests/unit/test_migration_phase4_cross_source_conflict.py`** (NEW, 14 tests — P2):
  - `TestOverwriteWithBackupCrossSource` (4): rename + retry succeeds returns MOVED; audit captures backup_name + cross_source: True; retry exception leaves backup per DM-5; retry HASH_MISMATCH leaves backup per DM-5.
  - `TestOverwriteWithBackupFallback` (2): resolver returns None degrades to skip with audit reason; rename hook returns False degrades to skip with audit reason.
  - `TestRenameWithSuffixCrossSource` (4): first attempt at `.curator-1` succeeds; two collisions then third succeeds (suffix_n=3 in audit); HASH_MISMATCH during retry halts loop with HASH_MISMATCH outcome; transfer exception during retry halts loop with FAILED outcome.
  - `TestRenameWithSuffixFallback` (1): 9999 candidates exhausted degrades to skip with audit reason.
  - `TestComputeSuffixName` (3): basic `foo.mp3 → foo.curator-3.mp3`; no extension `foo → foo.curator-1`; multi-dot `archive.tar.gz → archive.tar.curator-7.gz`.
- **All 24 tests passed first run with zero debugging needed** (lesson 8-for-8 read-code-first holding).

### Backward compatibility (DM-4 strictly additive)

- Plugins not implementing `curator_source_rename` get v1.3.0 degrade-to-skip behavior automatically. Pluggy returns None for plugins that don't implement → `_attempt_cross_source_backup_rename` returns `(False, 'plugin does not implement curator_source_rename')` → caller degrades.
- `skip` and `fail` modes unchanged from v1.3.0.
- Same-source paths unchanged.
- Resume across v1.3.0 → v1.4.0 is safe: v1.3.0 jobs that recorded SKIPPED_COLLISION outcomes for cross-source overwrite/rename modes remain SKIPPED_COLLISION on resume; v1.4.0 only changes behavior for NEW jobs started after upgrade.
- No schema changes. No CLI flag changes. No public API surface removals.

### Out of scope (Phase 5+)

- Separate `curator_source_exists` hookspec (DM-3 chose the FileExistsError retry-write pattern instead).
- Gdrive `curator_source_move` semantics fix (still `NotImplementedError` for parent-id-vs-path ambiguity; Phase Gamma).
- Retry-decorator interaction with rename (rename failure inside `_attempt_cross_source_backup_rename` doesn't go through `@retry_transient_errors`).
- `MigrationConflictError` raised on rename failure (currently degrades to skip; could be a future opt-in).
- Backup file visibility in `MigrationReport` (the renamed backup's path is currently in audit details only, not in report rows).
- MCP HTTP-auth deferred to v1.5.0 per DM-6.

## [1.3.0] — 2026-05-08 — Tracer Phase 3 (retry decorator + conflict resolution)

**Headline:** v1.2.0 → v1.3.0 (minor bump). Closes Tracer Phase 2's two highest-value deferrals: (1) quota-aware retry with exponential backoff for cross-source transient errors, and (2) four-mode destination-collision handling beyond the previous monolithic `SKIPPED_COLLISION` branch. Both are strictly additive at the user-facing surface; defaults preserve v1.2.0 behavior exactly. New CLI flags `--max-retries N` (default 3, capped 10) and `--on-conflict MODE` (`skip`|`fail`|`overwrite-with-backup`|`rename-with-suffix`; default `skip`). Three new `MigrationOutcome` variants (`MOVED_OVERWROTE_WITH_BACKUP`, `MOVED_RENAMED_WITH_SUFFIX`, `FAILED_DUE_TO_CONFLICT`) + new `MigrationConflictError` exception class + new `migration.conflict_resolved` audit action. See `docs/TRACER_PHASE_3_DESIGN.md` v0.3 IMPLEMENTED for the full design and per-DM implementation evidence.

### Added

- **`--max-retries N` CLI flag** (P1 — commit `fe5739f`). Per-job retry budget for transient cloud errors during cross-source migration. Default 3, clamped to `[0, 10]`. `0` disables retry entirely. Resumed jobs inherit the original `max_retries` from `migration_jobs.options_json` unless explicitly overridden.
- **New module `src/curator/services/migration_retry.py`** (148 LOC) with `_is_retryable` helper + `retry_transient_errors` stateless decorator. Retryable error classes: `googleapiclient.errors.HttpError` with status in `(403, 429, 500, 502, 503, 504)`, `requests.exceptions.ConnectionError`, `requests.exceptions.Timeout`, `socket.timeout`/`TimeoutError`, `urllib3.exceptions.ProtocolError`. Fail-fast classes: `OSError`, `HashMismatchError`, `MigrationDestinationNotWritable`, all other Exception subclasses. Backoff is exponential capped at 60 s; `Retry-After` header honored when present (gdrive sometimes provides one with 429s).
- **`@retry_transient_errors` applied to `_cross_source_transfer`**. Same-source local-FS errors are mostly permanent (disk full, permission denied, corruption) and don't benefit from retry per the v0.2 design discussion; only cross-source is decorated.
- **`MigrationService.set_max_retries(n)` method** clamping `n` to `[0, 10]`. Called by `apply()` and `run_job()` to configure the per-job retry budget. **CRITICAL FIX:** the `max_retries` parameter on `apply()` and `run_job()` was previously accepted but ignored — a silent gap caught during code-touchpoint verification at v0.1 issuance. v1.3.0 actually wires it through.
- **`--on-conflict MODE` CLI flag** (P2 — commit `08db2de`). Destination-collision policy. Valid modes: `skip` (default; preserves v1.2.0 behavior), `fail` (raise on first collision; CLI exits with code 1), `overwrite-with-backup` (rename existing dst to `<name>.curator-backup-<UTC-iso8601-compact><ext>` before proceeding), `rename-with-suffix` (migrate to `<name>.curator-N<ext>` for lowest free `N` in `[1, 9999]`). Cross-source migrations support `skip` + `fail` fully; `overwrite-with-backup` and `rename-with-suffix` degrade to skip with a warning + audit (no atomic-rename hook in the source-plugin contract yet; revisit in Phase 4).
- **3 new `MigrationOutcome` variants:** `MOVED_OVERWROTE_WITH_BACKUP`, `MOVED_RENAMED_WITH_SUFFIX`, `FAILED_DUE_TO_CONFLICT`. `MigrationReport.moved_count` + `bytes_moved` span all four MOVED variants via `ClassVar` tuples; `failed_count` picks up `FAILED_DUE_TO_CONFLICT` alongside `FAILED` + `HASH_MISMATCH`.
- **`MigrationConflictError`** exception class (carries `dst_path` + `src_path`). Raised by `apply()` on the first collision when `--on-conflict=fail`. The CLI catches it and renders a clean error message + exits with code 1. The Phase 2 worker loop catches it specifically and maps to `FAILED_DUE_TO_CONFLICT` outcome (distinct from generic `FAILED`).
- **`migration.conflict_resolved` audit action** with mode-specific details: `mode` (one of `skip`/`fail`/`overwrite-with-backup`/`rename-with-suffix`/`<mode>-degraded-cross-source`/`<mode>-failed`); `backup_path` (overwrite mode); `original_dst` + `renamed_dst` + `suffix_n` (rename mode); `cross_source` + `reason` + `fallback` (cross-source degrade); `error` (setup failures). Queryable via the MCP server's `query_audit_log(action='migration.conflict_resolved')`.
- **`MigrationService` helpers** for the conflict surface: `_compute_backup_path` (UTC ISO 8601 compact format, Windows-safe filename), `_find_available_suffix` (lowest free `N` in `[1, 9999]`; raises `RuntimeError` if exhausted), `_audit_conflict`, `_resolve_collision` (for the `MigrationMove` apply path), `_resolve_collision_for_progress` (sister method for the `MigrationProgress` persistent path — progress doesn't carry an in-memory outcome field, so the resolver returns a 4-tuple `(short_circuit, outcome_override, new_dst, conflict_error)` for the worker to act on).
- **Worker-loop conflict handling** (`_worker_loop`): catches `MigrationConflictError` separately from generic `Exception`, mapping it to `FAILED_DUE_TO_CONFLICT` so the report's `failed_count` and audit log queries can distinguish conflict-specific failures. Recognizes the new MOVED variants in the success branch.
- **`run_job()` reads `on_conflict` from `job.options`** for resumed jobs (mirrors the `max_retries` pattern from P1). An invalid persisted value falls back to `skip` with a warning rather than refusing to resume.
- **`MigrationConflictError` exported in `__all__`** alongside `MigrationOutcome`, `MigrationMove`, `MigrationPlan`, `MigrationReport`, `MigrationService`.

### Tests (+30 new — 414 → 444 in regression slice)

- **`tests/unit/test_migration_phase3_retry.py`** (NEW, 15 tests — P1):
  - `TestIsRetryable` (6): `HttpError 429` retryable; `HttpError 200` not retryable; `ConnectionError` retryable; `OSError` fail-fast; `HashMismatchError` fail-fast; arbitrary Exception fail-fast.
  - `TestRetryDecorator` (7): success on first try; recover on retry 1; recover on retry 2; exhausted budget propagates final exception; `max_retries=0` disables retry; `Retry-After` header used when present; backoff capped at 60 s.
  - `TestServiceIntegration` (2): `apply(max_retries=N)` actually calls `set_max_retries(N)`; `run_job` reads `max_retries` from `job.options` for resumed jobs.
- **`tests/unit/test_migration_phase3_conflict.py`** (NEW, 15 tests — P2):
  - `TestSkipMode` (1): default mode preserves v1.2.0 `SKIPPED_COLLISION` exactly.
  - `TestFailMode` (2): first collision raises `MigrationConflictError`; audit emits `migration.conflict_resolved` with `mode='fail'` BEFORE the raise.
  - `TestOverwriteWithBackup` (3): backup path format `<stem>.curator-backup-<iso-utc><ext>`; existing dst renamed to backup, src copied to dst; `report.moved_count` picks up `MOVED_OVERWROTE_WITH_BACKUP`.
  - `TestRenameWithSuffix` (3): `_find_available_suffix` returns `n=1` when no `.curator-N` exists; skips existing `.curator-1` + `.curator-2`, returns `n=3`; move's `dst_path` mutated to `.curator-1.<ext>`, original preserved.
  - `TestAuditConflictDetails` (3): overwrite-with-backup audit contains `backup_path`; rename-with-suffix audit contains `suffix_n` + `renamed_dst` + `original_dst`; fail mode audit emitted before `MigrationConflictError` raise.
  - `TestServiceClamping` (3): unknown mode raises `ValueError`; all 4 valid modes accepted by `set_on_conflict_mode`; default mode is `skip`.

### Changed

- Version `1.2.0` → `1.3.0` (minor bump). New `MigrationOutcome` enum values + new `MigrationConflictError` class + new audit action are new public surface; minor is honest. Per DM-6 of `docs/TRACER_PHASE_3_DESIGN.md`.
- `pyproject.toml` and `__init__.py` `__version__` reflect 1.3.0.
- `MigrationService.__init__` now seeds `_max_retries=3`, `_retry_backoff_cap=60.0`, and `_on_conflict_mode='skip'` instance attrs (the mutable per-job state the new methods configure).
- `MigrationReport.moved_count` / `failed_count` / `bytes_moved` now use `ClassVar` tuples (`_MOVED_VARIANTS`, `_FAILED_VARIANTS`) for inclusion sets, replacing the inline tuple literals. The change is internal; the reported counts remain correct for both old (v1.2.0) and new (v1.3.0) outcome enum values.

### Backward compatibility

- **Strictly additive.** All existing `curator migrate ... --apply` invocations behave identically when `--max-retries` and `--on-conflict` are unspecified. `--max-retries=3` is a behavior change in the failure path — cross-source transient errors that previously caused immediate `FAILED` may now succeed after retry. No successful-migration outcome changes.
- **Existing `MigrationOutcome` consumers** see new enum values they didn't previously enumerate. Code that switch-cases on outcome values needs to handle the new variants OR fall through to a default. The GUI Migrate tab and `MigrationReport.moved_count` / `failed_count` properties already handle this via the `ClassVar` tuples.
- **Existing audit log readers** see new action strings. Code filtering by exact action match (`action='migration.move'`) is unaffected. Code wildcard-matching `migration.*` sees the new `migration.retry` (when added in a future release) and `migration.conflict_resolved` events; this is the intended behavior.
- **`migration_jobs` and `migration_progress` schemas: unchanged.** `options_json` accommodates the new `max_retries` and `on_conflict` keys via Phase 2's forward-compat design.
- **Resume across v1.2.0 → v1.3.0:** A user who initiated a job on v1.2.0, killed the process, upgraded to v1.3.0, and runs `--resume` gets v1.3.0 default behavior (`max_retries=3`, `on_conflict='skip'`) for the remainder of the job. No partial-migration corruption — the per-file algorithm is idempotent up to `mark_completed`.
- **Cross-source plugin contract:** unchanged. Plugins don't need to know about retry — the decorator wraps caller-side. Cross-source `overwrite-with-backup` and `rename-with-suffix` degrading to skip is a runtime behavior, not a contract change.
- **Existing plugins (`local_source`, `gdrive_source`, `classify_filetype`, `lineage_*`, `curatorplug-atrium-safety` v0.3.0):** unchanged. Plugin suite 75/75 still passing.
- **DB schema:** unchanged.

### Lessons (now 6-for-6 read-code-first applications)

| # | Design phase | Caught BEFORE coding |
|---|---|---|
| 1 | atrium-reversibility | `CleanupService.purge_trash` doesn't exist (deferred at v0.3) |
| 2 | MCP P2 | 6 of 6 method-signature mismatches |
| 3 | Tracer Phase 3 v0.1 | retry was claimed but never shipped (Phase 2 silent gap) |
| 4 | Tracer Phase 3 v0.2 | all code-touchpoint claims in §4.4 verified |
| 5 | Tracer Phase 3 P1 | 2 deviations from §4.4 documented + silent CLI flag bug fixed |
| 6 | Tracer Phase 3 P2 | 3 collision sites + 3 downstream readers + `MigrationProgress` vs `MigrationMove` field divergence; raise-before-append bug caught during self-review |

### Phase 4+ deferrals

- Proactive bandwidth throttling beyond reactive retry-on-quota.
- Per-source retry policy (different `--max-retries` per leg of a multi-source job).
- `curator migrate-cleanup-backups <job_id>` utility for trashing accumulated `.curator-backup-*` files older than N days.
- Retry observability (per-job retry distribution, longest backoff seen).
- Async retry refactor for very long backoffs across many failed files.
- **Cross-source `overwrite-with-backup` + `rename-with-suffix`** — requires expanding the source-plugin hookspec with an atomic-rename hook (`curator_source_rename`) or exists-probe hook (`curator_source_exists`). Current Phase 3 P2 degrades these modes to skip for cross-source with a documented warning + audit.

### Cross-references

- `docs/TRACER_PHASE_3_DESIGN.md` v0.3 IMPLEMENTED — the design this implements; §12 revision log has the v0.3 entry with both commit hashes (`fe5739f` for P1, `08db2de` for P2) and the 6-for-6 lessons table.
- `docs/TRACER_PHASE_2_DESIGN.md` v0.3 IMPLEMENTED — the v1.1.0 stable foundation Phase 3 built on. §11 listed the deferrals; items 1 + 2 are now closed.
- `docs/CURATOR_MCP_SERVER_DESIGN.md` v0.3 IMPLEMENTED — version-line collision (originally claimed v1.3.0 for HTTP-auth) resolved by Tracer Phase 3 DM-6: Phase 3 claims v1.3.0, MCP HTTP-auth pushed to v1.4.0.
- `curatorplug-atrium-safety` v0.3.0 — plugin suite still 75/75 against v1.3.0; no plugin-side changes were needed for Phase 3.
- The headline LLM-client use case: query the audit log for conflict resolutions via the MCP server. `query_audit_log(action='migration.conflict_resolved')` returns the structured details (mode + paths + suffix_n / backup_path / cross_source flag) for every conflict the migration engine resolved.

## [1.2.0] — 2026-05-08 — MCP server (P1: scaffolding + 3 read-only tools)

**Headline:** v1.1.3 → v1.2.0 (minor bump). Adds an optional `[mcp]` extra that exposes a Model Context Protocol server (`curator-mcp`) for LLM clients (Claude Desktop, Claude Code, third-party MCP-aware agents). Speaks stdio by default; HTTP transport opt-in via `--http`. v1.2.0 ships P1 of the 3-session implementation plan: scaffolding + the first 3 read-only tools end-to-end functional. Remaining 6 tools land in P2 (next session). See `docs/CURATOR_MCP_SERVER_DESIGN.md` v0.2 RATIFIED for the design.

### Added

- **New optional extra `[mcp]`** in `pyproject.toml`. Pulls in `mcp>=1.20` (the Anthropic Python SDK + bundled FastMCP framework). Users not opting in pay zero cost; install via `pip install curator[mcp]`.
- **New console script `curator-mcp`** in `[project.scripts]`, mapped to `curator.mcp:main`. Launches the MCP server.
- **New module `src/curator/mcp/`** with three files:
  - `__init__.py` — exposes `main` and `create_server` as the public API.
  - `server.py` — FastMCP construction + transport selection (stdio default; `--http`, `--port`, `--host` flags for HTTP). Defensive: refuses to bind HTTP to non-loopback addresses without auth.
  - `tools.py` — Pydantic return models (`HealthStatus`, `SourceInfo`, `AuditEvent`) + `register_tools(mcp, runtime)` factory + 3 implemented v1.2.0 tools.
- **3 read-only MCP tools (P1)** — the first slice of the 9 designed in `docs/CURATOR_MCP_SERVER_DESIGN.md` v0.2 §4.3:
  - `health_check` — server / DB / plugin sanity check. Returns 'ok' if DB reachable AND plugin_count > 0.
  - `list_sources` — lists every configured Curator source (enabled and disabled).
  - `query_audit_log` — filtered query against the audit log. Supports `actor`, `action`, `entity_id`, `since`, `limit` (capped at 1000). The headline use case: an LLM client can ask "what did atrium-safety refuse last week?" and get structured data via `actor='curatorplug.atrium_safety', action='compliance.refused'`.
- **Tools.py module-level documentation** of the 6 P2 stubs (`query_files`, `inspect_file`, `get_lineage`, `find_duplicates`, `list_trashed`, `get_migration_status`) with the implementation pattern P2 should follow. Adding a P2 tool prematurely is flagged as a regression.
- **closure-based tool factory** (`register_tools(mcp, runtime)`) so tools bind to the runtime via closures — enables multiple servers with different runtimes to coexist (one per test case using a tmp DB).

### Tests (+23 new — 357 → 380 in regression slice)

- **`tests/unit/mcp/test_tools.py`** (NEW, 14 tests):
  - `TestServerRegistration` (2): exactly 3 tools registered (regression guard against accidental P2 tool registration); each tool has a non-trivial description.
  - `TestHealthCheck` (1): returns 'ok' status with default plugins + reachable DB; exposes curator_version + plugin_count + db_path.
  - `TestListSources` (3): empty for fresh runtime; returns inserted sources with all fields; includes disabled sources.
  - `TestQueryAuditLog` (8): empty for empty log; returns inserted events; filters by actor / action / entity_id; limit caps results; limit > 1000 capped silently; the headline atrium-safety use case (`actor='curatorplug.atrium_safety', action='compliance.refused'`) returns structured data.
- **`tests/integration/mcp/test_stdio.py`** (NEW, 9 tests):
  - `TestScriptEntryPoint` (6): subprocess `python -m curator.mcp.server --help` exits zero; help text describes the server and lists `--http`, `--port`, `--host` flags; invalid args exit nonzero.
  - `TestImportPath` (3): `from curator.mcp import ...` works in subprocess + in-process; `main` and `create_server` are callable.
  - **Note:** Full subprocess-based MCP protocol roundtrip (initialize → tools/list → tools/call) is deferred to P2. The unit tests already exercise `call_tool` through the same FastMCP code path the stdio server uses.

### Changed

- Version `1.1.3` → `1.2.0` (minor bump). New public surface (`curator-mcp` script + `curator.mcp` module + `[mcp]` extra) is meaningful enough to deserve a minor; patch would be dishonest. Per DM-6 of `docs/CURATOR_MCP_SERVER_DESIGN.md`.
- `pyproject.toml` and `__init__.py` `__version__` reflect 1.2.0.
- `[mcp]` added to the `all` aggregate extra so `pip install curator[all]` includes it.

### Backward compatibility

- **Strictly additive.** `pip install curator` (without `[mcp]`) is unaffected — no new mandatory deps, no new mandatory imports, no behavior change for users who don't opt in.
- **Existing CLI commands — unaffected.** The 13 existing `curator <subcommand>` calls work identically. `curator-mcp` is a separate console script (separate binary), not a `curator` subcommand.
- **Existing plugins — unaffected.** atrium-safety v0.3.0 + the three core hookspecs (v1.1.1 / v1.1.2 / v1.1.3) all continue to work. The MCP server reads from the audit log those plugins write to.
- **DB schema — unchanged.**
- **Config schema — unchanged.**
- **`CuratorRuntime` API — unchanged.** The MCP server consumes it as-is.

### What's deferred to P2

- 6 read-only tools: `query_files`, `inspect_file`, `get_lineage`, `find_duplicates`, `list_trashed`, `get_migration_status`. Each is documented in `docs/CURATOR_MCP_SERVER_DESIGN.md` v0.2 §4.3 with input schema + return shape. P2 implements following the same pattern as P1 (Pydantic return model with LLM-targeted docs + `@mcp.tool()`-decorated function + ~3-4 unit tests per tool).
- Full subprocess-based MCP protocol roundtrip test.

### What's deferred to P3

- README "MCP server (v1.2.0+)" section.
- Design doc v0.2 → v0.3 IMPLEMENTED stamp.
- End-to-end Claude Desktop demo notes.

### Cross-references

- `docs/CURATOR_MCP_SERVER_DESIGN.md` v0.2 RATIFIED — the design this implements.
- `docs/CURATOR_AUDIT_EVENT_HOOKSPEC_DESIGN.md` v0.3 IMPLEMENTED — `query_audit_log` reads from the channel that design established. Plug atrium-safety v0.3.0 into Curator v1.2.0 and an LLM client gets queryable enforcement-decision history out of the box.
- `curatorplug-atrium-safety` v0.3.0 — the headline consumer of `query_audit_log`. Run a migration with the safety plugin in strict mode, then query: `query_audit_log(actor='curatorplug.atrium_safety', action='compliance.refused')`.

## [1.1.3] — 2026-05-08 — `curator_audit_event` plugin hookspec (audit channel)

**Headline:** v1.1.2 → v1.1.3 (patch bump). Adds the `curator_audit_event` plugin hookspec and a core `AuditWriterPlugin` that persists plugin-emitted events to `AuditRepository`. Closes the audit-channel gap that `curatorplug-atrium-safety/DESIGN.md` v0.3 §9 named as out-of-scope: plugins can now write structured audit entries instead of (or alongside) `loguru` logging. Strictly additive; existing plugins and `MigrationService`'s direct-to-repo path are unaffected.

### Added

- **`curator_audit_event(actor, action, entity_type, entity_id, details)` hookspec** in `src/curator/plugins/hookspecs.py` under a new "Audit channel (v1.1.3+)" section. Field-based signature (per DM-1 RATIFIED): plugins call without importing `AuditEntry` from `curator.models.audit`. Pluggy's default `firstresult=False` applies; all hookimpls fire.
- **`AuditWriterPlugin`** in `src/curator/plugins/core/audit_writer.py` (NEW file). Implements `curator_audit_event` hookimpl: constructs an `AuditEntry` from the field args and inserts via `AuditRepository.insert`. Uses a placeholder pattern — registered by `register_core_plugins` with `audit_repo=None`, then `build_runtime` injects the real repo via `set_audit_repo` after construction. Events fired before injection (e.g., from a plugin's `curator_plugin_init` hookimpl) log at debug level and drop, consistent with DM-4's best-effort semantics.
- **Wiring in `register_core_plugins`** (`src/curator/plugins/core/__init__.py`): registers `AuditWriterPlugin` as `curator.core.audit_writer` alongside the other six core plugins.
- **Wiring in `build_runtime`** (`src/curator/cli/runtime.py`): after `audit_repo` construction, calls `pm.get_plugin("curator.core.audit_writer").set_audit_repo(audit_repo)` to enable persistence.

### Tests (+9 new — 348 → 357 in regression slice)

- **`tests/unit/test_audit_writer.py`** (NEW, 9 tests):
  - `TestAuditWriterPluginDirect` (4): hookimpl persists valid entry to a real repo; hookimpl swallows DB errors when insert raises (DM-4 best-effort); hookimpl drops events with debug log when audit_repo is None (placeholder pattern); `set_audit_repo` enables persistence for subsequent events.
  - `TestAuditEventHookspecAfterBuildRuntime` (3): hookspec is reachable via `pm.hook.curator_audit_event(...)` after `build_runtime`; the AuditWriterPlugin core plugin is registered AND has its repo injected; firing an event via `pm.hook` actually persists to `runtime.audit_repo`.
  - `TestExistingDirectAuditWritesStillWork` (2): regression guard for DM-3 — `audit_repo.insert(entry)` (the path `MigrationService` uses) still works unchanged; both write paths (direct insert + via hookspec) write to the same table and are queryable together.

### Changed

- Version `1.1.2` → `1.1.3` (patch). Strictly additive; no behavior change for users who don't have plugins firing the hook.
- `pyproject.toml` and `__init__.py` `__version__` reflect 1.1.3.

### Backward compatibility

- **Strictly additive.** Existing plugins (`local_source`, `gdrive_source`, `classify_filetype`, `lineage_*`, `curatorplug-atrium-safety` v0.1.0/v0.2.0) work unchanged. They don't fire `curator_audit_event` so the new path is invisible to them.
- **MigrationService unchanged.** Per DM-3 RATIFIED, `MigrationService._audit_move` and `_audit_copy` continue using direct-to-repo writes. The new hookspec is purely for plugin-driven events. Migration to the hookspec is a future-release decision.
- **Existing CLI invocations identical.** `curator audit-log query`, `curator scan`, etc. unchanged.
- **No schema change.** Reuses existing `migration_audit` table; `actor` field already accepts arbitrary strings; `details_json` is freeform.

### Cross-references

- `docs/CURATOR_AUDIT_EVENT_HOOKSPEC_DESIGN.md` v0.2 RATIFIED (commit ed... after this lands, will become v0.3 IMPLEMENTED in P3).
- `curatorplug-atrium-safety/DESIGN.md` v0.3 §9 — the design doc that explicitly named this gap as the natural follow-on.
- `curatorplug-atrium-safety` v0.3.0 (pending P2 of this plan) — the canonical consumer; will replace `loguru.warning` calls with structured `compliance.approved` / `compliance.refused` / `compliance.warned` audit events.

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
