# Curator — Flat Filename List

**Every single file across the Curator ecosystem, no descriptions, just filenames.**
Generated 2026-05-10 from recursive walk of `Curator/`, `curatorplug-atrium-safety/`, `curatorplug-atrium-citation/` (excluding `.venv`, `__pycache__`, `.git`, `.pytest_cache`, etc.).

**Total: 218 files** across 7 categories.

---

## Production Python source (96 files)

### Top-level + vendored

```
curator/__init__.py
curator/_vendored/__init__.py
curator/_vendored/ppdeep/__init__.py
curator/_vendored/send2trash/__init__.py
curator/_vendored/send2trash/exceptions.py
curator/_vendored/send2trash/mac/__init__.py
curator/_vendored/send2trash/plat_freedesktop.py
curator/_vendored/send2trash/util.py
curator/_vendored/send2trash/win/__init__.py
curator/_vendored/send2trash/win/legacy.py
curator/_vendored/send2trash/win/recycle_bin.py
```

### CLI

```
curator/cli/__init__.py
curator/cli/main.py
curator/cli/mcp_keys.py
curator/cli/runtime.py
```

### Config

```
curator/config/__init__.py
curator/config/defaults.py
```

### GUI

```
curator/gui/__init__.py
curator/gui/dialogs.py
curator/gui/launcher.py
curator/gui/lineage_view.py
curator/gui/main_window.py
curator/gui/migrate_signals.py
curator/gui/models.py
```

### MCP server

```
curator/mcp/__init__.py
curator/mcp/auth.py
curator/mcp/middleware.py
curator/mcp/server.py
curator/mcp/tools.py
```

### Models

```
curator/models/__init__.py
curator/models/audit.py
curator/models/base.py
curator/models/bundle.py
curator/models/file.py
curator/models/jobs.py
curator/models/lineage.py
curator/models/migration.py
curator/models/results.py
curator/models/source.py
curator/models/trash.py
curator/models/types.py
```

### Plugins

```
curator/plugins/__init__.py
curator/plugins/hookspecs.py
curator/plugins/manager.py
curator/plugins/core/__init__.py
curator/plugins/core/audit_writer.py
curator/plugins/core/classify_filetype.py
curator/plugins/core/gdrive_source.py
curator/plugins/core/lineage_filename.py
curator/plugins/core/lineage_fuzzy_dup.py
curator/plugins/core/lineage_hash_dup.py
curator/plugins/core/local_source.py
```

### Services

```
curator/services/__init__.py
curator/services/audit.py
curator/services/bundle.py
curator/services/classification.py
curator/services/cleanup.py
curator/services/code_project.py
curator/services/document.py
curator/services/fuzzy_index.py
curator/services/gdrive_auth.py
curator/services/hash_pipeline.py
curator/services/lineage.py
curator/services/migration.py
curator/services/migration_retry.py
curator/services/music.py
curator/services/musicbrainz.py
curator/services/organize.py
curator/services/photo.py
curator/services/safety.py
curator/services/scan.py
curator/services/trash.py
curator/services/watch.py
```

### Storage

```
curator/storage/__init__.py
curator/storage/connection.py
curator/storage/exceptions.py
curator/storage/migrations.py
curator/storage/queries.py
curator/storage/repositories/__init__.py
curator/storage/repositories/_helpers.py
curator/storage/repositories/audit_repo.py
curator/storage/repositories/bundle_repo.py
curator/storage/repositories/file_repo.py
curator/storage/repositories/hash_cache_repo.py
curator/storage/repositories/job_repo.py
curator/storage/repositories/lineage_repo.py
curator/storage/repositories/migration_job_repo.py
curator/storage/repositories/source_repo.py
curator/storage/repositories/trash_repo.py
```

---

## Tests (84 files)

### Test infrastructure

```
tests/conftest.py
tests/integration/__init__.py
tests/integration/mcp/__init__.py
tests/perf/__init__.py
tests/property/__init__.py
tests/unit/__init__.py
tests/unit/mcp/__init__.py
```

### GUI tests (9)

```
tests/gui/test_gui_audit.py
tests/gui/test_gui_bundle_editor.py
tests/gui/test_gui_inbox.py
tests/gui/test_gui_inspect.py
tests/gui/test_gui_lineage.py
tests/gui/test_gui_migrate.py
tests/gui/test_gui_models.py
tests/gui/test_gui_mutations.py
tests/gui/test_gui_settings.py
```

