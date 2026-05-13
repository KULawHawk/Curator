# DRAFT — Release Notes for Curator v2.0

**STATUS:** **DRAFT** — not yet released. This is a planning artifact for Jake to refine in The Log conversation. Created at v1.7.151 (Round 3 Tier 1 ship 6). Actual v2.0 ship requires the CLI Coverage Arc (Round 3 Tier 3) to close cleanly first.

**Target ship:** Post-Round 3 Tier 3 (after `cli/main.py` reaches 100% line + branch coverage).
**Expected version sequence:** v1.7.146 → ... → v1.7.169 (CLI arc close) → **v2.0.0** (release ceremony).

---

## Highlights — what v2.0 represents

**v2.0 is the apex-accuracy milestone.** What started as a small file-curator tool has matured into a fully-tested constellation pillar:

- **150+ versioned releases** across the v1.0 → v2.0 line
- **58+ modules at 100% line + branch coverage** (target post-CLI arc) under the **apex-accuracy doctrine**
- **6 multi-ship engineering arcs closed**: Migration Phase Gamma, Coverage Sweep, Plugins+MCP+Config, Storage Repositories, Mid-Size Services, CLI Coverage
- **95+ numbered lessons captured** documenting compounded engineering patterns
- **Three constitutional plugins integrated** (atrium-safety, atrium-citation, reversibility-deferred)
- **Bulletproof installer** with real-MCP-probe verification gate (Step 9)
- **5 PowerShell batch workflows** at `scripts/workflows/` with safety rails (plan preview → user confirmation → recycle-bin reversibility)
- **MCP server** with Bearer-auth HTTP transport for LLM client integration
- **PySide6 GUI** with Tools + Workflows menus (the GUI internals are not yet at 100% coverage — see "Known limitations")

## What's new since v1.0.0rc1

### Migration tool ("Tracer") — Phases 1, 2, 3, 4

- **v1.1.0:** Phase 1+2 — persistent resumable jobs, worker-pool concurrency, cross-source migration (local↔gdrive), PySide6 Migrate tab
- **v1.3.0:** Phase 3 — quota-aware retry with exponential backoff (`--max-retries`), four-mode `--on-conflict={skip,fail,overwrite-with-backup,rename-with-suffix}` resolution
- **v1.4.0:** Phase 4 — cross-source `overwrite-with-backup` + `rename-with-suffix` via the new `curator_source_rename` hookspec
- **v1.6.1:** audit details schema symmetry — `migration.move` and `migration.copy` emit `cross_source` / `src_source_id` / `dst_source_id` for ALL four code paths (unblocks downstream cross-source filtering by audit consumers)

### Plugin ecosystem (v1.1.1+)

- `curator_plugin_init(pm)` hookspec (v1.1.2) — plugins receive a pluggy reference so they can call other plugins' hooks from inside their own hookimpls
- `curator_audit_event(...)` hookspec (v1.1.3) + core `AuditWriterPlugin` — structured audit log entries from plugins
- `curatorplug-atrium-safety` v0.3.0 — enforces Atrium Principle 2 (Hash-Verify-Before-Move) via `curator_source_write_post`
- `curatorplug-atrium-citation` v0.2.0 — implements Atrium Principle 3 (Citation Chain Preservation) cross-source filter

### MCP server (v1.2.0+)

- Optional `[mcp]` extra exposes `curator-mcp` — 9 read-only tools for LLM clients (Claude Desktop, Claude Code, third-party agents)
- **v1.5.0:** HTTP transport with `BearerAuthMiddleware` + `curator mcp keys` CLI for token management + audit emission
- **v1.7.x:** 100% coverage on all 4 MCP modules (auth, middleware, server, tools)

### Three source plugins

- **`plugins/core/local_source.py`** — local filesystem source (send2trash integration, ignore patterns)
- **`plugins/core/gdrive_source.py`** — Google Drive via PyDrive2 (lazy-import, SourceConfig resolution with 4-tier priority order, parent_id sentinel translation)
- **(future)** OneDrive, Dropbox, S3 — plugin surface stable

### GUI (v1.6+)

- Tools menu — 5 placeholder dialogs (forward-compatible with v1.7+ GUI v2)
- Workflows menu — 5 launchers for click-to-run batch workflows
- v2 design captured at `docs/design/GUI_V2_DESIGN.md` (v1.7 foundation parity → v1.8 advanced → v1.9 native workflows + atrium-reversibility integration)

### Coverage & quality

