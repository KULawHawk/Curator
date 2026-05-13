# GUI testing strategy

**Owner:** Jake Leese · **Updated:** 2026-05-13 (v1.7.183, Round 4 Tier 1 ship 4)

This document captures Curator's GUI testing strategy: the **proven
foundation** (Round 3 Tier 4) plus the **extension plan** for the
remaining large GUI modules (Round 4 Tier 2+).

Anchor lessons: **#98 (Doctrine #16)** for the Qt headless pattern,
**#88 (Doctrine #7)** for scope splitting, **#93 (Doctrine #11)** for
the re-measure-baselines rule that produced this doc's corrected size
estimates.

---

## Baseline check (Lesson #93 / Doctrine #11)

Re-measured today with `pytest tests/unit/ --cov=curator.gui --cov-branch`:

| Module | Handoff predicted (stmts) | Measured stmts | Raw lines | Existing coverage |
|---|---:|---:|---:|---:|
| `gui/lineage_view.py` | 246 | **246** | 513 | **0.00%** |
| `gui/models.py` | 774 | **774** | 1,249 | **11.35%** (incidental from existing tests) |
| `gui/main_window.py` | 1,089 | **1,089** | 2,307 | **7.57%** (incidental from existing tests) |
| `gui/dialogs.py` | 2,234 | **2,234** | 4,286 | **0.00%** |
| **Total** | **4,343** | **4,343** | **8,355** | — |

**Handoff predictions match measured statement counts exactly** — they were sampled correctly. Raw line counts are 1.6–2.1× higher because of docstrings, blanks, and comments, but coverage measures statements, not raw lines. **No ship-count revision needed; the original handoff estimates stand.**

This is itself a useful Lesson #93 datapoint that has now been recorded:

> **When re-measuring baselines, use the same metric the original estimate used.** Raw `wc -l` is *not* the same as `coverage`-style statement count. Confirm the metric before declaring "predictions stale" — otherwise you risk a false alarm that itself violates Lesson #93's spirit (re-measure with rigor, not with the first easy number).

Codified inline here rather than as a new doctrine item because it's a clarification on existing Lesson #93, not an independent rule.

### Existing incidental coverage

