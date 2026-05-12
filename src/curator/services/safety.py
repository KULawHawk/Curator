"""Safety primitives for the smart drive organizer (Phase Gamma F1).

This is the foundation that gates every auto-organize action: before
Curator moves any file, it asks SafetyService whether it's safe to
touch. The service aggregates several signals and emits a structured
:class:`SafetyReport` with a level (SAFE / CAUTION / REFUSE) and a
list of concerns.

Signals (see DESIGN.md \u00a719, ROADMAP.md F1):

  * **Open handle** \u2014 a running process has the file open. Detected
    via ``psutil.Process.open_files()``. If psutil isn't installed,
    this signal is silently skipped (the service still runs; it just
    can't detect this concern).
  * **Project file** \u2014 the file lives inside a folder containing a
    project marker (``.git``, ``package.json``, ``pyproject.toml``,
    ``Cargo.toml``, etc.). The whole folder is treated as an atomic
    unit; we don't move files out of it. Pure Python, no deps.
  * **Application data** \u2014 the file is under a known app-data path
    (``%APPDATA%``, ``~/Library/Application Support``, ``~/.config``,
    ``~/.cache``, Steam library paths, etc.). Static list, user-
    extensible via Config.
  * **OS-managed** \u2014 the file is under an OS-managed path
    (``C:\\Windows``, ``/System``, ``/usr``, ``/etc``, etc.). Hard
    refusal \u2014 even with ``--apply`` the organizer will refuse.
  * **Symbolic link** \u2014 the file is a symlink, junction, or
    reparse point. Following these blindly can break OS infrastructure;
    surface as a caution and let higher layers decide.

Three levels:

  * **SAFE** \u2014 no concerns; auto-organize is OK.
  * **CAUTION** \u2014 at least one concern; user should review before
    organizing. The organize CLI will skip these unless ``--force`` is
    passed.
  * **REFUSE** \u2014 hard refusal. OS-managed paths in particular get
    this; no flag overrides it. (User can still operate on the file
    manually \u2014 SafetyService is advisory, not enforced at the OS
    level.)

The service is intentionally cheap to call: project-marker lookup
walks at most a few parent dirs; app-data / OS path matching is a
prefix check against pre-built lists; only open-handle detection is
expensive (it iterates over running processes), and that's optional.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable

from loguru import logger


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class SafetyLevel(str, Enum):
    """Aggregate safety verdict for a path."""

    SAFE = "safe"
    CAUTION = "caution"
    REFUSE = "refuse"


class SafetyConcern(str, Enum):
    """Categorical reason a path isn't fully safe."""

    OPEN_HANDLE = "open_handle"
    PROJECT_FILE = "project_file"
    APP_DATA = "app_data"
    OS_MANAGED = "os_managed"
    SYMLINK = "symlink"


# Map concerns to their default severity level. The aggregate level for
# a SafetyReport is the most-severe of any present concern.
_CONCERN_LEVEL: dict[SafetyConcern, SafetyLevel] = {
    SafetyConcern.OS_MANAGED: SafetyLevel.REFUSE,
    SafetyConcern.OPEN_HANDLE: SafetyLevel.CAUTION,
    SafetyConcern.PROJECT_FILE: SafetyLevel.CAUTION,
    SafetyConcern.APP_DATA: SafetyLevel.CAUTION,
    SafetyConcern.SYMLINK: SafetyLevel.CAUTION,
}

_LEVEL_RANK = {SafetyLevel.SAFE: 0, SafetyLevel.CAUTION: 1, SafetyLevel.REFUSE: 2}


@dataclass
class SafetyReport:
    """Structured safety assessment for a single path.

    Attributes:
        path: the absolute path that was checked.
        level: the most-severe level across all concerns.
        concerns: list of (concern, human-readable detail) pairs.
        holders: process names holding open handles to the file.
        project_root: detected project root (if path is inside one).
    """

    path: str
    level: SafetyLevel = SafetyLevel.SAFE
    concerns: list[tuple[SafetyConcern, str]] = field(default_factory=list)
    holders: list[str] = field(default_factory=list)
    project_root: str | None = None

    def add_concern(self, concern: SafetyConcern, detail: str) -> None:
        """Record a concern and lift the aggregate level if needed."""
        self.concerns.append((concern, detail))
        new_level = _CONCERN_LEVEL[concern]
        if _LEVEL_RANK[new_level] > _LEVEL_RANK[self.level]:
            self.level = new_level

    @property
    def is_safe(self) -> bool:
        return self.level == SafetyLevel.SAFE

    @property
    def is_refused(self) -> bool:
        return self.level == SafetyLevel.REFUSE


