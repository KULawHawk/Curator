# Curator Constellation - Authoritative Flat Filename List

**Generated 2026-05-11 directly from `git ls-files` across all three repos.**
This is the **100% accurate** list. No omissions possible - `git ls-files` is the source of truth.

**Total tracked files: 287** (251 Curator + 17 atrium-safety + 19 atrium-citation)

**Versions at generation time:**
- Curator: v1.7.2 (HEAD 0a1f827)
- curatorplug-atrium-safety: v0.3.0
- curatorplug-atrium-citation: v0.2.0

---

## Extension breakdown (all repos combined)

| Extension | Count |
|-----------|-------|
| .py | 199 |
| .md | 43 |
| .json | 12 |
| .ps1 | 7 |
| .png | 7 |
| .bat | 6 |
| .toml | 3 |
| .gitignore | 3 |
| .typed | 3 |
| .txt | 3 |
| .sql | 1 |
| **TOTAL** | **287** |

---

## Curator repo - 251 files

```
.gitignore
BUILD_TRACKER.md
CHANGELOG.md
DESIGN_PHASE_DELTA.md
DESIGN.md
docs/AD_ASTRA_CONSTELLATION.md
docs/ALL_FILES.md
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
docs/FEATURE_TODO.md
docs/lessons/2026-05-09_install_mcp_session.md
docs/NEXT_SESSION_CHECKLIST.md
docs/PHASE_BETA_LSH.md
docs/PHASE_BETA_WATCH.md
docs/PLUGIN_INIT_HOOKSPEC_DESIGN.md
docs/releases/v1.7.0.md
docs/ROADMAP.md
docs/TRACER_PHASE_2_DESIGN.md
docs/TRACER_PHASE_3_DESIGN.md
docs/TRACER_PHASE_4_DESIGN.md
docs/TRACER_SESSION_B_RUNBOOK.md
docs/USER_GUIDE.md
docs/v034_gui_screenshot.png
docs/v036_inspect_dialog.png
docs/v037_audit_log.png
docs/v038_settings.png
docs/v039_inbox.png
docs/v041_lineage_graph.png
docs/v043_bundle_editor.png
docs/v100a1_migration_demo.txt
ECOSYSTEM_DESIGN.md
examples/watch_demo.py
Github/CURATOR_RESEARCH_NOTES.md
Github/PROCUREMENT_INDEX.md
installer/Install-Curator.bat
installer/Install-Curator.ps1
installer/README.md
pyproject.toml
README.md
scripts/setup_gdrive_source.py
scripts/workflows/_common.ps1
scripts/workflows/01_initial_scan.bat
scripts/workflows/01_initial_scan.ps1
scripts/workflows/02_find_duplicates.bat
scripts/workflows/02_find_duplicates.ps1
scripts/workflows/03_cleanup_junk.bat
scripts/workflows/03_cleanup_junk.ps1
scripts/workflows/04_audit_summary.bat
scripts/workflows/04_audit_summary.ps1
scripts/workflows/05_health_check.bat
scripts/workflows/05_health_check.ps1
scripts/workflows/README.md
src/curator/__init__.py
src/curator/_vendored/__init__.py
src/curator/_vendored/LICENSE-PPDEEP.txt
src/curator/_vendored/LICENSE-SEND2TRASH.txt
src/curator/_vendored/ppdeep/__init__.py
src/curator/_vendored/send2trash/__init__.py
src/curator/_vendored/send2trash/exceptions.py
src/curator/_vendored/send2trash/mac/__init__.py
src/curator/_vendored/send2trash/plat_freedesktop.py
src/curator/_vendored/send2trash/util.py
src/curator/_vendored/send2trash/win/__init__.py
src/curator/_vendored/send2trash/win/legacy.py
src/curator/_vendored/send2trash/win/recycle_bin.py
src/curator/cli/__init__.py
src/curator/cli/main.py
src/curator/cli/mcp_keys.py
src/curator/cli/runtime.py
src/curator/config/__init__.py
src/curator/config/defaults.py
src/curator/gui/__init__.py
src/curator/gui/cleanup_signals.py
src/curator/gui/dialogs.py
src/curator/gui/launcher.py
src/curator/gui/lineage_view.py
src/curator/gui/main_window.py
src/curator/gui/migrate_signals.py
src/curator/gui/models.py
src/curator/gui/scan_signals.py
src/curator/mcp/__init__.py
src/curator/mcp/auth.py
src/curator/mcp/middleware.py
src/curator/mcp/server.py
src/curator/mcp/tools.py
src/curator/models/__init__.py
src/curator/models/audit.py
src/curator/models/base.py
src/curator/models/bundle.py
src/curator/models/file.py
src/curator/models/jobs.py
src/curator/models/lineage.py
src/curator/models/migration.py
src/curator/models/results.py
src/curator/models/source.py
src/curator/models/trash.py
src/curator/models/types.py
src/curator/plugins/__init__.py
src/curator/plugins/core/__init__.py
src/curator/plugins/core/audit_writer.py
src/curator/plugins/core/classify_filetype.py
src/curator/plugins/core/gdrive_source.py
src/curator/plugins/core/lineage_filename.py
src/curator/plugins/core/lineage_fuzzy_dup.py
src/curator/plugins/core/lineage_hash_dup.py
src/curator/plugins/core/local_source.py
src/curator/plugins/hookspecs.py
src/curator/plugins/manager.py
src/curator/py.typed
src/curator/services/__init__.py
src/curator/services/audit.py
src/curator/services/bundle.py
src/curator/services/classification.py
src/curator/services/cleanup.py
src/curator/services/code_project.py
src/curator/services/document.py
src/curator/services/forecast.py
src/curator/services/fuzzy_index.py
src/curator/services/gdrive_auth.py
src/curator/services/hash_pipeline.py
src/curator/services/lineage.py
src/curator/services/migration_retry.py
src/curator/services/migration.py
src/curator/services/music.py
src/curator/services/musicbrainz.py
src/curator/services/organize.py
src/curator/services/photo.py
src/curator/services/safety.py
src/curator/services/scan.py
src/curator/services/trash.py
src/curator/services/watch.py
src/curator/storage/__init__.py
src/curator/storage/connection.py
src/curator/storage/exceptions.py
src/curator/storage/migrations.py
src/curator/storage/queries.py
src/curator/storage/repositories/__init__.py
src/curator/storage/repositories/_helpers.py
src/curator/storage/repositories/audit_repo.py
src/curator/storage/repositories/bundle_repo.py
src/curator/storage/repositories/file_repo.py
src/curator/storage/repositories/hash_cache_repo.py
src/curator/storage/repositories/job_repo.py
src/curator/storage/repositories/lineage_repo.py
src/curator/storage/repositories/migration_job_repo.py
src/curator/storage/repositories/source_repo.py
src/curator/storage/repositories/trash_repo.py
src/curator/storage/schema_v1.sql
tests/conftest.py
tests/gui/test_gui_audit.py
tests/gui/test_gui_bundle_editor.py
tests/gui/test_gui_inbox.py
tests/gui/test_gui_inspect.py
tests/gui/test_gui_lineage.py
tests/gui/test_gui_migrate.py
tests/gui/test_gui_models.py
tests/gui/test_gui_mutations.py
tests/gui/test_gui_settings.py
tests/integration/__init__.py
tests/integration/mcp/__init__.py
tests/integration/mcp/test_stdio.py
tests/integration/test_cli_bundles.py
tests/integration/test_cli_cleanup_duplicates.py
tests/integration/test_cli_cleanup.py
tests/integration/test_cli_gdrive_auth.py
tests/integration/test_cli_migrate.py
tests/integration/test_cli_organize_code.py
tests/integration/test_cli_organize.py
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
tests/perf/__init__.py
tests/perf/results/index_build_scaling-20260506T205150.json
tests/perf/results/index_build_scaling-20260506T212007.json
tests/perf/results/index_build_scaling-20260506T223806.json
tests/perf/results/lineage_throughput_n100-20260506T205107.json
tests/perf/results/lineage_throughput_n100-20260506T211942.json
tests/perf/results/lineage_throughput_n100-20260506T223749.json
tests/perf/results/lineage_throughput_n1000-20260506T205110.json
tests/perf/results/lineage_throughput_n1000-20260506T211944.json
tests/perf/results/lineage_throughput_n1000-20260506T223750.json
tests/perf/results/lineage_throughput_n10000-20260506T205135.json
tests/perf/results/lineage_throughput_n10000-20260506T211959.json
tests/perf/results/lineage_throughput_n10000-20260506T223800.json
tests/perf/test_lineage_throughput.py
tests/property/__init__.py
tests/property/test_lineage_normalization.py
tests/unit/__init__.py
tests/unit/mcp/__init__.py
tests/unit/mcp/test_tools.py
tests/unit/test_audit_writer.py
tests/unit/test_cleanup_duplicates.py
tests/unit/test_cleanup_fuzzy_duplicates.py
tests/unit/test_cleanup_index_sync.py
tests/unit/test_cleanup.py
tests/unit/test_code_project.py
tests/unit/test_curator_source_rename.py
tests/unit/test_document.py
tests/unit/test_fuzzy_index.py
tests/unit/test_gdrive_auth.py
tests/unit/test_gdrive_source_v151_config_resolution.py
tests/unit/test_gdrive_source.py
tests/unit/test_lineage_detectors.py
tests/unit/test_mcp_auth.py
tests/unit/test_mcp_http_auth.py
tests/unit/test_mcp_keys_cli.py
tests/unit/test_migration_cross_source.py
tests/unit/test_migration_phase2.py
tests/unit/test_migration_phase3_conflict.py
tests/unit/test_migration_phase3_retry.py
tests/unit/test_migration_phase4_cross_source_conflict.py
tests/unit/test_migration_v141_sentinel_defaults.py
tests/unit/test_migration.py
tests/unit/test_models.py
tests/unit/test_music_enrichment.py
tests/unit/test_music_mb_enrichment.py
tests/unit/test_music.py
tests/unit/test_organize_apply.py
tests/unit/test_organize_index_sync.py
tests/unit/test_organize_stage.py
tests/unit/test_organize.py
tests/unit/test_photo.py
tests/unit/test_plugin_manager.py
tests/unit/test_safety.py
tests/unit/test_send2trash_cross_platform.py
tests/unit/test_source_write_hook.py
tests/unit/test_storage.py
tests/unit/test_watch.py
```

