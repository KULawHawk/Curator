"""Coverage closure for ``curator.services.hash_pipeline`` (v1.7.144).

Targets 52 uncovered lines + 12 partial branches.
"""

from __future__ import annotations

import sys
from datetime import datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from curator._compat.datetime import utcnow_naive
from curator.models import FileEntity
from curator.services.hash_pipeline import (
    DEFAULT_CHUNK_SIZE,
    PREFIX_BYTES,
    SUFFIX_BYTES,
    HashPipeline,
    HashPipelineStats,
)
from curator.storage.repositories.hash_cache_repo import (
    CachedHash,
    HashCacheRepository,
)


# ---------------------------------------------------------------------------
# Stub plugin manager
# ---------------------------------------------------------------------------


class _StubHookCaller:
    """Mimics ``pm.hook.curator_source_read_bytes(...)`` returning a list."""

    def __init__(self, reader):
        self._reader = reader

    def __call__(self, *, source_id, file_id, offset, length):
        return [self._reader(source_id, file_id, offset, length)]


class _StubPluginManager:
    """Plugin manager whose hook returns whatever the configured reader emits."""

    def __init__(self, reader):
        self.hook = MagicMock()
        self.hook.curator_source_read_bytes = _StubHookCaller(reader)


def _make_pm_from_bytes(content_by_path: dict[str, bytes]) -> _StubPluginManager:
    """Build a plugin manager that returns slices of in-memory bytes."""
    def _reader(source_id, file_id, offset, length):
        data = content_by_path.get(file_id, b"")
        return data[offset : offset + length]
    return _StubPluginManager(_reader)


def _make_pm_no_owner() -> _StubPluginManager:
    """Plugin manager whose hook returns None (no plugin owns the source)."""
    def _reader(source_id, file_id, offset, length):
        return None
    return _StubPluginManager(_reader)


def _file(path: str, **overrides) -> FileEntity:
    base = dict(
        source_id="local",
        source_path=path,
        size=overrides.pop("size", 100),
        mtime=overrides.pop("mtime", utcnow_naive()),
    )
    base.update(overrides)
    return FileEntity(**base)


# ---------------------------------------------------------------------------
# ppdeep import fallback chain (lines 68-72)
# ---------------------------------------------------------------------------


class TestPpdeepImportFallback:
    """Cover the chained ImportError handling at module top.

    The module is already loaded with whatever ppdeep variant is on the
    system. To exercise the fallback chain, we reload the module with
    sys.modules sentinels for both vendored and PyPI ppdeep."""

    def test_both_unavailable_sets_ppdeep_hash_to_none(self, monkeypatch):
        # Force ImportError for both vendored AND PyPI ppdeep
        monkeypatch.setitem(sys.modules, "curator._vendored.ppdeep", None)
        monkeypatch.setitem(sys.modules, "ppdeep", None)
        # Reload the hash_pipeline module so its top-level import code re-runs
        import importlib
        import curator.services.hash_pipeline as hp_mod
        reloaded = importlib.reload(hp_mod)
        assert reloaded._ppdeep_hash is None
        # Restore module for other tests (sys.modules sentinel will be torn down)
        importlib.reload(reloaded)


# ---------------------------------------------------------------------------
# process(): isolated vs non-isolated branch (line 142->144)
# ---------------------------------------------------------------------------


class TestProcessIsolatedBranches:
    def test_isolated_size_group_increments_skipped(self):
        """Line 142->144 (isolated True): bumps stats.skipped_unique_size."""
        cache = MagicMock(spec=HashCacheRepository)
        cache.get_if_fresh.return_value = None
        cache.upsert.return_value = None
        pm = _make_pm_from_bytes({"/x": b"x" * 100})

        pipeline = HashPipeline(pm, cache)
        f = _file("/x", size=100)
        _, stats = pipeline.process([f])
        assert stats.skipped_unique_size == 1
        assert stats.files_hashed == 1

    def test_non_isolated_size_group_no_skip(self):
        """Line 142->144 (isolated False): no skip increment."""
        cache = MagicMock(spec=HashCacheRepository)
        cache.get_if_fresh.return_value = None
        cache.upsert.return_value = None
        # Two files same size, different content
        pm = _make_pm_from_bytes({
            "/a": b"a" * 100,
            "/b": b"b" * 100,
        })
        pipeline = HashPipeline(pm, cache)
        f1 = _file("/a", size=100)
        f2 = _file("/b", size=100)
        _, stats = pipeline.process([f1, f2])
        assert stats.skipped_unique_size == 0
        # Both got hashed; prefix-split into 2 groups
        assert stats.files_hashed == 2
        assert stats.skipped_unique_prefix == 2  # each prefix unique


