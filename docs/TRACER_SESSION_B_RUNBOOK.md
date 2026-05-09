# Tracer Phase 2 Session B — Real-World Demo Runbook

**Status:** v1.0 — DRAFT 2026-05-08. Authored after Curator v1.4.0 release, optimized for the full v1.4.0 surface.
**Audience:** Jake (the only human in the loop). AI cannot execute this runbook because gdrive OAuth requires a real browser session with Google credentials.
**Estimated time:** ~30 min start-to-finish on first run, ~5 min on subsequent runs.
**Goal:** Validate Curator v1.4.0's cross-source migration features (Phase 2 worker pool + Phase 3 retry + Phase 4 overwrite-with-backup + rename-with-suffix) against real Google Drive responses, not mocks.

---

## Why this runbook exists

The v1.4.0 test suite has **543/543 tests passing** — but every gdrive interaction in those tests is mocked via `SimpleNamespace` + `_FakeDriveFile(dict)` patterns. The mocks were faithful to PyDrive2's documented behavior, but documented behavior and actual API responses sometimes diverge in production (eventual consistency, query-syntax edge cases, rate limits, OAuth token refresh quirks). Session B is the validation gate that catches those divergences.

This is the v1.4.0 equivalent of the original Tracer Phase 2 Session B that validated v1.1.0's basic cross-source plumbing.

---

## Pre-flight checklist (5 min)

Run each of these in order. If any fails, stop and resolve before proceeding.

### 1. Verify Curator install + version
```powershell
cd C:\Users\jmlee\Desktop\AL\Curator
.\.venv\Scripts\Activate.ps1
python -c "import curator; print(curator.__version__)"
# Expected: 1.4.0
```

### 2. Verify pyDrive2 + Google API client are installed
```powershell
pip show pydrive2 google-api-python-client
# Both should report a version, not 'WARNING: Package(s) not found'
```

If missing:
```powershell
pip install -e .[gdrive]
```

### 3. Verify gdrive credentials file exists
```powershell
$creds = Test-Path "$env:APPDATA\Curator\gdrive_credentials.json"
$secret = Test-Path "$env:APPDATA\Curator\client_secrets.json"
"creds: $creds, secret: $secret"
# Both should be True. If False, see "First-time gdrive setup" below.
```

### 4. Verify a test folder exists in your Drive
You'll need a Drive folder dedicated to this demo so it doesn't pollute real data. Create one in the Drive web UI:
- Open https://drive.google.com
- New → Folder → name it `curator-session-b-demo-2026-05-08` (or any unique name)
- Note its Drive ID from the URL: `https://drive.google.com/drive/folders/{ID_HERE}`

### 5. Verify Curator DB exists and has at least a few indexed files
```powershell
curator doctor
# Should report: integrity checks pass; if no files indexed, it'll say so.
# Alternatively, see recent activity:
curator audit --limit 20
```

If empty or you want fresh data:
```powershell
# Pick a small test folder (10-50 files)
curator scan local C:\Users\jmlee\Desktop\AL\test_corpus
# Or use any folder you don't mind reading
```

### 6. Verify the gdrive source is registered
```powershell
curator sources list
# Expected output includes a row for source_id 'gdrive' with display_name set
```

If not registered:
```powershell
curator sources add --source-id gdrive --type gdrive --display-name "Drive (demo)"
```

---

## First-time gdrive setup (skip if pre-flight #3 passed)

If you've never set up gdrive auth on this machine:

1. **Get OAuth client credentials** from Google Cloud Console:
   - https://console.cloud.google.com/apis/credentials
   - Create OAuth 2.0 Client ID → Desktop app
   - Download the JSON, save as `%APPDATA%\Curator\client_secrets.json`

2. **Run the auth flow once interactively:**
   ```powershell
   curator gdrive auth
   ```
   This will open a browser to the Google consent screen. Accept. The token gets saved to `%APPDATA%\Curator\gdrive_credentials.json` and refreshes automatically afterward.

