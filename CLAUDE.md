# CLAUDE.md — Curator

This file is read automatically by Claude Code at session start. It encodes the project conventions Claude must respect without being re-asked every session.

**Owner:** Jake Leese · **Updated:** 2026-05-13 (post-Round 5 complete — Lesson #106 retrospective)

---

## Project identity

- **Name:** Curator. Repo: `https://github.com/KULawHawk/Curator.git`. Local root: `C:\Users\jmlee\Desktop\AL\Curator\`.
- **Brand context:** Curator is one pillar within **Ad Astra** (the overarching umbrella). Governance constitution at `..\Atrium\CONSTITUTION.md` (v0.3 RATIFIED 2026-05-08). Constellation map at `..\AD_ASTRA_CONSTELLATION.md`.
- **Status: 🎯 v2.0.0-rc1 STAMPED (2026-05-13).** **214 ships total** (v1.0.0rc1 → v2.0.0-rc1, replacing v1.7.213 as HEAD). **Rounds 1–5 COMPLETE.** Round 5 final tally: Tier 1 (11 ships, v1.7.196-206) closed GUI Coverage Arc (gui/dialogs.py at 99.05%, 4,460 GUI stmts at ≥99%); Tier 2 (4 ships, v1.7.207-210) v2.0 RC1 prep — comprehensive coverage audit (99.76% Curator-wide, 0 missing lines!), release notes synthesis, constellation docs sync, README polish; Tier 3 (3 ships, v1.7.211-213) Atrium plugin coverage audit + Conclave Phase 0 readiness check (prerequisites CLEARED) + Nestegg trigger announcement. **76 of 78 source modules at 100% line + branch**. **8 multi-ship arcs closed** total. **4 real bugs surfaced & fixed by coverage work**. **Lessons through #106**. v2.0.0 stable release pending Jake's stamp ceremony in The Log after RC1 soak period. See `docs/RELEASE_NOTES_v2.0.md` for the formal narrative.
- **Python:** 3.13.12 in `.venv`. Windows 11 only (see Doctrine principle 3 below).

---

## Working-partnership directive

Per Jake's standing instruction: **speak up and push back when appropriate.** Aid Jake in avoiding wasted time and money. Build wisely without cutting corners on accuracy. Don't just execute — flag waste, propose alternatives, ask before shipping when shipping isn't obviously right.

---

## 🔴 REQUIRED: Lesson Adoption Protocol

This is non-negotiable. Before doing engineering work in a new session:

### At session start (within the first 5 turns)

1. **Read the most recent 5 CHANGELOG entries** in `CHANGELOG.md` and pay special attention to the "Lesson captured" sections.
2. **Read every lesson newer than the most recent one referenced in this CLAUDE.md.** The doctrine below lists lessons through #95. If CHANGELOG shows #96 or higher, those exist; read them.
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

These are non-negotiable. Read once, apply always. Lessons #79–105 below (#96–101 captured retroactively from Round 3 implicit patterns; #102 from Round 4 v1.7.180; #103–105 captured retroactively from Round 4 mid-Tier-4 implicit patterns); check CHANGELOG for #106+.

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

After every ship, write down lessons in the CHANGELOG entry's "Lesson captured" section. Number sequentially. Latest is #95 (v1.7.93b — arc-closure landmark for Migration Phase Gamma). Lessons compound — read prior ones before tackling unfamiliar territory. **See Lesson Adoption Protocol at top of this file — adopting lessons is REQUIRED, not optional.**

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

### 11. Test-design rewrites need a coverage diff (Lesson #93)

When *replacing* an existing test file with a different design (e.g. integration-style → direct-call unit tests, or the reverse), the new tests passing does NOT prove the new design covers everything the old design did. Integration-style tests carry *incidental* coverage of orchestration code paths; direct-call unit tests trade that for explicitness about what's tested. The trade only works if you compare the `--cov-report=term-missing` output against the pre-rewrite baseline.

**Discovery in v1.7.92:** the autostrip test file rewrite passed all 14 new tests but quietly uncovered line 830 (`self._auto_strip_metadata(move)` — the apply() dispatch into the helper). The old integration tests had implicitly covered it; the new direct-call tests skip apply() entirely. Coverage diff caught the regression before ship; a focused apply() integration test restored the line.

**How to apply:** before shipping a test-file rewrite (or deleting an integration test because "unit tests cover it"), diff the missing-line list. Lines that moved covered→uncovered need: (a) a restoring test, (b) `# pragma: no cover` with justification, or (c) explicit deferral documentation. This is distinct from Lesson #90 (control-flow tracing for new tests) — that's about correctness of new tests; this is about *coverage continuity* across the rewrite boundary.

### 12. Synchronous executor shim for testing threaded code (Lesson #94 — new)

When a production class uses `concurrent.futures.ThreadPoolExecutor` internally, prefer a synchronous shim over `workers=1` for unit tests. `workers=1` reduces concurrency but doesn't eliminate non-determinism: a real thread is still spawned, scheduling is still non-deterministic, and test teardown timing depends on thread join. Reserve real-executor tests for integration tests where the threading model is what you're testing.

**Pattern (v1.7.93b):** monkeypatch the module-level `ThreadPoolExecutor` reference (e.g. `curator.services.migration.ThreadPoolExecutor`) with a `_SyncExecutor` class whose `submit()` runs the callable inline and returns a future-like object whose `result()` returns the captured value (or re-raises the captured exception). The production code's loop (`for f in futures: f.result()`) is unchanged. All threading-related code paths (abort_event semantics, try/finally for cleanup, worker exception propagation via `f.result()`) are exercised.

**How to apply:** make the shim available via a pytest fixture (`@pytest.fixture def sync_executor(monkeypatch): ...`) and require it in any test that exercises threaded production code. Carries forward to other threaded code (Qt signal/slot in GUI tests, click.testing.CliRunner callbacks, async work eventually).

### 13. Pydantic validate_assignment bypass for defensive-boundary testing (Lesson #95)

Curator's models use pydantic v2 with `validate_assignment=True` (inherited from `CuratorEntity`). To test `except (AttributeError, TypeError):` clauses that catch type-incorrect field values at runtime, you can't inject the bad value via attribute assignment — pydantic rejects it.

**Pattern:** use `instance.__dict__[field] = bad_value` to bypass the descriptor entirely. The value lives in the instance dict; the next `getattr(instance, field)` returns it; method calls that assume the correct type then raise as expected.

**Discovery in v1.7.93b:** to test `run_job`'s `except (AttributeError, TypeError):` around `job.options.get("max_retries")` (which catches malformed options not being a dict), I tried `job.options = SimpleNamespace()` → `pydantic_core.ValidationError`. Fix: `job.__dict__["options"] = SimpleNamespace()`. Then `.get("max_retries")` raises AttributeError (no such method on SimpleNamespace) and the defensive except catches it.

**How to apply:** specifically for testing the "field has the wrong type at runtime" failure mode in pydantic-v2 models with `validate_assignment=True`. NOT a workaround to silently break model invariants in production code — only use in unit tests targeting specific defensive boundaries.

### 14. Coverage measurement varies with test selection (Lesson #96 — new from Round 3 retrospective)

When reporting "module X is at 100%", the number is conditional on which tests were run. Running `pytest tests/unit/test_migration_*.py --cov=curator.services.migration` may show 100%, while `pytest tests/ --cov=curator.services.migration` (with integration tests) may show 99.71%. The difference is integration-test-only paths that unit tests don't exercise.

**Discovery in v1.7.146:** Round 3 Tier 1's stabilization sprint investigated a coverage discrepancy on migration.py (reported 100% at v1.7.93b close, then 99.71% on a later measurement). Root cause: different pytest invocations covered different code paths. Lines 984-988 + branch 505→509 were integration-test-only paths.

**Rule:** when shipping a module to 100% under apex-accuracy, run the SAME pytest invocation your CI uses, not a narrower one. If unit tests alone produce 100% but the full suite produces less, the gap is either (a) integration-only lines (acceptable, but document) or (b) actual regression (close before ship). Document the invocation in release notes when it matters.

**Practical implementation:** the standard "shipping" invocation is `pytest tests/ --cov=curator.<module> --cov-report=term-missing --cov-branch`. Anything narrower is a fast-iteration tool, not a ship-quality measurement.

### 15. Mutation testing is its own arc, not a stabilization sub-task (Lesson #97 — new from Round 3 retrospective)

Mutation testing tools (mutmut, cosmic-ray) generate hundreds to thousands of code mutants per module and run the test suite against each one. For Curator-scale modules, this is **8-50+ hours of CPU time per module**, not minutes.

**Discovery in v1.7.150:** a Round 3 Tier 1 stabilization ship attempted mutmut on `services/migration.py` as a "spot-check". mutmut found 1092 mutants. Even with a focused test runner, that's 8-50 hours. Code killed the run honestly per the partnership directive (Lesson #92 — pre-commit to ship boundaries you can complete) and reported the budget overrun.

**Rule:** mutation testing is **never** a single-ship activity. It requires:
- A dedicated scope plan (per Lesson #88)
- Module-by-module batching (one module's mutmut run per ship at minimum)
- A triage protocol for survived mutations (categorize: actually-weak-test vs. equivalent-mutation vs. unreachable-code)
- Multi-day or multi-week budget on dedicated machine resources

**Future:** if mutation testing is desired for Curator, open it as an explicit "Mutation Testing Arc" with its own scope plan, baseline budget estimate (probably 1-2 weeks dedicated), and stop-conditions. Probably better suited to a CI nightly job than to interactive shipping. Full deferred-state record + signals-to-revisit + tool re-evaluation criteria in **[`docs/MUTATION_TESTING_DEFERRED.md`](docs/MUTATION_TESTING_DEFERRED.md)** (v1.7.182).

### 16. Qt headless testing pattern (Lesson #98 — new from Round 3 retrospective)

For testing PySide6 GUI modules under apex-accuracy without launching real windows, the established pattern is:

1. **Environment:** set `QT_QPA_PLATFORM=offscreen` before any Qt import. In test files: `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")` at module top, or via the shared `conftest.py`.
2. **QApplication singleton:** use a module-scoped `qapp` fixture:
   ```python
   @pytest.fixture(scope="module")
   def qapp():
       from PySide6.QtWidgets import QApplication
       return QApplication.instance() or QApplication(sys.argv)
   ```
3. **QThread workers:** call `worker.run()` directly (synchronous) instead of `worker.start()` + waiting for the event loop. The `run()` method is the actual work; bypassing the event loop makes tests deterministic.
4. **Stub QWidget classes:** for tests that would construct real windows/dialogs, `monkeypatch.setattr` the widget class to a lambda factory returning a stub object with just the methods the test needs.
5. **Signal payload type:** prefer `Signal(object)` over typed signals when payload shape varies across emit sites — lets tests emit arbitrary payloads without pydantic-style type rejection.

**Validated in v1.7.176–179:** four GUI signal modules (launcher, migrate_signals, scan_signals, cleanup_signals) covered 0% → 100% using this pattern alone. No `pytest-qt` dependency needed for signal-emitting modules.

**Extension for larger GUI modules:** dialogs, main_window, models likely need `pytest-qt`'s `qtbot` for actual widget interaction testing (button clicks, dialog acceptance, model row insertion). Add `pytest-qt` as a dev-dependency when the bigger GUI Coverage Arc opens.

### 17. Pragma audit at arc close (Lesson #99 — new from Round 3 retrospective)

For multi-ship arcs that close a single large module (e.g. `cli/main.py`, `services/migration.py`), reserve a final "pragma audit" ship for the defensive-boundary closures rather than dribbling `# pragma: no cover` annotations across all sub-ships.

**Why:**
- Per-ship pragmatization scatters the documentation — hard to audit later whether annotations are still justified.
- Batched at arc close, the team can see all defensive-boundary debt at once and decide which deserve real tests vs. pragmas.
- One coherent documentation pass produces uniform justification quality.

**Pattern (v1.7.175):** the CLI Coverage Arc's final ship batch-added 10 source pragmas to `cli/main.py`. Each pragma has a 1-line justification citing Lesson #91, grouped by defensive-boundary category (TB-size formatter unreachable on TB-class numbers, KeyboardInterrupt-during-confirm, None-default fallbacks, double-checks for already-validated conditions, etc.). The release notes lists every annotation with location and rationale.

**How to apply:** open large-module sweep arcs with a `pragma audit ship reserved` note in the scope plan. Sub-ships focus on real test surface; defensive-boundary residue accumulates and is resolved as one ship at arc close. Total ship count down by 1-3 (pragma sweep replaces multiple per-ship pragma decisions).

### 18. Surface dead/duplicate code for human decision (Lesson #100 — new from Round 3 retrospective)

When coverage work finds dead code, duplicate functions, or vestigial code paths, **never auto-delete**. Document the finding with options + impact + recommendation and defer to human decision.

**Discovery in v1.7.155:** Round 3 Tier 3 found a duplicate `_resolve_file` function at `cli/main.py` lines 187–216 with overlapping but slightly different semantics from the live version. The duplicate has substring matching the live version doesn't; the docstring references a function name that no longer matches reality.

Code's correct response: surface 3 options to Jake — (a) delete dead duplicate, (b) merge substring-match feature into live version, (c) update docstring only. Deferred decision. Then through 21 more sub-ships in the CLI arc, the deferral was preserved (the duplicate was pragma'd at v1.7.175 to keep ship momentum while the decision waited).

**Rule:** auto-deletion of code with unclear provenance can lose features silently. The cost of waiting for a human decision is small (one pragma annotation, maybe one ship's worth of "is this in scope right now"). The cost of wrong auto-deletion is potentially a regression that's invisible until it bites.

**Pattern for documenting:**
```
# DEFERRED: {function|class|module} at {file}:{line_range}
# Found: {what makes it suspicious - duplicate / unreferenced / pre-refactor remnant}
# Options:
#   (a) {action with consequence}
#   (b) {action with consequence}
#   (c) {action with consequence}
# Recommendation: {option letter + 1-line rationale}
# Decision: PENDING - {Jake / specific person / specific event}
```
Either in source as a comment block, or as a doc file like `docs/DEFERRED_DECISIONS.md`.

### 19. Defensive-boundary debt accumulates in big modules (Lesson #101 — new from Round 3 retrospective)

Large modules (1000+ statements) naturally accumulate defensive-boundary code: TB-size formatters that handle the impossible case, KeyboardInterrupt-during-confirm paths, None-default fallbacks for fields that are always set in practice, double-checks for conditions that were already validated upstream. These accumulate organically as the code matures and edge cases are discovered.

**Pattern observed (v1.7.175):** `cli/main.py` (1881 statements) needed **10 source pragmas at arc close** to document all defensive boundaries. Estimate: 1 pragma per 150-200 statements of mature CLI/orchestration code is normal. Pure data-transformation modules (models, parsers) typically have far fewer.

**Implication for scope planning:** when opening a large-module sweep arc, budget for:
- Real test coverage of all reachable lines
- A pragma audit closing ship (per Lesson #99)
- 5-15 pragmas with documented justifications (per Lesson #91)
- Each pragma is research time, not test-writing time — distinct activity

**Implication for code review:** if a new large module ships without any pragmas, either the module is unusually defensive-boundary-light, or the pragmas are owed and not yet annotated. Worth a focused review pass before declaring 100%.

### 20. Shadowed definitions become silent regressions (Lesson #102 — Round 4 v1.7.180)

When a module accumulates two `def name(...):` declarations at the same scope, Python binds the name to the **last** one. The first becomes silently dead. The hidden cost is not just dead lines — it's that any *advertised* behavior unique to the first definition (in help text, CLI argument helps, docstrings, user-facing strings) becomes a quiet lie.

**Discovery in v1.7.155 → v1.7.180:** `cli/main.py` had two `def _resolve_file`. The first (introduced v1.0.0rc1) supported UUID + exact-path + **path-prefix match** via SQL `LIKE`. The second (introduced v1.7.3 for the new status taxonomy) supported only UUID + exact-path. From v1.7.3 through v1.7.179, the prefix-match feature was silently dead — and `inspect`'s `typer.Argument(..., help="curator_id, full path, or path prefix.")` continued to advertise it. 175 ships of contract violation. v1.7.180 deleted the dead copy and merged the prefix-match into the live definition, restoring the documented behavior.

**Why static analysis didn't catch it:** ruff/pyflakes/mypy don't flag shadowed top-level definitions by default. `F811` (redefinition of unused name) only fires when the first definition is *also* unused; if anything reads or references the name between the two `def`s (imports, decorators, type hints), the duplicate passes lint clean.

**Rule:** when a coverage arc surfaces duplicate top-level definitions, the question is not just "delete or keep?" — it's **"what behavior did the dead copy advertise that's no longer happening?"** Before deciding:
1. Grep the surrounding CLI help text, docstrings, README, and user-facing strings for references to behaviors unique to the dead version
2. Check git history with `git log -S "def name"` to confirm whether the duplicate is a refactor remnant or an accidental shadowing
3. If user-facing strings reference behaviors unique to the dead version, that's a **silent regression** — option (b) "merge" usually beats option (a) "delete"
4. Surface to the human (per Lesson #100) with the silent-regression evidence in the recommendation

**Compounds with Lesson #100 (surface dead/duplicate code):** the v1.7.155 deferral correctly waited for Jake's call. The wait revealed not just a duplicate to delete, but a documented feature that had quietly regressed. Auto-deletion would have closed the door on the right answer.

### 21. GUI code is pragma-light when decomposition + testability seams are consistent (Lesson #103 — Round 4 retrospective)

The original Lesson #101 / Doctrine #19 estimate said large modules accumulate 1 pragma per 150-200 statements of mature application code. That held for `cli/main.py` (10 pragmas / 1881 stmts = ~1 per 188). But for GUI code in Curator, the actual figure has been **0 pragmas across 2300+ statements** so far.

**Pattern observed:** 4 modules closed in a row with 0 new pragmas added:
- `gui/launcher.py` (17 stmts), `gui/migrate_signals.py` (5 stmts), `gui/scan_signals.py` (25 stmts), `gui/cleanup_signals.py` (94 stmts) — Round 3 Tier 4
- `gui/lineage_view.py` (246 stmts) — Round 4 Tier 2
- `gui/models.py` (774 stmts, 380 branches) — Round 4 Tier 2
- `gui/main_window.py` (1089 stmts, 206 branches) — Round 4 Tier 3

**Why GUI is pragma-light here:** Curator's GUI was written with consistent patterns — every dialog/view has the same try/except → empty fallback shape for repo failures; every Qt protocol method follows the same DisplayRole/ToolTipRole/headerData structure; defensive boundaries are explicit (`if not hasattr(...)`) rather than implicit (`try:/except Exception:`). The uniformity makes every defensive path reachable via standard test patterns: stub-repo + createIndex + role-parameterized data() calls.

**Implication for scope planning:** when opening a GUI coverage arc, **set pragma budget to 0-2** (revised from #101's 5-15). If Code starts accumulating pragmas in GUI work, that's a signal that either (a) a defensive boundary is genuinely unreachable and the pragma is correct, or (b) the test strategy is wrong and needs revisiting. The default expectation is zero.

**Lesson #101 still holds for non-GUI mature application code** (CLI, service orchestration). This refinement applies specifically to GUI / Qt modules with consistent decomposition.

### 22. MagicMock can't honor property semantics; use a real class for property-access defensive tests (Lesson #104 — Round 4 retrospective)

To test defensive `except Exception:` clauses around property accesses on a stub (e.g. `runtime.config.source_path` where the property might raise), `MagicMock` is insufficient. `MagicMock.__getattr__` returns child mocks regardless of property semantics. The defensive boundary is unreachable through a MagicMock-based stub.

**Pattern (v1.7.191):** replace the stub with a **real class** that has an actual `@property` raising the desired exception:

```python
class _RaisingConfig:
    @property
    def source_path(self):
        raise AttributeError("simulated config access failure")

stub_runtime.config = _RaisingConfig()
# Now `runtime.config.source_path` actually raises, exercising the defensive except.
```

**How this differs from Lesson #95 (Pydantic `__dict__` bypass):**
- Lesson #95: target is a **Pydantic v2 field with validate_assignment=True**. Inject bad value via `instance.__dict__[field] = bad_value`. Used for `except (AttributeError, TypeError):` triggered by wrong-type-at-runtime.
- Lesson #104: target is a **regular Python `@property` on a stub class**. Replace the stub object entirely with a real class. Used for `except Exception:` triggered by property-access raises.

Both address "defensive boundary unreachable through standard mocking." Different mechanisms; same family of problems.

**When to use which:**
- Pydantic model field needs bad value → Lesson #95 (`__dict__` bypass)
- Stub service with `@property` that should raise → Lesson #104 (replace stub with real class)
- Plugin hook that should raise → Lesson #91 (monkeypatch `_hook_first_result` to raise)

### 23. Never `del` a class attribute in test cleanup; always restore the original (Lesson #105 — Round 4 retrospective)

When a test temporarily replaces a class attribute (especially a property) to inject failure behavior, the cleanup must **restore the original**, not `del` the attribute. `del type(instance).attr` works in isolation because the test doesn't access the attribute later, but in pytest suite order it leaves the class permanently broken for every subsequent test that touches that class.

**Discovery in v1.7.197:** `test_get_job_id_exception_returns` in `test_gui_main_window_part3_coverage.py` (v1.7.193) used `del type(model).job_id` in its `finally` block. The first time it ran, the `job_id` property was deleted from `MigrationProgressTableModel` entirely. Three tests in `test_gui_models_part3_coverage.py` that ran after it (in v1.7.190's file, which ran before v1.7.193's file alphabetically but **after** v1.7.197 ran the polluting test) failed with AttributeError.

**The trap:** the polluting test passes in isolation (`pytest test_gui_main_window_part3_coverage.py`). It only manifests when run alongside other tests that touch the same class. **Coverage measurement runs the full suite**, which is when the pollution surfaces.

**Pattern (correct):**
```python
cls = type(model)
original = cls.__dict__.get("job_id")  # capture BEFORE replacement
try:
    cls.job_id = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    # ... test the defensive except path
finally:
    if original is None:
        del cls.job_id  # OK only if it didn't exist before
    else:
        cls.job_id = original  # restore
```

Or simpler: use `monkeypatch.setattr(cls, "job_id", ...)` which auto-restores. The `monkeypatch` fixture is the canonical Python answer; only fall back to manual try/finally when you're outside a pytest test function.

**Generalized:** **never irreversibly mutate class state in test cleanup.** The cleanup must leave the class in exactly the state it was found. This applies to: class attributes, class properties, `__slots__` modifications, `__init_subclass__` overrides, anything at class scope.

### 24. Cross-pillar trigger-detection pattern (Lesson #106 — Round 5 retrospective)

When one Ad Astra pillar's progress unlocks work in another pillar (Curator v2.0 stamp → Nestegg brief generation; Conclave Phase 0 dependent on Curator's MCP being stable; future pillars triggered by Crucible directives) the unlocking event needs an explicit **trigger-detection document** so any subsequent Claude session (Log, Code, or other) can recognize the trigger fired and act on it without re-discovering the context.

**Discovery in v1.7.213:** the FINAL Round 5 ship created `docs/NESTEGG_TRIGGER_STATUS.md` per the trigger spec in `..\NESTEGG_BRIEF_PENDING.md`. The document codified a pattern that recurs across the constellation. Without explicit trigger docs, every cross-pillar dependency becomes tribal knowledge that future sessions have to re-derive.

**Pattern (the 5-section trigger-detection document):**

1. **Headline status** — human-readable indicator: 🟢 GO / 🟡 APPROACHING / 🔴 BLOCKED / ✅ FIRED. One-line summary of why.

2. **Trigger criteria check** — enumerated list of conditions, each marked satisfied/pending. Distinguishes "structurally satisfied" (the work is done) from "operationally satisfied" (the formal stamp has fired). Example: 7 of 8 conditions satisfied; remaining is Jake's v2.0 stamp ceremony.

3. **What happens when the trigger fires** — verbatim 10-step (or similar) action procedure. The receiving entity (Log Claude, Code session, human) follows this without re-deriving the steps. Include file paths, command examples, expected outputs.

4. **Authoritative inputs** — list of files (with locations) that should be at their expected state when the trigger fires. Receiving entity verifies these before executing the procedure.

5. **Detection mechanism** — 3+ indicators (git tag, CHANGELOG entry, version field, file existence, etc.) that distinguish "trigger fired" from "still pending". Receiving entity uses these as evidence the trigger event actually happened.

**Where these docs live:** in the *source* pillar's repo (the one producing the trigger), at `docs/<TARGET_PILLAR>_TRIGGER_STATUS.md`. Source pillar owns the trigger because it owns the state changes. Target pillar reads its own trigger doc to understand what's expected of it.

**How to apply:**
- When designing a new pillar with cross-pillar dependencies, identify trigger events upfront.
- For each trigger event, create or commit to create the trigger-detection doc.
- Update the trigger doc as criteria are progressively satisfied (status moves 🔴 → 🟡 → 🟢 → ✅).
- When the trigger fires, the source pillar's release notes should reference the trigger doc location for the receiving entity.

**Trigger doc lifecycle:**
- Created early (when the trigger relationship is first identified)
- Maintained as conditions progressively satisfied (live document)
- Closed/archived when trigger fires and target pillar acknowledges (mark ✅ FIRED, link to target's first ship that acted on it)

**Compounds with Lesson #92 (tool routing) and Lesson #100 (surface for human decision):** trigger detection is the *machine-readable* counterpart to the *human-readable* deferred-decisions index. Both pattern explicit communication across session/tool/human boundaries.

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

### GUI testing strategy

For PySide6 GUI modules (Round 4 Tier 2+), see **[`docs/GUI_TESTING_STRATEGY.md`](docs/GUI_TESTING_STRATEGY.md)** (v1.7.183). Foundation: the Qt headless pattern from Lesson #98 / Doctrine #16, proven on 4 modules in Round 3 Tier 4. Extension: `pytest-qt`'s `qtbot` fixture for widget interaction (modal dialogs, mouse clicks, menu activation) on the bigger modules. The strategy doc also carries the **post-Lesson-#93 revised baseline** for `gui/lineage_view.py`, `gui/models.py`, `gui/main_window.py`, `gui/dialogs.py` — line counts are 1.6–2.1× the Round 4 handoff predictions.

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

## Current arc state (Round 3 complete — v1.7.179)

```
ROUND 3 COMPLETE — 34 ships across 4 tiers (v1.7.146 -> v1.7.179)
  Tier 1: 6 ships (stabilization)              ✅
  Tier 2: 3 ships (CLI Coverage Arc kickoff)   ✅
  Tier 3: 21 ships (cli/main.py decomposition) ✅  10.73% -> 99.43%
  Tier 4: 4 ships (GUI signals)                ✅  4 modules: 0% -> 100% each
```

Awaiting Jake's direction on Round 4 / v2.0 cut. See `docs/RELEASE_NOTES_v2.0_DRAFT.md` (v1.7.151) and `docs/MUTATION_TESTING_REPORT.md` (v1.7.150) for forward-looking context.

**Pending decision deferred to Jake:** dead `_resolve_file` duplicate at `cli/main.py` lines 187-216 (3 options: delete / merge feature / fix docstring).

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