The **apex-accuracy doctrine** is captured in `CLAUDE.md`: ship 100% line + branch coverage on every module touched, with documented `# pragma: no cover` only for genuinely defensive boundaries (3 such annotations exist in the codebase, all with Lesson #91 justification).

**The 95-lesson library** in CHANGELOG's "Lesson captured" sections is the most valuable artifact this project produces. Lessons compound — captured patterns made later ships faster:

- Lesson #84 (stub reuse) → migration arc went from 13/13 failures in v1.7.90 to 14/14 first-iteration passes in v1.7.92
- Lesson #88 (split if scope >1.5x) → never tripped a true scope overrun across 5 arcs
- Lesson #91 (defensive boundary pragma) → 3 documented annotations, all justified
- Lesson #93 (re-measure baselines) → caught migration.py 99.71% / 100% measurement divergence at v1.7.146
- Lesson #94 (synchronous executor shim) → made threaded code testable without flake
- Lesson #95 (pydantic validate_assignment bypass) → made defensive boundary testing precise

### Six closed multi-ship arcs

| Arc | Versions | Modules | Note |
|---|---|---|---|
| Migration Phase Gamma | v1.7.89-93b | 19 services modules | First apex-accuracy arc closure |
| Coverage Sweep | v1.7.95-106 | 12 sweep targets | Closed 6 → 19 modules at 100% |
| Plugins + MCP + Config Sweep | v1.7.116-125 | 9 modules | All MCP + plugins + config covered |
| Storage Repositories Sweep | v1.7.126-137 | 11 modules | Entire `storage/` subpackage |
| Mid-Size Services Sweep | v1.7.140-145 | 5 service modules | photo, cleanup, organize, hash_pipeline, trash |
| **CLI Coverage Arc** | v1.7.152-169 (planned) | 1 large module + 2 small CLI | The v2.0 close |

## Breaking changes

**None at v1.7.x level.** v2.0 is intended as a **maturity milestone**, not an API break. The CLI surface, MCP tool signatures, plugin hookspec API, and storage schema are all considered stable from v1.7.x onward.

**TODO** (decided in The Log): does v2.0 introduce any breaking changes? Candidates considered:
- Remove deprecated `[mcp]` extra in favor of `[server]`? (probably not — `[mcp]` is named after the protocol)
- Change `MigrationOutcome` enum values? (no — would break downstream audit consumers)
- Remove pre-Phase-Gamma `--mode={dry-run,apply}` aliases? (probably yes — replaced by `--apply` flag)

Leaning toward: **strictly additive v2.0**, no breaking changes. The 145-ship engineering arc demonstrates the API has reached settled state.

## Known limitations

- **GUI test coverage at 0%** — `gui/dialogs.py` (2234 stmts), `gui/main_window.py` (1089 stmts), `gui/models.py` (774 stmts), `gui/lineage_view.py` (246 stmts) are all uncovered. A GUI testing strategy is the next major arc post-v2.0; needs a design conversation in The Log first.
- **Mutation testing not yet performed.** v1.7.150 documented why: cost is 36+ hours per major module, outside Tier 1 budget. Deferred to a separate dedicated arc. Apex-accuracy line+branch coverage is the verification floor; mutation testing is the next-order verification.
- **`cli/main.py` will be at 100% only after Round 3 Tier 3 closes** (planned v1.7.155-169). v2.0 release ceremony should wait until CLI arc closes.

## Roadmap to v2.0 ship

1. ✅ Round 1 (Migration Phase Gamma + Coverage Sweep)
2. ✅ Round 2 (Plugins+MCP+Config + Storage Sweep + Mid-Size Services)
3. ⏳ Round 3 Tier 1 (Stabilization — current; this draft is the last item)
4. ⏳ Round 3 Tier 2 (CLI Coverage Arc kickoff — scope plan + mcp_keys + mcp_orphans)
5. ⏳ Round 3 Tier 3 (cli/main.py decomposition by Click command group — 12-18 sub-ships)
6. ⏳ **v2.0 release ceremony** — Log conversation to decide:
   - Final version number (v2.0.0? v2.0-RC1 first?)
   - Final release notes text (this draft → real release notes)
   - GitHub release post
   - Constellation doc update (mark Curator as "stable v2.0")
   - Optional: PyPI release? (decision pending; currently install-from-source)

## TODO (decisions for The Log)

- [ ] Final v2.0 version number (v2.0.0 direct, or v2.0-RC1 first?)
- [ ] Are there any breaking changes worth introducing at v2.0?
- [ ] PyPI publication strategy
- [ ] GUI testing strategy arc (post-v2.0)
- [ ] Mutation testing arc (post-v2.0, dedicated budget)
- [ ] OneDrive plugin (post-v2.0)
- [ ] Constellation-level v2.0 announcement (cross-pillar doc updates)

## Bottom line

**Curator v2.0 represents the most thoroughly-tested file-management tool the author has ever shipped.** 150+ versioned releases, 58+ modules at 100% line+branch under apex-accuracy doctrine, 95+ numbered lessons, 6 closed multi-ship arcs, 3 constitutional plugins integrated. The CLI arc closes the final non-GUI coverage gap; GUI is the next era (post-v2.0 strategy conversation needed).

The 6-day arc from v1.7.89 (Round 1 kickoff) to v1.7.146+ (Round 3 in progress) demonstrates a sustainable engineering rhythm: lessons compound, doctrine clarifies, ships land cleanly. v2.0 is the marker that says "the foundation is verified."