3. **Test the token works:**
   ```powershell
   curator scan gdrive --root "{folder_id_from_step_4}" --dry-run
   ```
   Should list files in the folder without errors.

---

## Test 1 — Basic cross-source migration (validates v1.1.0 plumbing)

**Hypothesis:** A simple local→gdrive migration with no collisions completes successfully, indexes the dst, and emits expected audit entries.

### Setup
```powershell
# Create a small test corpus on local
$src = "C:\Users\jmlee\Desktop\session_b_test_src"
mkdir $src -Force | Out-Null
"file 1 content" | Out-File "$src\test1.txt" -Encoding utf8
"file 2 content" | Out-File "$src\test2.txt" -Encoding utf8
"file 3 content" | Out-File "$src\test3.txt" -Encoding utf8

# Index it
curator scan local $src

# Use the Drive folder ID from pre-flight #4
$dstFolderId = "PASTE_YOUR_FOLDER_ID_HERE"
```

### Run
```powershell
curator migrate `
  --src-source-id local --src-root $src `
  --dst-source-id gdrive --dst-root $dstFolderId `
  --max-retries 3 --on-conflict skip `
  --apply
```

### Validate
- [ ] CLI exit code 0
- [ ] Output shows "moved: 3, skipped: 0, failed: 0"
- [ ] Drive web UI shows `test1.txt`, `test2.txt`, `test3.txt` in the demo folder
- [ ] Local files at `$src` are gone (in Recycle Bin)
- [ ] Index reflects the move:
  ```powershell
  curator query --source-id gdrive | Select-String "test"
  # Should show all 3 files with source_id=gdrive
  ```
- [ ] Audit log has 3 `migration.move` entries with `cross_source: True`:
  ```powershell
  curator audit --action migration.move --limit 10
  ```

**If any of these fail:** capture the CLI output + `curator status --verbose` output and stop. Don't proceed to Test 2.

---

## Test 2 — Quota-aware retry (validates Phase 3 v1.3.0 retry decorator)

**Hypothesis:** When gdrive returns a transient 429 (rate limit) or 503, the `@retry_transient_errors` decorator on `_cross_source_transfer` retries with exponential backoff and the migration eventually succeeds.

This is the hardest test to trigger artificially because Google rarely throws 429s for low-volume traffic. **Two options:**

### Option A — Trust Google to throttle you (passive, slower)
Migrate ~500 small files in one batch. With `max_retries=3`, the job should complete even if Drive throws occasional 429s.

```powershell
# Create 500 small files
$src = "C:\Users\jmlee\Desktop\session_b_test_500"
mkdir $src -Force | Out-Null
1..500 | ForEach-Object { "small content $_" | Out-File "$src\file_$_.txt" -Encoding utf8 }
curator scan local $src

curator migrate `
  --src-source-id local --src-root $src `
  --dst-source-id gdrive --dst-root $dstFolderId `
  --max-retries 5 --workers 4 `
  --apply --verbose
```

Watch for log lines like:
```
MigrationService: transient error on attempt 1/5: HttpError 429 ... retrying after 4.2s
```

### Option B — Skip if Test 1 passed (recommended)
Test 1's clean run is reasonable evidence that the basic cross-source path works. Phase 3 retry is well-covered by unit tests with mocked transient errors. Skip Test 2 unless Test 1 surfaced something suspicious.

### Validate
- [ ] If retry triggered: CLI shows retry log lines + final outcome is `moved` not `failed`
- [ ] Job completes with `moved + skipped >= 495` (allowing for a few permission edge cases)

---

## Test 3 — Phase 4 overwrite-with-backup (THE main validation for v1.4.0)

**Hypothesis:** When dst has a file with the same name as src, `--on-conflict=overwrite-with-backup` renames the existing dst file to `<name>.curator-backup-<UTC>.<ext>` via the new `curator_source_rename` hook, then writes the new src bytes. Outcome is `MOVED_OVERWROTE_WITH_BACKUP`.