## atrium-safety repo - 17 files

```
.gitignore
CHANGELOG.md
DESIGN.md
pyproject.toml
README.md
src/curatorplug/atrium_safety/__init__.py
src/curatorplug/atrium_safety/enforcer.py
src/curatorplug/atrium_safety/exceptions.py
src/curatorplug/atrium_safety/plugin.py
src/curatorplug/atrium_safety/py.typed
src/curatorplug/atrium_safety/verifier.py
tests/conftest.py
tests/integration/test_curator_runtime.py
tests/unit/test_enforcer.py
tests/unit/test_plugin.py
tests/unit/test_re_read_verification.py
tests/unit/test_verifier.py
```

## atrium-citation repo - 19 files

```
.gitignore
CHANGELOG.md
DESIGN_V0_2.md
DESIGN.md
pyproject.toml
README.md
src/curatorplug/atrium_citation/__init__.py
src/curatorplug/atrium_citation/audit.py
src/curatorplug/atrium_citation/cli.py
src/curatorplug/atrium_citation/exceptions.py
src/curatorplug/atrium_citation/plugin.py
src/curatorplug/atrium_citation/py.typed
src/curatorplug/atrium_citation/sweep.py
tests/__init__.py
tests/conftest.py
tests/unit/test_audit_emission.py
tests/unit/test_cli.py
tests/unit/test_plugin.py
tests/unit/test_sweep.py
```
