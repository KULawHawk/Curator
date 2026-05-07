# Curator GitHub Procurement Index

**Owner:** Jake Leese
**Maintained by:** Claude (updated each round)
**Location:** `C:\Users\jmlee\Desktop\AL\Curator\Github\PROCUREMENT_INDEX.md`

---

## How this works

1. Claude posts a Round of N repos with **direct ZIP download URLs** in chat. Each gets a folder with `NN_` prefix that persists forever.
2. Folders are pre-created at `C:\Users\jmlee\Desktop\AL\Curator\Github\NN_<name>\`.
3. You download via direct ZIP URL, save inside the folder, extract alongside the zip.
4. When the round is done, type `round N ready` (or `rd N ready`).
5. Claude reads, integrates findings into design, updates this file's status table.

**Direct ZIP URL pattern:** `https://github.com/{user}/{repo}/archive/refs/heads/{branch}.zip` (branch is usually `main` or `master`).

**Mid-round priority insertions:** Claude may add `01a_<name>`, `01b_<name>` near a similar item.

**Numbering rule:** Numbers stay forever. Descriptive suffix can change if procurement clarifies the right repo.

**License rule:** License is informational, not gating. Adopt the best library for the job.

**File budget rule:** Files on disk are precious. Wishlists live in chat alongside disk files. One file per persistent purpose, updated in place.

---

## Round 1 — 12 repos (CONSUMED 2026-05-05)

