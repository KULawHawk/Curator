# Tracer Phase 2 — Session B runbook (cross-source local → Google Drive)

**Status:** v2 (rewritten 2026-05-09 with correct `curator migrate` syntax + working pre-reqs).

## What this runbook validates

Cross-source migration of files from a local folder into Google Drive,
end-to-end against a real Drive account. This is the v1.4.0+
production-validation gate that test mocks can't cover. The mocks have
been stable since Phase 4, but until you run this against actual Drive
API responses, you only have evidence the code is correct against your
mental model — not against Drive's real behavior.

After this runbook completes successfully, you'll have evidence that:

* PyDrive2 + the gdrive_source plugin successfully build a Drive
  client from your saved credentials and refresh tokens automatically
  (no re-auth prompts).
* Curator's `migrate` command computes a correct cross-source plan
  from a local source to a `gdrive` source.
* The hash-verify-before-move pipeline correctly hashes both sides
  (local xxhash3_128 of the source, MD5 from Drive's metadata) and
  records both in lineage.
* Audit log entries (`migration.move`, `migration.copy`, etc.) land
  with correct details.
* Phase 2 (workers > 1) writes durable migration_jobs / migration_progress
  rows that survive an interrupt.
* Resume after Ctrl+C picks up where it left off without duplicating files.

## Prerequisites

* PyDrive2 installed in Curator's venv: `pip list | findstr pydrive2`
  should show `pydrive2-1.21.3` or later.
* gdrive auth completed for the `src_drive` alias:
  `curator gdrive status src_drive` should report
  `credentials_present` with both `client_secrets.json` and
  `credentials.json` marked `found`.
* The PowerShell wrapper installed: `curator --version` works in any
  fresh PowerShell window without venv activation.

If any of those are missing, do those first; the runbook assumes them.

---

## Step 1 — Create a destination folder in Google Drive (~1 min, manual)

To keep test files scoped (so you can clean up easily and don't litter
your Drive root), create a dedicated test folder in Drive:

1. Open https://drive.google.com in your browser.
2. Sign in if needed (same account as the gdrive auth).
3. Right-click in My Drive → New → Folder. Name it
   `curator_session_b_test`.
4. Open the new folder. Look at the URL — it ends in `/folders/<ID>`.
   Copy the `<ID>` portion. It's a long alphanumeric string.

Hold on to that folder ID for the next step.

---

## Step 2 — Register the gdrive source (~30 sec, single PowerShell block)

Replace `PASTE_FOLDER_ID_HERE` with the ID you copied above, then run:

```powershell
cd C:\Users\jmlee\Desktop\AL\Curator
.\.venv\Scripts\Activate.ps1
python scripts\setup_gdrive_source.py src_drive --folder-id PASTE_FOLDER_ID_HERE
curator sources list
```

Expected output: `OK: registered source 'gdrive:src_drive'` plus a
`sources list` table showing both `local` and `gdrive:src_drive`
enabled. Note the `gdrive:` prefix — it's required for the
gdrive_source plugin to claim ownership of the source. Without the
prefix, migration to this source will fail with `no registered plugin
advertises supports_write`.

If you accidentally use the wrong folder ID, re-run the same command
with a corrected `--folder-id` — the script is idempotent and will
update rather than fail.

---

## Step 3 — Prepare a small test corpus (~30 sec, single block)

Use a small set of files (~10) so the test is fast and you can verify
results manually in the Drive UI:

```powershell
$src = "C:\Users\jmlee\Desktop\session_b_src"
New-Item -ItemType Directory -Path $src -Force | Out-Null

# Drop ~10 small text files in
1..10 | ForEach-Object {
    Set-Content -Path "$src\session_b_test_$_.txt" `
        -Value "Session B test file #$_ — $(Get-Date -Format o)"
}

