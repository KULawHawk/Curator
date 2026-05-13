# Round 3 Lessons Retrospective

**Captured:** 2026-05-13 (post-Round 3 close at v1.7.179)
**Reason:** Code reported "zero new numbered lessons in Round 3 — doctrine at saturation" at v1.7.179 release notes. The Log review found 6 implicit lessons that should be formalized for the lesson library. This doc records them with full discovery context and recommendations.

---

## Why retroactive capture matters

Per Lesson #5 (lesson capture is required) and the 🔴 Lesson Adoption Protocol in CLAUDE.md: lessons are the single most valuable artifact this project produces. They compound across sessions. A lesson that ships unnamed is a lesson that future Claude sessions can't inherit.

Code's "doctrine at saturation" report wasn't wrong — every situation in Round 3 *did* map onto existing lessons. But the act of *applying* established lessons in new contexts (GUI testing with Lesson #84 + Lesson #91 + a new Qt-headless pattern) creates a new compound lesson worth naming. The lesson library should record application-of-lessons patterns alongside the original discoveries.

---

## Lesson #96 — Coverage measurement varies with test selection

**Status:** Captured retroactively from v1.7.146 discovery.
**Added to CLAUDE.md as Doctrine #14.**

### Discovery
v1.7.146 (Round 3 Tier 1's first stabilization ship) investigated a coverage discrepancy on `services/migration.py`:
- v1.7.93b arc-closure release notes reported 100.00% line + branch
- A later coverage measurement showed 99.71% with lines 984-988 + branch 505→509 uncovered
- Root cause: different `pytest` invocations covered different paths. The "100%" report came from `pytest tests/unit/test_migration_*.py --cov=...`. The 99.71% came from `pytest tests/ --cov=...` (full suite including integration). Lines 984-988 were integration-test-only paths.

### Why this matters
The apex-accuracy doctrine (#1) demands 100% line + branch coverage. But "100% coverage" is only meaningful relative to a test selection. A module reported at 100% can quietly slip to <100% if integration-only paths were never counted in the baseline.

### Rule
When shipping a module to 100%, use the SAME `pytest` invocation CI uses. The standard "shipping" invocation is:
```powershell
pytest tests/ --cov=curator.<module> --cov-report=term-missing --cov-branch
```
Anything narrower (e.g., just `tests/unit/`) is a fast-iteration tool, not a ship-quality measurement.

### Application
- v1.7.146 closed the migration.py gap by either covering the integration-only lines or adding documented pragmas.
- Future module-sweep arcs should specify the invocation in their scope plans.
- Release notes should document the invocation used when measuring final coverage.

---

## Lesson #97 — Mutation testing is its own arc, not a stabilization sub-task

**Status:** Captured retroactively from v1.7.150 killed run.
**Added to CLAUDE.md as Doctrine #15.**

### Discovery
v1.7.150 (Round 3 Tier 1 stabilization) attempted a "mutmut spot-check" on `services/migration.py`. mutmut generated **1092 mutants**. Even with a focused test runner, the run was estimated at 8-50 hours of CPU time.

Code did the right thing: killed the run, reported the budget overrun honestly, and recorded the stop per the partnership directive. This was Lesson #92 (pre-commit to ship boundaries you can complete) and Lesson #88 (split if scope grows beyond 1.5x) applied in real time.

### Why this matters
Mutation testing is a fundamentally different scale of work than coverage testing. mutmut runs the entire test suite once per mutant. For Curator-scale modules, that's hours to days per module, not minutes.

The original Round 3 handoff (`CLAUDE_CODE_HANDOFF_ROUND3.md`) scoped mutmut as a "spot-check" tier-1 item. That was a planning error on the part of the handoff author (The Log Claude), not a Code failure. Code correctly recognized the scope mismatch and reported honestly.

### Rule
Mutation testing is **never** a single-ship activity. It requires:
- Its own scope plan (per Lesson #88)
- Module-by-module batching (one module per ship minimum)
- A triage protocol for survived mutations:
  - **Actually-weak-test:** add a test that kills the mutant
  - **Equivalent-mutation:** mutant produces identical observable behavior → can't be killed → document
  - **Unreachable-code:** mutant lives in code that's never executed → pragma the source line
- Multi-day to multi-week budget on dedicated CPU resources

### Future path
If mutation testing is desired for Curator, open a **Mutation Testing Arc** as a separate workstream. Probably 1-2 weeks dedicated. Probably better suited to a CI nightly job than to interactive shipping. See `docs/MUTATION_TESTING_DEFERRED.md` (created in v1.7.150 to document the deferred state).

### Application
- Round 3 handoff (and any future handoff) should NOT include mutation testing as a stabilization item.
- The Round 4 handoff and beyond should reference Lesson #97 when discussing mutation testing options.

---

## Lesson #98 — Qt headless testing pattern

**Status:** Captured retroactively from v1.7.176–179 validated pattern.
**Added to CLAUDE.md as Doctrine #16.**

### Discovery
Round 3 Tier 4 (the stretch tier) covered 4 PySide6 GUI signal modules from 0% to 100% line + branch without launching real windows. The pattern that emerged:

1. **`QT_QPA_PLATFORM=offscreen`** environment variable set before any Qt import
2. **Module-scoped `qapp` fixture:**
   ```python
   @pytest.fixture(scope="module")
   def qapp():
       from PySide6.QtWidgets import QApplication
       return QApplication.instance() or QApplication(sys.argv)
   ```
3. **Direct `worker.run()` calls** instead of `worker.start()` + waiting for the QThread event loop
4. **Stub QWidget classes** via `monkeypatch.setattr` for isolation
5. **`Signal(object)`** instead of typed signals for variable-payload signals

### Why this matters
Before Round 3 Tier 4, GUI testing was considered "needs strategy first" and deferred. After 4 successful ships using this pattern, the strategy is **proven for signal-emitting modules**. The 4 modules tested cover:
- `gui/launcher.py` (17 lines) — application launch + reuse-existing-app logic
- `gui/migrate_signals.py` (5 lines) — single signal bridge
- `gui/scan_signals.py` (25 lines) — 4-signal bridge + QThread worker
- `gui/cleanup_signals.py` (94 lines) — 2 bridges, 4 QThread workers, mode-dispatch validation

### Limits of the pattern
The pattern works for **signal-emitting modules and QThread workers** where the work is the `run()` method body. It does NOT cover:
- Real widget interaction (button clicks, dialog acceptance)
- Model row insertion/removal with view updates
- Event-loop-dependent behavior (timers, queued connections)
- Drag-drop, paint events, mouse/keyboard events

### Extension required for larger GUI modules
`gui/dialogs.py` (2234 lines), `gui/main_window.py` (1089 lines), `gui/models.py` (774 lines), and `gui/lineage_view.py` (246 lines) will likely need `pytest-qt`'s `qtbot` fixture for full coverage. Add `pytest-qt` as a dev-dependency when opening the big GUI Coverage Arc.

### Application
- All future GUI signal module work uses this pattern by default.
- The big GUI Coverage Arc scope plan should explicitly include "add pytest-qt to dev dependencies" as a setup ship.
- Round 4 handoff includes this pattern as the foundation for the GUI Coverage Arc.

---

## Lesson #99 — Pragma audit at arc close

**Status:** Captured retroactively from v1.7.175 pattern.
**Added to CLAUDE.md as Doctrine #17.**

### Discovery
The CLI Coverage Arc's final ship (v1.7.175) batch-added **10 source pragmas** to `cli/main.py` rather than dribbling them across the 21 sub-ships of the arc. Each pragma:
- Cites Lesson #91 in its justification
- Documents the specific defensive-boundary category (TB-size formatter unreachable, KeyboardInterrupt-during-confirm, None-default fallbacks, double-checks for validated conditions, etc.)
- Is recorded with location in the release notes

### Why this matters
**Per-ship pragmatization scatters the documentation.** A reviewer 6 months later can't easily audit whether annotations are still justified. **Batched at arc close**, the team sees all defensive-boundary debt at once and can make uniform decisions about which deserve real tests vs. pragmas.

The pattern also slows the ship cadence less: instead of pausing at every sub-ship to write a careful pragma justification, sub-ships focus on real test surface and defensive-boundary residue accumulates as one coherent resolution ship.

### Rule
Open large-module sweep arcs with a **"pragma audit ship reserved"** note in the scope plan. Sub-ships:
- Focus on real test surface
- Note defensive-boundary candidates inline as TODO comments
- Pragma decisions deferred to the closing ship

Closing ship:
- Reviews all accumulated TODOs
- Decides pragma vs. real-test for each
- Writes uniform justifications
- Documents in release notes with locations

### Application
- Round 4's GUI Coverage Arc scope plan should include a pragma audit ship.
- Any future large-module arc (>500 statements) should include a pragma audit ship in its scope plan.

---

## Lesson #100 — Surface dead/duplicate code for human decision

**Status:** Captured retroactively from v1.7.155 finding + v1.7.179 deferral.
**Added to CLAUDE.md as Doctrine #18.**

### Discovery
v1.7.155 (first ship of Round 3 Tier 3's cli/main.py decomposition) found a duplicate `_resolve_file` function at `cli/main.py` lines 187-216:
- Live function: standard signature, used by `inspect` and other commands
- Duplicate function: same name, slightly different semantics (has substring matching), docstring references a function name that no longer matches reality

Code's correct response: surfaced 3 options to Jake:
- **(a)** Delete dead duplicate
- **(b)** Merge substring-match feature into live version
- **(c)** Update docstring only

Decision deferred. Through 21 more sub-ships in the CLI arc, the deferral was preserved (the duplicate was pragma'd at v1.7.175 to keep ship momentum while waiting). At Round 3 close (v1.7.179), the decision was explicitly flagged again in the close-out report.

### Why this matters
**Auto-deletion of code with unclear provenance can lose features silently.** The duplicate might be:
- Dead code from a refactor (delete is right)
- Half-implemented feature (merge is right)
- Documentation reference target (update docstring is right)
- Backup of a previous working version (delete after verifying nothing depends on it)

Without context, Code can't pick. **The cost of waiting for a human decision is small.** The cost of wrong auto-deletion is potentially a regression invisible until it bites.

### Rule
When coverage work surfaces dead/duplicate code:
1. Document the finding (location, why suspicious)
2. List 2-4 options with consequences
3. Recommend one with a 1-line rationale
4. Mark as `DEFERRED - PENDING <decision_maker>`
5. Pragma the source if needed to keep ship momentum
6. Surface in release notes at arc close

### Pattern for documenting
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

### Application
- The `_resolve_file` duplicate decision is still pending for Jake.
- Round 4 Tier 1 should resolve this decision as a focused ship.
- A `docs/DEFERRED_DECISIONS.md` file is recommended as a single index of pending decisions across arcs.

---

## Lesson #101 — Defensive-boundary debt accumulates in big modules

**Status:** Captured retroactively from v1.7.175 pragma sweep.
**Added to CLAUDE.md as Doctrine #19.**

### Discovery
`cli/main.py` (1881 statements) needed **10 source pragmas at arc close** to document all defensive boundaries. The pragmas covered:
- TB-size formatters (e.g., handling >1TB in `fmt_size` when no practical use case produces TB-scale data)
- KeyboardInterrupt-during-confirm paths (Ctrl-C between prompt and response)
- None-default fallbacks (fields that are always set in practice but typed as Optional)
- Double-checks for conditions already validated upstream (defense-in-depth)

### Why this matters
**Large modules naturally accumulate defensive-boundary code** as they mature. This is *good engineering* — defensive code is the engineer's seatbelt. But it produces a predictable amount of uncoverable code that has to be pragmatized rather than tested.

The rough rate observed: **1 pragma per 150-200 statements of mature CLI/orchestration code.** Pure data-transformation modules (models, parsers, validators) have far fewer because they have less internal state and fewer "the impossible just happened" branches.

### Implication for scope planning
When opening a large-module sweep arc, budget for:
- Real test coverage of all reachable lines
- A pragma audit closing ship (per Lesson #99)
- 5-15 pragmas with documented justifications (per Lesson #91)
- Each pragma is research time (figure out *why* the line is unreachable), not test-writing time

### Implication for code review
If a new large module ships without any pragmas:
- Either the module is unusually defensive-boundary-light (e.g., pure data transformation)
- Or the pragmas are owed and not yet annotated (regression)

Worth a focused review pass before declaring 100% on any module >500 statements.

### Application
- `gui/dialogs.py` (2234 lines) will likely need 10-15 pragmas at arc close
- `gui/main_window.py` (1089 lines) will likely need 5-8
- `gui/models.py` (774 lines) will likely need 4-6
- `gui/lineage_view.py` (246 lines) will likely need 1-3

---

## Updated lesson library state

After this retrospective capture, the lesson library spans **#79–101** (23 lessons). CLAUDE.md doctrine has been updated to include all of them. Future Claude Code sessions inherit the full library via the Lesson Adoption Protocol at the top of CLAUDE.md.

**Code's "doctrine at saturation" report at v1.7.179** was 80% accurate (the lessons were being *applied*, just not *named*). The 20% gap was the absence of formal capture for the new compound patterns. This retrospective closes that gap.

**Going forward:** Code should still formally capture lessons even when they feel like "applications of existing doctrine." A lesson named is a lesson that compounds; a lesson unnamed is a lesson that future sessions have to re-discover.
