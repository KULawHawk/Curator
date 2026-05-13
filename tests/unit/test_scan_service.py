"""Focused unit tests for ScanService (services/scan.py).

Existing integration tests cover the happy paths (test_scan_flow.py et al.,
178 tests touching scan.py). This file targets the 15% of uncovered code
that's hard to reach via integration — specifically:

* The error-handling try/except blocks at each layer of the scan pipeline
* The defensive re-scan logic (un-soft-delete, re-derive extension)
* The "no source plugin" RuntimeError
* The ScanReport.duration_seconds None branch
* The _ensure_source `info is None` continue branch
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from uuid import UUID, uuid4

import pytest

from curator.models.file import FileEntity
from curator.models.jobs import ScanJob
from curator.models.source import SourceConfig
from curator.models.types import FileInfo
from curator.services.hash_pipeline import HashPipelineStats
from curator.services.scan import ScanReport, ScanService


NOW = datetime(2026, 5, 12, 12, 0, 0)


# ===========================================================================
# Stubs — minimal fakes for each ScanService dependency
# ===========================================================================


class StubFileRepository:
    """Minimal FileRepository for ScanService tests."""

    def __init__(self, files: list[FileEntity] | None = None):
        # Lookup is by (source_id, source_path)
        self._by_path: dict[tuple[str, str], FileEntity] = {
            (f.source_id, f.source_path): f for f in (files or [])
        }
        self._by_id: dict[UUID, FileEntity] = {
            f.curator_id: f for f in (files or [])
        }
        self.inserted: list[FileEntity] = []
        self.updated: list[FileEntity] = []
        self.marked_deleted: list[UUID] = []

    def find_by_path(self, source_id: str, path: str) -> FileEntity | None:
        return self._by_path.get((source_id, path))

    def insert(self, entity: FileEntity) -> None:
        self._by_path[(entity.source_id, entity.source_path)] = entity
        self._by_id[entity.curator_id] = entity
        self.inserted.append(entity)

    def update(self, entity: FileEntity) -> None:
        self._by_path[(entity.source_id, entity.source_path)] = entity
        self._by_id[entity.curator_id] = entity
        self.updated.append(entity)

    def mark_deleted(self, curator_id: UUID) -> None:
        self.marked_deleted.append(curator_id)
        if curator_id in self._by_id:
            self._by_id[curator_id].deleted_at = NOW


class StubSourceRepository:
    def __init__(self, sources: list[SourceConfig] | None = None):
        self._sources: dict[str, SourceConfig] = {
            s.source_id: s for s in (sources or [])
        }
        self.inserted: list[SourceConfig] = []

    def get(self, source_id: str) -> SourceConfig | None:
        return self._sources.get(source_id)

    def insert(self, source: SourceConfig) -> None:
        self._sources[source.source_id] = source
        self.inserted.append(source)


class StubScanJobRepository:
    def __init__(self):
        self.inserted: list[ScanJob] = []
        self.status_updates: list[tuple[UUID, str, str | None]] = []
        self.counter_updates: list[dict[str, Any]] = []

    def insert(self, job: ScanJob) -> None:
        self.inserted.append(job)

    def update_status(self, job_id: UUID, status: str, error: str | None = None) -> None:
        self.status_updates.append((job_id, status, error))

    def update_counters(self, job_id: UUID, **kwargs) -> None:
        self.counter_updates.append({"job_id": job_id, **kwargs})


@dataclass
class StubHashPipeline:
    """Stub for HashPipeline; returns a canned HashPipelineStats."""

    stats: HashPipelineStats = field(default_factory=lambda: HashPipelineStats())
    raise_on_process: Exception | None = None
    files_processed: list[FileEntity] = field(default_factory=list)

    def process(self, files: list[FileEntity]):
        if self.raise_on_process is not None:
            raise self.raise_on_process
        self.files_processed.extend(files)
        return (files, self.stats)


@dataclass
class StubClassificationService:
    apply_result: str | None = "document"
    apply_raises: Exception | None = None
    calls: list[FileEntity] = field(default_factory=list)

    def apply(self, file: FileEntity):
        if self.apply_raises is not None:
            raise self.apply_raises
        self.calls.append(file)
        return self.apply_result


@dataclass
class StubLineageService:
    edges_to_return: list = field(default_factory=list)
    compute_raises: Exception | None = None
    calls: list[FileEntity] = field(default_factory=list)

    def compute_for_file(self, file: FileEntity, *, persist: bool = True):
        if self.compute_raises is not None:
            raise self.compute_raises
        self.calls.append(file)
        return list(self.edges_to_return)


class StubBoundLogger:
    """Captures audit log calls from the bound logger."""

    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, action: str, **kwargs) -> None:
        self.calls.append((action, kwargs))


class StubAuditService:
    def __init__(self):
        self.bound_loggers: list[StubBoundLogger] = []

    def bind(self, *, actor: str, **default_details: Any) -> StubBoundLogger:
        logger = StubBoundLogger()
        self.bound_loggers.append(logger)
        return logger


# ---------------------------------------------------------------------------
# Pluggy stubs
# ---------------------------------------------------------------------------


@dataclass
class StubHookCaller:
    impl: Callable[..., list[Any]] = field(default_factory=lambda: lambda **_: [])

    def __call__(self, **kwargs) -> list[Any]:
        return self.impl(**kwargs)


@dataclass
class StubHooks:
    curator_source_enumerate: StubHookCaller = field(default_factory=StubHookCaller)
    curator_source_register: StubHookCaller = field(default_factory=StubHookCaller)


@dataclass
class StubPluginManager:
    hook: StubHooks = field(default_factory=StubHooks)

    def set_enumerate(self, fn):
        self.hook.curator_source_enumerate = StubHookCaller(impl=fn)

    def set_register(self, fn):
        self.hook.curator_source_register = StubHookCaller(impl=fn)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_file_info(
    path: str = "/data/file.txt",
    size: int = 100,
    mtime: datetime | None = None,
    ctime: datetime | None = None,
) -> FileInfo:
    return FileInfo(
        file_id=path,
        path=path,
        size=size,
        mtime=mtime or NOW,
        ctime=ctime or NOW,
        is_directory=False,
        extras={"inode": 12345},
    )


def make_service(
    *,
    file_repo: StubFileRepository | None = None,
    source_repo: StubSourceRepository | None = None,
    job_repo: StubScanJobRepository | None = None,
    hash_pipeline: StubHashPipeline | None = None,
    classification: StubClassificationService | None = None,
    lineage: StubLineageService | None = None,
    audit: StubAuditService | None = None,
    plugin_manager: StubPluginManager | None = None,
):
    """Build a ScanService with stubs (any None becomes a fresh default)."""
    return ScanService(
        plugin_manager=plugin_manager or StubPluginManager(),
        file_repo=file_repo or StubFileRepository(),
        source_repo=source_repo or StubSourceRepository(),
        job_repo=job_repo or StubScanJobRepository(),
        hash_pipeline=hash_pipeline or StubHashPipeline(),
        classification=classification or StubClassificationService(),
        lineage=lineage or StubLineageService(),
        audit=audit or StubAuditService(),
    )


# ===========================================================================
# ScanReport.duration_seconds
# ===========================================================================


class TestScanReport:
    def test_duration_seconds_returns_none_before_completion(self):
        # Line 87: return None when completed_at is None
        report = ScanReport(
            job_id=uuid4(),
            source_id="local",
            root="/data",
            started_at=NOW,
        )
        assert report.duration_seconds is None

    def test_duration_seconds_after_completion(self):
        from datetime import timedelta
        report = ScanReport(
            job_id=uuid4(),
            source_id="local",
            root="/data",
            started_at=NOW,
            completed_at=NOW + timedelta(seconds=5),
        )
        assert report.duration_seconds == 5.0


# ===========================================================================
# scan() top-level except (lines 182-185)
# ===========================================================================


class TestScanTopLevelException:
    def test_scan_exception_marks_job_failed_and_reraises(self):
        # Force _run to raise by giving an enumerate hook that raises.
        pm = StubPluginManager()
        pm.set_enumerate(lambda **_: [iter(_raising_iterator())])
        # Register a source so _ensure_source passes
        sources = StubSourceRepository(
            sources=[SourceConfig(
                source_id="local", source_type="local", display_name="Local",
            )]
        )
        job_repo = StubScanJobRepository()
        svc = make_service(
            plugin_manager=pm,
            source_repo=sources,
            job_repo=job_repo,
        )

        with pytest.raises(RuntimeError, match="boom"):
            svc.scan(source_id="local", root="/data")

        # Job was marked failed
        statuses = [(s, e) for (_, s, e) in job_repo.status_updates]
        assert any(s == "failed" for s, _ in statuses)


def _raising_iterator():
    """Yield a value then raise — exercises the per-file path before the outer except."""
    raise RuntimeError("boom")
    yield  # pragma: no cover


# ===========================================================================
# scan_paths() top-level except (lines 272-275)
# ===========================================================================


class TestScanPathsTopLevelException:
    def test_scan_paths_exception_marks_job_failed_and_reraises(self, monkeypatch):
        # Mock _run_paths to raise.
        sources = StubSourceRepository(
            sources=[SourceConfig(
                source_id="local", source_type="local", display_name="Local",
            )]
        )
        job_repo = StubScanJobRepository()
        svc = make_service(source_repo=sources, job_repo=job_repo)

        def boom(*args, **kwargs):
            raise RuntimeError("paths boom")

        monkeypatch.setattr(svc, "_run_paths", boom)

        with pytest.raises(RuntimeError, match="paths boom"):
            svc.scan_paths(source_id="local", paths=["/x.txt"])

        statuses = [s for (_, s, _) in job_repo.status_updates]
        assert "failed" in statuses


# ===========================================================================
# scan_paths() per-path error branches (lines 299-302, 318-322, 327-330)
# ===========================================================================


class TestScanPathsPerPathErrors:
    def _make_svc_with_source(self):
        sources = StubSourceRepository(
            sources=[SourceConfig(
                source_id="local", source_type="local", display_name="Local",
            )]
        )
        return make_service(source_repo=sources), sources

    def test_dedupe_skips_duplicate_paths(self, tmp_path):
        # Defensive dedup: same path twice is processed only once.
        svc, _ = self._make_svc_with_source()
        f = tmp_path / "a.txt"
        f.write_text("x")
        report = svc.scan_paths(
            source_id="local", paths=[str(f), str(f)]
        )
        # Only one file was "seen"
        assert report.files_seen == 1

    def test_invalid_path_object_caught(self, tmp_path):
        # Lines 299-302: Path(raw_path) raises TypeError/ValueError.
        svc, _ = self._make_svc_with_source()
        # An object whose __fspath__ returns something Path can't handle.
        # The simplest trigger: pass a path that's NOT str/PathLike at all.
        # Python's Path() raises TypeError for non-str-like objects.
        report = svc.scan_paths(
            source_id="local", paths=[12345]  # type: ignore[list-item]
        )
        assert report.errors == 1
        assert "12345" in report.error_paths[0]

    def test_nonexistent_path_marked_deleted_if_known(self, tmp_path):
        # The "vanished from disk" path: existing entity gets soft-deleted.
        ghost_path = tmp_path / "gone.txt"  # never created
        existing = FileEntity(
            source_id="local",
            source_path=str(ghost_path),
            size=100,
            mtime=NOW,
        )
        file_repo = StubFileRepository(files=[existing])
        sources = StubSourceRepository(
            sources=[SourceConfig(
                source_id="local", source_type="local", display_name="Local",
            )]
        )
        svc = make_service(file_repo=file_repo, source_repo=sources)
        report = svc.scan_paths(
            source_id="local", paths=[str(ghost_path)]
        )
        assert report.files_deleted == 1
        assert existing.curator_id in file_repo.marked_deleted

    def test_nonexistent_path_unknown_is_noop(self, tmp_path):
        # Vanished path with no entity → no-op (no error, no delete).
        svc, _ = self._make_svc_with_source()
        ghost = tmp_path / "never_existed.txt"
        report = svc.scan_paths(source_id="local", paths=[str(ghost)])
        assert report.files_deleted == 0
        assert report.errors == 0

    def test_directory_path_skipped_silently(self, tmp_path):
        # If a directory slips through, skip without error.
        svc, _ = self._make_svc_with_source()
        d = tmp_path / "subdir"
        d.mkdir()
        report = svc.scan_paths(source_id="local", paths=[str(d)])
        # Directory is neither processed nor reported as error
        assert report.errors == 0
        assert report.files_seen == 0

    def test_stat_oserror_caught(self, monkeypatch, tmp_path):
        # Lines 318-322: OSError on stat is caught.
        # We patch _stat_to_file_info directly rather than Path.stat,
        # because Path.exists() also calls stat internally and we don't
        # want our monkeypatch to break the existence check.
        svc, _ = self._make_svc_with_source()
        f = tmp_path / "broken.txt"
        f.write_text("")

        def boom_stat(p):
            raise OSError("simulated stat failure")

        monkeypatch.setattr(svc, "_stat_to_file_info", boom_stat)
        report = svc.scan_paths(source_id="local", paths=[str(f)])
        assert report.errors >= 1
        assert str(f) in report.error_paths[0]

    def test_upsert_exception_caught(self, monkeypatch, tmp_path):
        # Lines 327-330: _upsert_from_info raises → caught, errors++.
        svc, _ = self._make_svc_with_source()
        f = tmp_path / "good.txt"
        f.write_text("x")

        def boom_upsert(*args, **kwargs):
            raise RuntimeError("upsert boom")

        monkeypatch.setattr(svc, "_upsert_from_info", boom_upsert)
        report = svc.scan_paths(source_id="local", paths=[str(f)])
        assert report.errors == 1
        assert str(f) in report.error_paths[0]


# ===========================================================================
# scan_paths() post-process error (lines 355-361)
# ===========================================================================


class TestScanPathsPostProcessError:
    def test_post_process_exception_caught(self, monkeypatch, tmp_path):
        sources = StubSourceRepository(
            sources=[SourceConfig(
                source_id="local", source_type="local", display_name="Local",
            )]
        )
        svc = make_service(source_repo=sources)
        f = tmp_path / "x.txt"
        f.write_text("hi")

        def boom_pp(file, report):
            raise RuntimeError("post-process boom")

        monkeypatch.setattr(svc, "_post_process_one", boom_pp)
        report = svc.scan_paths(source_id="local", paths=[str(f)])
        # The file was upserted (files_seen >= 1) but post-processing failed.
        assert report.errors >= 1
        assert f.as_posix() in report.error_paths[0] or str(f) in report.error_paths[0]


# ===========================================================================
# scan() post-process error (lines 445-451)
# ===========================================================================


class TestScanPostProcessError:
    def test_post_process_exception_caught_in_full_scan(self, monkeypatch):
        # scan() also has a post-process try/except; same shape but
        # in the _run pipeline.
        sources = StubSourceRepository(
            sources=[SourceConfig(
                source_id="local", source_type="local", display_name="Local",
            )]
        )
        pm = StubPluginManager()
        pm.set_enumerate(lambda **_: [iter([make_file_info("/a.txt")])])
        svc = make_service(plugin_manager=pm, source_repo=sources)

        def boom_pp(file, report):
            raise RuntimeError("scan post-process boom")

        monkeypatch.setattr(svc, "_post_process_one", boom_pp)
        report = svc.scan(source_id="local", root="/data")
        assert report.errors >= 1


# ===========================================================================
# _enumerate_and_persist: no plugin → RuntimeError (line 470)
# ===========================================================================


class TestEnumerateNoPlugin:
    def test_no_source_plugin_raises_runtime_error(self):
        # Source pre-registered (so _ensure_source returns it without
        # touching the register hook), but enumerate hook returns no
        # iterators → RuntimeError.
        sources = StubSourceRepository(
            sources=[SourceConfig(
                source_id="local", source_type="local", display_name="Local",
            )]
        )
        pm = StubPluginManager()
        # All plugins return None (no source plugin claimed it)
        pm.set_enumerate(lambda **_: [None, None])
        svc = make_service(plugin_manager=pm, source_repo=sources)

        with pytest.raises(RuntimeError, match="No source plugin registered"):
            svc.scan(source_id="local", root="/data")


# ===========================================================================
# _enumerate_and_persist: upsert error (lines 479-485)
# ===========================================================================


class TestEnumerateUpsertError:
    def test_upsert_exception_during_enumerate_caught(self, monkeypatch):
        sources = StubSourceRepository(
            sources=[SourceConfig(
                source_id="local", source_type="local", display_name="Local",
            )]
        )
        pm = StubPluginManager()
        pm.set_enumerate(lambda **_: [iter([make_file_info("/bad.txt")])])
        svc = make_service(plugin_manager=pm, source_repo=sources)

        def boom_upsert(*args, **kwargs):
            raise RuntimeError("enumerate upsert boom")

        monkeypatch.setattr(svc, "_upsert_from_info", boom_upsert)
        report = svc.scan(source_id="local", root="/data")
        assert report.errors >= 1
        assert "/bad.txt" in report.error_paths[0]


# ===========================================================================
# _upsert_from_info: re-scan logic (lines 543, 546)
# ===========================================================================


class TestUpsertReScanLogic:
    def test_re_derive_extension_when_existing_extension_is_none(self):
        # Line 543: existing.extension is None → re-derive from path
        existing = FileEntity(
            source_id="local",
            source_path="/data/file.txt",
            size=100,
            mtime=NOW,
            extension=None,  # explicitly None
        )
        file_repo = StubFileRepository(files=[existing])
        sources = StubSourceRepository(
            sources=[SourceConfig(
                source_id="local", source_type="local", display_name="Local",
            )]
        )
        pm = StubPluginManager()
        pm.set_enumerate(lambda **_: [iter([make_file_info("/data/file.txt")])])
        svc = make_service(
            plugin_manager=pm, file_repo=file_repo, source_repo=sources,
        )
        svc.scan(source_id="local", root="/data")
        # Extension was re-derived
        assert existing.extension == ".txt"

    def test_undeletes_re_scanned_file(self):
        # Line 546: existing.deleted_at is not None → un-delete it.
        existing = FileEntity(
            source_id="local",
            source_path="/data/zombie.txt",
            size=100,
            mtime=NOW,
            extension=".txt",
            deleted_at=NOW,  # tombstoned
        )
        file_repo = StubFileRepository(files=[existing])
        sources = StubSourceRepository(
            sources=[SourceConfig(
                source_id="local", source_type="local", display_name="Local",
            )]
        )
        pm = StubPluginManager()
        pm.set_enumerate(lambda **_: [iter([make_file_info("/data/zombie.txt")])])
        svc = make_service(
            plugin_manager=pm, file_repo=file_repo, source_repo=sources,
        )
        svc.scan(source_id="local", root="/data")
        # Re-scan zeroed out deleted_at
        assert existing.deleted_at is None


# ===========================================================================
# _ensure_source: None plugin info skipped (line 604)
# ===========================================================================


class TestEnsureSource:
    def test_skips_none_plugin_infos(self):
        # Line 604: `if info is None: continue` in _ensure_source.
        # Trigger: source NOT pre-registered + register hook returns
        # a mix of None and one real SourcePluginInfo. The None ones
        # must be skipped without error.
        from curator.models.types import SourcePluginInfo
        sources = StubSourceRepository()  # source NOT pre-registered
        pm = StubPluginManager()
        # Register hook returns [None, real_info, None]
        real_info = SourcePluginInfo(
            source_type="local",
            display_name="Local FS",
            requires_auth=False,
            supports_watch=False,
        )
        pm.set_register(lambda **_: [None, real_info, None])
        # Enumerate hook returns an empty iterator (we don't need files)
        pm.set_enumerate(lambda **_: [iter([])])
        svc = make_service(plugin_manager=pm, source_repo=sources)
        # Should succeed despite the None entries in register results.
        report = svc.scan(source_id="local", root="/data")
        # Source got registered
        assert sources.get("local") is not None
        assert report.files_seen == 0

    def test_no_matching_plugin_raises(self):
        # If register hook returns nothing matching source_id, RuntimeError.
        sources = StubSourceRepository()
        pm = StubPluginManager()
        pm.set_register(lambda **_: [None])
        svc = make_service(plugin_manager=pm, source_repo=sources)
        with pytest.raises(RuntimeError, match="No source plugin matches"):
            svc.scan(source_id="local", root="/data")

    def test_skips_plugin_with_non_matching_source_type(self):
        # Branch 605->602: SourcePluginInfo present, but its source_type
        # doesn't match the requested source_id. Loop should continue
        # rather than register the mismatched plugin.
        from curator.models.types import SourcePluginInfo
        sources = StubSourceRepository()
        pm = StubPluginManager()
        # Plugin claims "gdrive", we ask for "local" -> mismatch -> continue.
        # The loop falls through to the RuntimeError at the end.
        mismatch = SourcePluginInfo(
            source_type="gdrive",
            display_name="Google Drive",
            requires_auth=True,
            supports_watch=False,
        )
        pm.set_register(lambda **_: [mismatch])
        svc = make_service(plugin_manager=pm, source_repo=sources)
        with pytest.raises(RuntimeError, match="No source plugin matches"):
            svc.scan(source_id="local", root="/data")


# ===========================================================================
# Remaining small branches (lines 398, 555, 573->577)
# ===========================================================================


class TestRemainingBranches:
    def test_mark_deleted_idempotent_for_already_deleted(self, tmp_path):
        # Line 398: existing entity is already deleted_at != None -> return
        # without calling mark_deleted again. Confirms idempotency.
        ghost_path = tmp_path / "already_gone.txt"  # never created on disk
        existing = FileEntity(
            source_id="local",
            source_path=str(ghost_path),
            size=100,
            mtime=NOW,
            deleted_at=NOW,  # already tombstoned
        )
        file_repo = StubFileRepository(files=[existing])
        sources = StubSourceRepository(
            sources=[SourceConfig(
                source_id="local", source_type="local", display_name="Local",
            )]
        )
        svc = make_service(file_repo=file_repo, source_repo=sources)
        report = svc.scan_paths(source_id="local", paths=[str(ghost_path)])
        # Should NOT have called mark_deleted again -- already deleted.
        assert file_repo.marked_deleted == []
        # files_deleted counter NOT incremented either (the function returns
        # before that line).
        assert report.files_deleted == 0

    def test_upsert_increments_files_unchanged_when_nothing_changed(self):
        # Line 555: existing entity with same size/mtime/inode -> unchanged.
        existing = FileEntity(
            source_id="local",
            source_path="/data/static.txt",
            size=100,
            mtime=NOW,
            inode=12345,
            extension=".txt",
        )
        file_repo = StubFileRepository(files=[existing])
        sources = StubSourceRepository(
            sources=[SourceConfig(
                source_id="local", source_type="local", display_name="Local",
            )]
        )
        pm = StubPluginManager()
        # FileInfo with identical size/mtime/inode -> no change
        unchanged_info = FileInfo(
            file_id="/data/static.txt",
            path="/data/static.txt",
            size=100,
            mtime=NOW,
            ctime=NOW,
            is_directory=False,
            extras={"inode": 12345},
        )
        pm.set_enumerate(lambda **_: [iter([unchanged_info])])
        svc = make_service(
            plugin_manager=pm, file_repo=file_repo, source_repo=sources,
        )
        report = svc.scan(source_id="local", root="/data")
        assert report.files_unchanged == 1
        assert report.files_updated == 0

    def test_classification_returns_none_skips_increment(self):
        # Branch 573->577: chosen is None -> skip the classifications_assigned
        # increment, but still call self.files.update(file).
        sources = StubSourceRepository(
            sources=[SourceConfig(
                source_id="local", source_type="local", display_name="Local",
            )]
        )
        classification = StubClassificationService(apply_result=None)
        pm = StubPluginManager()
        pm.set_enumerate(lambda **_: [iter([make_file_info("/a.txt")])])
        svc = make_service(
            plugin_manager=pm,
            source_repo=sources,
            classification=classification,
        )
        report = svc.scan(source_id="local", root="/data")
        # File was seen but classification didn't assign anything
        assert report.files_seen == 1
        assert report.classifications_assigned == 0
        # Classification was still called (apply_result returned None)
        assert len(classification.calls) == 1