# ---------------------------------------------------------------------------
# Inode-based dedup (lines 181-183, 205, 213)
# ---------------------------------------------------------------------------


class TestInodeDedup:
    def test_hardlink_siblings_inherit_hashes(self):
        """Lines 181-183, 205: hardlink siblings inherit hash from representative."""
        cache = MagicMock(spec=HashCacheRepository)
        cache.get_if_fresh.return_value = None
        cache.upsert.return_value = None
        # Two files same size + same inode → siblings
        pm = _make_pm_from_bytes({"/a": b"x" * 100, "/b": b"x" * 100})
        pipeline = HashPipeline(pm, cache)
        f1 = _file("/a", size=100, inode=42)
        f2 = _file("/b", size=100, inode=42)
        files, _ = pipeline.process([f1, f2])
        # Both got the same hash; only the representative was hashed
        assert files[0].xxhash3_128 is not None
        assert files[0].xxhash3_128 == files[1].xxhash3_128
        assert files[0].md5 == files[1].md5

    def test_files_without_inode_each_unique(self):
        """Line 213: files with inode=None get unique noinode keys."""
        cache = MagicMock(spec=HashCacheRepository)
        cache.get_if_fresh.return_value = None
        cache.upsert.return_value = None
        pm = _make_pm_from_bytes({"/a": b"a" * 100, "/b": b"b" * 100})
        pipeline = HashPipeline(pm, cache)
        f1 = _file("/a", size=100, inode=None)
        f2 = _file("/b", size=100, inode=None)
        files, stats = pipeline.process([f1, f2])
        # Both hashed independently
        assert stats.files_hashed == 2


# ---------------------------------------------------------------------------
# _full_hash exception handling (lines 271-275)
# ---------------------------------------------------------------------------


class TestFullHashError:
    def test_hash_failure_increments_errors(self):
        """Lines 271-275: _full_hash_one raise -> log + errors++."""
        cache = MagicMock(spec=HashCacheRepository)
        cache.get_if_fresh.return_value = None

        def _bad_reader(source_id, file_id, offset, length):
            raise RuntimeError("read failed")

        pm = _StubPluginManager(_bad_reader)
        pipeline = HashPipeline(pm, cache)
        f = _file("/err", size=10)
        _, stats = pipeline.process([f])
        assert stats.errors == 1


# ---------------------------------------------------------------------------
# Cache hit early return (lines 285-289)
# ---------------------------------------------------------------------------


class TestCacheHit:
    def test_cache_hit_skips_hashing(self):
        """Lines 285-289: fresh cache entry -> stats.cache_hits++ early return."""
        cache = MagicMock(spec=HashCacheRepository)
        cache.get_if_fresh.return_value = CachedHash(
            source_id="local", source_path="/x",
            mtime=datetime(2026, 1, 1), size=100,
            xxhash3_128="cached_hash", md5="cached_md5", fuzzy_hash=None,
            computed_at=datetime(2026, 1, 1),
        )
        pm = _make_pm_from_bytes({"/x": b"x" * 100})  # shouldn't be called
        pipeline = HashPipeline(pm, cache)
        f = _file("/x", size=100, mtime=datetime(2026, 1, 1))
        files, stats = pipeline.process([f])
        assert stats.cache_hits == 1
        assert stats.files_hashed == 0  # didn't actually hash
        assert files[0].xxhash3_128 == "cached_hash"


