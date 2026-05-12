# Platform scope

**Current state (as of v1.7.84, 2026-05-12):** Curator is **Windows-only** for development, CI, and accuracy guarantees.

This document records what was set aside, why, and how to resume macOS / Linux support if/when Jake decides to extend coverage.

---

## Decision

**Set aside on:** 2026-05-12 (v1.7.84)
**Decided by:** Jake (user directive: "i want to suspend worrying about macos or linux support. focus on windows in full")
**Followup directive:** "we can always resume if we want to cont the build out. just drop it and leave them noted of their state and where to resume if we do."

**Operational result:**
- CI matrix shrinks from **9 cells** (`{windows, ubuntu, macos} × {3.11, 3.12, 3.13}`) to **3 cells** (`windows × {3.11, 3.12, 3.13}`).
- macOS / Linux code paths in `src/curator/services/safety.py` are `# pragma: no cover` with explicit references back to this document.
- macOS / Linux helper scripts (`scripts/ci_diag.sh`, `scripts/setup_dev_hooks.sh`) remain on disk for future resume but are no longer validated in CI.
- Doctrine Principle 3 ("Functional parity > code parity (cross-platform)") is **suspended pending resume**. The principle's text is retained; an inline note marks it suspended.
- Doctrine Part V standing decision on the 9-cell matrix is **amended** to record the v1.7.84 narrowing.

---

## What was set aside

### 1. Non-Windows CI cells (6 of 9)
**Where:** `.github/workflows/test.yml`
**State before:** `os: [windows-latest, ubuntu-latest, macos-latest]` × 3 Python versions = 9 cells
**State after:** `os: [windows-latest]` × 3 Python versions = 3 cells
**Why:** macOS / Linux CI cells were validating code paths Jake doesn't currently care about. Six cells per push = real CI minutes saved.

### 2. macOS app-data + OS-managed path lists
**Where:** `src/curator/services/safety.py`
- `_macos_app_data_paths()` — returns `~/Library/Application Support`, `~/Library/Caches`, `~/Library/Containers`, `~/Library/Group Containers`, `~/Library/Preferences`, `~/Library/Logs`, `/Library`
- `_macos_os_managed_paths()` — returns `/System`, `/private/etc`, `/private/var/db`, `/private/var/log`, `/private/var/run`, `/private/var/spool`, `/Volumes`, `/usr`, `/sbin`, `/bin`, `/dev`
**State:** Functions retained, marked `# pragma: no cover` block-wise.
**Notable history:** v1.7.63 narrowed `/private` from over-broad to specific subdirs to avoid misclassifying user `TMPDIR` (which uses `/private/var/folders` on macOS). That fix is preserved.

### 3. Linux app-data + OS-managed path lists
**Where:** `src/curator/services/safety.py`
- `_linux_app_data_paths()` — returns `~/.config`, `~/.cache`, `~/.local/share`, `~/.local/state`, plus `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `XDG_DATA_HOME` if set.
- `_linux_os_managed_paths()` — returns `/boot`, `/sys`, `/proc`, `/dev`, `/etc`, `/usr`, `/var/log`, `/var/lib`, `/var/cache`, `/var/spool`, `/var/run`, `/var/mail`, `/var/db`, `/var/empty`, `/sbin`, `/bin`, `/lib`, `/lib64`
**State:** Functions retained, marked `# pragma: no cover` block-wise.
**Notable history:** v1.7.69 narrowed `/var` from over-broad to specific subdirs (FHS 3.0 §5.15 designates `/var/tmp` and `/var/local` as user-writable). That fix is preserved.

### 4. Cross-platform dispatchers (branches)
**Where:** `src/curator/services/safety.py`
- `get_default_app_data_paths()` — the `darwin` and `else` (Linux) branches are pragma'd.
- `get_default_os_managed_paths()` — same.
- `_is_under()` — the non-Windows case-sensitive comparison branch is pragma'd.

