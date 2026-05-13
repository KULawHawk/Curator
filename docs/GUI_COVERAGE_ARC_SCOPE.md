# GUI Coverage Arc — Scope Plan

**Status:** Active arc plan — opened v1.7.185 (Round 4 Tier 2)
**Owner:** Curator engineering doctrine
**Created:** 2026-05-13 (v1.7.185)
**Modules:** 4 PySide6 GUI modules across `src/curator/gui/`
**Target:** All 4 modules at 100% line + branch (per apex-accuracy doctrine)
**Companion docs:** `docs/GUI_TESTING_STRATEGY.md` (foundation + extension), `docs/DEFERRED_DECISIONS.md` (sandbox fragility)

---

## Why this arc

After Round 3 closed `cli/main.py` and the 4 small GUI signal modules, the **bigger GUI modules are the largest remaining uncovered region** in Curator. Round 4 Tier 2 + 3 close two of them (`lineage_view`, `models`, `main_window`); Tier 4 / Round 5 handles the largest (`dialogs.py`).

This arc is the gating item for v2.0 release readiness on the GUI layer.

## Baselines (re-measured 2026-05-13 per Lesson #93)

Verified against HEAD `7cad661` (v1.7.184). **Statement counts match the handoff exactly** — see `docs/GUI_TESTING_STRATEGY.md` for the Lesson #93 metric-matching clarification.

| Module | Stmts | Misses | Partials | Coverage | Raw lines |
|---|---:|---:|---:|---:|---:|
| `gui/lineage_view.py` | 246 | 246 | 54 br | **0.00%** | 513 |
| `gui/models.py` | 774 | 643 | 380 br | **11.35%** | 1,249 |
| `gui/main_window.py` | 1,089 | 991 | 206 br | **7.57%** | 2,307 |
| `gui/dialogs.py` | 2,234 | 2,234 | 432 br | **0.00%** | 4,286 |
| **Total** | **4,343** | **4,114** | **1,072 br** | — | 8,355 |