### Integration tests (25)

```
tests/integration/mcp/test_stdio.py
tests/integration/test_cli_bundles.py
tests/integration/test_cli_cleanup.py
tests/integration/test_cli_cleanup_duplicates.py
tests/integration/test_cli_gdrive_auth.py
tests/integration/test_cli_migrate.py
tests/integration/test_cli_organize.py
tests/integration/test_cli_organize_code.py
tests/integration/test_cli_read_commands.py
tests/integration/test_cli_safety.py
tests/integration/test_cli_scan_group_doctor.py
tests/integration/test_cli_sources.py
tests/integration/test_cli_stage.py
tests/integration/test_cli_trash_restore.py
tests/integration/test_lineage_lsh_equivalence.py
tests/integration/test_organize_document.py
tests/integration/test_organize_flow.py
tests/integration/test_organize_mb_enrichment.py
tests/integration/test_organize_music.py
tests/integration/test_organize_photo.py
tests/integration/test_recycle_bin.py
tests/integration/test_scan_flow.py
tests/integration/test_scan_paths.py
tests/integration/test_trash_restore.py
tests/integration/test_watch_smoke.py
```

### Unit tests (37)

```
tests/unit/mcp/test_tools.py
tests/unit/test_audit_writer.py
tests/unit/test_cleanup.py
tests/unit/test_cleanup_duplicates.py
tests/unit/test_cleanup_fuzzy_duplicates.py
tests/unit/test_cleanup_index_sync.py
tests/unit/test_code_project.py
tests/unit/test_curator_source_rename.py
tests/unit/test_document.py
tests/unit/test_fuzzy_index.py
tests/unit/test_gdrive_auth.py
tests/unit/test_gdrive_source.py
tests/unit/test_gdrive_source_v151_config_resolution.py
tests/unit/test_lineage_detectors.py
tests/unit/test_mcp_auth.py
tests/unit/test_mcp_http_auth.py
tests/unit/test_mcp_keys_cli.py
tests/unit/test_migration.py
tests/unit/test_migration_cross_source.py
tests/unit/test_migration_phase2.py
tests/unit/test_migration_phase3_conflict.py
tests/unit/test_migration_phase3_retry.py
tests/unit/test_migration_phase4_cross_source_conflict.py
tests/unit/test_migration_v141_sentinel_defaults.py
tests/unit/test_models.py
tests/unit/test_music.py
tests/unit/test_music_enrichment.py
tests/unit/test_music_mb_enrichment.py
tests/unit/test_organize.py
tests/unit/test_organize_apply.py
tests/unit/test_organize_index_sync.py
tests/unit/test_organize_stage.py
tests/unit/test_photo.py
tests/unit/test_plugin_manager.py
tests/unit/test_safety.py
tests/unit/test_send2trash_cross_platform.py
tests/unit/test_source_write_hook.py
tests/unit/test_storage.py
tests/unit/test_watch.py
```

### Property + perf (4)

```
tests/perf/test_lineage_throughput.py
tests/property/test_lineage_normalization.py
```

---

## Scripts (13 files)

```
scripts/setup_gdrive_source.py
scripts/workflows/_common.ps1
scripts/workflows/01_initial_scan.ps1
scripts/workflows/01_initial_scan.bat
scripts/workflows/02_find_duplicates.ps1
scripts/workflows/02_find_duplicates.bat
scripts/workflows/03_cleanup_junk.ps1
scripts/workflows/03_cleanup_junk.bat
scripts/workflows/04_audit_summary.ps1
scripts/workflows/04_audit_summary.bat
scripts/workflows/05_health_check.ps1
scripts/workflows/05_health_check.bat
scripts/workflows/README.md
```

---

## Installer (3 files)

```
installer/Install-Curator.bat
installer/Install-Curator.ps1
installer/README.md
```

---

## Documentation — docs/ (23 files)

