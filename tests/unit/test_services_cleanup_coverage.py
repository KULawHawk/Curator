"""Coverage closure for ``curator.services.cleanup`` (v1.7.142).

Targets the 50 uncovered lines + 9 partial branches.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from curator.services.cleanup import (
    ApplyOutcome,
    ApplyReport,
    ApplyResult,
    CleanupFinding,
    CleanupKind,
    CleanupReport,
    CleanupService,
)
from curator.services.fuzzy_index import FuzzyIndexUnavailableError
from curator.services.safety import SafetyLevel, SafetyReport, SafetyService


def _svc(audit=None, file_repo=None, *, safety=None) -> CleanupService:
    if safety is None:
        safety = SafetyService(app_data_paths=[], os_managed_paths=[])
    return CleanupService(safety, audit=audit, file_repo=file_repo)


# ---------------------------------------------------------------------------
# Report property: duration_seconds (when completed_at is None)
# ---------------------------------------------------------------------------


class TestDurationSecondsNone:
    def test_cleanup_report_duration_none_when_not_completed(self):
        r = CleanupReport(kind=CleanupKind.EMPTY_DIR, root="/x")
        # completed_at unset -> duration is None (line 223)
        assert r.duration_seconds is None

    def test_apply_report_duration_none_when_not_completed(self):
        r = ApplyReport(kind=CleanupKind.JUNK_FILE)
        # completed_at unset -> duration is None (line 175-177)
        assert r.duration_seconds is None

    def test_apply_report_duration_returns_total_seconds(self):
        """Line 177: when completed_at is set, return the elapsed time."""
        r = ApplyReport(
            kind=CleanupKind.JUNK_FILE,
            started_at=datetime(2026, 1, 15, 12, 0, 0),
        )
        r.completed_at = datetime(2026, 1, 15, 12, 0, 5)
        assert r.duration_seconds == 5.0

    def test_cleanup_report_duration_returns_total_seconds(self):
        """Line 177 (CleanupReport): completed_at set -> elapsed time."""
        r = CleanupReport(
            kind=CleanupKind.JUNK_FILE,
            root="/x",
            started_at=datetime(2026, 1, 15, 12, 0, 0),
        )
        r.completed_at = datetime(2026, 1, 15, 12, 0, 7)
        assert r.duration_seconds == 7.0


# ---------------------------------------------------------------------------
# find_empty_dirs / find_junk_files / find_broken_symlinks os.walk failures
# ---------------------------------------------------------------------------


class TestFindEmptyDirsOsWalkFailure:
    def test_os_walk_raises_is_recorded_in_errors(self, tmp_path, monkeypatch):
        """Lines 326-327: os.walk raising is caught + recorded."""
        svc = _svc()

        def _boom(*args, **kwargs):
            raise OSError("walk denied")

        monkeypatch.setattr(os, "walk", _boom)
        report = svc.find_empty_dirs(tmp_path)
        assert any("os.walk failed" in e for e in report.errors)


class TestFindJunkFilesErrors:
    def test_stat_failure_yields_zero_size(self, tmp_path, monkeypatch):
        """Lines 437-438: candidate.stat() OSError sets size=0."""
        (tmp_path / "Thumbs.db").write_bytes(b"junk")
        svc = _svc()

        original_stat = Path.stat
        calls = {"n": 0}

        def _flaky_stat(self, *a, **kw):
            # Pass through everything except the stat call on Thumbs.db
            if self.name == "Thumbs.db":
                calls["n"] += 1
                if calls["n"] >= 1:
                    raise OSError("denied")
            return original_stat(self, *a, **kw)

        monkeypatch.setattr(Path, "stat", _flaky_stat)
        report = svc.find_junk_files(tmp_path)
        # Should still record the finding with size=0
        assert any(f.path.endswith("Thumbs.db") and f.size == 0 for f in report.findings)

    def test_os_walk_failure_recorded(self, tmp_path, monkeypatch):
        """Lines 445-446: os.walk OSError recorded in errors."""
        svc = _svc()

        def _boom(*args, **kwargs):
            raise OSError("walk denied")

        monkeypatch.setattr(os, "walk", _boom)
        report = svc.find_junk_files(tmp_path)
        assert any("os.walk failed" in e for e in report.errors)


# ---------------------------------------------------------------------------
# find_broken_symlinks — fully mocked path
# ---------------------------------------------------------------------------


class TestFindBrokenSymlinks:
    def test_nonexistent_root_returns_error(self, tmp_path):
        svc = _svc()
        report = svc.find_broken_symlinks(tmp_path / "ghost")
        assert any("does not exist" in e for e in report.errors)

    def test_finds_broken_link_via_mocked_path_methods(
        self, tmp_path, monkeypatch,
    ):
        """Cover lines 370-386 by mocking Path.is_symlink + exists +
        os.readlink, since Windows blocks real symlinks without dev mode."""
        # Real file we'll pretend is a broken symlink
        fake_link = tmp_path / "broken.lnk"
        fake_link.write_text("link-data")

        svc = _svc()

        original_is_symlink = Path.is_symlink
        original_exists = Path.exists

        def _is_symlink(self):
            if self == fake_link:
                return True
            return original_is_symlink(self)

        def _exists(self, *, follow_symlinks=True):
            if self == fake_link:
                return False  # broken
            return original_exists(self, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(Path, "is_symlink", _is_symlink)
        monkeypatch.setattr(Path, "exists", _exists)
        monkeypatch.setattr(os, "readlink", lambda p: "/dst")

        report = svc.find_broken_symlinks(tmp_path)
        assert any(f.path.endswith("broken.lnk") for f in report.findings)

    def test_readlink_oserror_swallowed(self, tmp_path, monkeypatch):
        """Lines 373-374: os.readlink failure leaves target=None."""
        fake_link = tmp_path / "broken2.lnk"
        fake_link.write_text("x")

        svc = _svc()

        monkeypatch.setattr(
            Path, "is_symlink",
            lambda self: self == fake_link,
        )

        # exists() needs to handle both the broken-link False AND the
        # root tmp_path True
        original_exists = Path.exists

        def _exists(self, *, follow_symlinks=True):
            if self == fake_link:
                return False
            return original_exists(self, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(Path, "exists", _exists)

        def _readlink_boom(_p):
            raise OSError("readlink denied")

        monkeypatch.setattr(os, "readlink", _readlink_boom)

        report = svc.find_broken_symlinks(tmp_path)
        # Finding is still recorded; target is None
        finding = next((f for f in report.findings if f.path.endswith("broken2.lnk")), None)
        assert finding is not None
        assert finding.details.get("target") is None

    def test_oserror_on_link_check_records_in_errors(
        self, tmp_path, monkeypatch,
    ):
        """Lines 381-384: per-candidate OSError caught + recorded."""
        bad = tmp_path / "bad.lnk"
        bad.write_text("x")

        svc = _svc()

        def _is_symlink_boom(self):
            if self == bad:
                raise OSError("permission denied on link")
            return False

        monkeypatch.setattr(Path, "is_symlink", _is_symlink_boom)

        report = svc.find_broken_symlinks(tmp_path)
        assert any("bad.lnk" in e for e in report.errors)

    def test_os_walk_failure_recorded(self, tmp_path, monkeypatch):
        """Lines 385-386: outer os.walk OSError recorded."""
        svc = _svc()

        def _boom(*args, **kwargs):
            raise OSError("walk denied")

        monkeypatch.setattr(os, "walk", _boom)
        report = svc.find_broken_symlinks(tmp_path)
        assert any("os.walk failed" in e for e in report.errors)


# ---------------------------------------------------------------------------
# find_duplicates: file_repo.query exception
# ---------------------------------------------------------------------------


class TestFindDuplicatesQueryFailure:
    def test_query_exception_recorded(self):
        """Lines 550-553: file_repo.query raises -> error recorded."""
        file_repo = MagicMock()
        file_repo.query.side_effect = RuntimeError("repo down")
        svc = _svc(file_repo=file_repo)
        report = svc.find_duplicates()
        assert any("file_repo.query failed" in e for e in report.errors)


class TestFindDuplicatesNullHashSkip:
    def test_files_with_null_xxhash_skipped(self):
        """Line 559: defensive skip for candidates with xxhash3_128=None."""
        from curator.models import FileEntity
        from curator._compat.datetime import utcnow_naive

        # Two candidates with same hash + one with None hash
        f1 = FileEntity(
            source_id="local", source_path="/a", size=1, mtime=utcnow_naive(),
            xxhash3_128="aa",
        )
        f2 = FileEntity(
            source_id="local", source_path="/b", size=1, mtime=utcnow_naive(),
            xxhash3_128="aa",
        )
        f3 = FileEntity(
            source_id="local", source_path="/c", size=1, mtime=utcnow_naive(),
            xxhash3_128=None,
        )
        file_repo = MagicMock()
        file_repo.query.return_value = [f1, f2, f3]
        svc = _svc(file_repo=file_repo)
        report = svc.find_duplicates()
        # f3 (None hash) silently skipped; only one duplicate finding
        assert len(report.findings) == 1


# ---------------------------------------------------------------------------
# _find_fuzzy_duplicates failure paths
# ---------------------------------------------------------------------------


class TestFuzzyDuplicatesFailures:
    def test_query_exception_recorded(self):
        """Lines 632-635: file_repo.query raises -> error recorded."""
        file_repo = MagicMock()
        file_repo.query.side_effect = RuntimeError("repo down")
        svc = _svc(file_repo=file_repo)
        report = svc.find_duplicates(match_kind="fuzzy")
        assert any("file_repo.query failed" in e for e in report.errors)

    def test_fuzzy_index_init_unavailable_raises(self, monkeypatch):
        """Line 647: FuzzyIndexUnavailableError re-raises (matches handoff
        contract: caller asked for fuzzy mode, must know the failure)."""
        from curator.models import FileEntity
        from curator._compat.datetime import utcnow_naive

        f1 = FileEntity(
            source_id="local", source_path="/a", size=1, mtime=utcnow_naive(),
            fuzzy_hash="0:1:2",
        )
        file_repo = MagicMock()
        file_repo.query.return_value = [f1]

        def _unavailable(*args, **kwargs):
            raise FuzzyIndexUnavailableError("datasketch missing")

        monkeypatch.setattr(
            "curator.services.cleanup.FuzzyIndex", _unavailable,
        )
        svc = _svc(file_repo=file_repo)
        with pytest.raises(FuzzyIndexUnavailableError):
            svc.find_duplicates(match_kind="fuzzy")

    def test_fuzzy_index_init_unexpected_exception(self, monkeypatch):
        """Lines 647-651: FuzzyIndex init throws non-Unavailable error."""
        from curator.models import FileEntity
        from curator._compat.datetime import utcnow_naive

        f1 = FileEntity(
            source_id="local", source_path="/a", size=1, mtime=utcnow_naive(),
            fuzzy_hash="0:1:2",
        )
        f2 = FileEntity(
            source_id="local", source_path="/b", size=1, mtime=utcnow_naive(),
            fuzzy_hash="0:1:2",
        )
        file_repo = MagicMock()
        file_repo.query.return_value = [f1, f2]

        def _bad_init(*args, **kwargs):
            raise RuntimeError("LSH init error")

        monkeypatch.setattr(
            "curator.services.cleanup.FuzzyIndex", _bad_init,
        )
        svc = _svc(file_repo=file_repo)
        report = svc.find_duplicates(match_kind="fuzzy")
        assert any("FuzzyIndex init failed" in e for e in report.errors)

    def test_fuzzy_index_add_failure_recorded(self, monkeypatch):
        """Lines 660-661: FuzzyIndex.add() raises -> error recorded."""
        from curator.models import FileEntity
        from curator._compat.datetime import utcnow_naive

        f1 = FileEntity(
            source_id="local", source_path="/a", size=1, mtime=utcnow_naive(),
            fuzzy_hash="garbled",
        )
        f2 = FileEntity(
            source_id="local", source_path="/b", size=1, mtime=utcnow_naive(),
            fuzzy_hash="garbled",
        )
        file_repo = MagicMock()
        file_repo.query.return_value = [f1, f2]

        # Use a fake FuzzyIndex whose add() raises
        fake_index = MagicMock()
        fake_index.add.side_effect = ValueError("bad hash format")
        fake_index.query.return_value = []

        monkeypatch.setattr(
            "curator.services.cleanup.FuzzyIndex", lambda **kw: fake_index,
        )
        svc = _svc(file_repo=file_repo)
        report = svc.find_duplicates(match_kind="fuzzy")
        assert any("FuzzyIndex.add failed" in e for e in report.errors)

    def test_fuzzy_index_query_failure_skipped(self, monkeypatch):
        """Lines 672-673: FuzzyIndex.query() raises -> skipped via continue."""
        from curator.models import FileEntity
        from curator._compat.datetime import utcnow_naive

        f1 = FileEntity(
            source_id="local", source_path="/a", size=1, mtime=utcnow_naive(),
            fuzzy_hash="ok",
        )
        file_repo = MagicMock()
        file_repo.query.return_value = [f1]

        fake_index = MagicMock()
        fake_index.add.return_value = None
        fake_index.query.side_effect = ValueError("bad query")

        monkeypatch.setattr(
            "curator.services.cleanup.FuzzyIndex", lambda **kw: fake_index,
        )
        svc = _svc(file_repo=file_repo)
        report = svc.find_duplicates(match_kind="fuzzy")
        # No findings, but no fatal error (the exception was skipped)
        assert report.findings == []

    def test_fuzzy_neighbor_not_in_by_id_skipped(self, monkeypatch):
        """Line 678: defensive — neighbor returned by query not in by_id."""
        from curator.models import FileEntity
        from curator._compat.datetime import utcnow_naive
        from uuid import uuid4

        f1 = FileEntity(
            source_id="local", source_path="/a", size=1, mtime=utcnow_naive(),
            fuzzy_hash="ok",
        )
        file_repo = MagicMock()
        file_repo.query.return_value = [f1]

        fake_index = MagicMock()
        fake_index.add.return_value = None
        # Return a curator_id that's NOT in by_id (defensive check)
        fake_index.query.return_value = [uuid4(), f1.curator_id]

        monkeypatch.setattr(
            "curator.services.cleanup.FuzzyIndex", lambda **kw: fake_index,
        )
        svc = _svc(file_repo=file_repo)
        report = svc.find_duplicates(match_kind="fuzzy")
        # No real duplicates; the bogus neighbor is filtered
        assert report.findings == []


# ---------------------------------------------------------------------------
# apply: safety check exception + _delete_one branches
# ---------------------------------------------------------------------------


class TestApplySafetyException:
    def test_safety_check_exception_records_skipped_refuse(self, tmp_path):
        """Lines 834-844: safety.check_path raises -> SKIPPED_REFUSE."""
        target = tmp_path / "f.txt"
        target.write_text("x")

        safety = MagicMock()
        safety.check_path.side_effect = RuntimeError("safety boom")

        svc = _svc(safety=safety)
        report = CleanupReport(kind=CleanupKind.JUNK_FILE, root=str(tmp_path))
        report.findings.append(CleanupFinding(
            path=str(target), kind=CleanupKind.JUNK_FILE, size=1, details={},
        ))
        out = svc.apply(report)
        assert out.results[0].outcome == ApplyOutcome.SKIPPED_REFUSE
        assert "safety check failed" in (out.results[0].error or "")


class TestDeleteOneEmptyDirJunkUnlinkFailure:
    def test_empty_dir_junk_unlink_oserror_swallowed(self, tmp_path):
        """Lines 925-926: junk_path.unlink() OSError in EMPTY_DIR branch
        is swallowed (best-effort); rmdir below may still succeed."""
        d = tmp_path / "empty_with_thumbs"
        d.mkdir()
        # Don't create the junk file -> unlink will raise FileNotFoundError
        svc = _svc()
        report = CleanupReport(kind=CleanupKind.EMPTY_DIR, root=str(tmp_path))
        report.findings.append(CleanupFinding(
            path=str(d), kind=CleanupKind.EMPTY_DIR, size=0,
            details={"system_junk_present": ["Thumbs.db"]},  # doesn't exist
        ))
        out = svc.apply(report)
        # The dir was successfully removed (rmdir succeeded since it WAS empty)
        assert out.results[0].outcome == ApplyOutcome.DELETED
        assert not d.exists()


class TestDeleteOneBrokenSymlink:
    def test_broken_symlink_branch(self, tmp_path):
        """Line 930: BROKEN_SYMLINK branch unlinks the target."""
        # Use a regular file as a "fake symlink" — _delete_one just unlinks it
        target = tmp_path / "fake_link"
        target.write_text("link-data")

        svc = _svc()
        report = CleanupReport(kind=CleanupKind.BROKEN_SYMLINK, root=str(tmp_path))
        report.findings.append(CleanupFinding(
            path=str(target), kind=CleanupKind.BROKEN_SYMLINK, size=0, details={},
        ))
        out = svc.apply(report)
        assert out.results[0].outcome == ApplyOutcome.DELETED
        assert not target.exists()


class TestDeleteOneSendToTrashSuccess:
    def test_send2trash_success_returns_without_unlink(self, tmp_path, monkeypatch):
        """Line 942: send2trash succeeds -> early return, no unlink call."""
        target = tmp_path / "trash_me.tmp"
        target.write_text("junk")

        import sys
        import types
        fake_mod = types.ModuleType("curator._vendored.send2trash")
        trashed = []

        def _success(p):
            trashed.append(p)
            # Simulate trash: just delete the file
            Path(p).unlink()

        fake_mod.send2trash = _success
        monkeypatch.setitem(sys.modules, "curator._vendored.send2trash", fake_mod)

        svc = _svc()
        report = CleanupReport(kind=CleanupKind.JUNK_FILE, root=str(tmp_path))
        report.findings.append(CleanupFinding(
            path=str(target), kind=CleanupKind.JUNK_FILE, size=4, details={},
        ))
        out = svc.apply(report, use_trash=True)
        assert out.results[0].outcome == ApplyOutcome.DELETED
        assert trashed == [str(target)]
        assert not target.exists()


class TestDeleteOneSendToTrashFailureFallback:
    def test_send2trash_failure_falls_back_to_unlink(self, tmp_path, monkeypatch):
        """Lines 939-944: send2trash raises -> fallback to Path.unlink."""
        target = tmp_path / "junk.tmp"
        target.write_text("junk")

        # Inject a fake send2trash that raises
        import sys
        import types
        fake_mod = types.ModuleType("curator._vendored.send2trash")

        def _boom(_p):
            raise RuntimeError("trash unavailable")

        fake_mod.send2trash = _boom
        monkeypatch.setitem(sys.modules, "curator._vendored.send2trash", fake_mod)

        svc = _svc()
        report = CleanupReport(kind=CleanupKind.JUNK_FILE, root=str(tmp_path))
        report.findings.append(CleanupFinding(
            path=str(target), kind=CleanupKind.JUNK_FILE, size=4, details={},
        ))
        out = svc.apply(report, use_trash=True)
        # send2trash threw -> fallback to unlink -> DELETED
        assert out.results[0].outcome == ApplyOutcome.DELETED
        assert not target.exists()


class TestDeleteOneUnknownKind:
    def test_unknown_cleanup_kind_raises_value_error(self, tmp_path):
        """Line 951: unknown CleanupKind raises ValueError, which becomes
        a FAILED outcome in apply()."""
        target = tmp_path / "weird"
        target.write_text("x")

        # Construct a finding with an actual CleanupKind, then mutate
        # to force the unknown branch. Since CleanupKind is an enum, we
        # need a placeholder that ISN'T one of the 4 handled kinds.
        # The enum has: EMPTY_DIR, BROKEN_SYMLINK, JUNK_FILE, DUPLICATE_FILE.
        # Bypass pydantic validation to force a string kind that's not enum.
        finding = CleanupFinding(
            path=str(target), kind=CleanupKind.JUNK_FILE, size=1, details={},
        )
        # Direct attribute swap (pydantic field bypass per Lesson #95)
        finding.__dict__["kind"] = "UNHANDLED_KIND"

        svc = _svc()
        report = CleanupReport(kind=CleanupKind.JUNK_FILE, root=str(tmp_path))
        # Also bypass to allow construction with mismatched kind
        report.findings.append(finding)
        out = svc.apply(report)
        # The unknown branch raised ValueError -> FAILED
        assert out.results[0].outcome == ApplyOutcome.FAILED


# ---------------------------------------------------------------------------
# _mark_index_deleted: DUPLICATE_FILE not found in index (logger.debug branch)
# ---------------------------------------------------------------------------


class TestMarkIndexDeletedDuplicateNotFound:
    def test_duplicate_with_no_index_entity_logs_debug(self, tmp_path):
        """Line 999: DUPLICATE_FILE finding's path not found in index ->
        debug log fires (we just need to exercise the branch)."""
        target = tmp_path / "dup.txt"
        target.write_text("x")

        file_repo = MagicMock()
        file_repo.find_by_path.return_value = None  # not in index
        # Permissive safety so deletion proceeds
        svc = _svc(file_repo=file_repo)

        report = CleanupReport(kind=CleanupKind.DUPLICATE_FILE, root=str(tmp_path))
        report.findings.append(CleanupFinding(
            path=str(target), kind=CleanupKind.DUPLICATE_FILE, size=1,
            details={"source_id": "local"},
        ))
        out = svc.apply(report, use_trash=False)
        # Delete succeeded; _mark_index_deleted hit line 999 (debug log)
        assert out.results[0].outcome == ApplyOutcome.DELETED
        file_repo.find_by_path.assert_called()
        # find_by_path returned None, so mark_deleted was NOT called
        file_repo.mark_deleted.assert_not_called()