# ---------------------------------------------------------------------------
# Project-root markers (pure Python, no deps)
# ---------------------------------------------------------------------------

PROJECT_MARKERS: tuple[str, ...] = (
    # Version control
    ".git",
    ".hg",
    ".svn",
    # Python
    "pyproject.toml",
    "setup.py",
    "Pipfile",
    "poetry.lock",
    # JavaScript / TypeScript
    "package.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "tsconfig.json",
    # Rust
    "Cargo.toml",
    "Cargo.lock",
    # Go
    "go.mod",
    "go.sum",
    # Ruby
    "Gemfile",
    "Gemfile.lock",
    # Java / Kotlin
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    # .NET
    # (caught by *.sln / *.csproj patterns below)
    # General
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    ".editorconfig",
)
"""Filenames that, when present in a directory, mark it as a project root."""


PROJECT_MARKER_PATTERNS: tuple[str, ...] = (
    "*.sln",
    "*.csproj",
    "*.xcodeproj",
    "*.xcworkspace",
)
"""Glob patterns for project-root markers (e.g. Visual Studio solutions)."""


def find_project_root(path: Path, max_depth: int = 12) -> Path | None:
    """Walk up from ``path`` looking for a project-marker file.

    Returns the directory containing the marker, or None if none found
    within ``max_depth`` levels. Doesn't follow symlinks past the start
    point.
    """
    try:
        current = path.resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    if current.is_file():
        current = current.parent

    for _ in range(max_depth):
        if not current.exists():
            return None
        try:
            for marker in PROJECT_MARKERS:
                if (current / marker).exists():
                    return current
            for pattern in PROJECT_MARKER_PATTERNS:
                if any(current.glob(pattern)):
                    return current
        except OSError:
            return None
        if current.parent == current:
            return None  # reached filesystem root
        current = current.parent

    return None


# ---------------------------------------------------------------------------
# App-data + OS-managed path registries
# ---------------------------------------------------------------------------


def _windows_app_data_paths() -> list[Path]:
    """Windows app-data paths (env-var-driven; works on any Windows machine)."""
    out: list[Path] = []
    import os

    for env_var in ("APPDATA", "LOCALAPPDATA", "PROGRAMDATA"):
        v = os.environ.get(env_var)
        if v:
            out.append(Path(v))
    # Steam library default location (and ProgramFiles + ProgramFiles(x86))
    for env_var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        v = os.environ.get(env_var)
        if v:
            out.append(Path(v))
    return out


def _windows_os_managed_paths() -> list[Path]:
    """Windows OS-managed paths \u2014 hard refusal for any organize action."""
    import os

    out: list[Path] = []
    sys_root = os.environ.get("SystemRoot") or r"C:\Windows"
    out.append(Path(sys_root))
    # Path("C:") / "Boot" produces "C:Boot" (drive-relative current dir),
    # not "C:\Boot". We need the explicit root separator.
    sys_drive_str = (os.environ.get("SystemDrive") or "C:") + os.sep
    sys_drive = Path(sys_drive_str)
    for sub in ("Boot", "Recovery", "$Recycle.Bin", "System Volume Information"):
        out.append(sys_drive / sub)
    return out


def _macos_app_data_paths() -> list[Path]:  # pragma: no cover — set aside v1.7.84 (see docs/PLATFORM_SCOPE.md)
    home = Path.home()
    return [
        home / "Library" / "Application Support",
        home / "Library" / "Caches",
        home / "Library" / "Containers",
        home / "Library" / "Group Containers",
        home / "Library" / "Preferences",
        home / "Library" / "Logs",
        Path("/Library"),
    ]


def _macos_os_managed_paths() -> list[Path]:  # pragma: no cover — set aside v1.7.84 (see docs/PLATFORM_SCOPE.md)
    return [
        Path("/System"),
        # v1.7.63: replaced Path("/private") with specific subdirs.
        # The bare "/private" was over-broad: macOS uses /private/var/folders
        # as the user TMPDIR (where pytest's tmp_path lives), and /private/tmp
        # is the symlink target of /tmp. Both are user-writable and must NOT
        # be OS-managed. List only system-managed /private subdirs explicitly.
        Path("/private/etc"),
        Path("/private/var/db"),
        Path("/private/var/log"),
        Path("/private/var/run"),
        Path("/private/var/spool"),
        Path("/Volumes"),
        Path("/usr"),
        Path("/sbin"),
        Path("/bin"),
        Path("/dev"),
    ]


