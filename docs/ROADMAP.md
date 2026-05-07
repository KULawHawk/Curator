# Curator: Roadmap & Future Capabilities

**Status:** living doc \u2014 captures Jake's planned features and Claude's recommendations beyond Phase Beta.

**Cross-references:** `DESIGN.md` (current spec), `BUILD_TRACKER.md` (implementation log), `Github/CURATOR_RESEARCH_NOTES.md` (decision rationale), `Github/PROCUREMENT_INDEX.md` (repo catalog).

This is **Phase Gamma+ scope** \u2014 not part of Phase Beta. Phase Beta finishes with the file watcher (\u2705), LSH (\u2705), cross-platform send2trash (\u2705), gdrive scaffolding (\u2705), and either GUI or cloud-plugin completion. Everything below builds on Phase Beta foundations.

---

## Planned features

Features **F1–F5** are Jake's explicit asks; **F6–F17** are Claude's recommendations promoted to formal roadmap items at Jake's direction. The original ranking by him-vs-me is preserved in commit history; the working list treats them all as first-class.

### F1. Smart drive organizer with safety constraints

**Goal:** sort and organize media files within the overall drive tree intelligently, but **never sweep up assets that should remain where they are** (in use by a program, part of a project, system-managed, etc.). The aspirational target: "the greatest drive organizer and cleanup utility ever."

