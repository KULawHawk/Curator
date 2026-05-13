# CLAUDE.md — Curator

This file is read automatically by Claude Code at session start. It encodes the project conventions Claude must respect without being re-asked every session.

**Owner:** Jake Leese · **Updated:** 2026-05-13

---

## Project identity

- **Name:** Curator. Repo: `https://github.com/KULawHawk/Curator.git`. Local root: `C:\Users\jmlee\Desktop\AL\Curator\`.
- **Brand context:** Curator is one pillar within **Ad Astra** (the overarching umbrella). Governance constitution at `..\Atrium\CONSTITUTION.md` (v0.3 RATIFIED 2026-05-08). Constellation map at `..\AD_ASTRA_CONSTELLATION.md`.
- **Status:** v1.7.93a shipped. 93 ships total. Active arc: **Migration Phase Gamma** sub-ship 5a of 6 closed (split per Lesson #88; see `docs/MIGRATION_PHASE_GAMMA_SCOPE.md`). `migration.py` at 90.86% line + branch; v1.7.93b closes the arc to 100% (landmark).
- **Python:** 3.13.12 in `.venv`. Windows 11 only (see Doctrine principle 3 below).

---

## Working-partnership directive

Per Jake's standing instruction: **speak up and push back when appropriate.** Aid Jake in avoiding wasted time and money. Build wisely without cutting corners on accuracy. Don't just execute — flag waste, propose alternatives, ask before shipping when shipping isn't obviously right.

---

## 🔴 REQUIRED: Lesson Adoption Protocol

This is non-negotiable. Before doing engineering work in a new session:

### At session start (within the first 5 turns)

1. **Read the most recent 5 CHANGELOG entries** in `CHANGELOG.md` and pay special attention to the "Lesson captured" sections.
2. **Read every lesson newer than the most recent one referenced in this CLAUDE.md.** The doctrine below lists lessons through #93. If CHANGELOG shows #94 or higher, those exist; read them.
3. **Acknowledge the lessons in your first substantive response.** Not verbosely — something like "Read lessons through #N. Carrying #X and #Y forward as relevant here." This proves you actually read them.

### Before writing a new test, source-code change, or significant decision

4. **Ask: does any captured lesson apply here?** If yes, *say which one and why* in your next message. If the answer should be "I'd normally do X, but Lesson #N says do Y instead," then do Y.
5. **If a new lesson emerges during the work**, write it down in the next CHANGELOG entry's "Lesson captured" section with rich detail. Number sequentially. **Add it to this CLAUDE.md too** under the Doctrine section so future sessions inherit it.

### Why this is required

Lessons compound. The arc going from 13/13 failures in v1.7.90 to 14/14 first-iteration passes in v1.7.92 is direct evidence: each captured lesson made the next ship faster. Skipping the read-and-adopt step would undo that compounding. The lessons are the single most valuable artifact this project produces — protect them.

---

## 🟢 Tool routing — when to suggest offloading

Not every kind of work belongs in every Claude product. Claude should *recognize* when a different tool would serve Jake better and *say so explicitly*. The discipline of routing work is itself part of the doctrine.

### Use **Claude Code** (CLI tool — Jake has it via Max 20x) for:

- **Engineering arcs with tight test-iterate-ship loops** (this is what we're doing right now)
- **Long-running pytest / coverage / build commands** — native shell, no MCP timeouts
- **Multi-file edits where token efficiency matters** — no chat UI overhead per round trip
- **Anything where the cost of one chat round-trip > 5 seconds is a real bottleneck**
- **Daily Curator/Conclave/Nestegg dev sessions** — this is the default home for engineering work

### Use **The Log** (the designated `claude.ai` chat) for:

- **Cross-arc reflection** — synthesis across multiple Ad Astra pillars
- **Design discussions** — sketching, brainstorming, deciding architecture before code
- **Planning sessions** — scope plans, arc kickoffs, prioritization
- **Lesson-capture review** — periodic re-reading of the lesson library
- **Anything where conversational depth matters more than tool execution speed**

### Use **other Claude products** when:

- **Claude in Chrome**: web research, reading documentation, checking external APIs
- **Cowork (beta)**: when desktop file orchestration across multiple non-engineering tools is needed
- **Claude in Excel (beta)**: financial models, data exploration, the PY638 psychometrics work

### When to actively suggest a handoff

Claude should *proactively recommend* moving work to a different tool when:

1. **Token efficiency mismatch** — engineering work in The Log chat eats context quickly; chat-style reflection in Claude Code feels heavy
2. **The task crosses a natural boundary** — e.g. "we just shipped, now let's plan v1.8" → that planning belongs in The Log, not in Claude Code
3. **A multi-session arc is starting** — write a `CLAUDE.md` for the repo (or an equivalent context-prime doc) and recommend Jake start fresh in Claude Code
4. **The current session is hitting a budget cliff mid-arc** — better to hand off via a clean prompt than push through and end mid-ship

**The cost of a bad routing decision is real.** A mid-ship state that should have been a clean Claude Code handoff (see Lesson #92) is exactly the failure mode this section exists to prevent.

---

## Doctrine

These are non-negotiable. Read once, apply always. Lessons #79–93 below; check CHANGELOG for #94+.

### 1. APEX PRINCIPLE — ACCURACY

Ship 100% line + branch coverage on every module touched. Mark specific lines `# pragma: no cover` only with a documented justification in source (e.g. "defensive code for impossible case given pydantic validation"). **NEVER ship leaving uncovered branches with a "diminishing returns" rationale.** Untested code is untrusted code.