def _linux_app_data_paths() -> list[Path]:  # pragma: no cover — set aside v1.7.84 (see docs/PLATFORM_SCOPE.md)
    import os

    home = Path.home()
    out = [
        home / ".config",
        home / ".cache",
        home / ".local" / "share",
        home / ".local" / "state",
    ]
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        out.append(Path(xdg_config))
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        out.append(Path(xdg_cache))
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        out.append(Path(xdg_data))
    return out


def _linux_os_managed_paths() -> list[Path]:  # pragma: no cover — set aside v1.7.84 (see docs/PLATFORM_SCOPE.md)
    return [
        Path("/boot"),
        Path("/sys"),
        Path("/proc"),
        Path("/dev"),
        Path("/etc"),
        Path("/usr"),
        # v1.7.69: replaced Path("/var") with specific subdirs.
        # The bare "/var" was over-broad: while /var/log, /var/lib, /var/cache,
        # /var/spool, /var/run, /var/mail, /var/db are system-managed,
        # /var/tmp is officially designated as a user-writable persistent
        # temp area (FHS 3.0 §5.15), and some CI/sysadmin configurations set
        # TMPDIR=/var/tmp. Tests writing there would be misclassified as
        # OS_MANAGED (REFUSE) instead of SAFE/APP_DATA. /var/local is also
        # intended for site-local additions and is typically user-writable.
        # List only the system-managed /var subdirs explicitly. This mirrors
        # the v1.7.63 macOS /private surgical fix.
        Path("/var/log"),
        Path("/var/lib"),
        Path("/var/cache"),
        Path("/var/spool"),
        Path("/var/run"),
        Path("/var/mail"),
        Path("/var/db"),
        Path("/var/empty"),
        Path("/sbin"),
        Path("/bin"),
        Path("/lib"),
        Path("/lib64"),
    ]


def get_default_app_data_paths() -> list[Path]:
    """Platform-aware default app-data paths."""
    if sys.platform == "win32":
        return _windows_app_data_paths()
    if sys.platform == "darwin":  # pragma: no cover — set aside v1.7.84 (see docs/PLATFORM_SCOPE.md)
        return _macos_app_data_paths()
    return _linux_app_data_paths()  # pragma: no cover — set aside v1.7.84 (see docs/PLATFORM_SCOPE.md)


def get_default_os_managed_paths() -> list[Path]:
    """Platform-aware default OS-managed paths (hard refusal)."""
    if sys.platform == "win32":
        return _windows_os_managed_paths()
    if sys.platform == "darwin":  # pragma: no cover — set aside v1.7.84 (see docs/PLATFORM_SCOPE.md)
        return _macos_os_managed_paths()
    return _linux_os_managed_paths()  # pragma: no cover — set aside v1.7.84 (see docs/PLATFORM_SCOPE.md)


def _is_under(path: Path, root: Path) -> bool:
    """True if ``path`` is at or below ``root`` (resolved, case-insensitive on Windows)."""
    try:
        path_r = path.resolve(strict=False)
        root_r = root.resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    if sys.platform == "win32":
        return str(path_r).lower().startswith(str(root_r).lower())
    return str(path_r).startswith(str(root_r))  # pragma: no cover — set aside v1.7.84 (see docs/PLATFORM_SCOPE.md)


# ---------------------------------------------------------------------------
# Open-handle detection (psutil-based, optional)
# ---------------------------------------------------------------------------


def _psutil_available() -> bool:
    try:
        import psutil  # noqa: F401
    except ImportError:
        return False
    return True


def find_handle_holders(path: Path) -> list[str]:
    """Return process names holding open handles to ``path``.

    Returns an empty list if:
      * psutil isn't installed (silent skip \u2014 the caller decides what to do)
      * the path doesn't exist
      * no process holds it
      * we lack permissions to enumerate (logged as debug)

    This is the slow signal in SafetyService \u2014 typically O(N) over
    running processes, with each process's open_files() being a syscall.
    Caching results with a TTL is sensible at higher layers.
    """
    if not _psutil_available():
        return []
    if not path.exists():
        return []

    import psutil

    try:
        target = path.resolve(strict=False)
    except (OSError, RuntimeError):
        return []

    holders: list[str] = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            for f in proc.open_files():
                try:
                    if Path(f.path).resolve(strict=False) == target:
                        name = proc.info.get("name") or f"pid {proc.info.get('pid')}"
                        if name not in holders:
                            holders.append(name)
                        break
                except (OSError, RuntimeError):
                    continue
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except Exception as e:  # pragma: no cover - very defensive
            logger.debug(
                "find_handle_holders: process iteration error pid={pid}: {e}",
                pid=proc.info.get("pid"), e=e,
            )
            continue
    return holders


# ---------------------------------------------------------------------------
# SafetyService
# ---------------------------------------------------------------------------