Get-ChildItem $src
```

Expected: 10 files, each ~70 bytes.

---

## Step 4 — Scan the local source so Curator knows about the files (~5 sec)

```powershell
curator scan local $src
```

Expected: `files seen: 10, new: 10, files hashed: 10`.

---

## Step 5 — Plan the cross-source migration (~5 sec, no writes yet)

This shows what WOULD migrate without actually doing it:

```powershell
curator migrate local $src "/" --dst-source-id "gdrive:src_drive"
```

Note the args:
* `local` — the source plugin id (where files come from)
* `$src` — the path prefix at the source (only files under here are
  candidates)
* `"/"` — the destination root (relative to the gdrive source's
  configured `root_folder_id`; "/" means "directly inside the Drive
  folder you registered in step 2")
* `--dst-source-id "gdrive:src_drive"` — the destination source.
  Quote it because the colon can confuse some shells, and remember
  the `gdrive:` prefix is required.

Expected output: a plan showing `SAFE: 10, CAUTION: 0, REFUSE: 0,
Total: 10` and a list of would-move file pairs.

---

## Step 6 — Apply the migration (~2-10 sec depending on Drive latency)

```powershell
curator migrate local $src "/" --dst-source-id "gdrive:src_drive" --apply
```

Expected: `Migration applied in N.NNs, MOVED: 10, SKIPPED: 0,
FAILED: 0`.

If you see FAILED counts, paste the audit log:
`curator audit --limit 30 --json` — and I'll diagnose.

---

## Step 7 — Verify in the Drive UI + audit log (~1 min)

**Drive UI check:**

1. Refresh https://drive.google.com.
2. Open `curator_session_b_test`.
3. Confirm all 10 `session_b_test_N.txt` files are present.
4. Open one — content should match what was written locally.

**Audit log check:**

```powershell
curator audit --limit 20
```

Expected: 10 `migration.move` events with `actor=curator.migrate`,
plus the earlier `scan.start` / `scan.complete` from step 4 and
`source.registered` from step 2.

**Hash integrity check (single file):**

```powershell
# Pick one file, get its xxhash3 from the index
curator status (Get-ChildItem $src | Select-Object -First 1).FullName
```

(After migration, that local path no longer holds the file. The `status`
command should report it as "moved to gdrive:src_drive/session_b_test_1.txt"
or similar with the destination + matching hash.)

---

## Step 8 (optional) — Resume-after-interrupt test (~3 min)

This validates the Phase 2 path (durable migration_jobs rows). Skip if
step 6 succeeded and you don't need the resume guarantee tested:

```powershell
# Drop another set of files
$src2 = "C:\Users\jmlee\Desktop\session_b_src2"
New-Item -ItemType Directory -Path $src2 -Force | Out-Null
1..20 | ForEach-Object {
    Set-Content -Path "$src2\resume_test_$_.txt" `
        -Value ("Resume test #$_" + ("X" * 1000))  # padded for slower migration
}
curator scan local $src2

# Start migration with --workers 4 (forces Phase 2 persistent path)
# IMMEDIATELY hit Ctrl+C after a few file completions land
curator migrate local $src2 "/" --dst-source-id "gdrive:src_drive" --apply --workers 4
# Press Ctrl+C around 1-2 seconds in. The CLI will print partial results.

# List migration jobs to find the interrupted one
curator migrate --list

# Resume it (use the job_id from --list)
curator migrate --resume <PASTE_JOB_ID_HERE> --workers 4
```

Expected: the resume picks up only the unfinished files, no
duplicates land in Drive.

---

## Step 9 — Cleanup (~1 min, optional)

If you want to clear the test artifacts:

```powershell
# Local test dirs
Remove-Item C:\Users\jmlee\Desktop\session_b_src -Recurse -Force
Remove-Item C:\Users\jmlee\Desktop\session_b_src2 -Recurse -Force -ErrorAction SilentlyContinue

# Drive folder: delete `curator_session_b_test` from the Drive UI manually
# (Curator's CLI doesn't expose a destructive Drive-side operation
# in v1.5.0, by design.)

# Curator index: a fresh scan on the now-empty local paths will mark
# the file entities as deleted automatically:
curator scan local C:\Users\jmlee\Desktop\session_b_src
```

The `src_drive` source itself stays registered for future use; if you
want to remove it, `curator sources remove src_drive` (will fail if
any files still reference it; remove the file entities first or use
the index reset).

---

## Reporting back

After step 7 succeeds:

* **`Session B done`** + paste the output of `curator audit --limit 20`
  → I'll mark v1.4.0/v1.4.1/v1.5.0 as production-validated in
  BUILD_TRACKER.md and constellation doc.
* **`Step 8 done`** if you also did the resume test → adds Phase 2
  resume guarantee to the validation record.
* **`Failed at step N: <error>`** → paste error text and I'll diagnose.

---

## Document log

* **2026-05-09 v3:** Fixed source_id from `src_drive` to
  `gdrive:src_drive` (with the colon prefix). The gdrive_source
  plugin's `_owns()` check requires source_ids to be exactly
  `'gdrive'` or start with `'gdrive:'`; without the prefix the plugin
  doesn't claim the source and migration fails with `no registered
  plugin advertises supports_write`. Updated all migrate commands +
  setup_gdrive_source.py default + helper-script error message.
* **2026-05-09 v2:** Rewritten with correct `curator migrate` CLI
  syntax (positional `src_source_id src_root dst_root` rather than
  the `--src/--dst` flags I previously misnamed). Added Step 2 (gdrive
  source registration via setup_gdrive_source.py helper script — works
  around the v1.5.0 CLI gap where `sources add` doesn't expose
  per-source config). Added explicit prerequisite checklist. Added
  cleanup section.
* **2026-05-08 v1:** Initial draft. Had wrong CLI flags
  (`--src/--dst/--alias`) and missed pydrive2 + client_secrets.json
  prerequisites. Superseded.