```
docs/AD_ASTRA_CONSTELLATION.md
docs/APEX_INFO_REQUEST.md
docs/APEX_INFO_RESPONSE.md
docs/BUILDING_BLOCKS.md
docs/CONCLAVE_LENSES_v2.md
docs/CONCLAVE_PROPOSAL.md
docs/CURATOR_AUDIT_EVENT_HOOKSPEC_DESIGN.md
docs/CURATOR_INVENTORY.md
docs/CURATOR_MCP_HTTP_AUTH_DESIGN.md
docs/CURATOR_MCP_SERVER_DESIGN.md
docs/CURATORPLUG_ATRIUM_CITATION_DESIGN.md
docs/design/GUI_V2_DESIGN.md
docs/lessons/2026-05-09_install_mcp_session.md
docs/NEXT_SESSION_CHECKLIST.md
docs/PHASE_BETA_LSH.md
docs/PHASE_BETA_WATCH.md
docs/PLUGIN_INIT_HOOKSPEC_DESIGN.md
docs/ROADMAP.md
docs/TRACER_PHASE_2_DESIGN.md
docs/TRACER_PHASE_3_DESIGN.md
docs/TRACER_PHASE_4_DESIGN.md
docs/TRACER_SESSION_B_RUNBOOK.md
docs/USER_GUIDE.md
```

---

## Top-level Curator/ files (8 files)

```
.gitignore
BUILD_TRACKER.md
CHANGELOG.md
DESIGN.md
DESIGN_PHASE_DELTA.md
ECOSYSTEM_DESIGN.md
pyproject.toml
README.md
```

---

## External plugin: curatorplug-atrium-safety (15 files)

```
CHANGELOG.md
DESIGN.md
pyproject.toml
README.md
src/curatorplug/atrium_safety/__init__.py
src/curatorplug/atrium_safety/enforcer.py
src/curatorplug/atrium_safety/exceptions.py
src/curatorplug/atrium_safety/plugin.py
src/curatorplug/atrium_safety/verifier.py
tests/conftest.py
tests/integration/test_curator_runtime.py
tests/unit/test_enforcer.py
tests/unit/test_plugin.py
tests/unit/test_re_read_verification.py
tests/unit/test_verifier.py
```

---

## External plugin: curatorplug-atrium-citation (17 files)

```
CHANGELOG.md
DESIGN.md
DESIGN_V0_2.md
pyproject.toml
README.md
src/curatorplug/atrium_citation/__init__.py
src/curatorplug/atrium_citation/audit.py
src/curatorplug/atrium_citation/cli.py
src/curatorplug/atrium_citation/exceptions.py
src/curatorplug/atrium_citation/plugin.py
src/curatorplug/atrium_citation/sweep.py
tests/__init__.py
tests/conftest.py
tests/unit/test_audit_emission.py
tests/unit/test_cli.py
tests/unit/test_plugin.py
tests/unit/test_sweep.py
```

---

## Total count by category

| Category | Files |
|---|---:|
| Production Python source | 96 |
| Curator tests | 84 |
| Scripts (.ps1 / .bat / .py / .md) | 13 |
| Installer | 3 |
| docs/ markdown | 23 |
| Top-level Curator/ files | 8 |
| curatorplug-atrium-safety | 15 |
| curatorplug-atrium-citation | 17 |
| **TOTAL** | **218 files** |

---

## Just filenames (no paths) — alphabetical

If you want to search/grep for any single filename without worrying about directory structure:

```
__init__.py                              (many copies)
_common.ps1
_helpers.py
.gitignore
01_initial_scan.bat
01_initial_scan.ps1
02_find_duplicates.bat
02_find_duplicates.ps1
03_cleanup_junk.bat
03_cleanup_junk.ps1
04_audit_summary.bat
04_audit_summary.ps1
05_health_check.bat
05_health_check.ps1
AD_ASTRA_CONSTELLATION.md
APEX_INFO_REQUEST.md
APEX_INFO_RESPONSE.md
audit.py                                 (4 copies)
audit_repo.py
audit_writer.py
auth.py
base.py
bundle.py                                (3 copies)
bundle_repo.py
BUILD_TRACKER.md
BUILDING_BLOCKS.md
CHANGELOG.md                             (3 copies — Curator, safety, citation)
classification.py
classify_filetype.py
cleanup.py
cli.py                                   (atrium-citation)
code_project.py
CONCLAVE_LENSES_v2.md
CONCLAVE_PROPOSAL.md
conftest.py                              (3 copies)
connection.py
CURATOR_AUDIT_EVENT_HOOKSPEC_DESIGN.md
CURATOR_INVENTORY.md
CURATOR_MCP_HTTP_AUTH_DESIGN.md
CURATOR_MCP_SERVER_DESIGN.md
CURATORPLUG_ATRIUM_CITATION_DESIGN.md
defaults.py
DESIGN.md                                (3 copies — Curator, safety, citation)
DESIGN_PHASE_DELTA.md
DESIGN_V0_2.md
dialogs.py
document.py
ECOSYSTEM_DESIGN.md
enforcer.py
exceptions.py                            (3 copies)
file.py
file_repo.py
fuzzy_index.py
gdrive_auth.py
gdrive_source.py
GUI_V2_DESIGN.md
hash_cache_repo.py
hash_pipeline.py
hookspecs.py
Install-Curator.bat
Install-Curator.ps1
job_repo.py
jobs.py
launcher.py
legacy.py
lineage.py                               (2 copies — model + service)
lineage_filename.py
lineage_fuzzy_dup.py
lineage_hash_dup.py
lineage_repo.py
lineage_view.py
local_source.py
main.py
main_window.py
manager.py
mcp_keys.py
middleware.py
migrate_signals.py
migration.py                             (2 copies — model + service)
migration_job_repo.py
migration_retry.py
migrations.py
models.py                                (2 copies — gui/ + models/)
music.py
musicbrainz.py
NEXT_SESSION_CHECKLIST.md
organize.py
photo.py
PHASE_BETA_LSH.md
PHASE_BETA_WATCH.md
plat_freedesktop.py
plugin.py                                (2 copies — safety, citation)
PLUGIN_INIT_HOOKSPEC_DESIGN.md
ppdeep/__init__.py
pyproject.toml                           (3 copies — Curator, safety, citation)
queries.py
README.md                                (5 copies — Curator, workflows, installer, safety, citation)
recycle_bin.py
results.py
ROADMAP.md
runtime.py
safety.py
scan.py
server.py
setup_gdrive_source.py
source.py
source_repo.py
sweep.py
test_audit_emission.py
test_audit_writer.py
test_cleanup.py
test_cleanup_duplicates.py
test_cleanup_fuzzy_duplicates.py
test_cleanup_index_sync.py
test_cli.py                              (atrium-citation)
test_cli_bundles.py
test_cli_cleanup.py
test_cli_cleanup_duplicates.py
test_cli_gdrive_auth.py
test_cli_migrate.py
test_cli_organize.py
test_cli_organize_code.py
test_cli_read_commands.py
test_cli_safety.py
test_cli_scan_group_doctor.py
test_cli_sources.py
test_cli_stage.py
test_cli_trash_restore.py
test_code_project.py
test_curator_runtime.py
test_curator_source_rename.py
test_document.py
test_enforcer.py
test_fuzzy_index.py
test_gdrive_auth.py
test_gdrive_source.py
test_gdrive_source_v151_config_resolution.py
test_gui_audit.py
test_gui_bundle_editor.py
test_gui_inbox.py
test_gui_inspect.py
test_gui_lineage.py
test_gui_migrate.py
test_gui_models.py
test_gui_mutations.py
test_gui_settings.py
test_lineage_detectors.py
test_lineage_lsh_equivalence.py
test_lineage_normalization.py
test_lineage_throughput.py
test_mcp_auth.py
test_mcp_http_auth.py
test_mcp_keys_cli.py
test_migration.py
test_migration_cross_source.py
test_migration_phase2.py
test_migration_phase3_conflict.py
test_migration_phase3_retry.py
test_migration_phase4_cross_source_conflict.py
test_migration_v141_sentinel_defaults.py
test_models.py
test_music.py
test_music_enrichment.py
test_music_mb_enrichment.py
test_organize.py
test_organize_apply.py
test_organize_document.py
test_organize_flow.py
test_organize_index_sync.py
test_organize_mb_enrichment.py
test_organize_music.py
test_organize_photo.py
test_organize_stage.py
test_photo.py
test_plugin.py                           (2 copies — safety, citation)
test_plugin_manager.py
test_re_read_verification.py
test_recycle_bin.py
test_safety.py
test_scan_flow.py
test_scan_paths.py
test_send2trash_cross_platform.py
test_source_write_hook.py
test_stdio.py
test_storage.py
test_sweep.py
test_tools.py
test_trash_restore.py
test_verifier.py
test_watch.py
test_watch_smoke.py
tools.py
TRACER_PHASE_2_DESIGN.md
TRACER_PHASE_3_DESIGN.md
TRACER_PHASE_4_DESIGN.md
TRACER_SESSION_B_RUNBOOK.md
trash.py                                 (2 copies — model + service)
trash_repo.py
types.py
USER_GUIDE.md
util.py
verifier.py
watch.py
```
