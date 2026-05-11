"""Google Drive source plugin (Phase Beta gate #5 \u2014 scaffolding).

DESIGN.md \u00a76.4.

Implements the source-plugin contract for ``source_id`` values starting
with ``"gdrive"`` (e.g. ``"gdrive:jake@personal"``).

**Status:** v0.40 implements ``register / enumerate / stat /
read_bytes / write / delete``; ``move`` is Phase Gamma. The auth flow is
documented but not wrapped in an interactive helper \u2014 users authenticate
PyDrive2 separately and Curator reads the resulting credentials file.

Conventions:
    * ``file_id`` is Drive's native file ID (e.g. ``"1A2B3C4D5E"``).
    * ``path`` is the file's display name (NOT a unique identifier \u2014
      Drive permits duplicate names within a folder).
    * Google-native files (Docs, Sheets, etc.) have ``size=0`` because
      the Drive API doesn't expose a real byte count for them.
    * Folders are not yielded as :class:`FileInfo`; we descend into them.

Source config (``SourceConfig.config``):
    credentials_path: str
        Path to PyDrive2's persistent credentials JSON. PyDrive2 auto-
        manages refresh tokens here.
    client_secrets_path: str
        Path to the OAuth client_secrets.json from Google Cloud Console.
    root_folder_id: str = "root"
        Drive folder ID to start enumeration from. ``"root"`` is the
        user's My Drive root.
    include_shared: bool = False
        Include "Shared with me" files in enumeration (Phase Gamma).

Lazy-import: ``PyDrive2`` lives in ``[cloud]`` extras. The plugin's
``register`` hook returns ``None`` if PyDrive2 isn't installed, so
Curator simply doesn't claim ``gdrive:`` source_ids in that case.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Iterator

from loguru import logger

from curator.models.types import (
    FileInfo,
    FileStat,
    SourcePluginInfo,
)
from curator.plugins.hookspecs import hookimpl

if TYPE_CHECKING:
    from curator.storage.repositories.source_repo import SourceRepository


SOURCE_TYPE = "gdrive"
"""Source-type prefix this plugin owns. Source IDs are
``"gdrive"`` or ``"gdrive:<account_alias>"``."""

GOOGLE_NATIVE_PREFIX = "application/vnd.google-apps."
"""Google-native MIME types (Docs, Sheets, Slides, Forms, ...).
We yield these but with size=0 because Drive doesn't store byte counts
for them."""

GOOGLE_FOLDER_MIME = "application/vnd.google-apps.folder"
"""The mimeType of Drive folders. We descend into these instead of
yielding them as files."""


# ---------------------------------------------------------------------------
# Lazy availability check
# ---------------------------------------------------------------------------

def _pydrive2_available() -> bool:
    """True if PyDrive2 can be imported on this Python."""
    try:
        import pydrive2  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------------------
# Drive client construction
# ---------------------------------------------------------------------------

def _build_drive_client(config: dict[str, Any]):
    """Build an authenticated ``pydrive2.drive.GoogleDrive`` from a SourceConfig.

    Raises:
        ImportError: PyDrive2 not installed.
        FileNotFoundError: ``client_secrets_path`` or ``credentials_path``
                           missing on disk.
        RuntimeError: PyDrive2 auth failed (expired token, network, etc.).

    The returned object can call ``.ListFile`` / ``.CreateFile`` etc. The
    plugin's hook methods call this lazily so a misconfigured account
    doesn't crash plugin registration.
    """
    from pydrive2.auth import GoogleAuth
    from pydrive2.drive import GoogleDrive

    client_secrets = config.get("client_secrets_path")
    creds = config.get("credentials_path")
    if not client_secrets or not creds:
        raise RuntimeError(
            "gdrive source config requires both 'client_secrets_path' "
            "and 'credentials_path'. See "
            "https://docs.iterative.ai/PyDrive2/quickstart/ for setup."
        )

    gauth = GoogleAuth()
    gauth.LoadClientConfigFile(str(client_secrets))
    gauth.LoadCredentialsFile(str(creds))
    if gauth.credentials is None:
        raise RuntimeError(
            f"No credentials at {creds!r}. Run an interactive auth flow "
            "to populate it (see PyDrive2's quickstart docs)."
        )
    if gauth.access_token_expired:
        gauth.Refresh()
        gauth.SaveCredentialsFile(str(creds))
    else:
        gauth.Authorize()
    return GoogleDrive(gauth)


# ---------------------------------------------------------------------------
# Helpers: convert Drive API responses -> Curator types
# ---------------------------------------------------------------------------

def _parse_drive_datetime(value: str | None) -> datetime:
    """Parse Drive's ISO8601 timestamp ('2026-01-15T12:34:56.789Z')."""
    if not value:
        return datetime.utcnow()
    # Drive uses 'Z' for UTC; Python's fromisoformat can't handle that
    # directly until 3.11, where it can. Strip the trailing Z to be safe.
    cleaned = value.rstrip("Z")
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return datetime.utcnow()


