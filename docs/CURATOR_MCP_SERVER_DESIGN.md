# Curator — MCP Server Design

**Status:** v0.2 — RATIFIED 2026-05-08. Jake ratified all 6 DMs as recommended (`r` reply against the research-notes summary delivered in chat 2026-05-08; the formal v0.1 DRAFT step was collapsed since the research notes already presented the DMs and recommendations). Implementation cleared to begin. P1 lands as Curator v1.2.0 with the MCP scaffolding + first 3 read-only tools; P2 adds the remaining 6 tools + tests; P3 is documentation + release ceremony. See §5 for the implementation plan and §8 revision log for the v0.2 entry.
**Date:** 2026-05-08
**Authority:** Curator-side design. Adds a new optional `[mcp]` extra to the Curator package that exposes a Model Context Protocol server (stdio transport by default, HTTP optional). Lets LLM clients (Claude Desktop, Claude Code, third-party MCP-aware agents) query Curator's file index, audit log, lineage graph, and other read-only state programmatically.
**Companion documents:**
- `Curator\src\curator\cli\runtime.py` — `CuratorRuntime` dataclass that the MCP server wraps. Already exposes 9 repos + key services (audit, migration) as a clean integration point.
- `Curator\pyproject.toml` — gets one new entry under `[project.optional-dependencies]` (`mcp = ["mcp>=...", "fastmcp>=..."]`) and one new entry-point script (`curator-mcp = "curator.mcp.server:main"`).
- `Curator\docs\PLUGIN_INIT_HOOKSPEC_DESIGN.md` v0.3 IMPLEMENTED — sibling Curator-side design proving out the "small additive surface for plugin/agent capability" pattern this design follows.
- `Curator\docs\CURATOR_AUDIT_EVENT_HOOKSPEC_DESIGN.md` v0.3 IMPLEMENTED — the audit channel that `query_audit_log` reads from. The atrium-safety plugin's `compliance.*` events are queryable via this MCP tool.
- MCP specification (`spec.modelcontextprotocol.io`) — the protocol this server implements. `mcp` Python SDK + `fastmcp` framework are the canonical implementations.

---

## 1. Scope

### 1.1 The problem

Curator has a rich CLI surface (13 commands: `inspect`, `scan`, `group`, `lineage`, `trash`, `restore`, `audit`, `watch`, `doctor`, `organize`, `organize_revert`, `gui`, `migrate`) and an even richer programmatic surface (18 services + 10 repos exposed via `CuratorRuntime`). Today, that surface is reachable only by:

1. A human typing `curator <command>` in a terminal.
2. Python code that imports `from curator.cli.runtime import build_runtime` directly — i.e., a Python program co-resident with Curator.

Neither path serves the most natural use case for Curator's index: **an LLM agent reasoning about the user's files**. Claude Desktop / Claude Code can't ask "find me the Q3 review draft I worked on last week" without either screen-scraping CLI output (fragile) or being granted full Python interpreter access (overpowered). Future Synergy / Conclave agents need the same kind of programmatic, agent-discoverable interface.

### 1.2 The general capability being added

Curator gets an MCP (Model Context Protocol) server as an optional dependency. MCP is the open standard Anthropic ships for "LLM-discovers-and-calls-tools-on-external-services" — Claude Desktop already speaks it natively over stdio; Claude Code consumes it; third-party agent frameworks (LangChain, AutoGen, etc.) increasingly do too.

The server:

1. **Loads `CuratorRuntime` the same way the CLI does.** No initialization fork. The MCP server reads the same SQLite DB at the same path the CLI reads.
2. **Exposes a curated subset of Curator's surface as MCP tools.** v0.1 ships nine read-only tools (§4); writes defer to v0.2+.
3. **Speaks stdio by default.** That's the transport Claude Desktop / Claude Code use; zero-config for users who already have those installed.
4. **Optionally speaks HTTP/SSE via `--http`.** For remote use cases (Synergy, Conclave, multi-machine setups). HTTP transport implies authentication design that's deferred to v0.2 (§3 DM-5).
5. **Trust model: stdio = trust the OS user.** The MCP server runs under the user's process; same trust boundary as the CLI. No API keys, no per-tool authorization. HTTP transport changes this and is gated.

Concrete near-term consumers:

1. **Claude Desktop user** wanting to query their own Curator-indexed files. "Find PDFs from August about taxes." "What did atrium-safety refuse last week?" "Show me everything that links to this paper."
2. **Claude Code** as a coding agent that wants to inspect a project's file lineage / dedup state without invoking the CLI.
3. **Future Synergy / Conclave agents** needing programmatic Curator access for multi-step file workflows.

### 1.3 What this is NOT

- **Not a write tool surface in v0.1.** Migration apply, scan trigger, trash send/restore, organize — all are write operations that need careful confirmation UX (per-tool description prompts, structured danger flags, dry-run defaults). Deferred to v0.2+. v0.1 is read-only-only.
- **Not an authentication system.** stdio transport works under the OS user's already-authenticated session. HTTP transport (DM-3 (b)) would need API keys; deferred with the transport.
- **Not a replacement for the CLI.** The CLI remains the canonical human interface. The MCP server is for LLM agents; the two coexist.
- **Not a real-time event stream.** `WatchService` events ARE NOT exposed as a streaming MCP capability in v0.1. Polling-based `query_*` tools give point-in-time snapshots; subscription/streaming defers indefinitely.
- **Not cross-instance.** "Query Curator on machine X from machine Y" is not in scope; v0.1 reads the local Curator DB only. Multi-instance coordination is a future Synergy concern.
- **Not a GUI.** The MCP server speaks JSON over stdio/HTTP; rendering is the client's job (Claude Desktop, Claude Code, etc.).

---

## 2. Invariants the design must preserve

1. **`CuratorRuntime` initialization is shared.** The MCP server uses the same `build_runtime(...)` path the CLI uses. Same DB, same plugins, same config. No "MCP-specific runtime fork."
2. **Read-only tools never mutate state.** No tool in v0.1 calls a method that writes to any repo or filesystem. Static analysis of v0.1 tools should be able to confirm this.
3. **`pip install curator` (without `[mcp]`) is unaffected.** Adding the optional extra must not pull MCP dependencies into the default install path. Users who don't want the MCP server pay zero overhead in install size, startup time, or attack surface.
4. **MCP server startup is fast.** <1s wall-clock on a warm cache for the typical case. `CuratorRuntime` already loads in ~200ms; MCP framework overhead must not bloat this materially.
5. **Existing CLI invocations are unchanged.** Adding the `curator-mcp` script entry point must not collide with any of the 13 existing CLI commands. (`curator-mcp` is a separate console script, not a `curator` subcommand, to keep the surfaces distinct.)
6. **Tools fail-safe, not fail-crash.** A query that finds nothing returns an empty result list, not an error. Errors are reserved for genuine misuse (malformed input, permission denied, DB locked).
7. **No silent audit pollution.** Read-only tools do NOT themselves write to the audit log. Querying Curator should not generate noise in its own audit history. (Explicit DM resolved: this is invariant, not configurable, in v0.1.)
8. **Tool descriptions are LLM-targeted.** Each tool's description, parameter docstrings, and return-shape docstrings are written for an LLM reader. Consistent terminology (`source_id` not `source` not `src_id`); concrete examples in descriptions; no internal jargon.

---

## 3. Decisions Jake ratified

### DM-1 — Package location

**Question.** Where does the MCP server live?

Options:

- (a) **In-Curator**, e.g. `src/curator/mcp/`
- (b) **Separate `curatorplug-mcp` repository** following the established plugin pattern
- (c) **In-Curator with optional `[mcp]` extra**, i.e. `pip install curator[mcp]`

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake. **(c) — In-Curator with optional `[mcp]` extra.** Mirrors the existing `[beta]` and `[cloud]` extras. Single repo for shared CI / version sync; users who don't want the MCP server pay zero overhead. Adds new directory `src/curator/mcp/` with `server.py`, `tools.py`, and `__init__.py` exposing a `main()` entry point.

### DM-2 — Framework choice

**Question.** Build directly on the low-level `mcp` Python SDK, or use the higher-level `fastmcp` decorator framework?

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake. **FastMCP.** Decorator-based, type-hint-driven, minimal boilerplate. Same author as the underlying SDK; actively maintained. Tool registration is a one-line decorator (`@mcp.tool()`) on a function whose Pydantic-typed signature defines the input schema and whose return type defines the output schema. Reduces the per-tool implementation cost from ~30 lines to ~10.

### DM-3 — Transport