`main_window.py` (7.57%) and `models.py` (11.35%) already have some coverage from existing GUI integration tests (the ones that hang on the recycle bin in this sandbox — see `docs/DEFERRED_DECISIONS.md` #1). Round 4's Tier 2/3 ships need to **cover the remaining 88–93%** for those modules, which is the bulk of the work but slightly less than starting from zero.

---

## Foundation: the Qt headless pattern (proven)

Round 3 Tier 4 covered **4 GUI modules (launcher, migrate_signals,
scan_signals, cleanup_signals — 546 total lines)** from 0% to 100%
line + branch using this pattern alone, no `pytest-qt` dependency.

### Components

1. **Headless platform.** Set `QT_QPA_PLATFORM=offscreen` before any
   Qt import:
   ```bash
   # Bash / CI
   QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/unit/ ...
   ```
   ```powershell
   # PowerShell
   $env:QT_QPA_PLATFORM = "offscreen"; .\.venv\Scripts\python.exe -m pytest ...
   ```

2. **Module-scoped `qapp` fixture.** Share one `QApplication` instance
   across the whole test module:
   ```python
   @pytest.fixture(scope="module")
   def qapp():
       from PySide6.QtWidgets import QApplication
       import sys
       return QApplication.instance() or QApplication(sys.argv)
   ```

3. **Direct `worker.run()` calls.** For `QThread`-based workers, call
   `worker.run()` directly (synchronous) instead of `worker.start()` +
   event-loop wait. Bypasses the event loop entirely; tests stay
   deterministic.

4. **Stub QWidget classes.** For tests that would construct real
   windows/dialogs, `monkeypatch.setattr` the widget class with a
   factory returning a stub:
   ```python
   class _StubDialog:
       def __init__(self, *a, **kw): ...
       def exec(self): return 1   # QDialog.Accepted
   monkeypatch.setattr("curator.gui.dialogs.MigrateDialog", _StubDialog)
   ```

5. **`Signal(object)` for variable payloads.** Where signal payload
   shape varies across emit sites, declare the signal as
   `Signal(object)` instead of `Signal(SpecificType)`. This avoids
   PySide6's runtime type rejection on emit and keeps tests free to
   send any shape.

### Where it works

- **Signal-emitting modules** (the 4 Round 3 Tier 4 modules)
- **QThread workers** where the body is the `run()` method
- **Pure logic helpers** that happen to live in `gui/` (formatters,
  view-model glue)

### Where it doesn't work alone

These need `pytest-qt`'s `qtbot` fixture (see next section):

- **Real widget interaction** (button clicks, menu activation, mouse
  events)
- **Modal dialog acceptance/rejection** (you need `qtbot.mouseClick`
  or `qtbot.keyClicks` on the OK/Cancel button to drive `exec()` to
  return)
- **Qt model/view interactions** (row insertion via the model API +
  view repaint)
- **Event-loop-dependent behavior** (timers, queued connections,
  `QApplication.processEvents()`-driven work)
- **Drag-drop, paint events, focus changes**

The foundation pattern is necessary but not sufficient for these.

---

## Extension: `pytest-qt` for widget interaction

### Why `pytest-qt`

`pytest-qt` provides a `qtbot` fixture that wraps test widgets in a
proper Qt event loop, with convenience methods for the things the
foundation pattern can't reach:

- `qtbot.mouseClick(widget, button)` — synthesizes a real click
- `qtbot.keyClicks(widget, text)` — types into the widget
- `qtbot.waitSignal(signal, timeout=1000)` — blocks until signal fires
- `qtbot.waitUntil(callable, timeout=1000)` — polls a condition
- `qtbot.addWidget(widget)` — ensures cleanup after the test

`pytest-qt` is widely used, MIT-licensed, and integrates cleanly with
the existing `conftest.py`. Single-line addition to pyproject.toml's
dev dependencies.

### What the v1.7.184 smoke test must prove

- The `qtbot` fixture is available (`def test_X(qtbot, qapp): ...`)
- A trivial widget can be added and clicked
- A signal can be awaited via `qtbot.waitSignal`

Beyond that, each module's coverage tests will exercise `qtbot` further
as needed.

---

## Module-by-module strategy

### `gui/lineage_view.py` (~513 lines) — Tier 2 ships v1.7.186-187

**What it is:** a tree/graph viewer for the lineage edges of a selected
file. Likely a `QTreeView` or `QGraphicsView` subclass + model
integration.

**Test approach:**
- Instantiate the widget under `qapp` + `qtbot.addWidget`
- Feed it a fixture-built lineage report (small set of files + edges)
- Verify model row count / tree depth via the widget's own
  `model().rowCount(...)` API (no view interaction needed)
- For selection behavior: use `qtbot.mouseClick` on the rendered row
  rectangle, then assert the selection-changed signal payload
- For context-menu actions: trigger programmatically via the action
  object (e.g. `widget.copy_action.trigger()`) rather than synthesizing
  a right-click + menu navigation

**Predicted pragma budget:** **2–4** (Lesson #101 / Doctrine #19;
small-module rate is 1 per 150-200 statements). Likely a few defensive
boundaries on "lineage edge unexpectedly None" type guards.

**Ship plan:** 2 ships (Part 1 + Part 2-with-pragma-audit) — within
Lesson #88's 1.5× tolerance.

### `gui/models.py` (~1,249 lines) — Tier 2 ships v1.7.188-191

**What it is:** Qt model subclasses (probably
`QAbstractItemModel`/`QStandardItemModel`/`QSortFilterProxyModel`) for
file lists, lineage, scan results, etc.

**Test approach:**
- Construct models in isolation (no view attached)
- Drive the model API directly: `setData`, `insertRows`, `removeRows`,
  `data`, `rowCount`, `columnCount`, `headerData`, `flags`
- Use `qtbot.waitSignal` for signal emissions on
  `rowsInserted`/`rowsRemoved`/`dataChanged`
- For filter/sort proxy models: source-model the wrapped Qt API rather
  than a stub, then exercise filter/sort criteria

**Predicted pragma budget:** **5–7** (medium-module, ~1 per 200
statements). Likely on `headerData` Qt-role exhaustion fallbacks and
"sentinel item" defensive returns.

**Ship plan:** 2-4 ships per the handoff (Part 1, Part 2, optional Part 3, pragma audit). 11.35% already covered incidentally by existing GUI tests — Round 4 needs to close the remaining ~88%.

### `gui/main_window.py` (~2,307 lines) — Tier 3 (opt-in)

**What it is:** the application shell. Menus, toolbars, dock widgets,
status bar, action handlers, signal/slot wiring to services, window
state persistence, close-event handling.

**Test approach:**
- Decompose by functional area, one ship per area
- For action handlers: invoke `action.trigger()` directly,
  monkeypatch the service calls they invoke, assert the side effects
- For menus: walk the menu tree via the QMenuBar API, assert presence
  + connections
- For dock widget toggle: invoke `dock.setVisible(False)` directly,
  verify state-persistence write
- For signal/slot wiring: use `qtbot.waitSignal` to verify the wiring
  triggers the expected service call
- For window state persistence: use a tmp_path-backed QSettings,
  exercise save/restore round-trip

**Predicted pragma budget:** **10–12** (large-module rate, ~1 per
200-statements). Plus 1–2 dialogs.py defensive imports that get hit
during action setup.

**Ship plan:** 3-5 ships per handoff. 7.57% already covered incidentally; ~1,000 statements remaining. Suggested decomposition:
1. Window init + menus + toolbars (~250 stmts)
2. Action handlers (~300 stmts)
3. Dock widgets + state persistence (~200 stmts)
4. Signal/slot wiring + close handling (~250 stmts)
5. Pragma audit + close

### `gui/dialogs.py` (~4,286 lines) — Tier 4 (opt-in, likely Round 5)

**What it is:** every modal dialog in the application. Per the
handoff's inventory hypothesis: migration confirmations, source
configuration, scan/cleanup/organize confirmations, settings/preferences,
about/version/help.

**Doctrine call (Lesson #88):** at 2,234 statements, this is **2× larger than `main_window.py`** and likely the biggest single module Curator has ever opened a coverage arc on. The handoff explicitly flagged it ("may need to defer to Round 5"). Recommended path:

1. **Decomposition doc first** (v1.7.197 or equivalent): list every dialog class in the module with its statement count + complexity tag. This is itself a sub-ship.
2. **Per-dialog ships:** one dialog class per ship; complex ones (with their own internal models, multi-step flows) get split further. Probably **6-10 sub-ships** for full coverage.
3. **Pragma audit:** a closing ship per Lesson #99.

**Estimated total: 8-12 ships** (one decomposition doc + 6-10 per-dialog ships + 1 pragma audit). **Default expectation: dialogs.py is a Round 5 arc** — Round 4 budget is already meaningful at Tier 1 + Tier 2 + Tier 3 (12-17 ships).

**Test approach (when it opens):** for each dialog class:
- Construct with stubbed runtime + repos
- For modal flows: use `qtbot.waitUntil` to advance through the dialog's
  internal state machine, then call `dialog.accept()` / `dialog.reject()`
- For multi-tab dialogs: drive `setCurrentIndex` directly, exercise each
  tab's inputs
- For dialogs that emit progress signals: pair with `qtbot.waitSignal`
- For dialogs that wrap a service call: monkeypatch the service, assert
  the call args produced by user-input simulation

**Predicted pragma budget:** **12–18** (largest module, several
defensive fallbacks per dialog).

---

## Predicted total pragmas for the GUI Coverage Arc

Per Lesson #101 / Doctrine #19 (1 pragma per 150-200 mature statements):

| Module | Predicted pragmas at arc close |
|---|---:|
| `gui/lineage_view.py` | 2-4 |
| `gui/models.py` | 5-7 |
| `gui/main_window.py` | 10-12 |
| `gui/dialogs.py` | 12-18 |
| **Total** | **29-41** |

Each gets a documented justification (Lesson #91). Sweep batched at
each module's arc-close per Lesson #99.

---

## Ship counts: handoff prediction (confirmed)

| Tier | Module | Predicted (statement count basis) |
|---|---|---:|
| 2 | lineage_view.py | 2-3 |
| 2 | models.py | 2-4 |
| 3 | main_window.py | 3-5 |
| 4 | dialogs.py | 4-8 (may defer to R5) |
| **Total** | | **11-20** |

The handoff estimates stand. Sub-ship splits may still be needed per Lesson #88 once implementation reveals areas of unexpected complexity, but the **module-by-module ship counts are correctly scaled to statement counts.**

**Round 4 minimum viable** = Tier 1 (5 ships) + Tier 2 (4-7 ships) = **9-12 ships total**. **Stretch** = +Tier 3 (3-5 ships) = **12-17 ships**. **Dialogs.py (Tier 4) defaults to Round 5** per the handoff and per Lesson #88 — its ship count alone risks exceeding remaining budget.

---

## See also

- **`CLAUDE.md` Doctrine #16 (Lesson #98)** — the Qt headless pattern
  rule; this doc is the detail page for it
- **`CLAUDE.md` Doctrine #11 (Lesson #93)** — re-measure baselines; this
  doc is what that rule produced for Round 4 Tier 2 prep
- **`CLAUDE.md` Doctrine #7 (Lesson #88)** — scope splitting; the
  source of the 1.5× threshold cited above
- **`CLAUDE.md` Doctrine #17 (Lesson #99)** — pragma audit at arc
  close; each module's close ship implements this
- **`docs/ROUND_3_LESSONS_RETROSPECTIVE.md`** §Lesson #98 — the full
  discovery context for the Qt headless pattern
- **`docs/GUI_COVERAGE_ARC_SCOPE.md`** — the v1.7.185 scope plan
  (next ship after v1.7.184 pytest-qt setup) will reference this doc
  for the testing approach
- **`tests/unit/test_gui_cleanup_signals_coverage.py`** — the most
  complex Tier 4 ship; canonical example of the foundation pattern
  with multiple bridges + workers