| # | Folder | Repo | Verdict |
|---|---|---|---|
| 01 | `01_ppdeep` | [elceef/ppdeep](https://github.com/elceef/ppdeep) | TAKE & MODIFY → `curator/fingerprint/fuzzy.py` |
| 02 | `02_pycode_similar` | [fyrestone/pycode_similar](https://github.com/fyrestone/pycode_similar) | TAKE & MODIFY → `curator/lineage/python_ast.py` |
| 03 | `03_send2trash` | [arsenetar/send2trash](https://github.com/arsenetar/send2trash) | VENDOR → `curator/_vendored/send2trash/` |
| 04 | `04_datasketch` | [ekzhu/datasketch](https://github.com/ekzhu/datasketch) | VENDOR SUBSET (minhash, lsh, lean_minhash, hashfunc) |
| 05 | `05_fclones` | [pkolaczk/fclones](https://github.com/pkolaczk/fclones) | PORT ALGORITHM (multi-stage hash pipeline) |
| 06 | `06_dupeguru` | [arsenetar/dupeguru](https://github.com/arsenetar/dupeguru) | TAKE & MODIFY (Match + Group classes) |
| 07 | `07_czkawka` | [qarmin/czkawka](https://github.com/qarmin/czkawka) | RULES ONLY (detection categories) |
| 08 | `08_beets` | [beetbox/beets](https://github.com/beetbox/beets) | TAKE & MODIFY DBCore patterns |
| 09 | `09_calibre` | [kovidgoyal/calibre](https://github.com/kovidgoyal/calibre) | DEFERRED (Tier 7 reference) |
| 10 | `10_rich` | [Textualize/rich](https://github.com/Textualize/rich) | PIP INSTALL |
| 11 | `11_textual` | [Textualize/textual](https://github.com/Textualize/textual) | DEFERRED (Tier 7 candidate) |
| 12 | `12_typer` | [fastapi/typer](https://github.com/fastapi/typer) | PIP INSTALL |

---

## Round 2 — 14 repos (CONSUMED 2026-05-05)

| # | Folder | Repo | Verdict |
|---|---|---|---|
| 13 | `13_xxhash` | [ifduyue/python-xxhash](https://github.com/ifduyue/python-xxhash) | PIP INSTALL — `xxh3_128` primary fingerprint |
| 14 | `14_watchdog` | [gorakhargosh/watchdog](https://github.com/gorakhargosh/watchdog) | REFERENCE FALLBACK — superseded by 15 |
| 15 | `15_watchfiles` | [samuelcolvin/watchfiles](https://github.com/samuelcolvin/watchfiles) | PIP INSTALL — Tier 6 file watcher |
| 16 | `16_pluggy` | [pytest-dev/pluggy](https://github.com/pytest-dev/pluggy) | PIP INSTALL — plugin framework |
| 17 | `17_pydantic` | [pydantic/pydantic](https://github.com/pydantic/pydantic) | PIP INSTALL — entity definitions |
| 18 | `18_apsw` | [rogerbinns/apsw](https://github.com/rogerbinns/apsw) | DEFER to Phase Beta (FTS5 + virtual tables) |
| 19 | `19_whoosh` | various | REJECTED — both whoosh forks unmaintained; Meilisearch deferred |
| 20 | `20_imagehash` | [JohannesBuchner/imagehash](https://github.com/JohannesBuchner/imagehash) | DEFER to Phase Beta |
| 21 | `21_python-magic` | [ahupp/python-magic](https://github.com/ahupp/python-magic) | DEMOTED — superseded by filetype.py (D18) |
| 22 | `22_questionary` | [tmbo/questionary](https://github.com/tmbo/questionary) | PIP INSTALL — HITL prompts |
| 23 | `23_pyqt6_examples` | [janbodnar/PyQt6-Tutorial-Examples](https://github.com/janbodnar/PyQt6-Tutorial-Examples) | REFERENCE for Tier 7 GUI |
| 24 | `24_loguru` | [Delgan/loguru](https://github.com/Delgan/loguru) | PIP INSTALL — modern logging |
| 25 | `25_diskcache` | [grantjenks/python-diskcache](https://github.com/grantjenks/python-diskcache) | DEFER as fallback |

---

## Round 3 — 11 repos (CONSUMED 2026-05-05)

| # | Folder | Repo | Verdict |
|---|---|---|---|
| 26 | `26_tantivy-py` | [quickwit-oss/tantivy-py](https://github.com/quickwit-oss/tantivy-py) | DEFER to Phase Beta — Phase Beta richer search |
| 27 | `27_filetype` | [h2non/filetype.py](https://github.com/h2non/filetype.py) | **PRIMARY** file-type detector (D18) — pure Python, no install drama |
| 28 | `28_pytest` | [pytest-dev/pytest](https://github.com/pytest-dev/pytest) | PIP INSTALL — testing framework |
| 29 | `29_hypothesis` | [HypothesisWorks/hypothesis](https://github.com/HypothesisWorks/hypothesis) | PIP INSTALL — property-based testing |
| 30 | `30_platformdirs` | [tox-dev/platformdirs](https://github.com/tox-dev/platformdirs) | PIP INSTALL — cross-platform paths |
| 31 | `31_tomli` | [hukkin/tomli](https://github.com/hukkin/tomli) | PIP INSTALL — TOML reader (3.10 fallback) |
| 32 | `32_tomli-w` | [hukkin/tomli-w](https://github.com/hukkin/tomli-w) | PIP INSTALL — TOML writer |
| 33 | `33_fastapi` | [fastapi/fastapi](https://github.com/fastapi/fastapi) | PIP INSTALL — Curator-as-service REST API |
| 34 | `34_uvicorn` | [encode/uvicorn](https://github.com/encode/uvicorn) | PIP INSTALL — ASGI server |
| 35 | `35_pyfilesystem2` | [PyFilesystem/pyfilesystem2](https://github.com/PyFilesystem/pyfilesystem2) | **REJECTED** (D17) — slow maintenance, sync-only, build our own |
| 36 | `36_httpx` | [encode/httpx](https://github.com/encode/httpx) | PIP INSTALL — async HTTP client |
| 37 | `37_tree-sitter-python` | [tree-sitter/tree-sitter-python](https://github.com/tree-sitter/tree-sitter-python) | PIP INSTALL (Phase Beta) — bonus from R4 preview |

---

## Round 4 — 10 repos (CONSUMED 2026-05-05)

### Tier A — Cloud SDKs
| # | Folder | Repo | Verdict |
|---|---|---|---|
| 38 | `38_google-api-python-client` | [googleapis/google-api-python-client](https://github.com/googleapis/google-api-python-client) | TRANSITIVE DEP (via PyDrive2) — Google's official SDK in maintenance mode |
| 39 | `39_PyDrive2` | [iterative/PyDrive2](https://github.com/iterative/PyDrive2) | **PIP INSTALL** (D19) — Google Drive Source plugin, friendly OAuth + OO API |
| 40 | `40_msgraph-sdk-python` | [microsoftgraph/msgraph-sdk-python](https://github.com/microsoftgraph/msgraph-sdk-python) | PIP INSTALL (D20) — OneDrive Source plugin, async-native |
| 41 | `41_dropbox-sdk-python` | [dropbox/dropbox-sdk-python](https://github.com/dropbox/dropbox-sdk-python) | PIP INSTALL (D21) — Dropbox Source plugin, pin v12.0.2+ |

### Tier B — Windows system integration
| # | Folder | Repo | Verdict |
|---|---|---|---|
| 42 | `42_pywin32` | [mhammond/pywin32](https://github.com/mhammond/pywin32) | PIP INSTALL (D22) — Windows service mode, registry, COM |

### Tier C — Scheduling
| # | Folder | Repo | Verdict |
|---|---|---|---|
| 43 | `43_APScheduler` | [agronholm/apscheduler](https://github.com/agronholm/apscheduler) | PIP INSTALL (D24) — AsyncIOScheduler, SQLite job store, 3.x stable |

### Tier D — Distribution
| # | Folder | Repo | Verdict |
|---|---|---|---|
| 44 | `44_PyInstaller` | [pyinstaller/pyinstaller](https://github.com/pyinstaller/pyinstaller) | DEFER to Phase Gamma+ (D23) — single .exe distribution |

### Tier E — Desktop UX
| # | Folder | Repo | Verdict |
|---|---|---|---|
| 45 | `45_pystray` | [moses-palmer/pystray](https://github.com/moses-palmer/pystray) | PIP INSTALL — system tray icon |
| 46 | `46_plyer` | [kivy/plyer](https://github.com/kivy/plyer) | **REJECTED** (D25) — overkill; pystray + pywin32 cover us |

### Tier F — Office doc metadata
| # | Folder | Repo | Verdict |
|---|---|---|---|
| 47 | `47_pypdf` | [py-pdf/pypdf](https://github.com/py-pdf/pypdf) | PIP INSTALL (D26) — Phase Beta broken-PDF plugin |

---

## Status tracking

### Round 1
| # | Created | Placed | Extracted | Consumed |
|---|---|---|---|---|
| 01–12 | ✅ | ✅ | ✅ | ✅ |

### Round 2
| # | Created | Placed | Extracted | Consumed |
|---|---|---|---|---|
| 13–25 | ✅ | ✅ | ✅ | ✅ |

### Round 3
| # | Created | Placed | Extracted | Consumed |
|---|---|---|---|---|
| 26–37 | ✅ | ✅ | ✅ | ✅ |

### Round 4
| # | Created | Placed | Extracted | Consumed |
|---|---|---|---|---|
| 38–47 | ✅ | ✅ | ✅ | ✅ |

---

## Round 5 — TBD (preview)

Round 5 will be smaller and targeted, based on Phase Alpha implementation gaps:

1. **NSSM** — service wrapper alternative to pywin32 service framework
2. **windows-toasts** — modern Windows toast notifications if pystray's tray notifications insufficient
3. **cryptography** — for credential storage encryption
4. **keyring** — OS-native credential storage (Windows Credential Manager, macOS Keychain)
5. **tree-sitter-vba** grammar — Phase Beta VBA AST analysis
6. **mkdocs / mkdocs-material** — project documentation site
7. **briefcase** (BeeWare) — alternative installer creator if PyInstaller has gaps

Final list with locked ZIP URLs comes when Phase Alpha implementation reveals concrete gaps.

---

## Future watch list (NOT to be procured now)

- **Meilisearch** — if Curator scope expands to multi-user / semantic / web-facing search
- **whoosh / whoosh-reloaded** — superseded; both unmaintained
- **SQLAlchemy / SQLModel** — only if handwritten SQL becomes burdensome
- **PyFilesystem2** — superseded by our own Source plugin contract
- **plyer** — overkill for our needs; only if cross-platform notification abstraction matters
- **google-cloud-python** — for non-Drive Google services someday
- **msgraph-beta-sdk-python** — beta Microsoft Graph features
- **Slint / GTK** — alternative GUI frameworks if Tier 7 reaches that decision

Detailed reasoning in `CURATOR_RESEARCH_NOTES.md` §19.

---

## Total adoption summary

**Vendor / take-and-modify (8):** ppdeep, pycode_similar, send2trash, datasketch (subset), fclones (port algorithm), dupeguru (Match/Group), beets (DBCore patterns), pyqt6_examples (reference)

**Pip install dependencies (24):** xxhash, watchfiles, pluggy, pydantic, questionary, loguru, rich, typer, filetype, pytest, hypothesis, platformdirs, tomli, tomli-w, fastapi, uvicorn, httpx, PyDrive2 (+ google-api-python-client transitive), msgraph-sdk-python, dropbox-sdk-python, pywin32, APScheduler, pystray, pypdf

**Phase Beta deferred (6):** apsw (FTS5), tantivy-py (richer search), imagehash, python-magic (richer descriptions), tree-sitter-python (full AST), pypdf (also Phase Beta usage)

**Phase Gamma deferred (1):** PyInstaller (distribution)

**Reference only (3):** calibre, watchdog, czkawka

**Rejected (4):** whoosh, whoosh-reloaded, PyFilesystem2, plyer

**Future watch (8):** Meilisearch, SQLAlchemy/SQLModel, google-cloud-python, msgraph-beta, Slint, GTK, etc.

---

## Revision log

- **2026-05-05 v1.0** — Round 1 published.
- **2026-05-05 v1.1** — Round 1 fulfilled.
- **2026-05-05 v1.2** — Round 1 consumed.
- **2026-05-05 v1.3** — Round 2 published.
- **2026-05-05 v1.4** — Round 2 fulfilled.
- **2026-05-05 v1.5** — Round 2 consumed. Item 26 added mid-round.
- **2026-05-05 v1.6** — Folder `26_tantivy` renamed to `26_tantivy-py`.
- **2026-05-05 v1.7** — Round 3 published.
- **2026-05-05 v1.8** — Round 3 fulfilled. Bonus item 37 (tree-sitter-python jumped from Round 4 preview).
- **2026-05-05 v1.9** — Round 4 published. Direct ZIP URLs as hyperlinks with backup main pages.
- **2026-05-05 v2.0** — Round 3 + Round 4 consumed. 24 new tracker items (75-98). 10 new design decisions D17-D26. Major architectural shifts: filetype.py promoted to primary file-type detector (python-magic demoted); PyFilesystem2 rejected → build own Source plugin contract; PyDrive2 picked over raw google-api-python-client; msgraph-sdk-python for OneDrive; dropbox-sdk-python for Dropbox; APScheduler for periodic jobs; pystray for system tray; PyInstaller deferred to Phase Gamma+; plyer rejected. Round 5 preview added.