**Question.** stdio only, HTTP/SSE only, or both?

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake. **stdio default, optional `--http` flag.** stdio works zero-config with Claude Desktop and Claude Code (the two canonical clients). HTTP transport is exposed via `curator-mcp --http --port 8765` for remote use cases (future Synergy / Conclave). HTTP transport implies an authentication design (DM-5); v0.1 lands stdio-only-functional, HTTP scaffolded but warning-on-use until DM-5 is revisited in v0.2.

### DM-4 — v0.1 scope

**Question.** Read-only only, or include "safe writes" (plan_migration dry-run, scan trigger)?

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake. **Strictly read-only.** Even "safe writes" introduce confirmation UX questions (Should `plan_migration` show a dry-run preview the LLM has to approve? Should `scan_path` block the response while scanning, or queue and return job ID?). Deferring lets v0.1 ship faster and gives time to observe how LLM clients use the read surface before designing the write surface.

### DM-5 — Authentication

**Question.** Trust the OS user, require an API key, or pass through Curator's existing auth mechanisms?

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake. **Trust the OS user (stdio only); HTTP transport defers auth to v0.2.** stdio runs under the user's process; reading Curator's DB requires the user's filesystem permissions; same trust boundary as the CLI. HTTP transport (DM-3) is scaffolded with a warning ("HTTP transport without authentication is for local-network development only; do NOT expose to untrusted networks") until v0.2 adds API key support.

### DM-6 — Versioning

**Question.** v1.2.0 (new public surface) or v1.1.4 (additive only)?

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake. **v1.1.3 → v1.2.0 (minor).** New public surface (the `curator-mcp` console script + the `[mcp]` optional dependency set + `src/curator/mcp/` module) is meaningful enough to deserve a minor bump. Patch (v1.1.4) would be dishonest about the addition. Major (v2.0.0) would be overkill since nothing existing is changed.

---

## 4. Spec (per the ratified DMs)

### 4.1 Package layout (DM-1 ratified)

```
src/curator/mcp/
├── __init__.py        # exposes main() entry point
├── server.py          # FastMCP server construction; transport selection
└── tools.py           # the 9 v0.1 tool implementations (one function per tool)

tests/unit/mcp/
├── __init__.py
├── test_tools.py      # per-tool unit tests with mocked CuratorRuntime
└── test_server.py     # server bootstrap + tool registration tests

tests/integration/mcp/
├── __init__.py
└── test_stdio.py      # full stdio round-trip against a real CuratorRuntime
```

### 4.2 `pyproject.toml` additions

```toml
[project.optional-dependencies]
# existing extras unchanged...
mcp = [
    "mcp>=1.0",        # the protocol SDK (Anthropic)
    "fastmcp>=0.3",    # the decorator framework
]

[project.scripts]
# existing scripts unchanged...
curator-mcp = "curator.mcp:main"
```

### 4.3 The 9 read-only tools (DM-4 ratified)

| # | Tool name | Backed by | Input schema (key fields) | Return shape (summary) |
|---|---|---|---|---|
| 1 | `health_check` | `runtime.pm` ping + DB connection check | (no params) | `{status: "ok" \| "degraded", curator_version, plugin_count, db_path}` |
| 2 | `list_sources` | `source_repo.list_all()` | (no params) | `[{source_id, kind, root_path, file_count, last_scanned_at}]` |
| 3 | `query_audit_log` | `audit_repo.query(actor=..., action=..., entity_id=..., since=..., limit=...)` | `actor?, action?, entity_id?, since?, limit=50` | `[{audit_id, actor, action, entity_type, entity_id, details, created_at}]` |
| 4 | `query_files` | `file_repo.search(name_glob=..., source_id=..., extension=..., min_size=..., max_size=..., limit=...)` | `name_glob?, source_id?, extension?, min_size?, max_size?, limit=50` | `[{file_id, source_id, path, size_bytes, xxh3_128, mime_type, modified_at}]` |
| 5 | `inspect_file` | `file_repo.get_by_id(file_id)` + `lineage_repo.get_neighbors(file_id)` + `bundle_repo.get_memberships(file_id)` | `file_id` (required) | `{file: {...}, lineage: {parents: [...], children: [...]}, bundles: [...]}` |
| 6 | `get_lineage` | `lineage_repo.walk(file_id, max_depth=...)` | `file_id` (required), `max_depth=3` | Tree structure of lineage relationships with edge weights |
| 7 | `find_duplicates` | `file_repo.find_by_hash(xxh3_128)` (and the inverse: find files whose hash has multiple owners) | `file_id?` (find dups of this), `xxh3_128?` (find dups of this hash), `source_id?` (filter), `limit=50` | `[{xxh3_128, files: [{file_id, source_id, path}]}]` (groups of dups) |
| 8 | `list_trashed` | `trash_repo.list(source_id=..., since=..., limit=...)` | `source_id?, since?, limit=50` | `[{trash_id, file_id, original_path, trashed_at, trashed_by}]` |
| 9 | `get_migration_status` | `migration_job_repo.get(job_id)` + `migration_job_repo.list_recent(limit=...)` | `job_id?` (get specific) OR no params (list recent) | `[{job_id, src_source_id, dst_source_id, status, planned_count, moved_count, failed_count, started_at, finished_at}]` |