# ---------------------------------------------------------------------------
# Fuzzy hash path (lines 302, 309, 315-317)
# ---------------------------------------------------------------------------


class TestFuzzyHash:
    def test_text_file_with_injected_ppdeep_computes_fuzzy(self, monkeypatch):
        """Lines 302, 309, 315-317: wants_fuzzy True -> accumulator + fuzzy_hash.

        Inject a fake _ppdeep_hash at module level to force wants_fuzzy=True
        regardless of whether ppdeep is installed."""
        cache = MagicMock(spec=HashCacheRepository)
        cache.get_if_fresh.return_value = None
        cache.upsert.return_value = None
        pm = _make_pm_from_bytes({"/notes.txt": b"hello world content"})

        # Inject a fake _ppdeep_hash function
        fake_hash = MagicMock(return_value="fake_fuzzy_signature")
        monkeypatch.setattr(
            "curator.services.hash_pipeline._ppdeep_hash", fake_hash,
        )

        pipeline = HashPipeline(pm, cache)
        f = _file("/notes.txt", size=19, extension=".txt")
        files, stats = pipeline.process([f])
        assert files[0].fuzzy_hash == "fake_fuzzy_signature"
        assert stats.fuzzy_hashes_computed == 1
        fake_hash.assert_called_once()


# ---------------------------------------------------------------------------
# _read_chunks: no-owner RuntimeError (line 360), EOF arms (365, 368->350)
# ---------------------------------------------------------------------------


class TestReadChunks:
    def test_no_plugin_owns_source_raises(self):
        """Line 360: no plugin returns a chunk -> RuntimeError."""
        cache = MagicMock(spec=HashCacheRepository)
        cache.get_if_fresh.return_value = None
        pm = _make_pm_no_owner()
        pipeline = HashPipeline(pm, cache)
        f = _file("/unowned", size=10)
        _, stats = pipeline.process([f])
        # _full_hash catches the RuntimeError -> errors++
        assert stats.errors == 1

    def test_short_read_terminates_chunks(self):
        """Branch 368: short read (< chunk_size) returns -> EOF."""
        cache = MagicMock(spec=HashCacheRepository)
        cache.get_if_fresh.return_value = None
        cache.upsert.return_value = None
        # File is smaller than chunk_size; single short read ends iteration
        pm = _make_pm_from_bytes({"/small": b"tiny"})
        pipeline = HashPipeline(pm, cache, chunk_size=DEFAULT_CHUNK_SIZE)
        f = _file("/small", size=4)
        files, stats = pipeline.process([f])
        assert files[0].xxhash3_128 is not None

    def test_multi_chunk_reads_continue(self):
        """Branch 368->350: chunk size full (== chunk_size) -> continue loop."""
        cache = MagicMock(spec=HashCacheRepository)
        cache.get_if_fresh.return_value = None
        cache.upsert.return_value = None
        # Use a tiny chunk size to force multiple iterations
        content = b"a" * 100
        pm = _make_pm_from_bytes({"/multi": content})
        pipeline = HashPipeline(pm, cache, chunk_size=10)
        f = _file("/multi", size=100)
        files, stats = pipeline.process([f])
        # Multiple iterations: bytes_read >= 100
        assert stats.bytes_read >= 100
        assert files[0].xxhash3_128 is not None

    def test_empty_chunk_returns_eof(self):
        """Line 365: empty chunk = EOF, returns cleanly."""
        cache = MagicMock(spec=HashCacheRepository)
        cache.get_if_fresh.return_value = None
        cache.upsert.return_value = None
        # File reports size=10 but plugin returns empty bytes -> EOF
        # on first chunk
        pm = _make_pm_from_bytes({"/empty": b""})  # size mismatch
        pipeline = HashPipeline(pm, cache)
        f = _file("/empty", size=10)
        files, _ = pipeline.process([f])
        # Empty file still gets hashed (xxhash of empty = constant)
        assert files[0].xxhash3_128 is not None


# ---------------------------------------------------------------------------
# _read_segment (lines 375-386)
# ---------------------------------------------------------------------------


