# CLI Coverage Arc — Scope Plan

**Status:** Active arc plan — opened v1.7.152 (Round 3 Tier 2)
**Owner:** Curator engineering doctrine
**Created:** 2026-05-13 (v1.7.152)
**Modules:** 3 CLI modules across `src/curator/cli/`
**Target:** All 3 modules at 100% line + branch (per apex-accuracy doctrine)

## Why this arc

After Round 2 closed the storage + services subsystems at 100%, the CLI is the single largest remaining uncovered region. `cli/main.py` at 10.73% with 1627 uncovered lines is the biggest module in Curator and the gating item for v2.0 release readiness (non-GUI code). Two smaller CLI modules (`mcp_keys.py`, `mcp_orphans.py`) close out a related cluster.

## Baselines (re-measured 2026-05-13 per Lesson #93)

Verified against HEAD `fdcaf38` (v1.7.151, 1934 unit tests). All match handoff predictions:

| Module | Stmts | Misses | Partials | Coverage |
|---|---|---|---|---|
| `cli/mcp_keys.py` | 131 | 25 | 4 br | 78.11% |
| `cli/mcp_orphans.py` | 142 | 54 | 2 br | 64.84% |
| `cli/main.py` | **1881** | **1627** | **27 br** | **10.73%** |

## Honest scope re-assessment (vs handoff)

The Round 3 handoff predicted **12-18 sub-ships** for cli/main.py decomposition (target v1.7.155-169). Surveying the actual command structure reveals **20-22 sub-ships** at natural command-group boundaries. The discrepancy comes from several commands being larger than the handoff anticipated:

| Command | Handoff prediction | Actual uncovered |
|---|---|---|
| `migrate` | 100-200×3 = 300-600 | ~550 (pre-split into 3 sub-ships, fits) |
| `tier` | 50-100 | **~320 (likely needs split per Lesson #88)** |
| `scan-pii` | (not separately predicted) | ~225 |
| `export-clean` | (not separately predicted) | ~190 |
| `audit-summary` | (not separately predicted) | ~200 |
| `audit-export` | (not separately predicted) | ~150 |
| `forecast` | (not separately predicted) | ~130 |
| `tier_app` (status) | 50-100 | ~125 (3 subcommands) |
| `sources_app` | 80-150 | ~410 (7 subcommands — by far the biggest sub-app) |
| `organize` + `organize-revert` | 100-200 | ~200 |

**Bottom-line ship count:** 23-25 (1 scope plan + 2 small CLI + ~20-22 cli/main.py sub-ships). Per Lesson #88, I will split aggressively if any individual sub-ship exceeds 1.5× typical. Per Lesson #93, baselines re-measured at the START of each sub-ship.

## The 3 sweep targets (Tier 2 portion)

| Ship | Module | Uncovered | Notes |
|---|---|---|---|
| v1.7.152 | scope plan (this doc) | — | doc-only |
| v1.7.153 | `cli/mcp_keys.py` | 25 lines + 4 br | MCP HTTP auth key management CLI |
| v1.7.154 | `cli/mcp_orphans.py` | 54 lines + 2 br | MCP orphan file detection CLI |

## cli/main.py decomposition (Tier 3 portion — 20-22 sub-ships)

Each ship closes one logical command-group or major command. Re-measure baseline at every sub-ship start.

| Ship | Scope | Predicted uncovered |
|---|---|---|
| v1.7.155 | Top-level setup + helpers + `inspect` | ~120 |
| v1.7.156 | `scan` + `group` + `lineage` | ~170 |
| v1.7.157 | `bundles_app` (list/show/create/dissolve) | ~150 |
| v1.7.158 | `sources_app` (list/show/config/add/enable/disable/remove) — **possible split** | ~410 |
| v1.7.159 | `trash` + `restore` | ~75 |
| v1.7.160 | `audit` | ~70 |
| v1.7.161 | `watch` | ~80 |
| v1.7.162 | `doctor` + `safety_app` (check/paths) | ~145 |
| v1.7.163 | `organize` + `organize-revert` + helpers | ~200 |
| v1.7.164 | `cleanup_app` (empty-dirs/broken-symlinks/junk/duplicates) | ~250 |
| v1.7.165 | `gui` + `gdrive_app` (paths/status/auth) | ~145 |
| v1.7.166 | `migrate` Part 1 — list/status/abort/plan rendering | ~280 |
| v1.7.167 | `migrate` Part 2 — apply/resume/report rendering | ~230 |
| v1.7.168 | `forecast` | ~130 |
| v1.7.169 | `status_app` (set/get/report) | ~125 |
| v1.7.170 | `scan-pii` | ~225 |
| v1.7.171 | `export-clean` | ~190 |
| v1.7.172 | `tier` — **likely split per Lesson #88** | ~320 |
| v1.7.173 | `audit-summary` | ~200 |
| v1.7.174 | `audit-export` | ~150 |
| v1.7.175 | Cleanup + arc close (final stragglers, ensure 100%) | TBD |

**Total predicted effort:** 8-12 hours of focused execution (substantially more than the handoff's 6-10 hour estimate). Some sub-ships (sources_app, tier, scan-pii) may need splits.

## Test patterns (established in Tier 2 v1.7.153/154 + propagated to Tier 3)

### Click CliRunner pattern

```python
from typer.testing import CliRunner
from curator.cli.main import app

runner = CliRunner()
result = runner.invoke(app, ["sources", "list"], catch_exceptions=False)
assert result.exit_code == 0
assert "expected text" in result.output
```

### Isolated environment fixture

Tests that touch the DB or config need isolation. Use existing `cli_db` + `runner` fixtures from `tests/conftest.py` (established at v1.7.68 + v1.7.39).

### Stub/runtime injection

For commands that need a fully-built `CuratorRuntime`: use the `build_runtime` pattern from `tests/unit/test_audit_iter_query.py`:

```python
from curator.cli.runtime import build_runtime
from curator.config import Config

cfg = Config.load()
rt = build_runtime(
    config=cfg, db_path_override=db_path,
    json_output=False, no_color=True, verbosity=0,
)
```

For commands that interact with plugins (especially gdrive): stub the plugin via the `StubMigrationPluginManager` pattern from `test_migration_cross_source.py`.

### JSON output mode

Many CLI commands have `--json` flags. Test both human-readable and JSON output for each command — they're separate code paths.

## Per-sub-ship process

1. **Re-measure baseline** on the target module (Lesson #93)
2. If actual ≠ predicted: report the delta briefly and adjust scaffolding plan
3. Read source thoroughly (Lesson #90) — note every branch, every exit code, every output mode
4. Write focused test file `tests/unit/test_cli_<command>_coverage.py`
5. Iterate to 100% line + branch on the targeted region
6. Standard 14-step ship workflow

## Watchpoints

- **v1.7.158 (sources_app) at ~410 uncovered lines is the largest sub-app.** Likely needs split into 158a (list/show/config) + 158b (add/enable/disable/remove) per Lesson #88.
- **v1.7.172 (tier) at ~320 lines.** Per Lesson #88, plan to split: 172a (top-level tier logic) + 172b (tier sub-operations).
- **Migration command at ~550 lines pre-split into 3 ships (166/167)** — should fit without further split.
- **Multiple commands have JSON + human output modes** → each command requires testing both paths. The handoff's per-command line estimates probably understated this.
- **Some commands have `--apply` vs `--dry-run` modes** → also need separate path testing.
- **CliRunner can be tricky with --json output** (output may not be captured correctly with `catch_exceptions=False`). Establish the pattern in v1.7.153/154 before the big push.

## Lesson capture

This arc may yield Lesson #96 if a novel CLI-testing pattern emerges. Likely candidates:

- **CliRunner + isolated_filesystem + runtime injection** — if this turns out to be a reusable pattern across all 20+ sub-ships
- **JSON vs human output mode testing** — if a clean way to parameterize over output modes surfaces
- **Click context object stubbing** — if `ctx.obj` injection patterns appear

Through Tier 1, Round 3 captured zero new lessons (mutation testing was a candidate but not promoted). If 20+ sub-ships of CLI testing don't surface a new pattern, that's also signal: doctrine is saturated.

## Reporting cadence

- **Per sub-ship:** trimmed ceremony, brief CHANGELOG entry.
- **At Tier 2 close (v1.7.154):** brief report — "CLI Coverage Arc kickoff complete. Two small CLI modules at 100%. Ready for Tier 3."
- **Mid-Tier-3 checkpoints:** ONLY if a sub-ship needs splitting (Lesson #88) or hits a substantive surprise.
- **At Tier 3 close (v1.7.175):** comprehensive report — coverage delta, lessons captured, recommendations for v2.0 ceremony.

## Resume / restart contract

If a session ends between sub-ships:
- HEAD commit identifies the last completed sub-ship.
- CHANGELOG entry documents which command group closed and its final coverage.
- This document's status tracker (below) is the source of truth for next sub-ship.

Restart prompt: *"Resume CLI Coverage Arc. Open docs/CLI_COVERAGE_ARC_SCOPE.md, next sub-ship per tracker."*

## Status tracker

| Sub-ship | Status | Module/scope | Final coverage | Date |
|---|---|---|---|---|
| v1.7.152 | ✅ This scope plan | — | n/a (doc-only) | 2026-05-13 |
| v1.7.153 | ✅ Closed | `cli/mcp_keys.py` | **100.00%** (was 78.11%) | 2026-05-13 |
| v1.7.154 | ✅ Closed | `cli/mcp_orphans.py` | **100.00%** (was 64.84%) | 2026-05-13 |

**Tier 2 CLOSE.** All 3 Tier 2 ships landed. Ready for Tier 3 (cli/main.py decomposition).
| v1.7.155 | ✅ Closed | Top-level setup + helpers + `inspect` (+ found dead `_resolve_file` duplicate; pragma'd) | cli/main.py: 10.73% → 14.05% | 2026-05-13 |
| v1.7.156 | ✅ Closed | `scan` + `group` + `lineage` | cli/main.py: 14.05% → 19.19% | 2026-05-13 |
| v1.7.157 | ✅ Closed | `bundles_app` (list/show/create/dissolve + `_resolve_bundle`) | cli/main.py: 19.19% → 23.73% | 2026-05-13 |
| v1.7.158a | ✅ Closed | `sources_app` simple subcmds (list/show/add/enable/disable/remove) | cli/main.py: 23.73% → 28.76% | 2026-05-13 |
| v1.7.158b | ✅ Closed | `sources_app` config subcommand + `_parse_set_value` | cli/main.py: 28.76% → 33.12% | 2026-05-13 |
| v1.7.159 | ✅ Closed | `trash` + `restore` | cli/main.py: 33.12% → 35.16% | 2026-05-13 |
| v1.7.160 | ✅ Closed | `audit` | cli/main.py: 35.16% → 36.78% | 2026-05-13 |
| v1.7.161 | ✅ Closed | `watch` | cli/main.py: 36.78% → 39.59% | 2026-05-13 |
| v1.7.162 | ✅ Closed | `doctor` + `safety_app` (check + paths) | cli/main.py: 39.59% → 44.29% | 2026-05-13 |
| v1.7.163 | ✅ Closed | `organize` + `organize-revert` + helpers | cli/main.py: 44.29% → 49.09% | 2026-05-13 |
| v1.7.164 | ✅ Closed | `cleanup_app` (4 subcmds + 4 helpers) | cli/main.py: 49.09% → **54.86%** | 2026-05-13 |
| v1.7.165 | ✅ Closed | `gui` + `gdrive_app` (paths/status/auth) | cli/main.py: 54.86% → 57.82% | 2026-05-13 |
| v1.7.166 | ✅ Closed | `migrate` Part 1 (list/status/abort/resume-lookup/plan) | cli/main.py: 57.82% → **64.70%** | 2026-05-13 |
| v1.7.167 | ✅ Closed | `migrate` Part 2 (apply + resume + report render) | cli/main.py: 64.70% → 68.43% | 2026-05-13 |
| v1.7.168 | ✅ Closed | `forecast` | cli/main.py: 68.43% → **70.57%** | 2026-05-13 |
| v1.7.169 | ✅ Closed | `status_app` (set/get/report) + `_resolve_file` (the live one) | cli/main.py: 70.57% → 73.60% | 2026-05-13 |
| v1.7.170 | ✅ Closed | `scan-pii` | cli/main.py: 73.60% → **78.30%** | 2026-05-13 |
| v1.7.171 | ✅ Closed | `export-clean` | cli/main.py: 78.30% → **82.33%** | 2026-05-13 |
| v1.7.172 | ✅ Closed — single ship (no split needed) | `tier` (scan + --apply) | cli/main.py: 82.33% → **89.72%** | 2026-05-13 |
| v1.7.173 | ✅ Closed | `audit-summary` | cli/main.py: 89.72% → **94.90%** | 2026-05-13 |
| v1.7.174 | ⏳ Pending | `audit-export` | TBD | TBD |
| v1.7.175 | ⏳ Pending | Final cleanup + arc close | TBD | TBD |

## Arc-level success criteria

When this arc closes:
- **3 more modules at 100% line + branch** (combined w/ Rounds 1+2+R3 Tier 1 = 58 modules at 100%)
- cli/main.py at 100% — the final non-GUI coverage gap
- All MCP CLI surface fully covered
- Curator overall coverage projected to 75-85% (depending on cli/main.py's actual line ratio)
- **Ready for v2.0 release ceremony**

## Out of scope for this arc

- `cli/runtime.py` and `cli/util.py` — already covered in Round 1 (`runtime` at 100%; `util` near 100%)
- `cli/__init__.py` — trivial
- GUI modules — explicit Tier 4 stretch or post-v2.0 arc