`main_window.py` and `models.py` have incidental coverage from existing GUI integration tests (the ones that hang in this sandbox per DEFERRED_DECISIONS #1). Round 4 Tier 2+3 needs to close the remaining 88–93%.

## Tier 2 (Round 4) — 2 modules, 5-7 ships

| Ship | Scope | Predicted uncovered |
|---|---|---:|
| **v1.7.185** | scope plan (this doc) | — |
| **v1.7.186** | `gui/lineage_view.py` Part 1 — widget construction + model wiring + selection signals | ~120 stmts |
| **v1.7.187** | `gui/lineage_view.py` Part 2 + pragma audit close | ~126 stmts |
| **v1.7.188** | `gui/models.py` Part 1 — first 2-3 model classes (file list / scan result) | ~250 stmts (of the 643 uncovered) |
| **v1.7.189** | `gui/models.py` Part 2 — middle 2-3 model classes (lineage / bundles / tier) | ~250 stmts |
| **v1.7.190** | `gui/models.py` Part 3 + pragma audit close — remaining model classes + filter/sort proxies | ~143 stmts |

**Total Tier 2: 6 ships** (well within handoff's 5-7 range).

## Tier 3 (Round 4 opt-in) — `gui/main_window.py` — 5 ships

Decomposed by functional area per `docs/GUI_TESTING_STRATEGY.md`:

| Ship | Scope | Predicted uncovered |
|---|---|---:|
| **v1.7.191** | Window init + menus + toolbars | ~250 stmts |
| **v1.7.192** | Action handlers (file/scan/migration triggers) | ~300 stmts |
| **v1.7.193** | Dock widgets + state persistence | ~200 stmts |
| **v1.7.194** | Signal/slot wiring + close handling | ~241 stmts (the bulk of remaining) |
| **v1.7.195** | Pragma audit close + final 100% gate | TBD |

**Total Tier 3: 5 ships.**

## Tier 4 (Round 4 stretch / default Round 5) — `gui/dialogs.py` — 8-12 ships

**Default expectation: deferred to Round 5.** At 2,234 statements, this is the biggest module Curator has opened a coverage arc on. If Tier 1+2+3 close with significant Round 4 budget remaining, the decomposition doc could land in Round 4 (v1.7.196) and per-dialog ships start; otherwise hand off cleanly.

| Ship (if attempted) | Scope | Predicted uncovered |
|---|---|---:|
| **v1.7.196** | `docs/DIALOGS_DECOMPOSITION.md` listing every dialog class + line count + complexity tag | doc-only |
| **v1.7.197–204** | One ship per dialog class (or split for complex ones) | ~2,234 stmts split across ~7-9 ships |
| **v1.7.205** | Pragma audit close | — |

**Total Tier 4 (if attempted): 9-11 ships.**

## Honest scope assessment

- **Tier 1 already closed** (5 ships, v1.7.180-184) — deferred items resolved, strategy docs written, pytest-qt validated
- **Tier 2 = 6 ships** confirmed at this scope plan
- **Tier 3 = 5 ships** likely confirmed; first sub-ship will revalidate baseline per Lesson #93
- **Tier 4 = 9-11 ships** default-deferred to Round 5

**Total Round 4 minimum viable (Tier 1 + Tier 2):** 11 ships, 184 → **190**.
**Total Round 4 stretch (Tier 1 + 2 + 3):** 16 ships, 184 → **195**.
**Total Round 4 absolute stretch (all 4 tiers):** 25-27 ships, 184 → **204-206**.

## Test patterns (established in v1.7.176-179 + extended via pytest-qt)

### Foundation (Lesson #98 / Doctrine #16)

```python
# At top of test file
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# In test file
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    import sys
    return QApplication.instance() or QApplication(sys.argv)
```

### Extension: pytest-qt (validated v1.7.184)

```python
def test_button_click(qtbot, qapp):
    from PySide6.QtWidgets import QPushButton
    btn = QPushButton("Test")
    qtbot.addWidget(btn)
    with qtbot.waitSignal(btn.clicked, timeout=1000):
        qtbot.mouseClick(btn, Qt.MouseButton.LeftButton)
```

### Stubbing services

```python
runtime = MagicMock()
runtime.file_repo.find_by_path.return_value = mock_file_entity
# Drive the model/widget with the stub
```

## Pragma budget per module (Lesson #101 / Doctrine #19)

Predicted at 1 pragma per 150-200 statements of mature GUI code:

| Module | Predicted pragmas | Rationale |
|---|---:|---|
| `gui/lineage_view.py` | 2-4 | Lineage-edge None guards |
| `gui/models.py` | 5-7 | Qt-role exhaustion fallbacks, sentinel items |
| `gui/main_window.py` | 10-12 | Defensive action-setup fallbacks, dock None guards |
| `gui/dialogs.py` (if Tier 4) | 12-18 | Multiple defensive fallbacks per dialog |
| **Total** | **29-41** | Per Doctrine #19 sweep at each arc close |

## Coverage measurement caveat

Per DEFERRED_DECISIONS #1, the standard shipping invocation per Lesson #96 (`pytest tests/ --cov=...`) hangs on trash-flow tests that enumerate the real Windows recycle bin. **For this arc, use `tests/unit/`** (every GUI Coverage Arc test will be CliRunner/qtbot-based, all under `tests/unit/`):

```bash
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/unit/ \
    --cov=curator.gui.<MODULE> --cov-branch --cov-report=term-missing -q
```

Each sub-ship's release notes will document this disclosure until DEFERRED_DECISIONS #1 is resolved.

## Status tracker

| Ship | Status | Tag | Coverage outcome |
|---|---|---|---|
| v1.7.185 | scope plan (this doc) | ✅ closed | — |
| v1.7.186 | lineage_view Part 1 | ✅ closed | 0% → 65.67% |
| v1.7.187 | lineage_view Part 2 + close | ✅ closed | 65.67% → **100%** (0 pragmas added) |
| v1.7.188 | models Part 1 (helpers + 3 simplest models) | ✅ closed | 11.35% → 39.77% |
| v1.7.189 | models Part 2 (AuditLog + Config) | ✅ closed | 39.77% → 63.26% |
| v1.7.190 | models Part 3 + close | ✅ closed | 63.26% → **100%** (0 pragmas added) |
| v1.7.191 | main_window Part 1 (construction + helpers) | ✅ closed | 7.57% → 45.87% |
| v1.7.192 | main_window Part 2 (action handlers) | ✅ closed | 45.87% → 61.54% |
| v1.7.193 | main_window Part 3 (migrate + sources) + bug fix | ✅ closed | 61.54% → 78.53% |
| v1.7.194 | main_window Part 4 (trash/restore/bundle slots + context menus) | ✅ closed | 78.53% → 99.54% (0 missing lines, 6 partial branches) |
| v1.7.195 | main_window pragma audit + close | ✅ closed | 99.54% → **100%** (0 new pragmas, 7 tests closed all 6 partials) |
| v1.7.196+ | dialogs.py (default Round 5) | deferred | — |

## See also

- **`docs/GUI_TESTING_STRATEGY.md`** — testing pattern foundation + per-module strategy
- **`docs/DEFERRED_DECISIONS.md`** — #1 sandbox-fragility (the recycle-bin hang) + #2 dialogs strategy
- **`docs/CLI_COVERAGE_ARC_SCOPE.md`** — the prior arc's scope plan, model for this doc's structure
- **`tests/unit/test_pytest_qt_smoke.py`** — gate ship validating the qtbot idioms used here
- **`CLAUDE.md` Doctrine #16/19/20** — Lessons #98/101/102 carry through this arc
