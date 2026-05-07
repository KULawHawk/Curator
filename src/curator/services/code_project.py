"""CodeProjectService -- detect VCS-marked code projects and propose layouts.

Phase Gamma F5: organize a sprawling Code/Projects/Programming/...
folder tree by detecting VCS marker dirs (``.git/``, ``.hg/``, ``.svn/``),
inferring the primary language from file extensions, and proposing
destinations of the form ``{language}/{project_name}/{relative_path}``.

Design notes:
  * **Project root = outermost VCS marker.** If a repo has submodules,
    the parent .git/ wins. Descending into nested ``.git/`` would
    fragment monorepos at every submodule boundary.
  * **Language inference is by file count, not size.** A 50 MB binary
    blob shouldn't outvote 200 .py files. Tiebreaker is alphabetic on
    the language name for determinism.
  * **VCS marker dirs themselves are skipped** when counting language
    extensions (the ``.git/objects/`` tree shouldn't influence the
    inferred language).
  * **Build artifacts and dependency dirs are also skipped** for
    language inference (``node_modules``, ``.venv``, ``__pycache__``,
    ``target``, ``build``, ``dist``). They distort the count without
    representing what the project IS.
  * **Per-file destination computation** keeps the project's internal
    layout intact: ``project/src/main.py`` lands at
    ``Python/project/src/main.py``, not flattened into one folder.
  * **Service is read-only.** All filesystem walking happens here, but
    actual moves go through OrganizeService.stage / .apply.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from loguru import logger


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# VCS marker directories. Order matters: when multiple are present in
# the same dir (rare but possible), the first one wins as the canonical type.
VCS_MARKERS: tuple[str, ...] = (".git", ".hg", ".svn", ".bzr", "_darcs")

# Directories we skip during language inference. These distort the file
# count without representing what the project IS.
SKIP_DIR_NAMES: frozenset[str] = frozenset({
    # Dependency / vendor dirs
    "node_modules", "vendor", "bower_components",
    # Python virtualenvs + caches
    ".venv", "venv", "env", ".env", "__pycache__", ".tox", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "site-packages",
    # Build outputs
    "target", "build", "dist", "out", "bin", "obj", "_build",
    # IDE / editor metadata
    ".idea", ".vscode", ".vs", ".eclipse",
    # Coverage / tooling output
    "htmlcov", ".coverage", ".nyc_output",
    # Documentation builds
    "_site", ".docusaurus",
    # Misc cache
    ".cache", ".gradle", ".m2",
}) | frozenset(VCS_MARKERS)

# Extension -> language. Curated to cover the bulk of what a personal
# code library looks like; not exhaustive.
LANGUAGE_BY_EXTENSION: dict[str, str] = {
    # Major languages
    ".py": "Python", ".pyi": "Python", ".pyx": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".java": "Java",
    ".kt": "Kotlin", ".kts": "Kotlin",
    ".scala": "Scala", ".sc": "Scala",
    ".clj": "Clojure", ".cljs": "Clojure", ".cljc": "Clojure",
    ".rb": "Ruby",
    ".go": "Go",
    ".rs": "Rust",
    ".swift": "Swift",
    ".m": "Objective-C", ".mm": "Objective-C",
    ".cs": "C#", ".fs": "F#", ".fsx": "F#",
    ".vb": "VisualBasic",
    ".php": "PHP", ".phtml": "PHP",
    ".pl": "Perl", ".pm": "Perl",
    # C family (kept distinct)
    ".c": "C", ".h": "C",
    ".cpp": "Cpp", ".cc": "Cpp", ".cxx": "Cpp",
    ".hpp": "Cpp", ".hh": "Cpp", ".hxx": "Cpp",
    # Functional / less-common
    ".hs": "Haskell", ".lhs": "Haskell",
    ".ml": "OCaml", ".mli": "OCaml",
    ".elm": "Elm",
    ".ex": "Elixir", ".exs": "Elixir",
    ".erl": "Erlang", ".hrl": "Erlang",
    ".lisp": "Lisp", ".cl": "Lisp",
    ".scm": "Scheme", ".rkt": "Racket",
    ".lua": "Lua",
    ".r": "R",
    ".jl": "Julia",
    ".nim": "Nim",
    ".zig": "Zig",
    ".v": "Vlang",
    ".dart": "Dart",
    ".cr": "Crystal",
    # Shell / scripting
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".fish": "Shell",
    ".ps1": "PowerShell", ".psm1": "PowerShell",
    ".bat": "Batch", ".cmd": "Batch",
    # Web
    ".html": "Web", ".htm": "Web", ".css": "Web", ".scss": "Web",
    ".sass": "Web", ".less": "Web", ".vue": "Web", ".svelte": "Web",
    # Data / config considered "Other" -- not load-bearing for
    # language inference (a project full of .json isn't a JSON project).
    # We INCLUDE markdown as a hint but don't let it dominate.
    ".md": "Markdown", ".rst": "Documentation",
}

# Extensions to ignore entirely during language inference (they say
# nothing about what the project IS).
IGNORED_EXTENSIONS: frozenset[str] = frozenset({
    ".gitignore", ".gitattributes", ".gitmodules",
    ".lock", ".log", ".tmp", ".bak",
    # Lock / dependency files
    ".sum",
    # Binary / data
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".ico",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".mp3", ".mp4", ".mov", ".wav", ".flac",
    ".pyc", ".pyo", ".class", ".o", ".so", ".dll", ".exe",
})


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CodeProject:
    """A discovered code project (VCS-marked directory)."""

    root_path: Path
    """Absolute path to the project root (the dir containing the VCS marker)."""

    vcs_type: str
    """One of :data:`VCS_MARKERS` (without the leading dot), e.g. ``"git"``."""

    project_name: str
    """Basename of root_path -- used as the directory name in proposals."""

    primary_language: str
    """Inferred dominant language, or ``"Unknown"`` if no recognized
    code files were found (or all files were ignored extensions)."""

    file_count: int
    """Total number of files inside the project (excluding skipped dirs)."""

    total_size: int
    """Sum of file sizes in bytes."""

    last_modified: datetime | None
    """Most recent mtime of any file in the project, or None if empty."""

    language_breakdown: dict[str, int] = field(default_factory=dict)
    """Per-language file counts, for diagnostics / display."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class CodeProjectService:
    """Detect code projects and propose canonical destinations.

    Stateless. All methods are side-effect-free except for filesystem
    reads via ``os.walk`` / ``Path.iterdir``.
    """

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def is_project_root(self, path: str | Path) -> str | None:
        """Return the VCS type if ``path`` contains a VCS marker, else None.

        VCS type is the marker without its leading dot, e.g. ``"git"``.
        """
        p = Path(path)
        if not p.is_dir():
            return None
        for marker in VCS_MARKERS:
            try:
                if (p / marker).is_dir():
                    return marker.lstrip(".")
            except OSError:
                continue
        return None

    def find_projects(self, root: str | Path) -> list[CodeProject]:
        """Walk ``root`` and return every discovered project.

        Once a project root is found, the walk **prunes** that subtree
        -- nested .git/ dirs (submodules) are absorbed into the parent
        project, not reported as separate projects. This matches the
        intuition that ``myapp/`` is one project even when it contains
        ``myapp/lib/vendored-thing/.git/``.

        Permissions errors are swallowed silently (logged at debug); the
        walk continues with the remaining tree.
        """
        root_path = Path(root)
        if not root_path.is_dir():
            return []

        projects: list[CodeProject] = []
        # Walk manually so we can prune at project boundaries.
        # Stack is (path, depth) for diagnostics; depth is informational.
        stack: list[Path] = [root_path]
        while stack:
            current = stack.pop()
            try:
                vcs = self.is_project_root(current)
            except OSError:
                continue

            if vcs is not None:
                # Found a project. Analyze + record. DO NOT descend.
                try:
                    project = self.analyze_project(current, vcs_type=vcs)
                    projects.append(project)
                    logger.debug(
                        "find_projects: {p} ({l}, {n} files)",
                        p=current, l=project.primary_language,
                        n=project.file_count,
                    )
                except OSError as e:
                    logger.debug(
                        "find_projects: analyze failed for {p}: {e}",
                        p=current, e=e,
                    )
                continue

            # Not a project -- descend into subdirs.
            try:
                children = list(current.iterdir())
            except OSError:
                continue
            for child in children:
                if not child.is_dir():
                    continue
                # Skip dirs that exist explicitly to be ignored.
                if child.name in SKIP_DIR_NAMES:
                    continue
                stack.append(child)

        # Sort by root_path for deterministic output.
        projects.sort(key=lambda p: str(p.root_path))
        return projects

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def analyze_project(
        self,
        root_path: str | Path,
        *,
        vcs_type: str | None = None,
    ) -> CodeProject:
        """Walk a project tree and compute stats + primary language.

        Args:
            root_path: project root (the dir containing the VCS marker).
            vcs_type: pre-computed VCS type. If None, ``is_project_root``
                is consulted.
        """
        rp = Path(root_path)
        if vcs_type is None:
            vcs_type = self.is_project_root(rp) or "unknown"

        language_counter: Counter = Counter()
        file_count = 0
        total_size = 0
        latest_mtime: float | None = None

        for path in self._iter_project_files(rp):
            file_count += 1
            try:
                stat = path.stat()
            except OSError:
                continue
            total_size += stat.st_size
            if latest_mtime is None or stat.st_mtime > latest_mtime:
                latest_mtime = stat.st_mtime

            ext = path.suffix.lower()
            if ext in IGNORED_EXTENSIONS or not ext:
                continue
            lang = LANGUAGE_BY_EXTENSION.get(ext)
            if lang is None:
                continue
            # Markdown / Documentation count as half so they don't
            # outvote real code in tiebreakers.
            if lang in ("Markdown", "Documentation"):
                language_counter[lang] += 0  # registered but not weighted
            else:
                language_counter[lang] += 1

        primary = self._pick_primary_language(language_counter)
        last_modified = (
            datetime.fromtimestamp(latest_mtime) if latest_mtime is not None
            else None
        )

        return CodeProject(
            root_path=rp,
            vcs_type=vcs_type,
            project_name=rp.name,
            primary_language=primary,
            file_count=file_count,
            total_size=total_size,
            last_modified=last_modified,
            language_breakdown=dict(language_counter),
        )

    def _iter_project_files(self, root: Path) -> Iterable[Path]:
        """Yield every file under ``root``, skipping VCS + build dirs.

        The walk descends into ALL subdirs except those whose basename
        is in :data:`SKIP_DIR_NAMES`. This means ``project/src/`` is
        traversed but ``project/.git/`` and ``project/node_modules/``
        are not.
        """
        stack: list[Path] = [root]
        while stack:
            current = stack.pop()
            try:
                children = list(current.iterdir())
            except OSError:
                continue
            for child in children:
                try:
                    if child.is_dir():
                        if child.name in SKIP_DIR_NAMES:
                            continue
                        stack.append(child)
                    elif child.is_file():
                        yield child
                except OSError:
                    continue

    @staticmethod
    def _pick_primary_language(counter: Counter) -> str:
        """Pick the dominant language from a Counter.

        Tiebreaker: alphabetic on language name for determinism.
        Returns ``"Unknown"`` if the counter is empty or all values are 0.
        """
        if not counter:
            return "Unknown"
        # Sort by (-count, name) so highest count wins, ties go alphabetic.
        ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        top_lang, top_count = ranked[0]
        if top_count == 0:
            return "Unknown"
        return top_lang

    # ------------------------------------------------------------------
    # Destination proposals
    # ------------------------------------------------------------------

    def propose_destination(
        self,
        project: CodeProject,
        *,
        file_path: str | Path,
        target_root: str | Path,
    ) -> Path | None:
        """Compute the destination for a single file inside a project.

        Layout: ``{target_root}/{language}/{project_name}/{rel_subpath}``.

        ``file_path`` MUST be inside ``project.root_path``; otherwise
        returns None (the caller should treat that as "this file isn't
        part of the project").
        """
        fp = Path(file_path)
        try:
            rel = fp.relative_to(project.root_path)
        except ValueError:
            return None
        target = Path(target_root)
        return target / project.primary_language / project.project_name / rel

    def find_project_containing(
        self,
        file_path: str | Path,
        projects: list[CodeProject],
    ) -> CodeProject | None:
        """Return the project (from ``projects``) whose root contains the file.

        If multiple projects could contain the file (impossible in
        practice because find_projects prunes nested), the one with the
        longest matching root_path wins -- the most-specific.
        """
        fp = Path(file_path).resolve(strict=False)
        candidates: list[CodeProject] = []
        for project in projects:
            try:
                # is_relative_to was added in 3.9; we use a try/except
                # for portability across older Pythons (we target 3.11+
                # but this is robust regardless).
                fp.relative_to(project.root_path)
                candidates.append(project)
            except ValueError:
                continue
        if not candidates:
            return None
        # Most specific (longest root path) wins.
        candidates.sort(key=lambda p: len(str(p.root_path)), reverse=True)
        return candidates[0]


__all__ = [
    "VCS_MARKERS",
    "SKIP_DIR_NAMES",
    "LANGUAGE_BY_EXTENSION",
    "IGNORED_EXTENSIONS",
    "CodeProject",
    "CodeProjectService",
]
