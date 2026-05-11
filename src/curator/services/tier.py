"""Tiered Storage Manager — T-B05.

Identifies files that have aged into a different storage tier and
(optionally) migrates them to a cold-storage destination. Built on
top of the T-C02 status taxonomy (vital / active / provisional / junk)
and the existing migration pipeline.

Three tier-transition recipes ship in v1.7.8:

  * **cold**: ``status='provisional'`` AND ``last_scanned_at`` older
    than ``min_age_days`` (default 90). These files aren't classified
    as active work anymore but haven't been trashed either — they're
    candidates for cheap cold storage (external drive, Backblaze B2,
    archived NAS share). The provisional bucket is exactly where these
    accumulate over time.
  * **expired**: ``expires_at`` is set AND is in the past. Set during
    classification (e.g. ``curator status set <file> junk
    --expires-in-days 30``). These are candidates for deletion or
    forced cold-archive — the policy decision is the caller's.
  * **archive**: ``status='vital'`` AND ``last_scanned_at`` older than
    ``min_age_days`` (default 365). Long-stable vital files (signed
    contracts, board minutes, finished research datasets) belong in
    an immutable archive store, not on a working drive.

This service is **detect-only by default**. The ``--apply --target
<dst>`` CLI path delegates the actual moves to MigrationService so we
inherit its safety primitives (per-file hash verification, plan-then-
apply, resume-on-interrupt, audit-log emission) without re-implementing
them here.

Design constraints:

  * **No new schema.** Uses existing FileRepository status methods.
  * **No new audit actions.** Selection is logged as ``tier.suggest``;
    the migration that follows --apply emits its own audit events.
  * **No new background jobs.** The user runs ``curator tier`` when
    they want to. A future v1.8 daemon mode could schedule it.
  * **Single-purpose command, not a rule engine.** Three named recipes
    cover ~95% of the use cases. If users need custom criteria,
    generalize later — the FEATURE_TODO explicitly warns against
    premature rule-engine-ification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from curator.models.file import FileEntity
    from curator.storage.repositories.file_repo import FileRepository


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


class TierRecipe(str, Enum):
    """Named tier-transition recipes shipping in v1.7.8."""

    COLD = "cold"          # provisional + stale → cold storage
    EXPIRED = "expired"    # expires_at < now → drop or force-archive
    ARCHIVE = "archive"    # vital + very stale → immutable archive

    @classmethod
    def from_string(cls, s: str) -> "TierRecipe":
        s = s.lower().strip()
        for r in cls:
            if r.value == s:
                return r
        raise ValueError(
            f"Unknown tier recipe: {s!r}. "
            f"Valid: {', '.join(r.value for r in cls)}"
        )


@dataclass
class TierCriteria:
    """Filter parameters for a tier scan."""

    recipe: TierRecipe
    min_age_days: int = 90          # for COLD / ARCHIVE
    source_id: str | None = None    # restrict to one source
    root_prefix: str | None = None  # restrict to a path prefix
    now: datetime | None = None     # injectable for testability

    def cutoff(self) -> datetime:
        """The 'older than this' datetime cutoff for stale-based recipes."""
        now = self.now or datetime.utcnow()
        return now - timedelta(days=self.min_age_days)


@dataclass
class TierCandidate:
    """One file proposed for tier migration."""

    file: "FileEntity"
    reason: str  # human-readable explanation (e.g. "provisional, last scanned 134d ago")


@dataclass
class TierReport:
    """Result of a tier scan."""

    recipe: TierRecipe
    criteria: TierCriteria
    candidates: list[TierCandidate] = field(default_factory=list)
    scanned_count: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    @property
    def total_size(self) -> int:
        return sum(c.file.size for c in self.candidates if c.file.size)

    @property
    def duration_seconds(self) -> float:
        if not self.completed_at or not self.started_at:
            return 0.0
        return (self.completed_at - self.started_at).total_seconds()

    def by_source(self) -> dict[str, int]:
        """Count of candidates grouped by source_id."""
        out: dict[str, int] = {}
        for c in self.candidates:
            out[c.file.source_id] = out.get(c.file.source_id, 0) + 1
        return out


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class TierService:
    """Identify files matching tier-transition criteria.

    Stateless; reuses FileRepository for all queries. The selection
    logic is intentionally simple SQL — no streaming, no batching —
    because the result set is naturally small (the whole point of
    cold-storage candidates is that they're a manageable subset).
    For very large indexes (>10M files) the queries can grow slow;
    in that case, narrow scope with ``source_id`` or ``root_prefix``.
    """

    def __init__(self, file_repo: "FileRepository") -> None:
        self._file_repo = file_repo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self, criteria: TierCriteria) -> TierReport:
        """Find files matching ``criteria``. Returns a TierReport.

        The report's ``candidates`` list is in stable order (by
        ``last_scanned_at`` ascending so the oldest-by-staleness
        candidates appear first — the user is most likely to want
        to migrate those).
        """
        report = TierReport(
            recipe=criteria.recipe,
            criteria=criteria,
            started_at=datetime.utcnow(),
        )

        if criteria.recipe == TierRecipe.COLD:
            candidates = self._scan_cold(criteria)
        elif criteria.recipe == TierRecipe.EXPIRED:
            candidates = self._scan_expired(criteria)
        elif criteria.recipe == TierRecipe.ARCHIVE:
            candidates = self._scan_archive(criteria)
        else:  # pragma: no cover -- enum exhaustive
            candidates = []

        # Stable sort: oldest staleness first (most cold-storage-worthy)
        candidates.sort(key=lambda c: c.file.last_scanned_at or datetime.min)

        report.candidates = candidates
        report.scanned_count = len(candidates)
        report.completed_at = datetime.utcnow()
        return report

    # ------------------------------------------------------------------
    # Per-recipe scanners
    # ------------------------------------------------------------------

    def _scan_cold(self, criteria: TierCriteria) -> list[TierCandidate]:
        """COLD recipe: status='provisional' + last_scanned_at older than cutoff."""
        cutoff = criteria.cutoff()
        # Pull all provisional files (typically a manageable set)
        provisional = self._file_repo.query_by_status(
            status="provisional",
            source_id=criteria.source_id,
        )
        out: list[TierCandidate] = []
        for f in provisional:
            if not self._matches_root_prefix(f, criteria.root_prefix):
                continue
            last = f.last_scanned_at
            if last is None or last >= cutoff:
                continue
            days = (criteria.now or datetime.utcnow()) - last
            out.append(TierCandidate(
                file=f,
                reason=(
                    f"provisional, last scanned "
                    f"{int(days.total_seconds() / 86400)}d ago"
                ),
            ))
        return out

    def _scan_expired(self, criteria: TierCriteria) -> list[TierCandidate]:
        """EXPIRED recipe: expires_at IS NOT NULL AND expires_at < now."""
        now = criteria.now or datetime.utcnow()
        expired_files = self._file_repo.find_expiring_before(
            when=now,
            source_id=criteria.source_id,
        )
        out: list[TierCandidate] = []
        for f in expired_files:
            if not self._matches_root_prefix(f, criteria.root_prefix):
                continue
            exp = f.expires_at
            assert exp is not None  # repo guarantees this for find_expiring_before
            days_overdue = (now - exp).total_seconds() / 86400
            out.append(TierCandidate(
                file=f,
                reason=(
                    f"expired {int(days_overdue)}d ago "
                    f"(status={f.status}, expires_at={exp.date()})"
                ),
            ))
        return out

    def _scan_archive(self, criteria: TierCriteria) -> list[TierCandidate]:
        """ARCHIVE recipe: status='vital' + last_scanned_at older than cutoff."""
        cutoff = criteria.cutoff()
        vital = self._file_repo.query_by_status(
            status="vital",
            source_id=criteria.source_id,
        )
        out: list[TierCandidate] = []
        for f in vital:
            if not self._matches_root_prefix(f, criteria.root_prefix):
                continue
            last = f.last_scanned_at
            if last is None or last >= cutoff:
                continue
            days = (criteria.now or datetime.utcnow()) - last
            out.append(TierCandidate(
                file=f,
                reason=(
                    f"vital, last scanned "
                    f"{int(days.total_seconds() / 86400)}d ago "
                    "(archive candidate)"
                ),
            ))
        return out

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _matches_root_prefix(f: "FileEntity", prefix: str | None) -> bool:
        """Check that the file's source_path begins with ``prefix`` (case-insensitive)."""
        if prefix is None:
            return True
        return f.source_path.lower().startswith(prefix.lower())