class SafetyService:
    """Aggregate safety primitives behind a single API.

    Construction is cheap (just stores the path lists). Each
    :meth:`check_path` call performs the four cheap checks unconditionally,
    and the slow open-handle check only when ``check_handles=True``.

    Args:
        app_data_paths: override for app-data path list. Default: platform.
        os_managed_paths: override for OS-managed path list. Default: platform.
        extra_app_data: additional paths the user has marked as off-limits.
        extra_os_managed: additional hard-refusal paths.
    """

    def __init__(
        self,
        app_data_paths: Iterable[Path] | None = None,
        os_managed_paths: Iterable[Path] | None = None,
        extra_app_data: Iterable[Path] = (),
        extra_os_managed: Iterable[Path] = (),
    ) -> None:
        self.app_data = (
            list(app_data_paths)
            if app_data_paths is not None
            else get_default_app_data_paths()
        )
        self.app_data.extend(Path(p) for p in extra_app_data)
        self.os_managed = (
            list(os_managed_paths)
            if os_managed_paths is not None
            else get_default_os_managed_paths()
        )
        self.os_managed.extend(Path(p) for p in extra_os_managed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_path(
        self,
        path: str | Path,
        *,
        check_handles: bool = False,
    ) -> SafetyReport:
        """Build a :class:`SafetyReport` for a single path.

        Args:
            path: file or directory to check.
            check_handles: if True, run the (slow) open-handle scan.
                           Default False because it's O(processes) per call.

        Returns:
            A :class:`SafetyReport` with all detected concerns aggregated.
        """
        p = Path(path)
        report = SafetyReport(path=str(p))

        # 1. OS-managed (REFUSE)
        for root in self.os_managed:
            if _is_under(p, root):
                report.add_concern(
                    SafetyConcern.OS_MANAGED,
                    f"under OS-managed path {root}",
                )
                # OS-managed is REFUSE \u2014 we don't even bother checking
                # the lighter signals; nothing is going to flip the verdict.
                return report

        # 2. App data (CAUTION)
        for root in self.app_data:
            if _is_under(p, root):
                report.add_concern(
                    SafetyConcern.APP_DATA,
                    f"under application-data path {root}",
                )
                break  # one is enough; don't flood concerns with multiple matches

        # 3. Symlink (CAUTION) \u2014 cheap, no I/O beyond a single lstat.
        try:
            if p.is_symlink():
                report.add_concern(
                    SafetyConcern.SYMLINK,
                    "path is a symbolic link / junction / reparse point",
                )
        except OSError:
            # A path that fails lstat is suspicious enough that we
            # surface it but won't escalate further.
            pass

        # 4. Project file (CAUTION)
        proj = find_project_root(p)
        if proj is not None:
            report.project_root = str(proj)
            report.add_concern(
                SafetyConcern.PROJECT_FILE,
                f"inside project rooted at {proj}",
            )

        # 5. Open handle (CAUTION) \u2014 only if explicitly requested.
        if check_handles:
            holders = find_handle_holders(p)
            if holders:
                report.holders = holders
                pretty = ", ".join(holders[:5])
                if len(holders) > 5:
                    pretty += f" (+{len(holders) - 5} more)"
                report.add_concern(
                    SafetyConcern.OPEN_HANDLE,
                    f"open by: {pretty}",
                )

        return report

    def check_paths(
        self,
        paths: Iterable[str | Path],
        *,
        check_handles: bool = False,
    ) -> list[SafetyReport]:
        """Run :meth:`check_path` over a batch.

        Same per-path semantics as :meth:`check_path`; provided as a
        convenience and as a future-extension point for batch
        optimizations (e.g. enumerating processes once and matching all
        paths against a single open-files snapshot).
        """
        # Future optimization: if check_handles=True, build one
        # path \u2192 holders index by walking processes once. For now the
        # loop is fine and keeps semantics identical.
        return [self.check_path(p, check_handles=check_handles) for p in paths]

    # ------------------------------------------------------------------
    # Helpers exposed for tests / introspection
    # ------------------------------------------------------------------

    def is_app_data(self, path: str | Path) -> bool:
        return any(_is_under(Path(path), root) for root in self.app_data)

    def is_os_managed(self, path: str | Path) -> bool:
        return any(_is_under(Path(path), root) for root in self.os_managed)


__all__ = [
    "SafetyService",
    "SafetyReport",
    "SafetyLevel",
    "SafetyConcern",
    "PROJECT_MARKERS",
    "PROJECT_MARKER_PATTERNS",
    "find_project_root",
    "find_handle_holders",
    "get_default_app_data_paths",
    "get_default_os_managed_paths",
]