**This is the test that validates the new v1.4.0 functionality the entire Phase 4 cycle exists for.**

### Setup
```powershell
# Create a fresh src
$src = "C:\Users\jmlee\Desktop\session_b_test_owb"
mkdir $src -Force | Out-Null
"NEW VERSION content" | Out-File "$src\report.txt" -Encoding utf8
curator scan local $src

# Manually upload an OLD version of report.txt to the Drive demo folder
# via the web UI BEFORE running migrate. Content can be anything different
# from "NEW VERSION content".
#
# Confirm via web UI: dst folder has report.txt with old content.
Read-Host "Press Enter when you've uploaded the OLD report.txt to Drive"
```

### Run
```powershell
curator migrate `
  --src-source-id local --src-root $src `
  --dst-source-id gdrive --dst-root $dstFolderId `
  --max-retries 3 --on-conflict overwrite-with-backup `
  --apply
```

### Validate
- [ ] CLI exit code 0
- [ ] Output shows `moved_overwrote_with_backup: 1`
- [ ] Drive web UI shows TWO files in the demo folder:
  - `report.txt` with content `NEW VERSION content`
  - `report.curator-backup-2026-05-08T<HH-MM-SS>Z.txt` with the OLD content
- [ ] Audit log has a `migration.conflict_resolved` entry with:
  - `mode: overwrite-with-backup`
  - `cross_source: true`
  - `backup_name` ending in `.txt`
  - `existing_file_id` (a Drive file ID)
  ```powershell
  curator audit --action migration.conflict_resolved --limit 5
  ```
- [ ] Local file at `$src\report.txt` is in Recycle Bin
- [ ] Index now shows `report.txt` under `source_id=gdrive`

**If the backup file does NOT appear** but the new dst exists: check audit for a `mode: overwrite-with-backup-degraded-cross-source` entry. The `reason` field will tell you why the gdrive plugin's `curator_source_rename` impl failed. Most likely causes: (a) Drive query syntax issue with the file title containing characters that need escaping; (b) eventual-consistency race where ListFile didn't return the colliding file. Capture the audit details + Drive web-UI screenshot and report back.

---

## Test 4 — Phase 4 rename-with-suffix (validates the FileExistsError retry-write loop)

**Hypothesis:** When dst has a file with the same name AND `--on-conflict=rename-with-suffix`, the migration writes to `<name>.curator-1.<ext>` instead of overwriting. If `.curator-1` is also taken, falls through to `.curator-2`, etc.

### Setup
```powershell
# Fresh src
$src = "C:\Users\jmlee\Desktop\session_b_test_rws"
mkdir $src -Force | Out-Null
"NEW src content" | Out-File "$src\data.bin" -Encoding utf8
curator scan local $src

# Manually upload data.bin to Drive demo folder (any content)
# Optionally also upload data.curator-1.bin to test the retry loop
Read-Host "Press Enter when you've uploaded data.bin (and optionally data.curator-1.bin) to Drive"
```

### Run
```powershell
curator migrate `
  --src-source-id local --src-root $src `
  --dst-source-id gdrive --dst-root $dstFolderId `
  --on-conflict rename-with-suffix --apply
```

### Validate
- [ ] CLI exit 0
- [ ] Output shows `moved_renamed_with_suffix: 1`
- [ ] Drive web UI shows:
  - Original `data.bin` UNCHANGED (NOT overwritten)
  - New file `data.curator-1.bin` (or `data.curator-2.bin` if you pre-seeded `data.curator-1.bin`) with the SRC content
- [ ] Audit `migration.conflict_resolved` entry:
  - `mode: rename-with-suffix`
  - `cross_source: true`
  - `suffix_n: 1` (or 2 if applicable)
  - `original_dst` ends with `data.bin`
  - `renamed_dst` ends with `data.curator-1.bin` (or 2)

---

## Test 5 — Resume across interruption (validates Phase 2 persistent jobs)

**Hypothesis:** A migration job interrupted mid-flight (Ctrl+C) can be resumed via `curator migrate --resume <job_id>` and completes the remaining files without re-doing the already-moved ones.

### Setup
```powershell
$src = "C:\Users\jmlee\Desktop\session_b_test_resume"
mkdir $src -Force | Out-Null
1..50 | ForEach-Object { "resume test $_" | Out-File "$src\resume_$_.txt" -Encoding utf8 }
curator scan local $src
```

### Run + interrupt
```powershell
curator migrate `
  --src-source-id local --src-root $src `
  --dst-source-id gdrive --dst-root $dstFolderId `
  --workers 2 --apply &
$jobPID = $!
# Wait for ~10 files to complete (watch verbose output), then:
Stop-Process -Id $jobPID
```

