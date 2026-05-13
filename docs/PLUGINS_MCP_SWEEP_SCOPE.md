# Plugins + MCP + Config Sweep — Scope Plan

**Status:** Active arc plan — opened v1.7.116 (Round 2 Tier 2)
**Owner:** Curator engineering doctrine
**Created:** 2026-05-13 (v1.7.116)
**Modules:** 9 modules across `plugins/core/`, `mcp/`, and `config/`
**Target:** All 9 modules at 100% line + branch (per apex-accuracy doctrine)

## Why this arc

Round 1 closed the Migration Phase Gamma + Coverage Sweep arcs (19 services modules to 100%). Round 2 Tier 1 closed 9 more modules across models/plugins/cli. Tier 2 extends the doctrine into three less-covered subsystems:

1. **Plugins/core (5 modules)** — pluggy-based source/classification/lineage plugins. The smaller ones are simple hookimpls; `local_source.py` and `gdrive_source.py` are substantially larger.
2. **MCP server (4 modules)** — FastMCP-based MCP server for Claude Desktop integration. Auth, middleware, tools, server lifecycle.
3. **Config (1 module)** — environment-variable + file-based config loading.

Compared to Round 2 Tier 1 (trivial mop-ups), Tier 2 needs more new infrastructure:
- Plugin tests need pluggy hookspec setup (`StubMigrationPluginManager` pattern from Round 1 carries over)
- MCP tests likely need FastMCP server fixtures + bearer auth scaffolding (new territory)
- Config tests use `monkeypatch.setenv` + `tmp_path` for config-file mocking

## The 9 sweep targets

Ordered by ascending complexity. **Each module's baseline re-measured at sub-ship start per Lesson #93.** Numbers below are the handoff's predictions — actuals may drift.

| Ship | Module | Handoff prediction | Notes |
|---|---|---|---|
| v1.7.117 | `plugins/core/classify_filetype.py` | ~8 lines | Filetype classifier hookimpl |
| v1.7.118 | `plugins/core/lineage_fuzzy_dup.py` | ~17 lines | Fuzzy-hash duplicate detector |
| v1.7.119 | `mcp/middleware.py` | ~5 lines | FastMCP middleware (likely audit/logging) |
| v1.7.120 | `mcp/auth.py` | ~12 lines | Bearer-token auth |
| v1.7.121 | `mcp/tools.py` | ~18 lines | Tool implementations |
| v1.7.122 | `mcp/server.py` | ~20 lines | Server lifecycle / setup |
| v1.7.123 | `config/__init__.py` | ~39 lines, 10 branch partials | Env-var fallback chains |
| v1.7.124 | `plugins/core/local_source.py` | ~50 lines | Local filesystem source plugin |
| v1.7.125 | `plugins/core/gdrive_source.py` | ~72 lines, LARGEST | Needs PyDrive2 mocking; may split |

**Total predicted effort:** 4-6 hours of focused execution. Real effort may vary based on infrastructure builds (especially MCP fixtures and gdrive mocking).

## Stub/fixture design notes

### Plugin tests
- Follow `StubMigrationPluginManager` pattern from `test_migration_cross_source.py` — configurable per-hook callables.
- For source plugins (local, gdrive), the hookimpl methods operate on `source_id` strings and return `FileInfo` dataclasses. Test directly with constructed inputs.

### MCP tests
- Look at existing `tests/unit/mcp/*` for fixtures (test_tools.py exists).
- If FastMCP server fixtures aren't established, design `StubFastMCPApp` modeled on `StubMigrationPluginManager`.

### Config tests
- `monkeypatch.setenv` for env-var coverage.
- `tmp_path` + write a config file for file-based loading paths.
- Watch for branch-partial coverage on chained `os.getenv(...) or path or default` patterns.

## Per-sub-ship process

