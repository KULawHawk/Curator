# Ad Astra Constellation: CI Audit (v1.7.79)

**Date:** 2026-05-12
**Scope:** All public repos under the KULawHawk GitHub account
**Trigger:** v1.7.67 → v1.7.77 modernization arc (Node.js 20 deprecation, action version bumps)

## Question

After Curator's CI workflow was modernized to Node.js 24 (v1.7.67) and the latest GitHub Actions versions (v1.7.77: checkout@v6, setup-python@v6, upload-artifact@v7), do any sibling repos in the Ad Astra constellation need similar updates?

## Audit method

Queried `https://api.github.com/repos/KULawHawk/<repo>/contents/.github/workflows` for each sibling repo. A 200 response with content indicates workflows exist; a 404 indicates no workflows directory.

## Findings (as of 2026-05-12)

| Repo | Has `.github/workflows/`? | Action needed? |
|---|---|---|
| **Curator** (this repo) | Yes (`test.yml` v1.7.77) | N/A — source of the audit |
| `curatorplug-atrium-safety` | No | None |
| `Atrium` | No | None |
| `curatorplug-atrium-citation` | No | None |
| `curatorplug-atrium-reversibility` | No | None |
| `Ad-Astra` | No | None |

## Conclusion

**None of the sibling Ad Astra repos use GitHub Actions for CI.** They are unaffected by the Node.js 20 deprecation (forcing date 2026-06-02, removal 2026-09-16).

Curator is the only repo in the constellation that runs the 9-cell pytest matrix on every push.

## Implications

1. **No urgent CI work in sibling repos.** The Node 20 deprecation does not affect them.

2. **Sibling repos may want CI in the future.** They currently rely on:
   - Local pytest invocations (development)
   - Curator's plugin ecosystem testing (for the `curatorplug-*` repos, indirectly via Curator's own CI when their plugin is loaded)
   - No automated multi-OS / multi-Python validation

3. **If/when sibling repos add CI**, they should:
   - Use the same `checkout@v6 / setup-python@v6 / upload-artifact@v7` versions as Curator (current as of v1.7.77)
   - Adopt Dependabot for `github-actions` ecosystem (Curator's `.github/dependabot.yml` is a template)
   - Consider the same 3-OS × 3-Python matrix pattern from Curator's `test.yml`

## Curator's reusable CI patterns

For future sibling-repo CI adoption, Curator provides reference implementations of:

| Pattern | File | Ship |
|---|---|---|
| 9-cell matrix workflow | `.github/workflows/test.yml` | v1.7.54 (introduced), v1.7.77 (current) |
| Dependabot config | `.github/dependabot.yml` | v1.7.71 |
| Pre-commit lints | `.githooks/pre-commit` | v1.7.34/72/73 |
| Pre-push CI warning | `.githooks/pre-push` | v1.7.70 |
| Dev setup installer (PowerShell) | `scripts/setup_dev_hooks.ps1` | v1.7.74 |
| Dev setup installer (bash) | `scripts/setup_dev_hooks.sh` | v1.7.76 |
| CI diagnostic loop (PowerShell) | `scripts/ci_diag.ps1` | v1.7.65 |
| CI diagnostic loop (bash) | `scripts/ci_diag.sh` | v1.7.78 |

Any of these can be copied wholesale to a sibling repo as a starting point.

## Re-audit cadence

This audit should be re-run when:
1. A new sibling repo is added to the constellation
2. An existing sibling repo's `.github/` directory is created
3. GitHub announces another Node.js LTS deprecation (next: Node.js 22 in ~2027)

The Curator pre-push hook (v1.7.70) and Dependabot (v1.7.71) cover Curator itself; sibling repos won't get those benefits until they adopt similar tooling.

## Audit closure

This document closes the "audit Ad Astra sibling repos for similar Node 20 deprecation" backlog item from v1.7.74-v1.7.78's release notes. Result: **no action needed.** The audit was non-trivial in time (had to query each repo's `.github/workflows/` via API), but trivial in outcome.