Each tool gets:
- A clear, LLM-targeted description on the `@mcp.tool()` decorator
- Pydantic-typed input parameters with field-level descriptions
- A return value with a stable JSON-serializable shape (the schemas above will be exact Pydantic models in `tools.py`)
- Unit test coverage including the empty-result, multi-result, and bad-input cases

### 4.4 Transport (DM-3 ratified)

**stdio (default):**
```bash
curator-mcp
# Claude Desktop / Claude Code launch this with stdin/stdout pipes
```

**HTTP (opt-in):**
```bash
curator-mcp --http --port 8765
# Logs WARNING about no authentication; binds to 127.0.0.1 by default;
# refuses to bind to 0.0.0.0 without explicit --bind 0.0.0.0 + acknowledgement flag
```

### 4.5 Trust model (DM-5 ratified)

**stdio:** trust the OS user. The server reads `CuratorRuntime` (which reads the user's SQLite DB at the user's config path); same trust boundary as the CLI.

**HTTP:** v0.1 explicitly warns + binds to localhost only by default. v0.2 will add an `--api-key` flag (or read one from env) and reject requests without a matching `Authorization: Bearer <key>` header. Documented as a known gap in the v0.1 README.

---

## 5. Implementation plan

Three sessions, ~6h total.

### P1 — Curator v1.2.0 scaffolding + first 3 tools (~2h)

* Add `[project.optional-dependencies]` `mcp` entry to `pyproject.toml` and the `curator-mcp` script.
* Create `src/curator/mcp/__init__.py` (exposes `main()`), `src/curator/mcp/server.py` (FastMCP instantiation + transport selection from CLI args), `src/curator/mcp/tools.py` (the 9 tool functions; first 3 implemented, rest stubbed with `NotImplementedError` + a clear "P2" comment).
* Implement the 3 starter tools: `health_check`, `list_sources`, `query_audit_log`. These are the cheapest to get right and the most useful for proving end-to-end stdio integration with Claude Desktop.
* Add unit tests in `tests/unit/mcp/test_tools.py` for the 3 starter tools (per-tool: empty result, single result, multiple results; for `query_audit_log`, exercise filtering by actor + action).
* Add an integration test in `tests/integration/mcp/test_stdio.py` that launches the server in a subprocess, sends a JSON-RPC `tools/list` request over stdin, and confirms the 3 implemented tools are listed (with descriptions + schemas).
* Bump version 1.1.3 → 1.2.0; prepend `## [1.2.0]` entry to `CHANGELOG.md`.
* Commit, tag `v1.2.0`, push.

**P1 acceptance:** Claude Desktop / Claude Code with `pip install curator[mcp]` configured can launch `curator-mcp` and call `health_check`, `list_sources`, `query_audit_log` end-to-end.

### P2 — Remaining 6 tools + tests (~3h)

* Implement `query_files`, `inspect_file`, `get_lineage`, `find_duplicates`, `list_trashed`, `get_migration_status`.
* Per-tool unit tests (each tool: 3-4 tests covering happy path + edge cases). Target: ~24 new tests.
* Integration test extension: stdio round-trip exercises ALL 9 tools.
* Confirm tool descriptions are LLM-readable (review pass: terminology consistent, examples concrete, no Curator-internal jargon).
* No version bump; this is mid-cycle work.
* Commit + push (no new tag yet; v1.2.0 stays the most-recent tag).

**P2 acceptance:** All 9 v0.1 tools functional + tested + documented. Plugin suite still passes (75/75). Curator regression slice still passes (357/357 + new MCP tests).