def _drive_file_to_file_info(drive_file: Any) -> FileInfo:
    """Convert a PyDrive2 GoogleDriveFile metadata dict to FileInfo."""
    md = drive_file  # PyDrive2 file objects behave like dicts
    file_id = md["id"]
    name = md.get("title") or md.get("name") or file_id
    mime = md.get("mimeType", "")
    is_native = mime.startswith(GOOGLE_NATIVE_PREFIX) and mime != GOOGLE_FOLDER_MIME

    # Google-native files don't expose a usable byte count.
    if is_native:
        size = 0
    else:
        try:
            size = int(md.get("fileSize") or md.get("size") or 0)
        except (TypeError, ValueError):
            size = 0

    return FileInfo(
        file_id=file_id,
        path=name,
        size=size,
        mtime=_parse_drive_datetime(md.get("modifiedDate") or md.get("modifiedTime")),
        ctime=_parse_drive_datetime(md.get("createdDate") or md.get("createdTime")),
        is_directory=False,
        extras={
            "mime_type": mime,
            "drive_native": is_native,
            "drive_md5": md.get("md5Checksum"),
            "drive_parents": [p.get("id") for p in (md.get("parents") or [])],
        },
    )


def _drive_file_to_file_stat(drive_file: Any) -> FileStat:
    """Convert a PyDrive2 GoogleDriveFile metadata dict to FileStat."""
    md = drive_file
    mime = md.get("mimeType", "")
    is_native = mime.startswith(GOOGLE_NATIVE_PREFIX) and mime != GOOGLE_FOLDER_MIME
    if is_native:
        size = 0
    else:
        try:
            size = int(md.get("fileSize") or md.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
    return FileStat(
        file_id=md["id"],
        size=size,
        mtime=_parse_drive_datetime(md.get("modifiedDate") or md.get("modifiedTime")),
        ctime=_parse_drive_datetime(md.get("createdDate") or md.get("createdTime")),
        inode=None,
        permissions=None,
        extras={"mime_type": mime, "drive_native": is_native},
    )


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

class Plugin:
    """Google Drive source plugin.

    All hook methods short-circuit (return ``None``) when the
    ``source_id`` doesn't belong to this plugin. The first call that
    actually needs Drive lazily builds a ``GoogleDrive`` client; the
    client is cached per source_id on the plugin instance.

    For testing, callers may inject a pre-built client via
    :meth:`set_drive_client`. This bypasses :func:`_build_drive_client`
    entirely so tests don't need real OAuth credentials.
    """

    SOURCE_TYPE = SOURCE_TYPE

    def __init__(self) -> None:
        self._client_cache: dict[str, Any] = {}
        # v1.5.1: per-source-id config cache. Populated lazily by
        # _resolve_config() from one of three sources (in order):
        #   1. options['source_config'] (passed by scan via enumerate hook)
        #   2. self._source_repo lookup (injected by build_runtime)
        #   3. ~/.curator/gdrive/<alias>/ disk fallback
        # See _resolve_config docstring for the full resolution order.
        self._config_cache: dict[str, dict[str, Any]] = {}
        # v1.5.1: source_repo for SourceConfig lookup. Injected via
        # set_source_repo() during build_runtime; mirrors the pattern
        # used by AuditWriterPlugin.set_audit_repo. None until injected.
        # When None, _resolve_config falls back to disk (which loses
        # any custom root_folder_id from the SourceConfig).
        self._source_repo: "SourceRepository | None" = None

    def set_source_repo(self, source_repo: "SourceRepository") -> None:
        """Inject the source_repo so the plugin can look up SourceConfig
        by source_id at hook-call time (v1.5.1).

        Called by ``build_runtime`` once the source_repo is constructed.
        Mirrors :meth:`AuditWriterPlugin.set_audit_repo`.

        BEFORE injection: hooks like ``curator_source_write`` had no
        way to retrieve the SourceConfig (the hookspec doesn't pass
        it as an arg, and ``options={}`` was hardcoded). The plugin
        would fail with "gdrive source config requires both
        'client_secrets_path' and 'credentials_path'" because
        :func:`_build_drive_client` was called with empty config.

        AFTER injection: hooks call :meth:`_resolve_config` which uses
        ``self._source_repo.get(source_id)`` as a fallback when
        ``options`` doesn't carry a ``source_config``. The plugin can
        now build a real Drive client without depending on the caller
        to pass config.

        See ``CURATOR_GDRIVE_PLUGIN_CONFIG_INJECTION_DESIGN.md`` for the
        v1.5.1 architectural fix this enables.
        """
        self._source_repo = source_repo
        logger.debug(
            "gdrive_source: source_repo injected; SourceConfig lookups "
            "now route through the index instead of disk-only fallback"
        )

    # ------------------------------------------------------------------
    # Test/dev hook for injecting a pre-built or mocked client
    # ------------------------------------------------------------------

    def set_drive_client(self, source_id: str, client: Any) -> None:
        """Inject a Drive client for a specific source_id.

        Used by tests + by callers who manage their own auth flow.
        Production callers normally rely on ``_get_or_build_client``
        to construct one from the SourceConfig.
        """
        self._client_cache[source_id] = client

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    @hookimpl
    def curator_source_register(self) -> SourcePluginInfo | None:
        if not _pydrive2_available():
            # PyDrive2 not installed: don't claim 'gdrive' source IDs;
            # the user will see a clearer error from _ensure_source.
            return None
        return SourcePluginInfo(
            source_type=SOURCE_TYPE,
            display_name="Google Drive",
            requires_auth=True,
            supports_watch=False,  # Drive watch via push notifications is Phase Gamma
            supports_write=True,   # v0.40 - PyDrive2 CreateFile + Upload
            config_schema={
                "type": "object",
                "properties": {
                    "credentials_path": {
                        "type": "string",
                        "description": "Path to PyDrive2 persistent credentials JSON",
                    },
                    "client_secrets_path": {
                        "type": "string",
                        "description": "Path to OAuth client_secrets.json from Google Cloud Console",
                    },
                    "root_folder_id": {
                        "type": "string",
                        "description": "Drive folder ID to start from (default: 'root')",
                    },
                    "include_shared": {
                        "type": "boolean",
                        "description": "Include 'Shared with me' files (Phase Gamma)",
                    },
                },
                "required": ["credentials_path", "client_secrets_path"],
            },
        )

    # ------------------------------------------------------------------
    # Enumeration
    # ------------------------------------------------------------------

    @hookimpl
    def curator_source_enumerate(
        self,
        source_id: str,
        root: str,
        options: dict[str, Any],
    ) -> Iterator[FileInfo] | None:
        if not self._owns(source_id):
            return None
        client = self._get_or_build_client(source_id, options)
        if client is None:
            return None
        # ``root`` for Drive is a folder ID; if empty, default to "root".
        start_folder = root or options.get("root_folder_id") or "root"
        return self._iter_folder(client, start_folder)

    def _iter_folder(self, drive: Any, folder_id: str) -> Iterator[FileInfo]:
        """BFS over Drive folder children, yielding non-folder files.

        Tracks visited folder IDs so multi-parent folders don't loop.
        """
        visited: set[str] = set()
        queue: list[str] = [folder_id]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            try:
                # Drive query: children of this folder, not trashed.
                q = f"'{current}' in parents and trashed = false"
                listing = drive.ListFile({"q": q}).GetList()
            except Exception as e:
                logger.warning(
                    "gdrive list failed for folder {f}: {e}",
                    f=current, e=e,
                )
                continue
            for item in listing:
                mime = item.get("mimeType", "")
                if mime == GOOGLE_FOLDER_MIME:
                    queue.append(item["id"])
                    continue
                yield _drive_file_to_file_info(item)

    # ------------------------------------------------------------------
    # Stat
    # ------------------------------------------------------------------

    @hookimpl
    def curator_source_stat(
        self, source_id: str, file_id: str
    ) -> FileStat | None:
        if not self._owns(source_id):
            return None
        client = self._get_or_build_client(source_id, options={})
        if client is None:
            return None
        try:
            f = client.CreateFile({"id": file_id})
            f.FetchMetadata()
            return _drive_file_to_file_stat(f)
        except Exception as e:
            logger.warning("gdrive stat failed for {fid}: {e}", fid=file_id, e=e)
            return None

    # ------------------------------------------------------------------
    # Read bytes
    # ------------------------------------------------------------------

    @hookimpl
    def curator_source_read_bytes(
        self,
        source_id: str,
        file_id: str,
        offset: int,
        length: int,
    ) -> bytes | None:
        if not self._owns(source_id):
            return None
        client = self._get_or_build_client(source_id, options={})
        if client is None:
            return None
        try:
            f = client.CreateFile({"id": file_id})
            content = f.GetContentString().encode("utf-8")
            return content[offset : offset + length]
        except Exception as e:
            logger.warning("gdrive read_bytes failed for {fid}: {e}", fid=file_id, e=e)
            return None

    # ------------------------------------------------------------------
    # Delete (= move to Drive trash; Drive's native trash works for us)
    # ------------------------------------------------------------------

    @hookimpl
    def curator_source_delete(
        self,
        source_id: str,
        file_id: str,
        to_trash: bool,
    ) -> bool | None:
        if not self._owns(source_id):
            return None
        client = self._get_or_build_client(source_id, options={})
        if client is None:
            return None
        try:
            f = client.CreateFile({"id": file_id})
            if to_trash:
                f.Trash()
            else:
                f.Delete()
            return True
        except Exception as e:
            logger.error("gdrive delete failed for {fid}: {e}", fid=file_id, e=e)
            return False

    # ------------------------------------------------------------------
    # Move \u2014 Phase Gamma
    # ------------------------------------------------------------------

    @hookimpl
    def curator_source_move(
        self,
        source_id: str,
        file_id: str,
        new_path: str,
    ) -> FileInfo | None:
        if not self._owns(source_id):
            return None
        # Drive "move" is a parent-id swap, not a path operation. Phase
        # Gamma will add a higher-level API; for now refuse cleanly.
        raise NotImplementedError(
            "gdrive move is Phase Gamma: Drive moves operate on parent IDs, "
            "not paths. Use the Drive UI or PyDrive2 directly until then."
        )

    @hookimpl
    def curator_source_rename(
        self,
        source_id: str,
        file_id: str,
        new_name: str,
        *,
        overwrite: bool = False,
    ) -> FileInfo | None:
        """Rename a Drive file (title-only patch) within its parent (v1.4.0+).

        Drive "rename" is a metadata patch on the file's ``title`` field;
        no bytes re-upload required. The parent folder is unchanged. With
        ``overwrite=False`` (default), raises ``FileExistsError`` if a
        sibling with the same title already exists in the parent (Drive
        permits duplicate titles in a folder, so we check explicitly).
        With ``overwrite=True``, the existing sibling is sent to Drive's
        trash before the rename proceeds.

        Used by Tracer Phase 4's cross-source
        ``--on-conflict=overwrite-with-backup`` flow to rename existing
        destination files out of the way before the new cross-source
        write.

        Note on atomicity: Drive's metadata patch is server-atomic
        (single API call), but the existence check + rename pair is
        NOT atomic against concurrent Drive activity. A racy collision
        (extremely rare) results in two files with the same title; the
        next ``curator scan`` will surface them.
        """
        if not self._owns(source_id):
            return None
        client = self._get_or_build_client(source_id, options={})
        if client is None:
            return None
        try:
            f = client.CreateFile({"id": file_id})
            f.FetchMetadata()
        except Exception as e:
            logger.error(
                "gdrive rename: FetchMetadata failed for {fid}: {e}",
                fid=file_id, e=e,
            )
            return None
        # Determine parent for sibling-collision check.
        parents = f.get("parents") or []
        parent_id = parents[0].get("id") if parents else None
        if not overwrite and parent_id:
            try:
                # Drive query: same parent, same title, not trashed.
                # Escape single quotes in the title for the query.
                escaped = new_name.replace("'", r"\'")
                q = (
                    f"'{parent_id}' in parents "
                    f"and title='{escaped}' "
                    f"and trashed=false"
                )
                siblings = client.ListFile({"q": q}).GetList()
            except Exception as e:
                logger.warning(
                    "gdrive rename: sibling query failed for {fid}: {e}",
                    fid=file_id, e=e,
                )
                siblings = []
            # Exclude self from the collision check (file may already
            # have its target name in some race scenarios).
            colliders = [s for s in siblings if s.get("id") != file_id]
            if colliders:
                raise FileExistsError(
                    f"gdrive source rename: a file titled {new_name!r} "
                    f"already exists in parent {parent_id}; pass "
                    f"overwrite=True to replace"
                )
        elif overwrite and parent_id:
            # Send any colliding siblings to Drive trash before renaming.
            try:
                escaped = new_name.replace("'", r"\'")
                q = (
                    f"'{parent_id}' in parents "
                    f"and title='{escaped}' "
                    f"and trashed=false"
                )
                siblings = client.ListFile({"q": q}).GetList()
                for s in siblings:
                    if s.get("id") != file_id:
                        try:
                            s.Trash()
                        except Exception as e:
                            logger.warning(
                                "gdrive rename overwrite: failed to trash collider {sid}: {e}",
                                sid=s.get("id"), e=e,
                            )
            except Exception as e:
                logger.warning(
                    "gdrive rename overwrite: sibling cleanup failed: {e}", e=e,
                )
        # Patch the title.
        f["title"] = new_name
        try:
            f.Upload()
        except Exception as e:
            logger.error(
                "gdrive rename: title patch Upload failed for {fid}: {e}",
                fid=file_id, e=e,
            )
            raise
        return _drive_file_to_file_info(f)

    # ------------------------------------------------------------------
    # Write - v0.40 (Phase beta gate 5)
    # ------------------------------------------------------------------

    @hookimpl
    def curator_source_write(
        self,
        source_id: str,
        parent_id: str,
        name: str,
        data: bytes,
        *,
        mtime: datetime | None = None,
        overwrite: bool = False,
    ) -> FileInfo | None:
        """Create a new file in Drive under parent_id with title name.

        v0.40. Uses PyDrive2's CreateFile + content via BytesIO + Upload.
        Drive doesn't support setting mtime directly via the v2 API used
        by PyDrive2; mtime is recorded in the returned FileInfo but not
        written upstream.

        Args:
            parent_id: Drive folder ID (or 'root' for My Drive root).
            name: file title.
            data: bytes to upload.
            mtime: ignored upstream (Drive sets server-side).
            overwrite: if False, refuses when a file with the same
                title already exists in parent_id. If True, trashes the
                existing copy first (Drive permits duplicate titles, so
                we must check explicitly).
        """
        if not self._owns(source_id):
            return None
        client = self._get_or_build_client(source_id, options={})
        if client is None:
            return None

        # v1.5.1: translate "/" / "\\" / "" parent sentinels to the
        # configured root_folder_id. The migration service builds
        # parent_id from path semantics (Path(dst_path).parent), which
        # yields "/" for top-level destinations -- not a valid Drive
        # folder ID. _resolve_parent_id() catches the sentinels and
        # routes them to the SourceConfig's root_folder_id.
        target_parent = self._resolve_parent_id(source_id, parent_id)

        # Pre-flight existence check (Drive permits duplicate titles
        # in one folder, so we must check explicitly to honor overwrite
        # semantics).
        try:
            q = (
                f"'{target_parent}' in parents and title = '{name}' "
                f"and trashed = false"
            )
            existing = client.ListFile({"q": q}).GetList()
        except Exception as e:
            logger.warning(
                "gdrive write: existence check failed for {n} in {p}: {e}",
                n=name, p=target_parent, e=e,
            )
            existing = []

        if existing and not overwrite:
            raise FileExistsError(
                f"gdrive write: a file titled {name!r} already exists in "
                f"folder {target_parent!r}; pass overwrite=True to replace"
            )
        if existing and overwrite:
            for old in existing:
                try:
                    old_file = client.CreateFile({"id": old["id"]})
                    old_file.Trash()
                except Exception as e:
                    logger.warning(
                        "gdrive write: failed to trash existing {fid}: {e}",
                        fid=old.get("id"), e=e,
                    )

        try:
            new_file = client.CreateFile({
                "title": name,
                "parents": [{"id": target_parent}],
            })
            import io
            new_file.content = io.BytesIO(data)
            new_file.Upload()
            new_file.FetchMetadata()
        except Exception as e:
            raise RuntimeError(
                f"gdrive write: upload of {name!r} to {target_parent!r} failed: {e}"
            ) from e

        return _drive_file_to_file_info(new_file)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _owns(self, source_id: str) -> bool:
        """True iff this plugin owns the given source_id.

        Two checks (in order):
        1. Legacy convention: source_id is ``"gdrive"`` or starts with
           ``"gdrive:"`` (multi-account convention).
        2. Database lookup (v1.6.5+): source_id is registered in the
           sources table with ``source_type='gdrive'``. Only runs if
           :attr:`_source_repo` has been injected (it always is during
           normal CLI / GUI / MCP operation).

        Mirrors the v1.6.4 fix to the local plugin. Closes the v1.6.x
        limitation where ``curator sources add my_drive --type gdrive``
        succeeded but ``curator scan my_drive <folder_id>`` failed with
        ``RuntimeError: No source plugin registered``.

        Test contexts that construct the plugin without going through
        build_runtime see only check #1 active, matching pre-v1.6.5
        behavior.
        """
        if source_id == SOURCE_TYPE or source_id.startswith(f"{SOURCE_TYPE}:"):
            return True
        if self._source_repo is not None:
            try:
                source = self._source_repo.get(source_id)
                if source is not None and source.source_type == SOURCE_TYPE:
                    return True
            except Exception:  # noqa: BLE001 -- defensive boundary
                # Don't let a transient DB issue make scans worse than
                # they would be without the fix. Caller will see a
                # normal error from the underlying hookimpl if we
                # return False here.
                pass
        return False

    def _get_or_build_client(
        self,
        source_id: str,
        options: dict[str, Any],
    ) -> Any:
        """Return a (cached or freshly built) PyDrive2 client.

        Returns None on construction failure (logged); the calling hook
        should propagate by returning None too.

        v1.5.1: uses :meth:`_resolve_config` to find the SourceConfig
        instead of relying solely on ``options.get('source_config')``
        (which was always empty for write/read_bytes/stat/delete/rename
        because those hooks don't carry options through the hookspec).
        """
        client = self._client_cache.get(source_id)
        if client is not None:
            return client
        config = self._resolve_config(source_id, options)
        if not config:
            logger.warning(
                "gdrive: no SourceConfig resolved for {sid}; client build "
                "refused", sid=source_id,
            )
            return None
        try:
            client = _build_drive_client(config)
        except (ImportError, FileNotFoundError, RuntimeError) as e:
            logger.warning(
                "gdrive client build failed for {sid}: {e}",
                sid=source_id, e=e,
            )
            return None
        self._client_cache[source_id] = client
        return client

    def _resolve_config(
        self,
        source_id: str,
        options: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Find this source's config dict, in priority order (v1.5.1).

        1. ``options['source_config']`` -- passed by the scan service
           through the enumerate hookspec. Always preferred when
           present.
        2. ``self._config_cache[source_id]`` -- previously resolved
           and cached.
        3. ``self._source_repo.get(source_id).config`` -- injected
           via :meth:`set_source_repo`; reads from the SQLite
           ``sources`` table. This is the production path for hooks
           like ``curator_source_write`` that don't carry options
           through the hookspec.
        4. ``source_config_for_alias(alias)`` -- conventional disk
           layout under ``~/.curator/gdrive/<alias>/``. Used when no
           SourceConfig is available; loses any custom
           ``root_folder_id``.

        Returns None if no config resolves.
        """
        # 1. Caller-provided options (scan path)
        cfg = options.get("source_config")
        if cfg and cfg.get("client_secrets_path"):
            self._config_cache[source_id] = cfg
            return cfg
        # 2. Cache from prior resolution
        cached = self._config_cache.get(source_id)
        if cached:
            return cached
        # 3. SourceRepository lookup (injected by build_runtime)
        if self._source_repo is not None:
            try:
                src = self._source_repo.get(source_id)
            except Exception as e:
                logger.warning(
                    "gdrive: source_repo lookup failed for {sid}: {e}",
                    sid=source_id, e=e,
                )
                src = None
            if src is not None and src.config and \
                    src.config.get("client_secrets_path"):
                self._config_cache[source_id] = src.config
                return src.config
        # 4. Disk fallback via conventional layout
        if source_id == SOURCE_TYPE:
            alias = "default"
        elif source_id.startswith(f"{SOURCE_TYPE}:"):
            alias = source_id.split(":", 1)[1]
        else:
            return None
        try:
            from curator.services.gdrive_auth import (
                paths_for_alias,
                source_config_for_alias,
            )
            # Verify the alias has the auth files actually present on
            # disk before returning a config that points at them. Without
            # this check, the disk fallback would return a path-only
            # config that fails later in _build_drive_client with a
            # less helpful error. v1.5.1: short-circuit here for a
            # cleaner failure mode.
            paths = paths_for_alias(alias)
            if not paths.client_secrets.is_file():
                return None
            cfg = source_config_for_alias(alias)
        except Exception as e:
            logger.warning(
                "gdrive: disk fallback config load failed for {sid}: {e}",
                sid=source_id, e=e,
            )
            return None
        if cfg and cfg.get("client_secrets_path"):
            self._config_cache[source_id] = cfg
            return cfg
        return None

    def _resolve_parent_id(self, source_id: str, parent_id: str) -> str:
        """Translate a migration-supplied ``parent_id`` to a Drive folder ID (v1.5.1).

        The migration service builds parent_id from path semantics:
        ``Path(dst_path).parent``. For dst_path=``/session_b_test_1.txt``
        this yields ``parent_id="/"`` which is NOT a valid Drive folder
        ID. We map a few well-known "root" sentinels to the configured
        ``root_folder_id`` from this source's SourceConfig (or to
        ``"root"`` as a final fallback for the user's My Drive root).

        Drive folder IDs are alphanumeric strings ~28 chars long; the
        sentinel mapping below catches the common "this means root"
        cases without false positives on real folder IDs.
        """
        ROOT_SENTINELS = ("/", "\\", "", ".")
        if parent_id in ROOT_SENTINELS or parent_id is None:
            cfg = self._config_cache.get(source_id) or {}
            return cfg.get("root_folder_id") or "root"
        return parent_id


__all__ = ["Plugin", "SOURCE_TYPE"]