**Why it's hard:**
- A file's location often encodes meaning the filename doesn't (a `.dll` in `Program Files\SomeApp\` is not the same as a `.dll` in `Downloads`).
- Programs hold open file handles; moving an in-use file breaks the program.
- Project folders (git repos, npm packages, build outputs) are atomic units even if they contain "media files."
- Some files are referenced from elsewhere by absolute path (registry, config files, OS shortcuts).

**Safety primitives we need before any "organize" action runs:**

1. **Open-handle detection** \u2014 know which files are currently locked / open by a running process. (Library: `psutil` + Windows Restart Manager API or `lsof` on Unix.)
2. **Project-root detection** \u2014 if a folder contains `.git`, `package.json`, `pyproject.toml`, `Cargo.toml`, `Gemfile`, `*.sln`, `node_modules`, `vendor/`, etc., treat the whole folder as an atomic unit. Don't move files inside it. (Pure Python; just a marker-file lookup.)
3. **Application data path registry** \u2014 a curated list of paths that should NEVER be touched: `%APPDATA%`, `%LOCALAPPDATA%`, `~/Library/Application Support`, `~/.config`, `~/.cache`, Steam library, npm/pip cache dirs, Adobe asset folders, OneDrive/Dropbox/iCloud sync roots. (Static list + user-extensible TOML.)
4. **OS-managed paths** \u2014 `Windows\\System32`, `/usr/lib`, `/System` on macOS \u2014 hard refusal.
5. **Recently-used hint** \u2014 OS-tracked recent files (Windows: Recent folder + jump lists; macOS: `~/Library/Application Support/com.apple.recents`; Linux: `recently-used.xbel`). Files used in the last N days are higher-confidence "in use" and lower-confidence "abandoned."
6. **Symlink + reparse-point + junction handling** \u2014 don't follow blindly; some are critical OS infrastructure.
7. **Hardlink detection** \u2014 already done in Phase Alpha via `inode`; reuse here so we don't move "the same file" from two locations.

**Action model:**

The organizer should always operate in three modes, ranked by reversibility:
- **Plan** (default): produce a JSON / table preview showing every proposed move, with a per-file safety score and a flagged-reasons column.
- **Stage**: move to an intermediate `_curator_staging/` folder under the same drive, leaving an audit trail. Files can be restored with one command. (Cross-filesystem moves never happen automatically.)
- **Apply**: actually move into final destinations. Always preceded by `--apply` flag. Always writes audit + lineage entries so a future "undo" is possible.

Curator's existing TrashService + restore pattern provides the "undo" primitive for free \u2014 reuse it.

### F2. Type-specific organization (music as the canonical example)

**Goal:** within a designated source ("organize my Music folder"), automatically organize files by their domain semantics. Music: `Artist / Album / NN - Track.ext`. Photos: `Year / YYYY-MM-DD - Event /`. Videos, ebooks, papers similarly.

**Per-type pipelines:**

| Type | Source of truth | Library | Output template |
|---|---|---|---|
| Music (MP3/FLAC/M4A) | ID3/Vorbis/M4A tags | `mutagen` | `Artist/Album/NN - Title.ext` |
| Photos (JPEG/HEIC/RAW) | EXIF | `pyexiftool`, `pillow-heif` | `YYYY/YYYY-MM-DD/IMG_xxxx.ext` |
| Videos (MP4/MKV) | container metadata | `pymediainfo`, `ffmpeg-python` | `YYYY/YYYY-MM-DD - Title.ext` |
| Ebooks (EPUB/MOBI/PDF) | EPUB OPF / PDF metadata / ISBN lookup | `ebooklib`, `pdfplumber`, OpenLibrary API | `Author/Title (Year).ext` |
| Papers (PDF) | DOI lookup / Crossref | `habanero` (Crossref client) | `FirstAuthor YYYY - Title.pdf` |

**Confidence-aware:**
- Files with full, reliable tags get auto-organized.
- Files with missing tags get a "needs metadata" tag, surfaced for manual review (or AI-assisted lookup against MusicBrainz / OpenLibrary / Crossref).
- Files with conflicting tags (e.g. ID3v1 says one artist, ID3v2 says another) get flagged for human resolution.
- Reuse Curator's existing classification confidence scores from Phase Alpha.

**Music-specific extras:**
- **AcoustID + Chromaprint** for audio fingerprinting when tags are missing/wrong \u2014 fingerprint matches against MusicBrainz's database. Means even untagged rips can be identified.
- **Duplicate handling** \u2014 same album in MP3 + FLAC, or same track at different bitrates: surface, let the user pick a "canonical" copy, archive the rest (or use lineage to mark them as related but not identical).

### F3. Photo face-tagging with iterative confirmation

**Goal:** when a photo is tagged with a specific person, Curator surfaces a handful of candidate photos it thinks contain the same person and asks the user to confirm or deny. The model refines from confirmations and eventually auto-applies tags.

**Active-learning loop:**

1. User tags Photo A with person "Alice."
2. Curator extracts a face embedding from Photo A using `face_recognition` (dlib-based) or `InsightFace` (modern ArcFace embeddings, better accuracy).
3. Curator computes embeddings for ALL faces across ALL indexed photos (one-time cost; cached in DB).
4. Curator picks the K closest unmapped faces by cosine similarity \u2014 "I think these K faces are Alice; please confirm."
5. User confirms / denies each. Curator records the confirmations and uses them as positive examples; denials become hard negatives.
6. Curator builds a per-person centroid embedding from confirmed faces. New faces are auto-tagged when they're within a configurable distance of the centroid AND no closer to a different person's centroid.
7. Distance threshold is calibrated automatically over confirmed examples \u2014 tighter threshold for higher precision, looser for higher recall. User-tunable.

**Privacy:** all embeddings stay local. No cloud APIs, no data leakage. (`face_recognition` and `InsightFace` both run fully offline.)

**Edge cases worth handling:**
- Children whose appearance changes over years \u2014 store multiple per-person centroids over time, age-bucketed.
- Twins \u2014 they'll cluster too close; Curator should detect "two centroids basically equal" and let the user disambiguate.
- Group photos: detect all faces, attempt to identify each independently.
- Faces that match nothing known \u2014 cluster them with each other (same unknown person) so the user can tag the cluster once.

### F4. Photo subject auto-tagging

**Goal:** auto-tag photos with subject categories like "beach," "cityscape," "pet," "food," "sunset," and so on. User-customizable category list.

**Approach: zero-shot classification with CLIP.**
- `open_clip` (community, more checkpoints) or original OpenAI CLIP.
- Embed each photo into CLIP's joint image-text space.
- For each user-defined subject category, embed the text "a photo of a {category}" into the same space.
- Cosine similarity \u2192 confidence score. Apply tags above a threshold.

**Why CLIP works well here:**
- Zero-shot \u2014 you don't need to train a model on your photo library. Categories are just text.
- User-customizable \u2014 add "beach" or "obscure_film_aesthetic" or "pictures of my cat Max" with no retraining.
- Local \u2014 no API. Models run on consumer GPUs (or CPU, slower).
- Composable \u2014 a photo can match many subjects at once.

**Storage:** embed each photo once, cache the 512-dim vector. Re-classify on demand by comparing against new text categories. Marginal cost per category is one text embedding (microseconds).

**Bonus:** EXIF GPS \u2192 reverse geocoding \u2192 location tags ("San Francisco," "Costa Rica") for free, no ML needed. Library: `reverse_geocoder` (offline, no API key).

### F5. NotebookLM integration (low priority)

**Goal:** make NotebookLM accessible from Curator workflows.

**Reality check:** NotebookLM has no stable public API as of this writing. Earlier attempts at MCP integration (per Jake's prior notes) didn't work cleanly. Anthropic and Google don't currently have a direct integration.

**Three paths forward, in order of viability:**

1. **Local RAG (recommended substitute).** Build the same capability locally over Curator's index using `llama_index` or `langchain` + `sentence-transformers` for embeddings + `qdrant` or `chromadb` for vector storage. This is what NotebookLM does, just run on the user's machine. Pros: no API, total privacy, integrates with Curator's existing index naturally. Cons: requires user to download embedding model.

2. **Gemini API with file uploads.** Google's Gemini 1.5 Pro accepts file uploads and answers questions about them \u2014 essentially the same primitive NotebookLM exposes. This is a real public API. Curator could ship a `curator notebook ask "<question>" --files <paths>` command that uploads and queries. Pros: actual official API, multimodal. Cons: requires API key + sends data to Google.

3. **Browser automation against NotebookLM web UI.** Use `playwright` to drive notebooklm.google.com programmatically. Pros: works today. Cons: brittle (UI changes break it), fragile to rate-limits, ToS-grey.

**Recommendation:** build path 1 first (local RAG using Curator's existing index). It gives you the capability without depending on Google's roadmap. Path 2 is a 50-line wrapper if Jake later wants the cloud version. Path 3 is a last resort.

---

## Claude's recommendations: libraries & GitHub assets

### \ud83c\udfb5 Music & audio

| Library | Use | License |
|---|---|---|
| [`mutagen`](https://github.com/quodlibet/mutagen) | Read/write ID3, Vorbis, M4A, FLAC tags. The reference implementation. | GPL-2 (use as runtime dep, not vendored) |
| [`tinytag`](https://github.com/tinytag/tinytag) | Lighter read-only audio metadata library. Good for fast bulk scanning. | MIT |
| [`musicbrainzngs`](https://github.com/alastair/python-musicbrainzngs) | MusicBrainz API client \u2014 canonical music metadata lookup. | LGPL-2.1 |
| [`pyacoustid`](https://github.com/beetbox/pyacoustid) | AcoustID + Chromaprint audio fingerprinting. Identify songs even when tags are missing or wrong. | MIT |
| [`beets`](https://github.com/beetbox/beets) | The gold-standard music organizer in Python. Worth studying as prior art (and possibly extracting subset). | MIT |

### \ud83d\udcf7 Photos & images

| Library | Use | License |
|---|---|---|
| [`pyexiftool`](https://github.com/sylikc/pyexiftool) | Wrapper around the canonical `exiftool` binary. Reads EXIF, IPTC, XMP, GPS \u2014 way more comprehensive than pure-Python alternatives. | BSD-3 |
| [`Pillow`](https://github.com/python-pillow/Pillow) | Image manipulation foundation. | MIT-CMU |
| [`pillow-heif`](https://github.com/bigcat88/pillow_heif) | HEIC/HEIF support for Pillow. iPhone photos. | BSD-3 |
| [`face_recognition`](https://github.com/ageitgey/face_recognition) | dlib-based face detection + recognition. Mature, simple API. | MIT |
| [`InsightFace`](https://github.com/deepinsight/insightface) | Modern face recognition (ArcFace embeddings). Higher accuracy than face_recognition; heavier deps. | MIT |
| [`open_clip`](https://github.com/mlfoundations/open_clip) | OpenCLIP \u2014 zero-shot image classification via CLIP-style models. The thing for subject auto-tagging. | MIT |
| [`reverse_geocoder`](https://github.com/thampiman/reverse-geocoder) | Offline coords-to-city lookup (no API, no key). Good fit for EXIF GPS. | LGPL-3 |
| [`exifread`](https://github.com/ianare/exif-py) | Pure-Python EXIF reader; lighter than pyexiftool when you don't need write or non-EXIF metadata. | BSD-3 |

### \ud83c\udfa5 Video

| Library | Use | License |
|---|---|---|
| [`pymediainfo`](https://github.com/sbraz/pymediainfo) | Wrapper around MediaInfo lib \u2014 container metadata, codec, duration, dimensions, audio tracks. | MIT |
| [`ffmpeg-python`](https://github.com/kkroening/ffmpeg-python) | ffmpeg bindings for thumbnails, format conversion, frame extraction. | Apache-2.0 |
| [`whisper`](https://github.com/openai/whisper) (or `faster-whisper`) | Audio transcription. Useful for content-based organization of podcasts, voice memos, recorded interviews. | MIT |

### \ud83d\udcc4 Documents & text

| Library | Use | License |
|---|---|---|
| [`pdfplumber`](https://github.com/jsvine/pdfplumber) | Better PDF text extraction than `pypdf` (already in deps), especially for tables. | MIT |
| [`unstructured`](https://github.com/Unstructured-IO/unstructured) | Extract text from .docx, .pptx, .html, emails, basically anything. | Apache-2.0 |
| [`pytesseract`](https://github.com/madmaze/pytesseract) | Tesseract OCR wrapper. Make scanned PDFs / image-only documents searchable. | Apache-2.0 |
| [`langdetect`](https://github.com/Mimino666/langdetect) | Identify document language. Useful for organizing multilingual collections. | Apache-2.0 |
| [`ebooklib`](https://github.com/aerkalov/ebooklib) | EPUB / MOBI parsing. | AGPL-3 (careful) |
| [`habanero`](https://github.com/sckott/habanero) | Crossref client \u2014 DOI lookup for academic papers. | MIT |

### \ud83d\udd12 Safety & "in use" detection

| Library | Use | Platform |
|---|---|---|
| [`psutil`](https://github.com/giampaolo/psutil) | Process + open-file enumeration. **Cornerstone of safety primitives.** | Cross-platform |
| Restart Manager API (via `ctypes`) | Windows-native "which app has this file locked." Available without external deps. | Windows |
| `lsof` (subprocess) | Same on Unix. | mac/Linux |
| [`watchdog`](https://github.com/gorakhargosh/watchdog) | Alternative to watchfiles; useful only if we hit watchfiles limitations (we haven't). | Cross-platform |

### \ud83e\udde0 Local AI / RAG (NotebookLM substitute)

| Library | Use | License |
|---|---|---|
| [`sentence-transformers`](https://github.com/UKPLab/sentence-transformers) | Local semantic embeddings for documents. Runs CPU-only. | Apache-2.0 |
| [`llama_index`](https://github.com/run-llama/llama_index) | RAG orchestration framework. The closest thing to "build NotebookLM yourself." | MIT |
| [`chromadb`](https://github.com/chroma-core/chroma) | Local vector store. Simpler than qdrant for single-user. | Apache-2.0 |
| [`qdrant-client`](https://github.com/qdrant/qdrant) | Vector store with better filtering / metadata querying than chromadb. | Apache-2.0 |

---

## Additional features (promoted from Claude's recommendations)

These push Curator from "good drive utility" toward "the greatest one." They're ranked by my estimate of value-to-cost ratio, but priority is in the milestone schedule below — not the order of this list.

### F6. Disk-space heatmap with project awareness
A `du`-style visual tree (think WinDirStat but smarter) where Curator highlights:
- Project folders (don't move \u2014 just show the size).
- Genuinely large stale files (top of the priority list for cleanup).
- Cache / build artifacts (`node_modules`, `__pycache__`, `target/`, `.venv/`) \u2014 safe to wipe en masse.
- Files older than N years that haven't been opened.

Combine this with the safety primitives from F1 and you get a "go through my drive and tell me where to start" command.

### F7. Stale-download cleanup
Files in `Downloads` (and the OS-equivalent paths) older than N days that haven't been opened. Curator can detect "haven't been opened" via OS atime or by checking the recently-used registry. With one-tap "trash these all to system recycle bin," a feature that's been on every productivity nerd's wishlist for a decade.

### F8. Duplicate cleanup with smart "keep" heuristics
You already have lineage \u2014 use it for cleanup. For each duplicate group:
- Keep newest? Keep with best metadata? Keep in best folder location? Keep highest-resolution? 
- Curator picks a default based on file type (photos: highest res; documents: most recent; music: highest bitrate) but lets the user override.
- Move losers to staging, then trash after N days if not restored. Reversible by design.

### F9. Conflict-version cleanup
iCloud, Dropbox, Google Drive, OneDrive all leave `(1)` and `Conflicted Copy` files behind when sync goes wrong. Curator detects these by filename pattern, compares content hashes against the canonical filename, and offers a sweep. Specifically high value for anyone who's used cloud sync for >1 year.

### F10. Email-attachment dedup
If you've saved 5 versions of `report.pdf` from email over the years, Curator can find them all (via filename + similar content), pick the canonical one, and offer to archive the rest. Bridges Curator's lineage detection with Outlook / Apple Mail / Gmail attachment metadata.

### F11. "Surface forgotten gems"
Files older than 1 year that score high on relevance heuristics: were referenced in recently-edited documents; are in a project folder that's getting active commits; have many incoming lineage edges. Curator proactively shows these as "you might want to look at this again." Inverts the usual "find old crap to delete" framing into "rediscover stuff you'd forgotten existed."

### F12. Burst-photo grouping
EXIF timestamp clustering \u2014 photos taken within a few seconds of each other are almost always a burst. Group them, surface the "best" one (sharpness score via `Pillow` / OpenCV), let the user keep the canonical and archive the rest. Same pattern as B3 but specialized.

### F13. Folder-template enforcement
User-defined "this folder follows this template" rules. Example: `~/Documents/Tax/{Year}/` \u2014 Curator audits the folder periodically and flags / suggests fixes for files that aren't in their expected location. Lightweight reflective layer that turns Curator into a slow-running compliance checker.

### F14. Cross-source dedup
Already a natural extension of Curator's source plugin model: if a file exists locally AND in Google Drive AND is identical, surface that. Useful for "I don't need this on disk because it's already in the cloud, archive the local copy."

### F15. Project-aware archival
When a project hasn't seen a commit in N months and isn't in active dev folders, suggest archiving the whole tree to a designated archive location (or compressed tarball). Reversible.

### F16. Clipboard history capture
When Curator detects you've copy-pasted a chunk of text or a file path, log it locally. Searchable later. Pure quality-of-life feature, but addictively useful once it exists.

### F17. "Recently reorganized" log + revert
Every organize action writes an audit entry; a single command rewinds the last N organizations. Curator's existing audit + restore primitives already enable this; just needs UX.

---

## Suggested priority ordering (Phase Gamma)

Roughly grouped into milestones \u2014 each milestone delivers a usable feature without depending on the next.

### Milestone Gamma-1: Safety primitives
1. **Open-handle detection** (`psutil` + Restart Manager) \u2014 the foundation everything else needs.
2. **Project-root detection** \u2014 small, pure-Python, immediate value for F1.
3. **Application-data path registry** \u2014 static list + user-extensible TOML.

These three together unlock F1 (smart organizer) safely. Without them, no organize action should exist.

### Milestone Gamma-2: Music organization (F2 partial)
4. **`mutagen` / `tinytag` integration** \u2014 read music metadata.
5. **Music organizer pipeline** \u2014 `Artist/Album/NN - Title` template.
6. **MusicBrainz fallback** for missing tags.

Music is the cleanest type to start with because tags are well-standardized.

### Milestone Gamma-3: Photo intelligence (F3, F4)
7. **`pyexiftool` + `pillow-heif`** \u2014 photo metadata foundation.
8. **CLIP subject auto-tagging** (F4) \u2014 zero-shot, no training data needed.
9. **Face embeddings + active-learning loop** (F3) \u2014 the big one.

Photos are second because the metadata story is messier (HEIC handling, EXIF variance), but the AI features are higher-value.

### Milestone Gamma-4: Document intelligence
10. **OCR for scanned PDFs** \u2014 makes existing index searchable.
11. **Local RAG** (`llama_index` + `sentence-transformers` + `chromadb`) \u2014 NotebookLM substitute.
12. **DOI / ISBN lookup** for papers and books.

### Milestone Gamma-5: Cleanup & polish
13. **F6 Disk-space heatmap with project awareness** — du-style visual tree, project-aware, surfaces stale outliers and safe-to-wipe caches.
14. **F8 Duplicate cleanup with smart keep heuristics** — reuses Curator's lineage edges + per-file-type "best copy" heuristics; reversible via stage → trash flow.
15. **F7 Stale-download cleanup** — `Downloads/` files older than N days, never opened.
16. **F9 Conflict-version cleanup** — iCloud `(1)`, Dropbox "Conflicted Copy", OneDrive `-<machinename>` patterns.

### Milestone Gamma-6: Discovery & recall
17. **F11 Surface forgotten gems** — inverts "find old crap" into "rediscover what you'd forgotten."
18. **F12 Burst-photo grouping** — EXIF timestamp clustering + sharpness-best pick.
19. **F14 Cross-source dedup** — same file local + cloud → archive the local copy.

### Open-ended polish
20. F5 NotebookLM integration via Gemini API (path 2 of the three) once milestones 11 + the local-RAG primitive are in.
21. **F13 Folder-template enforcement** — user-defined layout rules, slow-running compliance audit.
22. **F15 Project-aware archival** — inactive projects auto-archive to designated location.
23. **F10 Email-attachment dedup** — Outlook / Apple Mail / Gmail attachment rationalization.
24. **F16 Clipboard history capture** — logged-locally, searchable.
25. **F17 "Recently reorganized" log + revert** — N-step undo on organize actions.

---

## Notes on where this overlaps existing tools

Worth being honest: parts of this roadmap exist as separate tools today. Curator's value isn't reinventing them \u2014 it's having ONE index that ties them together.

| Existing tool | Overlaps with | Why Curator wins |
|---|---|---|
| WinDirStat / GrandPerspective | B1 (disk heatmap) | Curator knows what's a project, what's in use, what's a duplicate. |
| `beets` | F2 music | Curator's lineage + safety + multi-source story. Beets is music-only. |
| Picasa / Mylio / digiKam | F3 face tagging | Local-only, integrated with the broader index, cross-source aware. |
| NotebookLM | F5 | Local-only via path 1; works against your whole indexed corpus, not just one notebook. |
| Hazel (macOS) / File Juggler (Windows) | B8 folder templates | Cross-platform, source-aware, with audit + reversibility. |

The pitch isn't "yet another organizer." It's: **every other tool sees one slice. Curator sees the whole drive at once and can reason across slices.**