### 2. Hash-Verify-Before-Move (Atrium Principle 2)

Every file relocation triple-checks: source-absent + destination-present + hash-match BEFORE trashing the source. Order:
1. Hash source bytes
2. Make dst parent dirs
3. `shutil.copy2` (preserves mtime)
4. Hash dst bytes
5. Verify `hash(src) == hash(dst)`; on mismatch: delete dst, mark FAILED, leave src
6. Update FileEntity index pointer
7. Trash source (best-effort)

### 3. Windows-only scope (current)

Cross-platform parity is **SUSPENDED** (documented in `docs/PLATFORM_SCOPE.md`). CI matrix: `windows-latest × {3.11, 3.12, 3.13}`. Do not add macOS/Linux conditionals without explicit user direction.

### 4. Ship ceremony

**Trimmed ceremony** (memory edit #5) for routine ships. Skip the standard ritual sections: no Catches / Limitations / Arc-state sections in release notes. **KEEP** lessons-learned content rich and detailed regardless of ship size. Full ceremony reserved for landmark ships (capstones, doctrine amendments, major arc closures).

### 5. Lesson capture

After every ship, write down lessons in the CHANGELOG entry's "Lesson captured" section. Number sequentially. Latest is #93 (v1.7.92). Lessons compound — read prior ones before tackling unfamiliar territory. **See Lesson Adoption Protocol at top of this file — adopting lessons is REQUIRED, not optional.**

### 6. Mid-ship is not acceptable session-end state (Lesson #86)

A "ship" means: tests pass, coverage measured, CHANGELOG written, release notes written, commit msg written, committed, tagged, pushed. Don't stop in the middle. If budget runs out, finish the current ship before pausing.

### 7. Scope discipline

Multi-ship arcs need explicit scope plans (Lesson #88). The plan IS a ship. Mid-arc is unacceptable too. Calibrate estimates after sub-ship 1 (Lesson #89). Split if scope grows beyond 1.5x.

### 8. Service-orchestration tests trace the data flow (Lesson #90)

When testing service methods, read the orchestration's control flow from entry to the branch you're targeting. Note every short-circuit, every copy, every mutation. Specifically for migration tests: `apply()` creates a **fresh copy** of each move into `report.moves`. Always assert against `report.moves[0]`, not against the original move handed in via `plan.moves[0]`.

### 9. Defensive boundaries can be effectively unreachable (Lesson #91)

Some `except` clauses are protected by intermediate layers that swallow the exception (e.g. `_hook_first_result` catches all non-FileExistsError exceptions internally). To test these branches, monkeypatch the intermediate layer itself. Alternative: annotate `# pragma: no cover` with justification.

### 10. Tool routing is a discipline (Lesson #92)

Knowing which Claude product or session to use for which kind of work is itself part of the craft. Engineering arcs go in Claude Code. Cross-arc reflection and design go in The Log chat. Recognize budget cliffs early — a clean handoff via a context-prime prompt (like the v1.7.92 one at `..\.curator\CLAUDE_CODE_HANDOFF_v1792.md`) is *better* than pushing through and ending mid-ship. The "don't worry about tokens" directive doesn't make context unlimited; pre-commit to ship boundaries you can actually complete. See "Tool routing" section above for the routing matrix.

### 11. Test-design rewrites need a coverage diff (Lesson #93 — new)

When *replacing* an existing test file with a different design (e.g. integration-style → direct-call unit tests, or the reverse), the new tests passing does NOT prove the new design covers everything the old design did. Integration-style tests carry *incidental* coverage of orchestration code paths; direct-call unit tests trade that for explicitness about what's tested. The trade only works if you compare the `--cov-report=term-missing` output against the pre-rewrite baseline.

**Discovery in v1.7.92:** the autostrip test file rewrite passed all 14 new tests but quietly uncovered line 830 (`self._auto_strip_metadata(move)` — the apply() dispatch into the helper). The old integration tests had implicitly covered it; the new direct-call tests skip apply() entirely. Coverage diff caught the regression before ship; a focused apply() integration test restored the line.

**How to apply:** before shipping a test-file rewrite (or deleting an integration test because "unit tests cover it"), diff the missing-line list. Lines that moved covered→uncovered need: (a) a restoring test, (b) `# pragma: no cover` with justification, or (c) explicit deferral documentation. This is distinct from Lesson #90 (control-flow tracing for new tests) — that's about correctness of new tests; this is about *coverage continuity* across the rewrite boundary.

---

## Repo layout

```
src/curator/
├── services/       ← The core; migration.py is the biggest (3400+ lines)
├── storage/        ← repositories + queries (SQLite layer)
├── models/         ← dataclasses + enums
├── plugins/        ← pluggy-based source plugins (local, gdrive)
├── cli/            ← Click-based CLI
├── gui/            ← PySide6 GUI (v1.6+)
├── mcp/            ← MCP server for Claude Desktop integration
└── _vendored/      ← send2trash, etc.

tests/
├── unit/           ← Fast, isolated. test_migration_*.py are the active arc.
└── integration/    ← Slower, real-IO. test_cli_migrate.py is the big one.

docs/
├── MIGRATION_PHASE_GAMMA_SCOPE.md  ← Current arc scope plan (priority-locked)
├── releases/v1.7.{N}.md            ← Per-ship release notes
├── TRACER_PHASE_{2,3,4}_DESIGN.md  ← Design docs (M-rules, DM-rules)
└── PLATFORM_SCOPE.md               ← Windows-only scope decision

.curator/                            ← External scratch (commit msgs, handoff prompts)
   (located at ..\.curator\, NOT inside the repo)
```

---

## Test discipline

### Running tests

```powershell
# Single test file
$env:QT_QPA_PLATFORM = "offscreen"; $env:NO_COLOR = "1"
.\.venv\Scripts\python.exe -m pytest tests/unit/test_migration_X.py --no-header --timeout=30

# Migration arc combined coverage
.\.venv\Scripts\python.exe -m pytest tests/ -k "migrat" --no-header --timeout=60 `
    --cov=curator.services.migration --cov-report=term --cov-branch -q -W ignore
```

### Faster feedback loops

Use `-x --ff` to stop at first failure and re-run failures first:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_X.py -x --ff -q
```

### Stub patterns (Lesson #84)

The migration test files share a stub vocabulary. Reuse from `tests/unit/test_migration_plan_apply.py`:

```python
from tests.unit.test_migration_plan_apply import (
    NOW, StubAuditRepository, StubFileRepository, StubSafetyService,
    StubSourceRepository, StubMetadataStripper, make_service,
    make_file_entity, make_move,
)
```

For cross-source tests, reuse `StubMigrationPluginManager` from `tests/unit/test_migration_cross_source.py`.

For auto-strip, reuse `StubMetadataStripper` from `tests/unit/test_migration_autostrip.py`.

**Do not redesign stubs.** New tests should compose existing stubs unless there's a genuinely new collaborator that needs one (then add it as a class, import-from-elsewhere pattern).

---

## Ship workflow

Every ship follows this order. **Don't skip steps.** Don't reorder them.

1. **Read relevant code thoroughly** before writing tests (per Lesson #90).
2. **Confirm lesson adoption** (per Lesson Adoption Protocol at top).
3. **Write tests.** Iterate to pass.
4. **Measure coverage** on the target module(s). Compare to scope plan target.
5. **Update scope plan** status tracker.
6. **Write CHANGELOG entry.** Top of `CHANGELOG.md`. Include: scope, coverage delta, branches closed, **lesson captured** (rich detail).
7. **Write release notes** at `docs/releases/v1.7.{N}.md`. Trimmed ceremony.
8. **Write commit message** to `..\.curator\v17{NN}_commit_msg.txt`.
9. **Stage** the files (`git add` — only the ones changed for this ship).
10. **Commit** using `-F` flag pointing at the commit message file.
11. **Tag** `v1.7.{N}` with annotated message (`-a -m "..."`).
12. **Push** main branch + the tag.
13. **Cleanup** the commit-message scratch file.
14. **Update CLAUDE.md** if a new lesson was captured.

---

## Git credentials

Personal access token is already in the user's shell environment as `GH_TOKEN`. Do not echo it. If `git push` requires credentials, the token works.

GitHub repo: `https://github.com/KULawHawk/Curator.git`.

---

## Current arc state (v1.7.92 mid-ship)

```
v1.7.88 ✅ Scope plan
v1.7.89 ✅ Plan() + Apply() control flow         (68.18%, +1.44%)
v1.7.90 ✅ Same-source + 4 conflict modes        (70.05%, +1.87%)
v1.7.91 ✅ Cross-source + overwrite-with-backup  (77.47%, +7.42%)  ← biggest
v1.7.92 ⏳ Auto-strip + small defensive bits     MID-SHIP NOT COMMITTED
v1.7.93 ⏳ Persistent path + doctrine close      (target 100%)
```

**v1.7.92 mid-ship state:** `tests/unit/test_migration_autostrip.py` written, 14 tests pass. Coverage not measured. Not committed. Resume protocol at `..\.curator\CLAUDE_CODE_HANDOFF_v1792.md`.

---

## Where to find things

- **Scope plan (priority-locked):** `docs/MIGRATION_PHASE_GAMMA_SCOPE.md`
- **Lesson history:** Scan recent `CHANGELOG.md` entries for "Lesson captured" sections (#79–92 captured; #93+ to come).
- **Doctrine amendments:** `docs/PLATFORM_SCOPE.md` (Windows-only suspension)
- **Design docs:** `docs/TRACER_PHASE_{2,3,4}_DESIGN.md` for the M-rules + DM-rules that the code references
- **Coverage history:** Each release notes file at `docs/releases/v1.7.{N}.md` records the coverage delta
- **Handoff prompts:** `..\.curator\CLAUDE_CODE_HANDOFF_*.md` for cross-session resume protocols

---

## Things NOT in scope

Do not propose or implement unless explicitly directed:

- Cross-platform parity revival (`PLATFORM_SCOPE.md` deferred indefinitely)
- New CLI subcommands (the surface is stable; v1.6+)
- Refactoring `migration.py` (deferred to a future major version)
- Documentation prose improvements (focus on code + tests)
- Performance optimization (not the current bottleneck)

---

## When in doubt

- Read existing test files in `tests/unit/test_migration_*.py` for patterns.
- Read the prior 3-5 CHANGELOG entries for the doctrine in practice.
- Ask Jake. The partnership directive (top of this file) explicitly says: don't just execute.
- Consider routing the question to The Log chat if it's a design or planning matter rather than implementation (per Tool Routing section).
