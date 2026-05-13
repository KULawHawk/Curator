# `gui/dialogs.py` decomposition

**Owner:** Jake Leese · **Updated:** 2026-05-13 (v1.7.196)
**Scope plan:** Round 4 Tier 4 — `gui/dialogs.py` coverage arc
**Companion docs:** `docs/GUI_COVERAGE_ARC_SCOPE.md`, `docs/GUI_TESTING_STRATEGY.md`

This document inventories every dialog class in `src/curator/gui/dialogs.py` (the largest single module Curator has opened a coverage arc on at **2234 statements**) and proposes a sub-ship plan for the Tier 4 close.

---

## File inventory

Raw size: **4286 lines** | Coverage-relevant statements: **2234** | Branches: **432**

### Module-level helpers (3)

| Symbol | Lines | Purpose |
|---|---:|---|
| `_make_kv_table(rows)` | 73–88 (16 lines) | Build a 2-column key/value `QTableWidget` |
| `_make_table(headers, rows)` | 89–105 (17 lines) | Build an N-column `QTableWidget` |
| `_stringify(v)` | 106–120 (15 lines) | Coerce values to display strings |

### Result/data classes (3)

| Class | Lines | Purpose |
|---|---:|---|
| `BundleEditorResult` | 303–329 (27 lines) | Captures bundle editor dialog result fields |
| `_CheckResult` | 734–742 (9 lines) | Single health-check item result |
| `HealthCheckResult` | 743–770 (28 lines) | Aggregated health-check report |

### Dialog classes (10)

| Class | Lines | Approx stmts | Complexity tier | Function |
|---|---:|---:|---|---|
| `FileInspectDialog` | 121–302 (182) | ~110 | **small** | Read-only file detail viewer |
| `BundleEditorDialog` | 330–733 (404) | ~250 | medium | Create/edit bundle dialog with member picker |
| `HealthCheckDialog` | 771–1229 (459) | ~280 | medium | Diagnostic dialog (8 health checks) |
| `ScanDialog` | 1230–1600 (371) | ~225 | medium | Folder scan picker with progress |
| `GroupDialog` | 1601–2108 (508) | ~310 | large | Two-phase duplicate finder |
| `CleanupDialog` | 2109–2646 (538) | ~330 | large | Three-mode cleanup (junk / empty / symlinks) |
| `SourceAddDialog` | 2647–3166 (520) | ~320 | large | Source create/edit with validation |
| `VersionStackDialog` | 3167–3373 (207) | ~130 | small | Fuzzy-match version stack viewer |
| `ForecastDialog` | 3374–3548 (175) | ~110 | small | Drive capacity forecast (read-only) |
| `TierDialog` | 3549–4286 (737) | ~450 | **largest** | Cold/expired/archive tier picker |

