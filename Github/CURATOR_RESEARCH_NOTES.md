# Curator Round 1 — Reading Notes & Synthesis

**Date:** 2026-05-05
**Reader:** Claude Opus 4.7
**Status:** Round 1 fully consumed. Synthesis complete. New tracker items 31–55 surfaced.

---

## How to read this file

This is my working notes from reading Round 1's 12 repos. It has three audiences:

1. **You** — to understand what I learned and what design decisions follow
2. **Future me** — to remember why we made the choices we made
3. **Future maintainers** — to understand the lineage of Curator's architecture

Sections:
- §1: Per-repo findings (what each repo taught us)
- §2: Cross-cutting design decisions (the patterns that emerged across multiple repos)
- §3: New tracker items (numbered 31+, your call to add)
- §4: What's NOT here (deferred / out of scope)
- §5: Round 2 candidates (what I want to read next)

---

## §1 — Per-repo findings

### 01_ppdeep — Pure-Python ssdeep (fuzzy hashing)

**Verdict: ADOPT AS DEPENDENCY.** No need to vendor; just `pip install ppdeep`.

**Key facts:**
- Single 200-line file, Apache 2.0 license (corrected from "MIT" in procurement index)
- Pure Python, zero dependencies
- API: `hash(bytes_or_str)`, `hash_from_file(filename)`, `compare(hash1, hash2) -> int(0..100)`
- Hash format: `block_size:hash_string1:hash_string2` (two-part rolling hash with adjustable block size)
- Has a `__main__` so it works as a CLI too

**How Curator uses it:**
- Compute a fuzzy hash for every text-based file (`.py`, `.bas`, `.md`, `.txt`)
- Store in DB alongside MD5 hash
- For lineage queries: "find files with fuzzy_hash similarity ≥ 70 to file X" → catches near-duplicates that aren't byte-equal
- This is the secret sauce for detecting "this is a slightly modified version of that file" without doing slow content diff

**Edge cases noted:**
- Empty file behavior is implicit (probably returns minimal hash, untested)
- The hash is stable across Python versions (good)
- `_common_substring` is naive O(n²) but acceptable for hash strings ≤ 64 chars

---

### 02_pycode_similar — Normalized-AST similarity for Python code

**Verdict: VENDOR THE ALGORITHM.** Copy the file, attribute clearly, integrate as `curator/lineage/python_ast.py`.