### 5. POSIX hook + CI diagnostic shell scripts
**Where:**
- `scripts/setup_dev_hooks.sh` — bash sibling of `setup_dev_hooks.ps1` (the dev-environment installer)
- `scripts/ci_diag.sh` — bash sibling of `ci_diag.ps1` (the CI diagnostic loop, writes to `~/.curator/logs/`)
**State:** Files retained on disk. Infrastructure audit (`test_infrastructure_audit.py`) still checks for their existence (they're recorded in `EXPECTED_SCRIPTS`).

**Rationale for retention:** the audit test enforces file presence, not runtime validation. Keeping these scripts costs nothing if no one runs them. Removing them would force a heavier reinstall when macOS / Linux support resumes.

### 6. POSIX hook shebangs
**Where:** `.githooks/pre-commit`, `.githooks/pre-push`
**State:** Shebangs (`#!/usr/bin/env bash`) retained; `test_hooks_have_posix_shebang` still passes.
**Rationale:** Hooks run via Git's own hook protocol which respects shebangs on POSIX. The shebang is harmless on Windows (Git for Windows interprets it via the bundled MSYS bash). No reason to strip.

---

## How to resume

When Jake decides to re-enable macOS / Linux support, work through this checklist:

### Step 1 — Restore CI matrix
```yaml
# .github/workflows/test.yml
matrix:
  os: [windows-latest, ubuntu-latest, macos-latest]
  python-version: ["3.11", "3.12", "3.13"]
```

### Step 2 — Update the infrastructure audit
Edit `tests/integration/test_infrastructure_audit.py`:
- Rename `test_ci_workflow_is_windows_only` back to `test_ci_workflow_has_full_matrix`.
- Restore the 9-cell assertion.

### Step 3 — Strip the `# pragma: no cover` markers
In `src/curator/services/safety.py`, search for `# pragma: no cover — set aside v1.7.84` and remove those markers. The annotated blocks are:
- `_macos_app_data_paths` body
- `_macos_os_managed_paths` body
- `_linux_app_data_paths` body
- `_linux_os_managed_paths` body
- `get_default_app_data_paths` darwin + else branches
- `get_default_os_managed_paths` darwin + else branches
- `_is_under` non-Windows branch

### Step 4 — Write the macOS / Linux tests
These tests will need to be written (they don't exist yet because they were never covered):
- `TestMacosAppDataPaths` — call `_macos_app_data_paths()` directly; assert it returns the expected fixed paths.
- `TestMacosOsManagedPaths` — same shape.
- `TestLinuxAppDataPaths` — same; include test cases with and without `XDG_*` env vars set.
- `TestLinuxOsManagedPaths` — same shape.
- `TestPlatformDispatch` — use `monkeypatch.setattr(safety.sys, "platform", "darwin")` / `"linux"` to exercise the dispatcher branches.
- `TestIsUnderCaseSensitive` — `monkeypatch.setattr(safety.sys, "platform", "linux")` to hit the non-Windows comparison branch.

Estimated effort: ~15-20 tests, similar shape to the existing Windows tests.

### Step 5 — Reactivate Doctrine Principle 3
Edit `docs/ENGINEERING_DOCTRINE.md`:
- Remove the "**SUSPENDED v1.7.84**" note from Principle 3's header.
- Update Part V standing decisions to revert the 9-cell row.
- Add an entry to the lessons section noting when and why support was re-enabled.

### Step 6 — Run CI matrix end-to-end
Push a no-op commit and confirm all 9 cells go green. If any cell fails, that's the first thing to fix.

---

## Why this is set aside, not deleted

Three reasons:

1. **The code is correct.** macOS / Linux logic was working before this scope decision; setting it aside is a CI-cost decision, not a quality decision. Deleting would lose institutional knowledge that's documented in v1.7.63 (macOS `/private` narrowing) and v1.7.69 (Linux `/var` narrowing).

2. **Reversal is cheap.** Six steps above, ~30-60 minutes of focused work plus CI verification time. Deleting and rewriting would be days.

3. **The apex principle is accuracy, not minimalism.** Pragma-marking with a clear resume path is more accurate than deletion — it states "this code is not validated in our current scope" rather than pretending it never existed.

---

## Related

- Doctrine: `docs/ENGINEERING_DOCTRINE.md` Part V standing decision, Principle 3 suspension note.
- Apex accuracy principle: see Lesson #71 in `CHANGELOG.md` (v1.7.83 entry).
- CI workflow: `.github/workflows/test.yml`.
- Infrastructure audit: `tests/integration/test_infrastructure_audit.py`.