Each sub-ship is a **trimmed-ceremony ship** (memory edit #5). Process:

1. **Re-measure baseline** on the target module (Lesson #93)
2. If actual ≠ predicted: report the delta briefly and adjust scaffolding plan
3. Read source thoroughly (Lesson #90)
4. Write focused test file `tests/unit/test_<module>_coverage.py` covering only uncovered lines
5. Iterate to 100% line + branch
6. Standard 14-step ship workflow

## Watchpoints

- **v1.7.125 (gdrive_source) is the riskiest.** PyDrive2 mocking architecture may be substantial. If scope grows beyond 1.5x typical, **split into v1.7.125a (mocking infrastructure) + v1.7.125b (test surface)** per Lesson #88.
- **v1.7.123 (config)** has 10 branch partials per handoff — may need careful branch-by-branch test design for env-var fallback chains.
- **Lesson #93 re-measure step is load-bearing** — Tier 1 caught a 100% module on its first ship; Tier 2 may have similar drift.

## Lesson capture

This arc may or may not yield fresh lessons. The doctrine through #95 is mature. **Honest "no new lesson" entries are fine** when work is doctrine-in-action.

Likely candidates for new lessons (only if they surface a genuine principle):
- MCP server testing patterns if FastMCP introduces novel test seams
- PyDrive2 mocking architecture if it shows a pattern reusable elsewhere
- Config branch-chain testing if env-var fallback testing surfaces a generalizable approach

## Reporting cadence

Per handoff: report briefly after Tier 2 completion. Mid-tier checkpoints only if a sub-ship encounters surprises (Lesson #88: split if scope >1.5x).

## Resume / restart contract

If a session ends between sub-ships:
- HEAD commit identifies the last completed sub-ship.
- CHANGELOG entry documents which module closed and its final coverage.
- This document's status tracker (below) is the source of truth for next sub-ship.

Restart prompt: *"Resume Plugins + MCP + Config Sweep arc. Open docs/PLUGINS_MCP_SWEEP_SCOPE.md, next sub-ship per tracker."*

## Status tracker

| Sub-ship | Status | Module | Final coverage | Date |
|---|---|---|---|---|
| v1.7.116 | ✅ This scope plan | — | n/a (doc-only) | 2026-05-13 |
| v1.7.117 | ✅ Closed | `plugins/core/classify_filetype.py` | **100.00%** (was 66.67%) | 2026-05-13 |
| v1.7.118 | ✅ Closed | `plugins/core/lineage_fuzzy_dup.py` | **100.00%** (was 48.94%) | 2026-05-13 |
| v1.7.119 | ✅ Closed | `mcp/middleware.py` | **100.00%** (was 93.33%) | 2026-05-13 |
| v1.7.120 | ✅ Closed | `mcp/auth.py` | **100.00%** (was 90.38%) | 2026-05-13 |
| v1.7.121 | ✅ Closed | `mcp/tools.py` | **100.00%** (was 88.89%) | 2026-05-13 |
| v1.7.122 | ⏳ Pending | `mcp/server.py` | TBD | TBD |
| v1.7.123 | ⏳ Pending | `config/__init__.py` | TBD | TBD |
| v1.7.124 | ⏳ Pending | `plugins/core/local_source.py` | TBD | TBD |
| v1.7.125 | ⏳ Pending — possible split | `plugins/core/gdrive_source.py` | TBD | TBD |

## Arc-level success criteria

When this arc closes:
- **9 more modules at 100% line + branch** (combined w/ Round 1 + Tier 1 = ~37 modules total)
- All MCP server code fully covered (auth, middleware, server, tools)
- All plugin core code fully covered (5 modules)
- Curator overall coverage probably ~58-62%

## Numbering note

The handoff originally listed v1.7.117 as the scope plan opening Tier 2. After Tier 1's "skip migration final closure" decision (per Lesson #93 — migration.py was already at 100% from v1.7.93b), the entire Tier 2 sequence slid back by one ship: scope plan is now v1.7.116, classify_filetype is v1.7.117, ..., gdrive_source closes at v1.7.125 instead of v1.7.126.