**Key facts:**
- Single 400-line file, version 1.4
- Uses Python's `ast` module (stdlib) + `difflib` (stdlib) — no external deps
- Optional `zss` library for tree edit distance (we'll skip this; UnifiedDiff is "good enough")
- License: looking at the file, it includes a 3-clause BSD attribution for the FuncInfo helper from `astor` library; main code is presumably MIT per repo claim

**Three key techniques to learn from:**

1. **Identifier stripping** — `BaseNodeNormalizer` walks the AST and DELETES:
   - Variable names (`visit_Name`: `del node.id`)
   - Function arguments (`visit_arg`: `del node.arg, del node.annotation`)
   - Attribute names (`visit_Attribute`: `del node.attr`)
   - String contents (`visit_Str`: `del node.s`)
   - Imports entirely (`visit_Import`, `visit_ImportFrom`: `pass`)
   
   This means a function that's been renamed and had its variables shuffled looks IDENTICAL to its original. Brilliant.

2. **Comparison operator normalization** — `visit_Compare` swaps `a > b` to `b < a` so `a > b` and `b < a` are seen as the same expression.

3. **AST-to-string-then-line-diff** — instead of doing tree edit distance (slow), it dumps the normalized AST to a structured string, then uses `difflib.SequenceMatcher` for line-based diff. This is fast AND good enough for our use case.

**How Curator uses it:**
- Function-by-function similarity comparison for any two `.py` files
- "Is `Stats_II_Master_Complete.bas` v3 a renamed/modified version of v1?" — answerable
- API will be: `python_ast_similarity(file_a, file_b) -> {function_name: similarity_percent}`

**Limitations:**
- Python only — can't help with `.bas` files. We'll need a similar approach for VBA in Round 2 (likely build our own simpler version since VBA tooling is sparse).
- Function-granularity, not line-granularity — if the user wants to know which lines changed, they need a different tool.

---

### 03_send2trash — Cross-platform recycle bin integration

**Verdict: ADOPT AS DEPENDENCY.** Just `pip install send2trash`.

**Key facts:**
- BSD license
- Clean platform dispatch: `__init__.py` selects `mac/`, `win/`, or `plat_other/plat_gio` based on `sys.platform`
- Single API: `send2trash(path)` — that's it
- Maintained by `arsenetar` (same author as dupeguru)
- For Windows, uses two implementations: `modern.py` (IFileOperation COM API, Windows 7+) and `legacy.py` (SHFileOperation, fallback)
- Raises `TrashPermissionError` if the trash itself is inaccessible

**How Curator uses it:**
- Curator's "soft-delete to trash" calls `send2trash()` 
- Files go to Windows Recycle Bin natively → user can restore via Windows UI as a backstop
- Curator ALSO maintains its own trash sidecar (with metadata: original path, hash, bundle memberships, reason) so we have richer restore info than Windows Recycle Bin provides

**Decision: dual-trash system**
- Layer 1: Send2Trash to Windows Recycle Bin (OS-level safety net, 30-day TTL is OS default)
- Layer 2: Curator's own trash registry (in SQLite + sidecar JSON) tracks what was trashed, when, why, and what bundle it belonged to
- Restore: Curator preferentially restores from its own registry (preserves all metadata); Windows Recycle Bin is the fallback if Curator's metadata is gone

This redundancy is good. Belt AND suspenders.

---

### 04_datasketch — MinHash, LSH, HyperLogLog, HNSW

**Verdict: ADOPT AS DEPENDENCY.** Larger surface than I expected; only need a small subset initially.

**Key facts:**
- MIT license
- Includes far more than just MinHash/LSH:
  - `MinHash` — basic Jaccard similarity estimation
  - `LeanMinHash` — compressed serialization
  - `MinHashLSH` — locality-sensitive hashing for sub-linear similarity search
  - `MinHashLSHForest` — variable-precision LSH
  - `MinHashLSHEnsemble` — for containment queries
  - `MinHashLSHBloom` — memory-efficient variant
  - `WeightedMinHash` — weighted variant
  - `bBitMinHash` — compressed MinHash
  - `HNSW` — approximate nearest neighbor search (vector embeddings)
  - `HyperLogLog`, `HyperLogLogPlusPlus` — cardinality estimation
- Active maintenance, well-documented, robust

**How Curator uses it (Phase Alpha):**
- `MinHash` + `MinHashLSH` only
- Used for: "given file X, find the K most similar files in the corpus" without doing N² pairwise comparisons
- Particularly useful when corpus exceeds a few thousand files

**Reserved for later:**
- `HNSW` — useful if we ever want to embed file content as vectors and do semantic similarity
- `HyperLogLog` — useful for cardinality estimation in big-corpus stats

---

### 05_fclones — Multi-stage hashing strategy (READ FOR IDEAS)

**Verdict: PORT THE STAGED-HASHING ALGORITHM.** Don't import (Rust), don't vendor (Rust), but the pipeline is gold.

**The killer technique — multi-stage hashing pipeline:**

```
Stage 1: Group by file size
  → groups smaller than 2 are dropped (no possible duplicates)
  → cheap (just stat each file)

Stage 2: Group by inode/file-id (Windows: file index)
  → within a size-group, hard-linked files share an inode → collapse them
  → free deduplication of hardlinks

Stage 3: Hash a tiny prefix (e.g., first 4KB)
  → split groups whose prefix hashes differ
  → most non-matching files get filtered out here at minimal I/O cost

Stage 4: Hash a tiny suffix (e.g., last 4KB)
  → same split logic
  → catches files with same prefix but different bodies

Stage 5: Full hash only for survivors
  → at this point, file pairs are very likely actual duplicates
  → expensive operation runs on a tiny fraction of files
```

**Why this matters:**
- Naive approach: hash every file fully → reads ALL bytes from disk → slow on huge corpora
- This approach: read maybe 10% of bytes for 99% of files → orders-of-magnitude speedup
- fclones benchmarks show 3-10× faster than other dedup tools

**How Curator uses it:**
- Curator's `fingerprint` command implements this exact pipeline
- Each stage is a separate function with clear input/output contracts
- Allows interruption between stages (resume from cached intermediate results)

**Other fclones lessons:**
- `xxhash3` (128-bit, fast, non-cryptographic) is the recommended hash function for dedup. Cryptographic hashes are unnecessary for our use case. We adopt this default.
- Persistent cache: hash + (file mtime, size) → cached. Invalidate only when mtime/size changes. Identifies files by inode (so renames preserve cache).
- Two-phase command structure: `group` produces a report; `link/remove/move` consume the report. **Curator already plans this pattern (item 14 — hard separation of scan and act).**
- `--dry-run` everywhere by default. **Already covered by item 10.**
- Rich output formats: text, fdupes-compatible, CSV, JSON. **New tracker item: 31.**

---

### 06_dupeguru — Word-based fuzzy matching + Group abstraction

**Verdict: ADOPT THE GROUP MODEL, READ THE WORD-MATCHER FOR IDEAS.**

License is GPL-3, but per your decision (item 28 reversed), we can use it freely. Still, no need to vendor large chunks — the patterns are simple enough to reimplement.

**Key takeaways:**

1. **Match abstraction:**
   ```python
   Match = namedtuple("Match", "first second percentage")
   ```
   Two files + a similarity score. Simple.

2. **Group abstraction:**
   - A `Group` is N files where every pair has a recorded `Match`
   - Has a `ref` (the canonical file, the one to keep) and `dupes` (the rest, candidates for removal)
   - `prioritize(key_func, tie_breaker)` reorders the group; useful for "keep newest" / "keep largest path-depth" / etc.
   - `discard_matches()` frees memory by dropping match data once the group is finalized

3. **Word-based matching** (not relevant to file content but useful for FILENAMES):
   - `getwords(s)` decomposes a string into words, normalizes accents, lowercases, strips punctuation
   - `compare(words1, words2)` returns 0-100 based on word overlap
   - Optional flags: `MATCH_SIMILAR_WORDS` (uses difflib.get_close_matches), `WEIGHT_WORDS` (longer words count more), `NO_FIELD_ORDER`

4. **Content-based matching:**
   - `getmatches_by_contents()` does size + partial digest + full digest, similar to fclones but simpler
   - Skips zero-byte files (treats them as 100% match without hashing)

5. **Memory-conscious** — has explicit `MemoryError` handlers, partial-result fallback

**How Curator uses it:**
- Adopt the Match/Group abstractions wholesale (with possibly different field names)
- Word-based filename matching is a useful Tier-2 feature ("find files with similar names" — catches `Stats_II_v2.bas` ↔ `stats_ii_v3.bas` cases)
- Skip the GUI patterns; we'll do our own UI design in the GUI tier

---

### 07_czkawka — Breadth of detection types (READ FOR IDEAS)

**Verdict: ADOPT THE CATEGORY LIST.**

Czkawka's main value is showing what *kinds* of file issues a comprehensive tool detects. Their list:

1. Duplicates (basic content-equal)
2. Similar images (perceptual hash)
3. Similar videos (perceptual)
4. Similar music (tag-based or content-based)
5. **Empty files** ← relevant to Curator
6. **Empty folders** ← relevant to Curator
7. **Big files** (top N by size) ← relevant
8. **Temporary files** (.tmp, ~$, .swp, etc.) ← relevant
9. **Broken files** (corrupted, truncated) ← VERY relevant
10. **Bad extensions** (file content doesn't match extension) ← VERY relevant
11. **Invalid symlinks** (broken targets) ← relevant
12. Exif removal (metadata stripper)
13. Video optimizer
14. **Bad names** (special chars, reserved Windows names) ← relevant

For Curator's academic/clinical context, items 5-11 and 14 all have direct value. Tracker items 32-38 cover them.

**Cache structure:** Czkawka caches between runs — second scan is much faster. Curator already plans this (in fclones notes).

---

### 08_beets — DBCore + Plugin architecture (READ FOR IDEAS, ADOPT PATTERNS)

**Verdict: HEAVILY INFLUENCE CURATOR'S CORE ARCHITECTURE.** Don't import beets directly (it's a music tool), but the patterns are gold.

**Key patterns to adopt:**

1. **Three-tier attribute model:**
   - **Fixed attributes** — predefined columns in SQLite table (path, hash, size, mtime, etc.)
   - **Flex attributes** — free-form key-value in separate table (user tags, plugin metadata)
   - **Computed attributes** — read-only fields computed by getter functions
   
   This is exactly Curator's need: core fields + user tags + plugin-contributed fields.

2. **Lazy type conversion** — `LazyConvertDict` only converts SQL → Python types when accessed. Big perf win for large queries.

3. **Migration system** — versioned schema migrations recorded in a `migrations` table. **Already in tracker item 16.**

4. **Transaction context manager** — thread-local tx stacks, proper concurrency. Critical for any tool that might be run from multiple processes.

5. **Dirty tracking** — only writes back fields that changed. Avoids unnecessary SQL.

6. **Plugin namespace pattern** (`beetsplug`) — plugins discovered by importing from a known namespace. Each plugin is a Python package.

7. **Event system** — typed `EventType` literal, `register_listener(event, func)`, central `send(event, **kwargs)` dispatcher. Plugins react to events without knowing about each other.

8. **Plugin contributions** — plugins extend the system through:
   - `commands()` — add CLI subcommands
   - `queries()` — add custom query types
   - `template_funcs` / `template_fields` — formatting hooks
   - Field types — add new flex field types
   
9. **Conflict detection** — `PluginConflictError` when two plugins try to define the same flex field with different types.

10. **Query/Sort objects** — composable query objects that translate to SQL, plus sort objects. Slow vs fast paths (some queries can't be done in SQL, so they fall back to Python filtering after fetch).

**How Curator uses it:**
- Adopt the three-tier attribute model verbatim (rename: `fixed/flex/computed`)
- Adopt the plugin namespace pattern: `curatorplug.<name>`
- Adopt the event system for the file-monitoring tier
- Adopt the migration system
- Don't adopt the music-specific stuff (importer pipeline, autotag, mediafile)

**This is the single most architecturally informative repo in Round 1.** Worth re-reading later.

---

### 09_calibre — (read for ideas, deferred)

Briefly inspected at directory level. Calibre is enormous (~50 MB) and 80% of it is irrelevant (ebook conversion, OPDS, etc.). The relevant subset for Curator:

- Library/database structure (similar lessons to beets)
- Custom column system (their version of flex fields)
- The Qt GUI patterns (relevant for Tier 7)
- Plugin system

Marking deferred — not enough time to justify deep reading when beets covers most of the same ground. If we hit a specific gap in Round 2 (especially GUI), I'll come back.

---

### 10_rich — CLI UI library

**Verdict: ADOPT AS DEPENDENCY.** No-brainer.

Inspected at directory level only — no need to read source for adoption decisions.

**Modules we'll use immediately in Curator's CLI:**
- `console.py` — base console object (auto-detects terminal capabilities)
- `table.py` — beautiful tabular output for file listings, dedup groups, etc.
- `tree.py` — hierarchical display for bundle structure
- `progress.py` — progress bars during scans/hashes
- `prompt.py` — interactive y/n prompts during `--apply` confirmations
- `panel.py` — bordered status messages
- `syntax.py` — syntax highlighting for code previews in lineage compare
- `traceback.py` — beautiful exception display (drops in, replaces default)
- `logging.py` — drop-in handler that makes log output beautiful
- `pretty.py` — beautiful repr for debugging

**How Curator uses it:**
- Every CLI command renders output through Rich
- Progress bars for any operation that loops over files (scan, hash, compare)
- Tables for inspection commands (`curator inspect`, `curator group`)
- Tree view for bundle visualization

---

### 11_textual — Modern Python TUI framework (deferred)

**Verdict: STRONG CANDIDATE FOR TIER 7 GUI, defer evaluation until we get there.**

Same author as Rich. Modern reactive TUI (text-based UI) framework. Could replace PyQt for Curator's interactive UI tier.

**Pros:**
- Pure Python, no Qt/GTK dependency
- Hot-reload during development
- CSS-like styling
- Async-native
- Beautiful by default (uses Rich for rendering)
- Same author/community as Rich → consistency

**Cons:**
- TUI not GUI — no native window controls; runs in terminal
- Newer than Qt, less battle-tested
- Some users will prefer a "real" GUI

**Decision: defer to Tier 7 design.** When we get there, we'll have a real Tier-7 design discussion comparing PyQt, Textual, and possibly web-based (FastAPI + React).

---

### 12_typer — Modern CLI argument framework

**Verdict: ADOPT AS DEPENDENCY.**

Built on top of Click (which is mature). Uses Python type hints to auto-generate CLI from function signatures. Modern, friendly.

**How Curator uses it:**
- Replace argparse / Click / etc. — Typer is strictly nicer
- Decorators for commands, sub-commands
- Auto-generated `--help` from docstrings + type hints
- Plays well with Rich (typer auto-uses rich for help formatting)

**Example of how Curator commands will look:**

```python
import typer
from rich.console import Console

app = typer.Typer()
console = Console()

@app.command()
def inspect(
    path: str = typer.Argument(..., help="Path to file or directory to inspect"),
    deep: bool = typer.Option(False, "--deep", help="Run all available analyzers, even slow ones"),
):
    """Show detailed Curator information about a file or directory."""
    ...

@app.command()
def group(
    paths: list[str] = typer.Argument(..., help="Paths to scan for duplicates"),
    apply: bool = typer.Option(False, "--apply", help="Actually move files (default: dry-run)"),
):
    """Find groups of related files (duplicates, near-duplicates, version progressions)."""
    ...
```

Clean, self-documenting, type-safe.

---

## §2 — Cross-cutting design decisions

These are patterns that emerged across multiple repos. Each becomes a load-bearing piece of Curator's architecture.

### D1 — Multi-stage filtering pipeline
**Source:** fclones, dupeguru, beets (queries)
**Pattern:** Cheap filters first, expensive filters only on survivors. Apply to:
- Hash pipeline (size → inode → prefix → suffix → full)
- Lineage classification (filename → extension → header → fingerprint → AST)
- Plugin loading (registered → enabled → instantiated → initialized)

### D2 — Two-phase commands (scan/group/inspect → act)
**Source:** fclones, dupeguru, beets
**Pattern:** All commands fall into one of two categories:
- **Scan/Read** — produce a report, never modify state
- **Act/Mutate** — consume a report (or take explicit args), modify state, require `--apply`

Curator commands always have a default-dry-run mode. Even act commands print "would do X" without `--apply`.

### D3 — Three-tier attribute model
**Source:** beets (DBCore)
**Pattern:** Fixed / Flex / Computed for every entity in Curator:
- `File` entity has fixed (path, hash, size, mtime), flex (user tags), computed (similarity_score(other))
- `Bundle` entity has fixed (id, name, type), flex (description, owner), computed (member_count, total_size)
- `LineageEdge` entity has fixed (from_id, to_id, kind, confidence), flex (notes), computed (age_days)

### D4 — Event-driven plugin system
**Source:** beets
**Pattern:** Plugins register listeners for typed events. Core fires events at well-defined moments. Plugins compose without coupling. Curator events:
- `file_seen` — scanner found a new file
- `file_hashed` — fingerprint computed
- `file_classified` — file type identified
- `lineage_edge_proposed` — possible relationship found
- `lineage_edge_confirmed` — user/auto confirmed
- `bundle_created` / `bundle_modified` / `bundle_dissolved`
- `file_trashed` / `file_restored`
- `query_executed`

### D5 — Group abstraction for related files
**Source:** dupeguru
**Pattern:** `Group` = set of files where every pair has a recorded relationship + a designated `ref` (canonical / keeper). Curator's bundles use this internally; cluster-of-similar-files uses this; dedup groups use this.

### D6 — Lazy type conversion + dirty tracking
**Source:** beets
**Pattern:** SQLite is the persistent store; Python objects materialize on access; only changed fields write back. Curator's data model uses this for performance on large corpora.

### D7 — Plugin namespace + namespace package
**Source:** beets (`beetsplug`)
**Pattern:** Plugins live in a known package (`curatorplug.*`). Auto-discovered by walking that package. Each plugin is a Python package with optional `requirements.txt`. Curator loads plugins by name from config.

### D8 — Confidence scores everywhere
**Source:** dupeguru (Match.percentage), my own item 9
**Pattern:** Any classification, lineage edge, or similarity score has a confidence (0.0–1.0). Documented thresholds for "definite," "probable," "possible." UI surfaces confidence visually. Already in tracker (item 9).

### D9 — Dual-trash with restore metadata
**Source:** Send2Trash + my synthesis
**Pattern:** `send2trash()` to OS recycle bin (OS-level safety net) + Curator's own trash registry with rich metadata (original path, hash, bundle memberships, reason for trashing, age). Restore preferentially from Curator's registry.

### D10 — Hash function: xxhash3 default, MD5 for compatibility
**Source:** fclones
**Pattern:** xxhash3 (128-bit, fast, non-cryptographic) for primary fingerprinting. MD5 as secondary for compatibility with external tools and existing inventories (Sort_Inventory uses MD5). Both stored in the registry.

---

## §3 — New tracker items (31 onward)

Numbered following the established protocol. Same `Nm` format applies for pitches.

### Detection capabilities (from czkawka inspiration)

31. **Output format flexibility** — Curator's `group`/`inspect` commands support text (default), JSON, CSV, HTML reports. Lets users pipe Curator output to other tools or build dashboards.
32. **Empty file detection** — flag zero-byte files as a category. They're often debris.
33. **Empty folder detection** — flag folders with no files (recursively). Often leftover from previous reorgs.
34. **Big-file ranking** — `curator big --top 50` shows the 50 largest files in a corpus. Useful for storage hunting.
35. **Temporary-file detection** — flag `.tmp`, `~$*`, `.swp`, `*.bak`, `Thumbs.db`, `.DS_Store`, etc. as candidates for cleanup.
36. **Broken-file detection** — verify file integrity for known formats (PDF, docx, xlsx). Truncated or corrupted files get flagged.
37. **Bad-extension detection** — verify file content matches its declared extension (e.g., `.pdf` whose first bytes aren't `%PDF`). Flags renamed-but-not-converted files.
38. **Invalid-symlink detection** — find symlinks/junctions whose target doesn't exist.
39. **Bad-name detection** — flag names with reserved Windows characters, names exceeding path length limits, names with control characters.

### Hash and fingerprint enhancements (from fclones, ppdeep)

40. **Multi-stage hash pipeline** (D1 above) — implement size → inode → prefix → suffix → full as Curator's primary fingerprinting strategy. Avoids reading full file contents until necessary.
41. **xxhash3 as primary hash** — use 128-bit xxhash3 for non-cryptographic fingerprinting (faster than MD5, plenty wide enough). Keep MD5 as secondary for compatibility with external tools.
42. **Persistent hash cache invalidated by (mtime, size)** — once a file is hashed, don't re-hash unless mtime or size changed. Massive speedup on rescans.
43. **Fuzzy-hash field on every text file** — automatically compute ppdeep hash for `.py`, `.bas`, `.md`, `.txt`, `.rst`, `.json`, etc. Powers near-duplicate detection.

### Architecture and integration (from beets, dupeguru)

44. **Adopt three-tier attribute model** (D3 above) — Fixed (schema columns) + Flex (key-value table) + Computed (read-only getters). For `File`, `Bundle`, `LineageEdge` entities.
45. **Plugin namespace `curatorplug.*`** (D7 above) — auto-discover plugins by walking the namespace. Each plugin is a Python package.
46. **Event system with typed events** (D4 above) — plugins react to events without knowing about each other.
47. **Group abstraction for clusters** (D5 above) — adopt dupeguru's Match/Group pattern.
48. **Dual-trash with restore metadata** (D9 above) — Send2Trash + Curator's own registry.
49. **Lazy type conversion** (D6 above) — borrow LazyConvertDict pattern for SQLite query results.

### CLI/UX (from rich, typer, fclones)

50. **Typer for CLI argument parsing** — type-hinted, self-documenting CLI commands.
51. **Rich for all CLI output** — tables, progress, panels, prompts, syntax highlighting.
52. **`--dry-run` and `--apply` flags universal** — consistent across all destructive commands. Already in item 10; this just confirms the pattern.

### Curator-specific lessons

53. **Two-phase commands as architectural rule** (D2 above) — every Curator command is either Scan/Read (produces report) or Act/Mutate (consumes report, requires `--apply`). No commands do both.
54. **Confidence scores throughout** — already in item 9; reaffirming as a load-bearing pattern with documented thresholds.
55. **VBA parser as future work** — pycode_similar handles Python; we'll need a similar approach for `.bas` (VBA Macros). Likely build our own lighter version using regex + state machine since VBA tooling is sparse. Not Phase Alpha.

---

## §4 — What's NOT here (and why)

- **Calibre deep dive** — deferred. Beets covers the same architectural ground in 1/10th the codebase. Will revisit if Tier 7 GUI design needs it.
- **Textual deep dive** — deferred to Tier 7. No design decisions hinge on it yet.
- **Czkawka source code** — README told us everything we needed. The Rust source is irrelevant for our Python implementation.
- **Dupeguru GUI source** — deferred to Tier 7. Their CLI patterns are what we wanted; their GUI is Qt-specific.
- **Datasketch source code** — adopt as black-box dependency. Deep reading not needed for adoption.

These deferrals are explicit per the operating rule "tell me if you skip something and why." If any of these become relevant, I'll resurface them.

---

## §5 — Round 2 candidates

Things I want to read next, based on what Round 1 revealed:

1. **`xxhash` Python bindings** — confirm we can use xxhash3 from Python easily (likely PyPI: `xxhash`)
2. **`watchdog`** — file system watcher for Tier 6 monitoring (was in original suggestions, didn't make Round 1 cut)
3. **`SQLAlchemy` Core** — should we use it for migrations and queries, or roll our own with sqlite3 stdlib like beets does?
4. **`pluggy`** — Pytest's plugin framework, abstracted. More mature than beets' homegrown plugin system. Worth comparing.
5. **`watchfiles`** — modern alternative to watchdog (faster, written in Rust + Python bindings). Compare with watchdog.
6. **`pydantic`** — for typed data models. Beets uses dataclasses + custom Type wrappers; pydantic might be cleaner.
7. **`whoosh` or `tantivy-py`** — full-text search library. Might be useful for content-based file search inside Curator.
8. **`apsw`** — alternative SQLite library with more features than stdlib `sqlite3`. Worth knowing about.

Round 2 will probably be ~6-8 repos, focused on filling these specific gaps. I'll write up a Round 2 procurement index when we're ready.

---

## §6 — Open questions for you

- **VBA support priority** (item 55) — important for migration of Stats II macros (we have 3 .bas files we want to compare). Phase Alpha or defer? My rec: defer; do filename + size + fuzzy hash for .bas in Phase Alpha, full AST-style comparison in Phase Beta.
- **HTML report format** (item 31) — worth implementing now or defer to Phase Beta? My rec: defer. JSON + text are enough for Phase Alpha.
- **Empty/temp/big-file detection** (items 32-35) — these are simple to implement. Bundle into Phase Alpha or hold for plugins? My rec: simple ones (32-35) in core; complex ones (36-37 broken/bad-extension) as plugins.

---

## Revision log

- **2026-05-05 v1.0** — Initial Round 1 reading complete. 21 new tracker items surfaced (31-55). Architecture-shaping decisions D1-D10 documented. Round 2 candidates identified.


---

# ROUND 2 (2026-05-05)

## §7 — Round 2 per-repo findings

### 13_xxhash — Python bindings for xxHash

**Verdict: ADOPT AS DEPENDENCY.** `pip install xxhash`.

API is clean: `xxh3_128`, `xxh3_64`, `xxh32` classes for stateful hashing; `xxh3_128_digest()`, `xxh3_128_hexdigest()`, `xxh3_128_intdigest()` for one-shot hashing. Standard `update()` / `digest()` / `hexdigest()` / `copy()` / `reset()` interface. BSD-2 license. Powers Curator's primary fingerprinting per fclones-derived multi-stage pipeline (item 40-41).

### 14_watchdog vs 15_watchfiles — file system watching

**Decision: 15_watchfiles wins for Curator's Tier 6.**

| Aspect | 14 watchdog | 15 watchfiles |
|---|---|---|
| Implementation | Pure Python | Rust core + Python bindings |
| Async-native | No (callback-based) | Yes (anyio) |
| Event types | 7 (Created/Modified/Deleted/Moved/Closed/ClosedNoWrite/Opened) | 3 (added/modified/deleted) |
| Debouncing | No (manual) | Built in (configurable ms) |
| Pattern matching | Built in (PatternMatchingEventHandler, RegexMatchingEventHandler) | Filter callable per change |
| Maturity | 12+ years | Newer but mature; Pydantic team |
| Default for Curator | — | ✅ |

**Why watchfiles wins:** debounce is critical (saves can fire 50 events; debouncing to one is right). Async-native fits modern stack. Simpler 3-event model is what Curator actually needs (we'd debounce/coalesce watchdog's 7 events anyway). Rust speed is bonus.

**Watchdog stays in research as fallback** if Rust binary issues hit on a specific Windows config.

### 16_pluggy — pytest's plugin framework

**Verdict: ADOPT AS PLUGIN FRAMEWORK** (replacing the beets-style event-listener pattern from D4).

Pluggy is the spec-and-impl pattern: define `HookspecMarker` for what hooks exist, `HookimplMarker` on implementations. `PluginManager.add_hookspecs()` registers the hook contract; `pm.register(plugin)` registers an implementation. Calling `pm.hook.myhook(arg=...)` runs ALL implementations and returns a list of their results.

**Why this beats beets-style:**
- **Signature enforcement** — if a plugin's hook impl signature doesn't match the spec, registration fails. Catches integration bugs at startup, not runtime.
- **Type discipline** — hookspecs serve as typed documentation; IDE autocompletion works.
- **Aggregation built-in** — host gets a list of all plugin contributions (perfect for "ask all file-type analyzers what this file is, return all opinions").
- **Ordering control** — `tryfirst=True` / `trylast=True` decorators for ordering when it matters.
- **Hookwrappers** — AOP-style wrap-around hooks for cross-cutting concerns.
- **Battle-tested** — runs pytest, tox, devpi.

This **revises decision D4** (event-driven plugin system). The model is now hook-based, not event-based, but the underlying pattern (plugins extend core via well-defined contracts) is the same.

### 17_pydantic — typed data models

**Verdict: ADOPT AS DEPENDENCY** for entity definitions.

Pydantic v2 (current) provides typed data classes via type hints with built-in validation, serialization, JSON schema generation. Curator's entities (`File`, `Bundle`, `LineageEdge`, `TrashRecord`) become pydantic `BaseModel` subclasses.

**Architecture decision still open:** how pydantic interfaces with storage:
- **Option A:** pydantic models on top of beets DBCore (pydantic for the API/serialization layer, DBCore for storage)
- **Option B:** pydantic models with raw stdlib sqlite3 + handwritten queries (drop DBCore complexity)
- **Option C:** pydantic + SQLModel (SQLModel = pydantic + SQLAlchemy from the same author)

Lean toward Option B for Phase Alpha simplicity. Reconsider if query complexity grows.

### 18_apsw — alternative SQLite library

**Verdict: DEFER to Phase Beta.** Stdlib sqlite3 is sufficient for Phase Alpha.

APSW (Another Python SQLite Wrapper) exposes the full SQLite C API including:
- **FTS5** (full-text search, the answer to dead whoosh)
- **Virtual tables** (custom storage views)
- **VFS** (custom file systems)
- **Sessions** (changeset/patchset tracking)
- **JSONB** (binary JSON storage)
- **Async support** via aio module

For Phase Alpha, stdlib sqlite3 covers all our needs. Reserve APSW for **Phase Beta when we want FTS5 for content search** (or virtual tables for live filesystem views).

### 19_whoosh, whoosh-reloaded, meilisearch, 26_tantivy — full-text search options

**Both whoosh and whoosh-reloaded are NO LONGER MAINTAINED.** READMEs explicitly say so. Don't adopt.

**Meilisearch:** powerful (hybrid search, typo tolerance, faceting, sub-50ms queries), MIT licensed, but it's a **Rust server process** — runs as a daemon on a port. Overkill for a single-user, single-machine tool. Operational overhead doesn't pay back at our scale (5k–10k files).

**Tantivy** (Rust core, Python bindings via tantivy-py): Lucene-class library, BM25 scoring, phrase queries, faceted search, multithreaded indexing, <10ms startup, mmap directory. **Library-style not server-style** — runs in-process. Right answer for Curator if we want richer search than basic FTS5.

**Decision matrix for Phase Beta:**

| Need | Pick |
|---|---|
| Basic "find files mentioning X" | APSW + FTS5 (single store, no extra dep) |
| Rich search: BM25, phrase queries, faceting, snippets | tantivy-py |
| Multi-user, web-facing, semantic/vector search | Meilisearch (Phase Gamma+) |

**Phase Alpha decision:** defer all full-text search. Use grep-style scanning if needed.
**Phase Beta default plan:** APSW FTS5. Upgrade to tantivy-py if/when richer features needed.
**Future watch:** Meilisearch if Curator ever expands toward multi-user or semantic.

### 20_imagehash — perceptual image hashing

**Verdict: DEFER to Phase Beta.** `pip install imagehash` when needed.

Multiple algorithms: aHash, pHash, dHash, wHash, colorhash, crop-resistant. BSD-2, active maintenance (4.3 added type annotations). Heavy dependencies (PIL/Pillow, numpy, scipy.fftpack), but standard scientific stack — no install drama on Windows expected.

For Curator: Phase Beta capability for "find visually similar images" — relevant for assessment-package PDFs and figures. Not Phase Alpha.

### 21_python-magic — content-based file type detection

**Verdict: ADOPT WITH CAVEAT** — needed for item 37 (bad-extension detection).

Wraps libmagic (the C library behind Unix `file` command). API: `magic.from_file(path)` returns description, `magic.from_file(path, mime=True)` returns MIME type. MIT licensed.

**Windows risk:** libmagic must be installed separately. Options:
1. **`python-magic-bin`** — bundles libmagic for Windows (separate PyPI package, maintenance varies)
2. **Manual install** — download libmagic DLL, add to PATH (per README)
3. **Backup: `filetype` library** — pure Python, no C dep, more limited but covers common formats

**Plan:** Try python-magic first via `python-magic-bin`. If install issues, fall back to `filetype`. Add `filetype` to Round 3 procurement.

### 22_questionary — interactive CLI prompts

**Verdict: ADOPT AS DEPENDENCY.** `pip install questionary`.

Provides text, password, confirmation, select, rawselect, checkbox, autocomplete, file path prompts. Built on prompt_toolkit. Used by Rasa. MIT licensed, active.

**For Curator:** powers HITL (human-in-the-loop) escalation flows — when Curator can't auto-classify a file with sufficient confidence, it prompts the user via questionary. Autocomplete is particularly valuable for "where does this file belong?" interactions.

Rich and questionary compose well: rich for output rendering, questionary for input collection.

### 23_pyqt6_examples — Tier 7 GUI reference

**Verdict: KEEP AS REFERENCE for Tier 7 design.**

Tutorial examples organized by category: customwidget, datetime, dialogs, dragdrop, events, layout, menustoolbars, painting, tetris, widgets, widgets2. MIT licensed.

When Tier 7 GUI design begins, this is where to look up "how does PyQt6 do X" patterns. No Phase Alpha use.

### 24_loguru — modern logging library

**Verdict: ADOPT AS DEPENDENCY** (mild upgrade over stdlib logging).

`from loguru import logger` and you're done. Beautiful by default, file rotation/retention/compression built into `logger.add()`, structured logging with `bind()`, lazy evaluation with `opt(lazy=True)`, full exception tracebacks with variable values, multiprocess-safe with `enqueue=True`.

**For Curator:** drop-in replacement for stdlib logging. Combines with rich (loguru output can render through rich console). No real downside.

### 25_diskcache — persistent local cache

**Verdict: DEFER as fallback.** Pure Python, Apache 2, uses SQLite + memory-mapped files internally.

Genuine alternative to building our own hash cache (item 42). Faster than Memcached for our use case. Multi-policy eviction (LRU, LFU). Tag metadata.

**Decision:** for Phase Alpha, build hash cache as a table in Curator's main SQLite database (single source of truth). If cache logic grows complex (eviction, tag-based purging, multi-process safety), switch to diskcache. Note as available alternative.

### 26_tantivy — full-text search engine (procured wrong repo)

**Verdict: NEED THE PYTHON BINDINGS REPO INSTEAD.**

The `quickwit-oss/tantivy` repo is the Rust engine itself (Cargo.toml, Rust source). To use from Python, we need `quickwit-oss/tantivy-py`:
- Direct ZIP: `https://github.com/quickwit-oss/tantivy-py/archive/refs/heads/master.zip`

Tantivy engine capabilities (from README): BM25 scoring, phrase queries, faceted search, multithreaded indexing, <10ms startup, mmap directory, JSON fields, range queries, configurable tokenizers, LZ4/Zstd compression. Library-style (no daemon).

If procurement of tantivy-py happens, replace contents of `26_tantivy/` (folder number persists per the rule).

---

## §8 — Round 2 design decisions (additional cross-cutting)

These supplement decisions D1-D10 from Round 1.

### D11 — Plugin framework: pluggy hook-based, not event-based
**Source:** 16_pluggy
**Pattern:** Replaces D4. Curator defines hookspecs; plugins implement hooks. Host calls `pm.hook.<name>(args)` and gets list of results. Signature enforcement at registration time.

### D12 — File watching: watchfiles, debounced
**Source:** 15_watchfiles vs 14_watchdog comparison
**Pattern:** Tier 6 monitoring uses `awatch()` async generator. Default 1600ms debounce groups bursts of saves into single events. 3-event model (added/modified/deleted) — moves detected by Curator's logic via add+delete pairs of same content hash.

### D13 — Data model: pydantic for entity definitions, separate from storage layer
**Source:** 17_pydantic + general architecture
**Pattern:** All Curator entities (`File`, `Bundle`, `LineageEdge`, `TrashRecord`) are pydantic `BaseModel` subclasses. Storage layer (Phase Alpha: stdlib sqlite3 with handwritten queries) reads rows and constructs pydantic objects. Validation happens at the entity boundary.

### D14 — Logging: loguru, rendered through rich
**Source:** 24_loguru + 10_rich (Round 1)
**Pattern:** `from loguru import logger` everywhere. Configured to route output through Rich console for color/formatting. File logging with rotation (size or time based) and Zstd compression.

### D15 — Hash cache: own table first, diskcache as fallback
**Source:** 25_diskcache + simplicity argument
**Pattern:** Cache table in Curator's main SQLite database, indexed on (path, mtime, size). If cache complexity grows (eviction policies, multi-process locking), switch to diskcache as drop-in replacement.

### D16 — Full-text search: deferred to Phase Beta, APSW FTS5 is the plan
**Source:** 19_whoosh investigation (both options dead) + 18_apsw + 26_tantivy
**Pattern:** No FTS in Phase Alpha. Phase Beta uses APSW with FTS5 (integrated with main DB, no extra dependency). Upgrade path to tantivy-py if richer features needed (BM25, snippets, faceting). Meilisearch reserved for hypothetical Phase Gamma multi-user/semantic future.

---

## §9 — New tracker items 56+

### Plugin and architecture

56. **Adopt pluggy for plugin framework** (D11) — typed hookspecs, signature-enforced impl registration, list-aggregation of plugin results. Replaces beets-style event listeners.
57. **Define core hookspecs** — `curator_classify_file(path) -> FileType`, `curator_compute_lineage(file_a, file_b) -> Optional[LineageEdge]`, `curator_validate_file(path, file_type) -> ValidationResult`, `curator_propose_bundle(files) -> Optional[Bundle]`, `curator_pre_trash(file) -> ConfirmationResult`. These are the primary plugin extension points.
58. **Hookwrappers for cross-cutting concerns** — logging, performance timing, audit trail. Plugins can wrap any other plugin's hook calls.

### Data and storage

59. **Pydantic models for all entities** (D13) — `File`, `Bundle`, `LineageEdge`, `TrashRecord`, `BundleMembership`, `FileTag`, `ScanReport`. Validation at entity boundary.
60. **Storage layer: stdlib sqlite3 + handwritten queries for Phase Alpha** (D13) — drop beets DBCore in favor of simpler explicit SQL. Reconsider if query complexity grows.
61. **Migrations via simple version table** — `schema_versions(name TEXT, applied_at TIMESTAMP)`. Each migration is a function that takes a connection. Upgrade path is linear.
62. **Hash cache as a table in main DB** (D15) — `hash_cache(path TEXT PRIMARY KEY, mtime REAL, size INTEGER, xxhash3_128 TEXT, md5 TEXT, fuzzy_hash TEXT, computed_at TIMESTAMP)`. Indexed on path. Invalidate row when mtime+size change.

### File watching and Tier 6

63. **Use watchfiles for Tier 6 monitoring** (D12) — `awatch()` async generator. Default 1600ms debounce. Filter callable applies Curator's ignore patterns (gitignore-style + temp file patterns).
64. **Move detection via add+delete coalescing** — when watchfiles reports `(deleted, X)` and `(added, Y)` within the debounce window AND content hash matches, emit single `move` event. Solves moves without needing watchdog's 7-event model.

### Logging and observability

65. **Loguru everywhere** (D14) — `from loguru import logger` is the only logging interface. Configured at app startup with rich-rendered console + rotated file handler.
66. **Structured log events** — every meaningful Curator action emits a log with `bind(event_type=..., entity_id=..., confidence=...)`. Lets us reconstruct sessions from logs alone.
67. **Performance timing decorator** — `@logger.contextualize(operation=name)` wrapper for any function we want to track. Outputs to debug log; can be enabled selectively.

### Search (Phase Beta planning, not Phase Alpha work)

68. **Phase Beta full-text search via APSW + FTS5** (D16) — integrated with main DB. Indexed fields: file content (for text files), filename, tags, notes.
69. **Phase Beta upgrade path: tantivy-py** if FTS5 proves insufficient. Snippet highlighting + BM25 + faceting are the killer features.

### File type detection

70. **python-magic for content-based file type** — primary detector for item 37 (bad-extension). Uses libmagic via `python-magic-bin` on Windows.
71. **`filetype` library as fallback** — pure-Python, smaller scope but no C dependency. Used if libmagic install issues hit.
72. **Combined detection result** — `FileTypeDetection` pydantic model: `extension`, `magic_mime`, `magic_description`, `confidence`, `detector_used`. Plugins can add their own detection results, all combined into final assessment.

### Interactive UX

73. **Questionary for HITL prompts** — when Curator escalates a decision to user (low confidence classification, ambiguous bundle membership, irreversible action confirmation). Autocomplete-backed for path/tag inputs.
74. **Rich + questionary composition pattern** — Rich for output (tables, panels, progress), questionary for input collection. Document this as the standard UX pattern in DESIGN.md.

---

## §10 — Round 3 candidates

Based on Round 2 findings, candidates for the next round (not finalized):

1. **`tantivy-py`** — Python bindings for Tantivy (correcting the wrong-repo procurement of #26)
2. **`filetype`** — pure-Python alternative to python-magic
3. **`python-magic-bin`** — Windows bundle of libmagic (procurement to verify install path)
4. **`pytest`** — testing framework (needed for synthetic test corpus, items 11/29)
5. **`hypothesis`** — property-based testing for fuzzy hash / lineage detection
6. **`tomli` and `tomli-w`** — TOML reading/writing for `curator.toml` config file
7. **`platformdirs`** — cross-platform user data/cache directory locations
8. **`tree-sitter` and `tree-sitter-python`** — for the Phase Beta "real" Python AST analysis if pycode_similar's regex-based approach proves limiting
9. **`tree-sitter-vba`** — for the Phase Beta VBA full AST (item 55)

Final Round 3 list will be posted when Phase Alpha implementation hits specific gaps.

---

## §11 — Future watch list (NOT current targets)

Things worth keeping an eye on for possible later inclusion. NOT to be procured now.

### Meilisearch
- **Use case:** if Curator ever evolves to multi-user web UI or needs hybrid (full-text + semantic vector) search
- **What it adds beyond tantivy-py:** typo tolerance, geosearch, multi-tenancy, SaaS-grade search experience, native vector search for embeddings
- **What it costs:** separate Rust daemon process, operational overhead
- **Trigger to revisit:** if Curator scope expands from single-user file management → multi-user knowledge management, or if semantic search becomes a requirement

### whoosh-reloaded
- Both maintenance status and implementation now superseded by APSW FTS5 / tantivy-py
- Don't revisit unless those alternatives prove inadequate

### SQLAlchemy / SQLModel
- Heavier ORM layer
- Would replace Option B (handwritten SQL) with ORM if query complexity grows
- Trigger to revisit: if we find ourselves writing repetitive query boilerplate in Phase Beta

### tree-sitter ecosystem
- Multi-language AST parsing
- For Phase Beta if we want consistent AST analysis across Python, VBA, JavaScript, etc.
- Trigger to revisit: if pycode_similar (Python) + custom regex (VBA) prove insufficient

---

## §12 — Open questions for you (Round 2 additions)

- **Round 2 storage decision:** Stick with stdlib sqlite3 + handwritten queries (Option B in §7's pydantic notes) for Phase Alpha? **My rec: yes.** Simplest, fewest dependencies, no ORM magic to debug.
- **Logging library choice:** Loguru as recommended? Or stick with stdlib logging since we already adopted Rich (which has its own logging integration)? **My rec: Loguru.** The ergonomic upgrade is real, no real downside, integrates with Rich.
- **python-magic install approach:** try `python-magic-bin` (bundles libmagic) or attempt manual libmagic install? **My rec: python-magic-bin first.** Easier path; only fall back to manual if it fails.

---

## Revision log

- **2026-05-05 v1.0** — Initial Round 1 reading complete. 21 new tracker items surfaced (31-55). Architecture-shaping decisions D1-D10 documented. Round 2 candidates identified.
- **2026-05-05 v2.0** — Renamed from `ROUND_1_NOTES.md` to `CURATOR_RESEARCH_NOTES.md` (one growing file per file-budget protocol). Round 2 reading complete (12 new repos: 13-25 + 26 added mid-round). 17 new tracker items surfaced (56-74). 6 new design decisions D11-D16. Major findings: pluggy beats beets-style for plugins; watchfiles beats watchdog; both whoosh forks dead → APSW FTS5 / tantivy-py path; Meilisearch deferred to Phase Gamma+ watch list; tantivy procurement got core engine instead of Python bindings (need to procure tantivy-py separately).


---

# ROUND 3 (2026-05-05)

## §13 — Round 3 per-repo findings

Round 3 driver: complete Phase Alpha foundation + add abstractions for system-wide vision (REST API, source plugins, rules engine).

### 26_tantivy-py — corrected from Round 2

**Verdict: PIP INSTALL** (`pip install tantivy`). Phase Beta when richer search than APSW FTS5 is needed. Confirmed correct repo this time.

### 27_filetype — pure-Python file type detection

**Verdict: PROMOTED to PRIMARY** file-type detector for Curator (revising prior decision to use python-magic).

Pure Python, zero C dependency, "Cross-platform file recognition" works on Windows out of the box. API:

```python
import filetype
kind = filetype.guess('path/to/file')
if kind: print(kind.extension, kind.mime)
```

Reads first 261 bytes of file for type inference. Plugin pattern for custom matchers. No libmagic install drama. **D18:** filetype.py is Curator's primary file-type detector. python-magic deferred to Phase Beta as optional enhancement for richer text descriptions.

### 28_pytest, 29_hypothesis — testing infrastructure

**Verdict: ADOPT BOTH.**

- pytest: standard Python testing framework. All Curator tests under `tests/` directory.
- hypothesis: property-based testing. Critical for fuzzy hash and lineage detection — generates random inputs to find edge cases that example-based tests miss.

Use case: hypothesis generates pairs of files with random transformations (rename, edit single line, reorder functions) and verifies Curator's lineage detection identifies them as related with appropriate confidence scores.

### 30_platformdirs — cross-platform user directories

**Verdict: ADOPT.** API surfaces:
- `user_data_dir()` → Curator's main DB location
- `user_config_path()` → `curator.toml` location
- `user_cache_dir()` → temporary computation cache (separate from main DB)
- `user_log_dir()` → log file location

Windows mapping: `user_data_dir("Curator", "JakeLeese")` → `%APPDATA%\Roaming\JakeLeese\Curator\`. Handles macOS and Linux conventions automatically when we cross-platform later.

### 31_tomli, 32_tomli-w — TOML config IO

**Verdict: ADOPT BOTH** (with `tomli` only as fallback for Python 3.10).

`curator.toml` is the user-editable config. We read with stdlib `tomllib` (Python 3.11+) or `tomli` (older). We write back with `tomli-w` when Curator updates settings programmatically.

Sample structure:
```toml
[detection.temp_files]
patterns = ["*.tmp", "~$*", ".DS_Store", "Thumbs.db"]

[hash]
primary = "xxh3_128"
secondary = "md5"
fuzzy_for = [".py", ".bas", ".md", ".txt", ".rst", ".json"]

[trash]
provider = "windows_recycle_bin"
restore_metadata = true

[source.local]
roots = ["C:/Users/jmlee/Desktop/AL", "C:/Users/jmlee/Desktop/Apex"]
ignore = [".git", "node_modules", "__pycache__", "venv"]

[source.gdrive]
account = "jake@example.com"
folders = ["1py38t20LyDJB84uIeaPlD14Je3AeRL8g"]  # RCS folder
```

### 33_fastapi — REST API framework

**Verdict: ADOPT** for Curator-as-service mode.

Modern, async-native, type-hint-driven, auto-generates OpenAPI docs, integrates with pydantic (which we're already using). Curator's REST endpoints:
- `GET /files?source=local&query=*.py` — search
- `GET /files/{curator_id}` — file metadata
- `GET /bundles` — list bundles
- `GET /bundles/{id}` — bundle members
- `GET /lineage/{curator_id}` — lineage edges from this file
- `POST /scan` — trigger scan (async, returns job ID)
- `GET /jobs/{id}` — scan job status

External tools (APEX, future) talk to this API instead of touching the SQLite DB directly. Single source of truth, version-managed schema.

### 34_uvicorn — ASGI server

**Verdict: ADOPT** as FastAPI's runtime. `uvicorn curator.api:app` runs the service. Hot-reload during development with `--reload`.

### 35_pyfilesystem2 — REJECTED for Source abstraction

**Verdict: REJECTED.** Slow maintenance (last release May 2022), uses `six` and Python 2/3 era patterns, sync-only fights our async stack. **D17:** build our own Source plugin contract via pluggy. Stdlib `zipfile`/`tarfile` for archives. Cloud SDKs (Round 4) for cloud sources. PyFilesystem2 stays in research as fallback only.

### 36_httpx — async HTTP client

**Verdict: ADOPT.** Async-native, requests-style API, used for cloud SDK calls and FastAPI test client. Companion to FastAPI in modern Python web stack.

### 37_tree-sitter-python — bonus from Round 4 preview

**Verdict: PIP INSTALL** (Phase Beta upgrade). Verified on disk. When pycode_similar's regex-based AST normalization proves limiting (e.g., for cross-version compatibility), tree-sitter-python provides a real, language-agnostic AST framework. Phase Beta deferral confirmed.

---

## §14 — Round 3 design decisions

### D17 — Source plugin contract is ours, not PyFilesystem2's
Pluggy hookspecs define the contract. Each source (LocalFS, GoogleDrive, OneDrive, Dropbox, S3, etc.) is a plugin. Hook contract:
```python
@hookspec
def curator_source_enumerate(source_id: str, root: str, options: dict) -> Iterator[FileInfo]: ...

@hookspec
def curator_source_read_bytes(source_id: str, file_id: str, offset: int, length: int) -> bytes: ...

@hookspec
def curator_source_stat(source_id: str, file_id: str) -> FileStat: ...

@hookspec
def curator_source_move(source_id: str, file_id: str, new_path: str) -> FileInfo: ...

@hookspec
def curator_source_delete(source_id: str, file_id: str, to_trash: bool) -> bool: ...

@hookspec
def curator_source_watch(source_id: str, root: str) -> AsyncIterator[ChangeEvent]: ...
```

### D18 — filetype.py as primary file-type detector
Pure Python, zero install drama, works on every Windows machine. python-magic deferred to optional Phase Beta enhancement.

---

# ROUND 4 (2026-05-05)

## §15 — Round 4 per-repo findings

Round 4 driver: cloud + system integration for system-wide deployment.

### 38_google-api-python-client + 39_PyDrive2 — Google Drive

**Verdict: ADOPT PyDrive2** as primary Google Drive interface. google-api-python-client is the underlying dependency.

google-api-python-client is in **maintenance mode** (per their own README: "no new features"). Still officially supported by Google.

PyDrive2 is **actively maintained by the DVC (Data Version Control) team** — serious users behind it. Provides:
- OAuth2 in 2 lines (`gauth.LocalWebserverAuth()`)
- Object-oriented file management (`drive.CreateFile()`, `file.Upload()`, `file.GetContentFile()`)
- Auto-pagination on listings
- fsspec filesystem implementation (could be useful as bridge to other Python tools)
- Sits on top of google-api-python-client for the actual API calls

**D19:** PyDrive2 for Google Drive Source plugin. Auth via `LocalWebserverAuth()` (opens browser, captures redirect). Credentials stored per Curator's auth abstraction.

### 40_msgraph-sdk-python — OneDrive (via Microsoft Graph)

**Verdict: ADOPT** for OneDrive Source plugin.

Official Microsoft SDK, **async-native by default** (fits our stack). Heavy package — README warns about install time. Auth via `azure-identity`:
- `DeviceCodeCredential` for personal/CLI use (user enters code on microsoft.com/devicelogin)
- `InteractiveBrowserCredential` for desktop app use
- `ClientSecretCredential` for org/enterprise apps

Requires Azure AD app registration (one-time setup; clientID + tenantID, user does this once).

**D20:** msgraph-sdk-python for OneDrive Source plugin. Use DeviceCodeCredential or InteractiveBrowserCredential for personal Curator use.

### 41_dropbox-sdk-python — Dropbox

**Verdict: ADOPT** for Dropbox Source plugin.

Official Dropbox SDK, MIT licensed. **Sync API** (no async), so wrap calls with `asyncio.to_thread()` for our async stack. PKCE OAuth flow recommended for desktop apps (no client secret needed).

⚠️ **Important compatibility note:** must be on v12.0.2 or newer by January 2026 due to Dropbox API server certificate changes. Pin minimum version in our `pyproject.toml`.

**D21:** dropbox-sdk-python for Dropbox Source plugin. PKCE OAuth flow, async wrapper around sync calls.

### 42_pywin32 — Windows API access

**Verdict: ADOPT** for Windows-specific integrations.

The de-facto Windows API binding for Python. Required for:
- **Windows service installation** — `win32serviceutil`, `win32service`, `servicemanager` modules. Service runs as `LocalSystem` or specified user account.
- **Registry access** — `winreg` (stdlib) is sufficient for simple cases; pywin32 adds advanced features.
- **System tray (alternative path)** — `Shell_NotifyIcon` directly. We're using pystray which abstracts this.
- **COM access** — for any deep Windows integration.

Post-install gotcha: `python -m pywin32_postinstall -install` must run with elevated permissions for service mode to work. Document this in Curator's Windows install guide.

**D22:** pywin32 for Windows service mode + registry. Evaluate NSSM (Non-Sucking Service Manager) as simpler alternative during Phase Beta if pywin32's service framework proves unwieldy.

### 43_APScheduler — periodic job scheduler

**Verdict: ADOPT** for periodic scan/maintenance jobs.

Comprehensive: sync AND async flavors (we use AsyncIOScheduler), multiple persistent stores (SQLite is what we want), cron/interval/calendar/one-off triggers. v4.0 is in pre-release (warning about backward incompat) — use stable 3.x.

Use cases:
- "Scan local Documents folder every Sunday at 3 AM"
- "Refresh Google Drive index every 6 hours"
- "Run hash cache cleanup weekly"
- "Generate weekly bloat report"

**D24:** APScheduler 3.x stable, AsyncIOScheduler, SQLite job store sharing Curator's main DB.

### 44_PyInstaller — distribution

**Verdict: DEFER to Phase Gamma+** for distribution as standalone .exe.

Mature (Python 3.8-3.14), works on Windows 7+, bundles dependencies + Python interpreter into single .exe or folder. Bundles major packages (numpy, Qt6) out-of-box.

Phase Alpha and Beta: pip install in venv is fine. Phase Gamma when we want to distribute to users without Python installed: PyInstaller becomes essential.

**D23:** PyInstaller for Phase Gamma+ distribution. Phase Alpha/Beta uses venv.

### 45_pystray — system tray icon

**Verdict: ADOPT** for system tray.

Cross-platform (Linux Xorg/GNOME/Ubuntu, macOS, Windows). Simple API: create icon with image, attach menu. Notifications are attached to the icon (no need for plyer).

Use case: when Curator runs as a service or background tool, system tray gives user quick access to:
- Recent scan results
- Pending HITL escalations
- "Open Curator dashboard" (launches GUI)
- Pause/resume monitoring
- Exit

### 46_plyer — REJECTED

**Verdict: REJECT.** Overkill for our needs. Plyer wraps platform-specific APIs for hardware (accelerometer, GPS, vibrator, etc.) — Curator doesn't need any of that. For notifications specifically, pystray + pywin32 cover Windows; macOS has its own native APIs we'd handle directly. Cross-platform notifications via plyer would only matter if we wanted the same code path everywhere with no platform specifics — that's not Curator's design.

### 47_pypdf — PDF library

**Verdict: ADOPT** for Phase Beta `curatorplug.broken_pdf` plugin.

Pure Python, mature (replaces deprecated PyPDF2). Capabilities:
- Open PDFs and detect corruption (exception on bad files)
- Extract metadata (title, author, page count, creation date)
- Detect encrypted PDFs and password-protected vs locked
- Read text content (Phase Beta: feeds into APSW FTS5 for content search)

Two-stage detection workflow:
1. filetype.py says "this is a PDF based on signature"
2. pypdf opens it; if exception → broken PDF flag

**D26:** pypdf for Phase Beta broken-PDF detection plugin and PDF metadata extraction.

---

## §16 — Round 4 design decisions

### D19 — Google Drive: PyDrive2
Friendly API on top of google-api-python-client. Maintained by DVC team.

### D20 — OneDrive: msgraph-sdk-python with DeviceCodeCredential
Async-native, official Microsoft SDK. Requires Azure AD app registration (one-time).

### D21 — Dropbox: dropbox-sdk-python with PKCE OAuth
Sync API wrapped with asyncio.to_thread. Pin v12.0.2+ for January 2026 cert compat.

### D22 — Windows service: pywin32 (NSSM as fallback)
Standard pywin32 service framework. NSSM evaluated if pywin32 service classes prove too heavy.

### D23 — Distribution: PyInstaller in Phase Gamma+
Phase Alpha/Beta is venv-based.

### D24 — Periodic jobs: APScheduler AsyncIOScheduler
SQLite job store integrated with main Curator DB.

### D25 — Notifications: pystray + pywin32, NOT plyer
Cross-platform via platform-specific code, not abstraction.

### D26 — PDF analysis: pypdf
Phase Beta broken-PDF plugin and metadata extraction.

---

## §17 — New tracker items 75–98

### From Round 3 architectural changes

75. **Source plugin contract via pluggy hookspecs** (D17) — define the 6 hooks (enumerate, read_bytes, stat, move, delete, watch) as the formal contract. Local FS becomes the first source plugin.
76. **filetype.py as primary file-type detector** (D18) — replaces python-magic in Phase Alpha. python-magic moved to Phase Beta optional enhancement.
77. **Stable Curator IDs (UUIDs) on every entity from day 1** — assigned at first sighting, persist through renames/moves. Fundamental to lineage tracking.
78. **platformdirs for all OS-specific paths** — DB, config, cache, logs all use platformdirs functions.
79. **TOML config via tomli/tomli-w** — `curator.toml` is the user-editable config; programmatic updates via tomli-w.
80. **pytest + hypothesis testing infrastructure** — synthetic test corpus generator (item 11/29) uses hypothesis to generate edge-case file pairs for lineage detection accuracy.
81. **FastAPI service mode with REST API** — Curator-as-service exposes `/files`, `/bundles`, `/lineage`, `/scan`, `/jobs` endpoints. CLI becomes thin client when service is running.
82. **uvicorn as ASGI server** — packaged with Curator. Service mode runs `uvicorn curator.api:app`.
83. **httpx for async HTTP** — used by cloud SDK plugins and FastAPI test client.

### From Round 4 cloud + system integrations

84. **Google Drive Source plugin via PyDrive2** (D19) — `curatorplug.source_gdrive`. OAuth via `LocalWebserverAuth()`.
85. **OneDrive Source plugin via msgraph-sdk-python** (D20) — `curatorplug.source_onedrive`. DeviceCodeCredential for personal use.
86. **Dropbox Source plugin via dropbox-sdk-python** (D21) — `curatorplug.source_dropbox`. PKCE OAuth flow. Pin v12.0.2+.
87. **Windows service installer via pywin32** (D22) — `curator service install` / `curator service uninstall` commands. Runs as LocalSystem or specified user.
88. **Periodic scan jobs via APScheduler** (D24) — AsyncIOScheduler, SQLite job store. Default jobs: nightly local scan, 6-hour cloud refresh, weekly cleanup.
89. **System tray via pystray** — service mode shows tray icon with menu (recent scans, pending escalations, pause/resume, exit).
90. **PyInstaller distribution in Phase Gamma+** (D23) — single-file .exe for end users without Python.
91. **pypdf broken-PDF plugin** (D26) — `curatorplug.broken_pdf`. Foundation for the broader broken-file plugin family.

### Cross-cutting from architecture work

92. **Auth credential storage abstraction** — each Source plugin handles its own credentials. Curator provides a `CredentialStore` interface (encrypted local storage). Cloud OAuth tokens, refresh tokens, API keys all flow through this.
93. **Source-aware path representation** — paths are qualified by source ID. `local:C:/Users/jmlee/Desktop/AL/file.txt` vs `gdrive:1py38t20.../subfolder/file.txt`. Curator's stable IDs are independent of paths.
94. **Cross-source bundle membership** — a Bundle can have files from local + Google Drive + OneDrive. Bundle.members is a list of `(source_id, file_id)` tuples.
95. **OAuth callback handler in service mode** — when running as service, FastAPI exposes `/auth/callback/{source_id}` for OAuth redirect URLs. Cloud sources route OAuth flows through this.
96. **Service mode vs CLI mode dispatch** — same Curator API surface. `curator inspect path/file` works whether running as CLI process OR talking to running service via REST. Detection: try local API first, fall back to direct DB access.
97. **Rules engine for auto-organization** — declarative rules in `curator.toml`:
    ```toml
    [[rules]]
    name = "Sort screenshots"
    match = "*.png"
    where = "name contains 'Screenshot'"
    move_to = "{user_pictures}/Screenshots/{year}/{month}"
    confidence_threshold = 0.95
    ```
    Rules engine evaluates conditions, plugins can extend with custom rule types. All moves go through audit log + dual-trash for rollback.
98. **APEX integration as documented use case** — Curator's REST API serves APEX's assessment file management. APEX writes assessment records that reference Curator IDs. Curator's lineage tracking + audit log provide forensic-grade traceability.

---

## §18 — Round 5 candidates (TBD)

Now that cloud + system integrations are scoped, Round 5 will likely be smaller and more targeted:

1. **NSSM** — service wrapper alternative to pywin32 service framework (evaluation only)
2. **windows-toasts** — modern Windows toast notifications if pystray's tray notifications insufficient
3. **cryptography** — for credential storage encryption
4. **keyring** — OS-native credential storage (Windows Credential Manager, macOS Keychain)
5. **tree-sitter-vba** grammar — for Phase Beta VBA AST analysis (Round 4 preview, deferred)
6. **mkdocs / mkdocs-material** — for project documentation site (when we have something to document)
7. **briefcase** (BeeWare) — alternative installer creator if PyInstaller has gaps

Round 5 size: ~5-7 repos. Will finalize when Phase Alpha implementation reveals concrete gaps.

---

## §19 — Updated future watch list

### Promoted to Round 5 from previous "future watch":
- tree-sitter-vba (Phase Beta VBA AST)

### Still on watch list (NOT to be procured now):
- **Meilisearch** — if Curator scope expands to multi-user / semantic / web-facing
- **whoosh-reloaded** — superseded by APSW FTS5 / tantivy-py
- **SQLAlchemy / SQLModel** — only if handwritten SQL becomes burdensome
- **PyFilesystem2** — superseded by our own Source plugin contract; only revisit if we want ZIP/TAR/FTP sources cheaply
- **PyDrive2's fsspec implementation** — if other Python tools (pandas, dask) need to read Curator-managed files via fsspec
- **plyer** — only if cross-platform notifications without platform code becomes important
- **google-cloud-python** Cloud Client Libraries — if we want non-Drive Google services (Cloud Storage, BigQuery) someday
- **msgraph-beta-sdk-python** — beta Microsoft Graph features

---

## Revision log (continued)

- **2026-05-05 v3.0** — Round 3 reading complete (11 repos: 26-37). Round 4 reading complete (10 repos: 38-47). 24 new tracker items surfaced (75-98). 10 new design decisions D17-D26. Major architectural shifts: PyFilesystem2 rejected → build own Source plugin contract; filetype.py promoted to primary detector → python-magic demoted; PyDrive2 picked over raw google-api-python-client for Google Drive; msgraph-sdk-python for OneDrive; dropbox-sdk-python for Dropbox; APScheduler for periodic jobs; pystray for system tray; PyInstaller deferred to Phase Gamma+; plyer rejected as overkill. New cross-cutting concepts: Source-aware path representation, cross-source bundles, OAuth callback handler in service mode, service-mode-vs-CLI dispatch, rules engine for auto-organization, APEX integration as documented use case.