(Statement counts approximate from line-count ratio observed in `main_window.py`. Actual counts re-measured per sub-ship per Lesson #93.)

---

## Sub-ship plan (predicted: 11 sub-ships, v1.7.197 → v1.7.207)

Following Lesson #88 (split if scope > 1.5× typical) + Lesson #99 (pragma audit at arc close), grouping by complexity tier:

| Ship | Scope | Predicted stmts | Notes |
|---|---|---:|---|
| **v1.7.197** | Helpers + result classes | ~85 | `_make_kv_table` + `_make_table` + `_stringify` + 3 result classes |
| **v1.7.198** | `FileInspectDialog` + `ForecastDialog` | ~220 | Both small, both mostly read-only |
| **v1.7.199** | `VersionStackDialog` + `ScanDialog` Part 1 | ~250 | Small + medium-1 |
| **v1.7.200** | `ScanDialog` Part 2 + `BundleEditorResult` (already in 197) | ~140 | Finish ScanDialog |
| **v1.7.201** | `BundleEditorDialog` | ~250 | Medium |
| **v1.7.202** | `HealthCheckDialog` | ~280 | Medium |
| **v1.7.203** | `GroupDialog` | ~310 | Large |
| **v1.7.204** | `CleanupDialog` | ~330 | Large |
| **v1.7.205** | `SourceAddDialog` | ~320 | Large |
| **v1.7.206** | `TierDialog` | ~450 | Largest single class — possibly split |
| **v1.7.207** | Pragma audit close + final 100% gate | TBD | Per Lesson #99 |

**Total: 11 ships** including this decomposition doc + the closer.

### Adaptive sizing

Per Lesson #93, **re-measure baseline at every sub-ship start.** If any class is 1.5× the predicted statement count, split into two ships immediately rather than overflowing. The TierDialog at 737 raw lines is the most likely split candidate — could end up as v1.7.206a + v1.7.206b.

---

## Test strategy

### Foundation (Lesson #98 / Doctrine #16) carried forward

- `QT_QPA_PLATFORM=offscreen` before any Qt import
- Module-scoped `qapp` fixture
- `qtbot.addWidget(dialog)` for cleanup after each test
- `MagicMock` for runtime + repos
- `silence_qmessagebox` for dialogs that invoke `QMessageBox.{question, warning, critical, information}`

### Extension: pytest-qt's `qtbot` for modal interaction

Each dialog has the same general shape:

1. **Construction** — verify the dialog builds without error under a stubbed runtime
2. **Initial state** — verify default values, button states, error labels
3. **Input handling** — drive `QLineEdit.setText`, `QComboBox.setCurrentIndex`, etc. directly via the Qt API (no `qtbot.keyClicks` needed)
4. **Button clicks** — `qtbot.mouseClick(btn, Qt.LeftButton)` to trigger handlers
5. **Modal accept/reject** — call `dialog.accept()` / `dialog.reject()` directly; verify the result fields are populated correctly
6. **Worker-thread interaction** (for dialogs that spawn QThreads) — stub the worker class with a synchronous version; verify the signal-slot wiring fires

### Stubbing background workers

Dialogs that spawn `QThread` workers (`ScanDialog`, `GroupDialog`, `CleanupDialog`) need their workers stubbed. Pattern:

```python
class _SyncWorker(QObject):
    progress_updated = Signal(object)
    completed = Signal(object)
    failed = Signal(object)

    def __init__(self, *args, **kwargs):
        super().__init__()

    def start(self):
        # Synchronously emit the completed signal with a stub result
        self.completed.emit({"files": 0})

monkeypatch.setattr(
    "curator.gui.dialogs.<WorkerClass>", _SyncWorker,
)
```

Same `_SyncWorker` pattern from `main_window.py` Tier 3 testing.

### Pragma budget

Per Lesson #101 / Doctrine #19's original estimate: ~1 pragma per 150-200 statements of mature application code. **Curator's GUI pattern (4 modules at 100% with 0 new pragmas in a row)** suggests this dialogs.py will probably also close pragma-free, BUT the larger surface (2234 stmts) means more defensive-boundary candidates. Realistic prediction: **0-5 pragmas** at arc close.

---

## Risks

1. **Modal dialog `.exec()` blocks the event loop.** Tests must either monkeypatch `.exec()` to return immediately, or call `dialog.accept()`/`dialog.reject()` directly without entering `.exec()`.
2. **Worker-thread spawning.** Dialogs with background workers will hang if real workers are used in tests. Stub via `monkeypatch.setattr` on the worker class.
3. **Service-method monkeypatching needed.** Dialogs that call into `runtime.scan / runtime.cleanup / runtime.bundle / etc.` need those methods stubbed.
4. **Sandbox-fragility carry-over.** Per DEFERRED_DECISIONS #1, the recycle-bin trash flow hangs in this sandbox. Any dialog test that ultimately calls `runtime.trash.send_to_trash` needs the trash service stubbed.
5. **TierDialog at 737 raw lines** is the biggest single class. If statement count comes in >500, split into two ships.

---

## Success criteria

- All 10 dialog classes at **100% line + branch**
- All 3 result/data classes at **100%**
- All 3 helpers at **100%**
- Pragma count: **0-5** total (with documented Lesson #91 justifications for any added)
- **GUI Coverage Arc CLOSED** at v1.7.207 — every `gui/*` module at 100%
- **Curator at v2.0-RC1 state** — every module 100% line + branch except policy-deferred items

---

## See also

- **`docs/GUI_COVERAGE_ARC_SCOPE.md`** — overall arc plan; this doc is the Tier 4 detail page
- **`docs/GUI_TESTING_STRATEGY.md`** — testing pattern foundation
- **`docs/DEFERRED_DECISIONS.md`** — #1 sandbox-fragility carries through
- **`CLAUDE.md` Doctrines #16/17/19/20** — Lessons #98/99/101/102