### Find the job
```powershell
curator migrate --list-jobs --limit 5
# Note the job_id of the interrupted job
$jobId = "PASTE_JOB_ID_HERE"
```

### Resume
```powershell
curator migrate --resume $jobId
```

### Validate
- [ ] Resume picks up where it left off (output shows fewer remaining files than the original 50)
- [ ] Final state: all 50 files in Drive demo folder
- [ ] No file appears in Drive twice
- [ ] Local files all in Recycle Bin

---

## Cleanup

```powershell
# Empty the demo folder in Drive web UI (or via CLI)
curator gdrive trash --folder-id $dstFolderId --confirm

# Remove local test corpora
rm -r "C:\Users\jmlee\Desktop\session_b_test_*"

# Optionally truncate the test indexes from Curator's DB
# (only do this if you're sure no other work depends on these entries)
curator query --source-id gdrive --path-prefix $dstFolderId --json `
  | ConvertFrom-Json | ForEach-Object { curator delete --curator-id $_.curator_id --hard }
```

---

## What to report back to Claude after running

After Test 3 (the main v1.4.0 validation), reply with:

1. **Did the backup file appear in Drive?** (Yes/No)
2. **Did the audit log show `mode: overwrite-with-backup`** (success) or `mode: overwrite-with-backup-degraded-cross-source` (degrade with reason)?
3. **If degraded: what was the `reason` field?**
4. **Any unexpected CLI output, error messages, or behaviors during Tests 1-5?**

That's enough signal to know whether the v1.4.0 mocks accurately captured real Drive behavior. If everything passes Tests 1, 3, 4: v1.4.0 is fully validated against real-world Drive and we can close the Phase 4 chapter.

---

## Failure modes catalog

If something goes wrong, here's where to look:

| Symptom | Likely cause | Where to investigate |
|---|---|---|
| OAuth flow fails on first run | client_secrets.json missing or wrong type | Re-download as Desktop App OAuth client |
| `curator gdrive auth` opens browser but Google says "redirect_uri_mismatch" | OAuth client config | Add `http://localhost:8080/` to authorized redirect URIs |
| Test 1 fails with "supports_write=False" | gdrive plugin not registered in pm | Check `curator sources list`; restart venv |
| Test 3 backup not appearing in Drive | `curator_source_rename` failing | Check audit log for degrade reason; capture Drive query in error |
| Test 4 lands at `.curator-1` even when `.curator-1` exists | FileExistsError not raised by gdrive plugin's write | Drive query syntax mismatch; check `_iter_folder` query in `gdrive_source.py` |
| Test 5 resume re-uploads already-done files | `migration_progress.outcome` not persisted before crash | Check `migration_progress` table for the interrupted job |
| Random 429s during Test 2 | Drive rate limiting (expected) | Confirm retry decorator activates in logs; should auto-recover |

---

## Document log

* **2026-05-08 v1.0 — DRAFT.** Authored immediately after Curator v1.4.0 release ceremony. Designed to validate Phase 2 + Phase 3 + Phase 4 in a single end-to-end pass. Test 3 (overwrite-with-backup) is the primary v1.4.0 validation; Tests 1, 2, 4, 5 cover prior versions to ensure no regression. Total 5 tests, ~30 min on first run.
