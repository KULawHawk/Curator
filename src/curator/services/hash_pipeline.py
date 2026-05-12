"""Multi-stage hash pipeline.

DESIGN.md §7.

Strategy (ported from fclones, item 05): cheap filters first, expensive
ones last. For each stage, files that pass become candidates for the
next stage; files that fail are confirmed-different and skip remaining
stages.

  Stage 1: Group by size              cheapest, no I/O on file content
  Stage 2: Dedup by inode             skip hardlinks (free dedup)
  Stage 3: Hash 4KB prefix            small I/O, eliminates most non-matches
  Stage 4: Hash 4KB suffix            small I/O, catches files w/ same prefix
  Stage 5: Full hash (xxhash3_128)    full file read, only on remaining
  Stage 6: Fuzzy hash (ppdeep)        only for text-eligible files
  Stage 7: MD5                        cheap once full content was read

For Phase Alpha: sequential processing. Parallelism comes when we have
benchmarks showing it's a bottleneck (DESIGN.md §7.4 notes
ThreadPoolExecutor as the future option).

Source-agnostic by design: all reads go through the
``curator_source_read_bytes`` hook so the same pipeline works for local
files, Google Drive, etc.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from curator._compat.datetime import utcnow_naive
from typing import Iterator, Optional

import pluggy
import xxhash
from loguru import logger

from curator.models.file import FileEntity
from curator.storage.repositories.hash_cache_repo import (
    CachedHash,
    HashCacheRepository,
)


PREFIX_BYTES = 4096
SUFFIX_BYTES = 4096
DEFAULT_CHUNK_SIZE = 65536  # 64KB

# Files with these extensions get a fuzzy hash (ppdeep) in addition to
# xxhash + md5. Keep in sync with the hint set in models/file.py.
TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py", ".bas", ".vb", ".md", ".txt", ".rst", ".json", ".yaml", ".yml",
        ".toml", ".ini", ".cfg", ".html", ".css", ".js", ".ts", ".sql",
        ".csv", ".tsv", ".log", ".xml",
    }
)


# ---------------------------------------------------------------------------
# Optional ppdeep import (vendored in Step 8)
# ---------------------------------------------------------------------------
_ppdeep_hash = None
try:
    from curator._vendored.ppdeep import hash_buf as _ppdeep_hash  # type: ignore[import-not-found,assignment]
except ImportError:
    try:
        from ppdeep import hash as _ppdeep_hash  # type: ignore[import-not-found,assignment]
    except ImportError:
        _ppdeep_hash = None


# ---------------------------------------------------------------------------
# Stats container
# ---------------------------------------------------------------------------

@dataclass
class HashPipelineStats:
    """Per-run statistics emitted by :meth:`HashPipeline.process`."""

    files_seen: int = 0
    cache_hits: int = 0
    files_hashed: int = 0
    bytes_read: int = 0
    fuzzy_hashes_computed: int = 0
    skipped_unique_size: int = 0  # Stage 1 short-circuit
    skipped_unique_prefix: int = 0  # Stage 3 (only-one-in-bucket; will hash later)
    errors: int = 0


# ---------------------------------------------------------------------------
# HashPipeline
# ---------------------------------------------------------------------------

class HashPipeline:
    """Multi-stage hash pipeline.

    Phase Alpha behavior:
        * For batches of files that share a size+prefix+suffix bucket,
          we compute the full xxhash3_128 / md5 / fuzzy_hash on each one
          and let downstream lineage detection compare them.
        * For files alone in their size bucket, we still compute the
          full hash (a single-file scan still wants change detection).
        * The hash cache short-circuits ANY stage when an entry is fresh
          (mtime + size match what's recorded).

    Hashes are written to the FileEntity in-place. Caller persists.
    """

    def __init__(
        self,
        plugin_manager: pluggy.PluginManager,
        hash_cache: HashCacheRepository,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ):
        self.pm = plugin_manager
        self.cache = hash_cache
        self.chunk_size = chunk_size

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(self, files: list[FileEntity]) -> tuple[list[FileEntity], HashPipelineStats]:
        """Process a batch of files and return them with hashes populated.

        Modifies entities in-place AND returns the same list. Stats
        come back as the second tuple element.
        """
        stats = HashPipelineStats(files_seen=len(files))

        # Stage 1: group by size.
        by_size: dict[int, list[FileEntity]] = defaultdict(list)
        for f in files:
            by_size[f.size].append(f)

        for size, group in by_size.items():
            isolated = len(group) == 1
            if isolated:
                stats.skipped_unique_size += 1
            self._process_size_group(group, isolated=isolated, stats=stats)

        return files, stats

    # ------------------------------------------------------------------
    # Stage handling
    # ------------------------------------------------------------------

    def _process_size_group(
        self,
        files: list[FileEntity],
        *,
        isolated: bool,
        stats: HashPipelineStats,
    ) -> None:
        """Run stages 2-7 on a group of files that share a size."""
        # Stage 2: dedup by inode within the same source. Hardlinks share
        # an inode and therefore content; we compute hashes once and
        # propagate to siblings.
        representatives, hardlink_siblings = self._dedup_by_inode(files)

        # Stages 3-4: prefix+suffix only matter when comparing files
        # against each other. For a single file (or after inode dedup
        # reduces the group to 1), skip straight to full hashing.
        if not isolated and len(representatives) > 1:
            sub_groups = self._group_by_prefix_suffix(representatives, stats=stats)
        else:
            sub_groups = [representatives]

        # Stages 5-7: full hash + fuzzy + md5 per sub-bucket.
        for sub in sub_groups:
            self._full_hash(sub, stats=stats)

        # Propagate hashes to hardlink siblings.
        for rep in representatives:
            siblings = hardlink_siblings.get(self._inode_key(rep), [])
            for sib in siblings:
                sib.xxhash3_128 = rep.xxhash3_128
                sib.md5 = rep.md5
                sib.fuzzy_hash = rep.fuzzy_hash

    def _dedup_by_inode(
        self,
        files: list[FileEntity],
    ) -> tuple[list[FileEntity], dict[tuple, list[FileEntity]]]:
        """Group by inode within source. Returns (representatives, siblings).

        ``representatives`` has one entry per unique (source_id, inode);
        ``siblings`` maps that key to the OTHER entries that share it.
        Files without an inode are each their own representative.
        """
        groups: dict[tuple, list[FileEntity]] = defaultdict(list)
        for f in files:
            key = self._inode_key(f)
            groups[key].append(f)

        reps: list[FileEntity] = []
        siblings: dict[tuple, list[FileEntity]] = {}
        for key, members in groups.items():
            reps.append(members[0])
            if len(members) > 1:
                siblings[key] = members[1:]
        return reps, siblings

    @staticmethod
    def _inode_key(file: FileEntity) -> tuple:
        if file.inode is not None:
            return (file.source_id, file.inode)
        # Files without an inode are unique by curator_id (no dedup).
        return ("noinode", str(file.curator_id))

    def _group_by_prefix_suffix(
        self,
        files: list[FileEntity],
        *,
        stats: HashPipelineStats,
    ) -> list[list[FileEntity]]:
        """Stages 3+4: split a group by 4KB prefix, then 4KB suffix.

        Reads only ``PREFIX_BYTES + SUFFIX_BYTES`` per file regardless of
        file size. When all files in the size bucket have prefixes that
        differ, we end up with single-element subgroups that don't need
        full hashing for dedup purposes (but we still hash them — see
        the comment in :meth:`process`).
        """
        # Group by prefix
        by_prefix: dict[bytes, list[FileEntity]] = defaultdict(list)
        for f in files:
            try:
                prefix = self._read_segment(f, 0, PREFIX_BYTES)
            except Exception as e:  # pragma: no cover — defensive
                logger.warning("prefix read failed for {p}: {e}", p=f.source_path, e=e)
                stats.errors += 1
                prefix = b""
            stats.bytes_read += len(prefix)
            by_prefix[prefix].append(f)

        # Within each prefix-group, split by suffix.
        out: list[list[FileEntity]] = []
        for prefix_group in by_prefix.values():
            if len(prefix_group) == 1:
                stats.skipped_unique_prefix += 1
                out.append(prefix_group)
                continue
            by_suffix: dict[bytes, list[FileEntity]] = defaultdict(list)
            for f in prefix_group:
                offset = max(0, f.size - SUFFIX_BYTES)
                try:
                    suffix = self._read_segment(f, offset, SUFFIX_BYTES)
                except Exception as e:  # pragma: no cover
                    logger.warning("suffix read failed for {p}: {e}", p=f.source_path, e=e)
                    stats.errors += 1
                    suffix = b""
                stats.bytes_read += len(suffix)
                by_suffix[suffix].append(f)
            out.extend(by_suffix.values())
        return out

    # ------------------------------------------------------------------
    # Full hashing (stages 5-7)
    # ------------------------------------------------------------------

    def _full_hash(self, files: list[FileEntity], *, stats: HashPipelineStats) -> None:
        """Compute xxhash3_128 + md5 + (optional) fuzzy_hash for each file."""
        for f in files:
            try:
                self._full_hash_one(f, stats=stats)
            except Exception as e:
                logger.error(
                    "hash failed for {p}: {err}", p=f.source_path, err=e,
                )
                stats.errors += 1

    def _full_hash_one(self, file: FileEntity, *, stats: HashPipelineStats) -> None:
        """Hash a single file, using the cache when fresh."""
        # Cache check. Fresh entry => populate from cache and return.
        cached = self.cache.get_if_fresh(
            file.source_id, file.source_path,
            mtime=file.mtime, size=file.size,
        )
        if cached is not None and cached.xxhash3_128 is not None:
            file.xxhash3_128 = cached.xxhash3_128
            file.md5 = cached.md5
            file.fuzzy_hash = cached.fuzzy_hash
            stats.cache_hits += 1
            return

        # Single-pass read: compute xxhash3 and md5 simultaneously while
        # also accumulating content for the (optional) fuzzy hash.
        xxh = xxhash.xxh3_128()
        md5h = hashlib.md5()
        accumulator: Optional[bytearray] = None
        wants_fuzzy = (
            _ppdeep_hash is not None
            and file.extension is not None
            and file.extension.lower() in TEXT_EXTENSIONS
        )
        if wants_fuzzy:
            accumulator = bytearray()

        bytes_read = 0
        for chunk in self._read_chunks(file):
            xxh.update(chunk)
            md5h.update(chunk)
            if accumulator is not None:
                accumulator.extend(chunk)
            bytes_read += len(chunk)

        file.xxhash3_128 = xxh.hexdigest()
        file.md5 = md5h.hexdigest()
        if accumulator is not None and _ppdeep_hash is not None:
            try:
                file.fuzzy_hash = _ppdeep_hash(bytes(accumulator))
                stats.fuzzy_hashes_computed += 1
            except Exception as e:  # pragma: no cover — defensive
                logger.warning("fuzzy hash failed for {p}: {e}", p=file.source_path, e=e)
                file.fuzzy_hash = None

        stats.bytes_read += bytes_read
        stats.files_hashed += 1

        # Update cache.
        self.cache.upsert(
            CachedHash(
                source_id=file.source_id,
                source_path=file.source_path,
                mtime=file.mtime,
                size=file.size,
                xxhash3_128=file.xxhash3_128,
                md5=file.md5,
                fuzzy_hash=file.fuzzy_hash,
                computed_at=utcnow_naive(),
            )
        )

    # ------------------------------------------------------------------
    # Source plugin reads
    # ------------------------------------------------------------------

    def _read_chunks(self, file: FileEntity) -> Iterator[bytes]:
        """Iterate over a file's bytes via ``curator_source_read_bytes``.

        Emits chunks of ``self.chunk_size`` until EOF (or until the
        source plugin returns a short read indicating end of file).
        """
        offset = 0
        while True:
            results = self.pm.hook.curator_source_read_bytes(
                source_id=file.source_id,
                file_id=file.source_path,
                offset=offset,
                length=self.chunk_size,
            )
            chunk = next((r for r in results if r is not None), None)
            if chunk is None:
                # No source plugin owned this source_id.
                raise RuntimeError(
                    f"No source plugin owns source_id={file.source_id!r}"
                )
            if not chunk:
                # EOF
                return
            yield chunk
            offset += len(chunk)
            if len(chunk) < self.chunk_size:
                # Short read = EOF for the local source plugin (and most
                # cloud sources too).
                return

    def _read_segment(self, file: FileEntity, offset: int, length: int) -> bytes:
        """Read a single segment via the source plugin."""
        results = self.pm.hook.curator_source_read_bytes(
            source_id=file.source_id,
            file_id=file.source_path,
            offset=offset,
            length=length,
        )
        chunk = next((r for r in results if r is not None), None)
        if chunk is None:
            raise RuntimeError(
                f"No source plugin owns source_id={file.source_id!r}"
            )
        return chunk