### P3 — Documentation + release ceremony (~1h)

* Update Curator `README.md` with a new "MCP server (v1.2.0+)" section (~25 lines) describing what the server is, install path (`pip install curator[mcp]`), Claude Desktop config snippet, the 9 tools, and the v0.2 deferred-write note.
* Add `docs/CURATOR_MCP_SERVER_DESIGN.md` v0.2 → v0.3 IMPLEMENTED stamp with the v0.3 revision-log entry (full implementation history, test counts, end-to-end demo confirmation).
* Final regression sweep: full Curator slice + plugin suite + new MCP tests all green.
* End-to-end demo: launch Claude Desktop with `curator-mcp` configured; confirm a representative query end-to-end works (e.g. "Use the audit log query tool to find any compliance.refused events from the last week"). Capture as a notes block in CHANGELOG.
* No new version bump (v1.2.0 was bumped in P1; this is just the docs catching up).
* Commit + push.

**P3 acceptance:** Design doc at v0.3 IMPLEMENTED; README reflects the new surface; end-to-end Claude Desktop demo confirmed working; the plan is closed.

---

## 6. Backward compatibility

**v1.1.3 → v1.2.0 is a minor bump.**

- ✅ Existing `pip install curator` — unaffected (no MCP deps pulled).
- ✅ Existing CLI commands — unaffected (`curator-mcp` is a separate script, no collision).
- ✅ Existing plugins (core + atrium-safety) — unaffected.
- ✅ `CuratorRuntime` API — unchanged (the MCP server consumes it; doesn't modify it).
- ✅ DB schema — unchanged.
- ✅ Config schema — unchanged.

Users opting into the new surface install `pip install curator[mcp]`. Users not opting in pay zero cost.

---

## 7. Cross-references

- `Curator/docs/PLUGIN_INIT_HOOKSPEC_DESIGN.md` v0.3 IMPLEMENTED — sibling additive-surface design.
- `Curator/docs/CURATOR_AUDIT_EVENT_HOOKSPEC_DESIGN.md` v0.3 IMPLEMENTED — `query_audit_log` reads from the audit channel this design established. atrium-safety v0.3.0's `compliance.*` events are queryable via `query_audit_log` once both ship.
- `curatorplug-atrium-safety/DESIGN.md` v0.4 IMPLEMENTED — the headline use case for `query_audit_log`. An LLM client asking "what did the safety plugin refuse last week?" gets real structured data via this MCP server.
- `Curator/src/curator/cli/runtime.py` — `CuratorRuntime` dataclass; the integration point.
- MCP specification: https://spec.modelcontextprotocol.io/
- FastMCP framework: https://github.com/jlowin/fastmcp

---

## 8. Revision log

- **2026-05-08 v0.1** (informal) — research notes shared in chat as a tight summary covering: the existing Curator surface (13 commands, 18 services, 10 repos), proposed v0.1 scope (9 read-only tools), 6 architectural DMs with recommendations. No formal v0.1 DRAFT document was created; the summary served the same function. Jake reviewed the recommendations and replied `r` to ratify all 6.
- **2026-05-08 v0.2** — RATIFIED. Formal design document created at this version. Captures: §1 scope (the gap LLM agents face accessing Curator programmatically; what the MCP server enables; what it's NOT — no writes in v0.1, no auth, no streaming, no cross-instance), §2 eight invariants (shared runtime init, read-only never mutates, default install unaffected, fast startup, no CLI collision, fail-safe-not-crash, no audit pollution, LLM-targeted tool descriptions), §3 six DMs all RATIFIED with Jake's recommendations as written (in-Curator with `[mcp]` extra, FastMCP framework, stdio default + optional HTTP, strictly read-only v0.1, trust-the-OS-user for stdio + HTTP-defers-auth, v1.1.3 → v1.2.0 minor bump), §4 spec (package layout, pyproject additions, the 9 tools with backing repos/services + input/return schemas, transport details, trust model details), §5 three-session implementation plan (~6h: P1 scaffolding + 3 starter tools, P2 remaining 6 tools + tests, P3 docs + release ceremony) with per-session acceptance criteria, §6 backward compat (strictly additive minor bump), §7 cross-references. No code has been written; the v0.1 RATIFIED state means P1 implementation is cleared to begin in the next session. Next step: P1 lands as Curator v1.2.0 with the scaffolding + first 3 tools end-to-end functional against Claude Desktop.