class TestReadSegment:
    def test_basic_segment_read(self):
        """Lines 375-386 (happy path): prefix+suffix reads during
        _group_by_prefix_suffix for non-isolated multi-file group."""
        cache = MagicMock(spec=HashCacheRepository)
        cache.get_if_fresh.return_value = None
        cache.upsert.return_value = None
        # Two files with DIFFERENT prefixes so prefix split kicks in
        pm = _make_pm_from_bytes({
            "/a": b"aaa" + b"x" * 200,
            "/b": b"bbb" + b"y" * 200,
        })
        pipeline = HashPipeline(pm, cache)
        f1 = _file("/a", size=203)
        f2 = _file("/b", size=203)
        files, stats = pipeline.process([f1, f2])
        # Prefix read happened: bytes_read > 0
        assert stats.bytes_read > 0

    def test_no_plugin_owns_raises_runtime_error(self):
        """Lines 382-385: _read_segment raises when no plugin owns the source.

        Trigger via two same-size files (forces _group_by_prefix_suffix
        which calls _read_segment) with a plugin manager that returns None."""
        cache = MagicMock(spec=HashCacheRepository)
        cache.get_if_fresh.return_value = None
        cache.upsert.return_value = None
        pm = _make_pm_no_owner()
        pipeline = HashPipeline(pm, cache)
        f1 = _file("/x", size=100)
        f2 = _file("/y", size=100)
        # The _read_segment failure is caught by _group_by_prefix_suffix's
        # `try/except Exception: # pragma: no cover` — both files end up in
        # the same prefix bucket (b"") then suffix bucket (b""), then go
        # to full hash which ALSO fails. errors will be > 0.
        _, stats = pipeline.process([f1, f2])
        # Errors logged; we don't assert specific count because the
        # exception arm in prefix is # pragma: no cover.
        assert stats.errors > 0


# ---------------------------------------------------------------------------
# _group_by_prefix_suffix branch: single-file prefix subgroup (line 244)
# ---------------------------------------------------------------------------


class TestPrefixSuffixGrouping:
    def test_unique_prefix_skipped_at_245(self):
        """When two files share size but differ in prefix, each goes
        into a single-file prefix subgroup (line 244-247)."""
        cache = MagicMock(spec=HashCacheRepository)
        cache.get_if_fresh.return_value = None
        cache.upsert.return_value = None
        pm = _make_pm_from_bytes({
            "/a": b"prefix_a_unique" + b"x" * 200,
            "/b": b"prefix_b_unique" + b"y" * 200,
        })
        pipeline = HashPipeline(pm, cache)
        f1 = _file("/a", size=215)
        f2 = _file("/b", size=215)
        _, stats = pipeline.process([f1, f2])
        assert stats.skipped_unique_prefix == 2

    def test_same_prefix_different_suffix_two_subgroups(self):
        """When files share size + prefix but differ in suffix, suffix
        split produces two subgroups (covers line 258)."""
        cache = MagicMock(spec=HashCacheRepository)
        cache.get_if_fresh.return_value = None
        cache.upsert.return_value = None
        # Same prefix (first 4KB), different suffix (last 4KB), same size
        # PREFIX_BYTES = SUFFIX_BYTES = 4096.
        # Total: prefix(4096) + middle(100) + suffix(4096) = 8292
        prefix = b"p" * PREFIX_BYTES
        suffix_a = b"a" * SUFFIX_BYTES
        suffix_b = b"b" * SUFFIX_BYTES
        middle = b"m" * 100
        pm = _make_pm_from_bytes({
            "/a": prefix + middle + suffix_a,
            "/b": prefix + middle + suffix_b,
        })
        pipeline = HashPipeline(pm, cache)
        size = PREFIX_BYTES + 100 + SUFFIX_BYTES
        f1 = _file("/a", size=size)
        f2 = _file("/b", size=size)
        files, stats = pipeline.process([f1, f2])
        # Both hashed (one in each suffix bucket)
        assert stats.files_hashed == 2
