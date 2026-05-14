# Atrium Plugin Coverage Audit

**Owner:** Jake Leese · **Audit date:** 2026-05-13 (v1.7.211)
**Scope:** All 3 charter `curatorplug-atrium-*` repos in the Ad Astra constellation
**Purpose:** Establish whether Atrium plugins should track Curator's apex-accuracy doctrine post-v2.0

## Headline

The 3 Atrium plugins span 3 lifecycle states:

| Plugin | Version | State | Coverage | Tests | Recommendation |
|---|---:|---|---:|---:|---|
| `curatorplug-atrium-safety` | **v0.4.0** | shipped + maintained | **94%** | 70 | **Promote to apex-accuracy** |
| `curatorplug-atrium-citation` | **v0.2.0** | shipped + maintained | **94%** | 47 | **Promote to apex-accuracy** |
| `curatorplug-atrium-reversibility` | DESIGN v0.3 | **DEFERRED indefinitely** | n/a | 0 | No change — design-only |

**Bottom line:** Both shipped Atrium plugins are at 94% coverage — a respectable level but **6 percentage points below Curator's 99.76% apex-accuracy bar**. Promoting them to the same doctrine is a post-v2.0 stretch arc (estimated 4-8 ships total across both repos).

---

## Per-plugin audit

### 1. `curatorplug-atrium-safety` v0.4.0

**Location:** `C:\Users\jmlee\Desktop\AL\curatorplug-atrium-safety\`
**HEAD:** `f9b60f7` — `feat(retention): T-B02 curator_pre_trash hookimpl (v0.4.0)`
**Purpose:** Constitutional plugin enforcing Atrium **Principle 2 (Hash-Verify-Before-Move)** via the `curator_source_write_post` hook. Added v0.4 retention check via `curator_pre_trash` hookimpl.

**Coverage breakdown (70 unit tests passing in 1.30s):**

| Module | Stmts | Miss | Branch | BrPart | Cover |
|---|---:|---:|---:|---:|---:|
| `__init__.py` | 5 | 0 | 0 | 0 | 100% |
| `enforcer.py` | 25 | 0 | 8 | 0 | 100% |
| `exceptions.py` | 5 | 0 | 0 | 0 | 100% |
| `plugin.py` | 122 | 9 | 42 | 4 | **92%** |
| `verifier.py` | 9 | 0 | 2 | 0 | 100% |
| **TOTAL** | **166** | **9** | **52** | **4** | **94%** |

**Gap analysis:**
- 9 missing lines + 4 partial branches all live in `plugin.py` (the largest module)
- 4 of 5 modules already at 100% — `enforcer.py`, `exceptions.py`, `verifier.py`, `__init__.py`
- `plugin.py` 92% is plausibly closable with 3-5 additional defensive-boundary tests

**Promotion estimate (if Jake green-lights):**
- ~1 sub-ship to map the 9 missing lines + 4 partials to test scenarios
- ~1 sub-ship to write the focused gap-closer tests
- Total: **2 sub-ships** to reach 100% line + branch
- Follow the Curator pattern: scope plan → gap-closer tests → pragma audit close

### 2. `curatorplug-atrium-citation` v0.2.0

**Location:** `C:\Users\jmlee\Desktop\AL\curatorplug-atrium-citation\`
**HEAD:** `2660422` — `v0.2.0: cross-source filter (DM-2 RATIFIED finally honored)`
**Purpose:** Constitutional plugin implementing Atrium **Principle 3 (Citation Chain Preservation)** + **Aim 5 (Fidelity)** at v0.3 ordering. v0.2 ships the cross-source filter that unblocked once Curator v1.6.1 made audit details schema-symmetric.

**Coverage breakdown (47 tests passing in 1.85s):**

| Module | Stmts | Miss | Branch | BrPart | Cover |
|---|---:|---:|---:|---:|---:|
| `__init__.py` | 3 | 0 | 0 | 0 | 100% |
| `audit.py` | 28 | 0 | 2 | 0 | 100% |
| `cli.py` | 57 | 6 | 22 | 2 | **87%** |
| `exceptions.py` | 2 | 0 | 0 | 0 | 100% |
| `plugin.py` | 24 | 3 | 0 | 0 | **88%** |
| `sweep.py` | 79 | 1 | 14 | 1 | **98%** |
| **TOTAL** | **193** | **10** | **38** | **3** | **94%** |

**Gap analysis:**
- 3 modules below 100%: `cli.py` (87%, biggest gap), `plugin.py` (88%), `sweep.py` (98%)
- `cli.py` gap (6 missing lines + 2 partials) likely needs targeted Typer CliRunner tests
- `plugin.py` gap (3 missing lines) likely defensive `except` boundaries
- `sweep.py` (98%) is right at the doorstep — 1 line + 1 partial away

**Promotion estimate:**
- ~1 sub-ship to map gaps + write `cli.py` CliRunner tests (the CLI Coverage Arc patterns from Curator port cleanly)
- ~1 sub-ship to close `plugin.py` + `sweep.py` + pragma audit
- Total: **2 sub-ships** to reach 100% line + branch

### 3. `curatorplug-atrium-reversibility` (DESIGN v0.3 DEFERRED)

**Location:** `C:\Users\jmlee\Desktop\AL\curatorplug-atrium-reversibility\`
**HEAD:** `84ee978` — `docs: defer DESIGN to v0.3 DEFERRED`
**Purpose (designed but not built):** Constitutional plugin for Atrium **Aim 6 (Reversibility)** at v0.3 ordering.

**Status: indefinitely deferred.** Per `AD_ASTRA_CONSTELLATION.md`:

> Deferred indefinitely: the v0.2 design's implementation premise (`CleanupService.purge_trash` as the irreversible op) doesn't match Curator's actual code — there is no `purge_trash` method; permanent deletion happens via the OS Recycle Bin under user control, outside Curator's hookspec surface. Curator's existing protections (send2trash-by-default, duplicate-keeper-preservation, SafetyService REFUSE-tier checks, the existing `pre_trash` veto hookspec) already implement Principle 3 reasonably. DESIGN.md preserved as record of the analysis. Rule 13 (read code first) caught this before P1 work happened.

**No coverage to audit.** The repo contains only DESIGN.md — no source, no tests, no implementation. Curator's existing protections cover Aim 6 reasonably.

**Recommendation:** No change. Leave deferred per the v0.3 decision. Reopen only if Curator gains a genuine irreversible-op surface in the future.

---

## Combined inventory

| Metric | safety v0.4 | citation v0.2 | Combined |
|---|---:|---:|---:|
| Statements | 166 | 193 | **359** |
| Missing lines | 9 | 10 | **19** |
| Branches | 52 | 38 | **90** |
| Partial branches | 4 | 3 | **7** |
| Tests | 70 | 47 | **117** |
| Coverage | 94% | 94% | **~94%** |

**To bring both plugins to Curator's 99.76% standard:** estimated **4 sub-ships total** (~2 per plugin) following the same pattern Curator's GUI Coverage Arc used:
1. Scope plan (counts the gap, predicts ship count)
2. Per-module gap-closer ships
3. Pragma audit close

This is roughly the same effort as Round 5 Tier 1 sub-ship 8 (the pragma audit close at v1.7.206).

---

## Recommendation summary

### For the two shipped plugins (safety + citation)

**Promote to apex-accuracy doctrine** as a **post-v2.0 stretch arc**. Justification:

1. **They're constitutional plugins.** Atrium Principles 2 + 3 are the constitutional enforcement layer — they deserve the same verification rigor as the core they enforce.
2. **The gap is tractable.** 4 sub-ships across 2 repos is small relative to the 8 already-closed Curator arcs.
3. **The doctrine ports cleanly.** Both repos use pytest + the same pluggy framework. The 105-lesson library applies directly. The CLI Coverage Arc patterns (CliRunner-based) port to atrium-citation's `cli.py`.
4. **The constellation map matures.** Currently `AD_ASTRA_CONSTELLATION.md` shows Curator at 99.76% and the plugins at 94% — the inconsistency is visible and a low-effort fix.

**Do NOT block v2.0 stamp on this.** This is a post-v2.0 arc. The plugins are operationally stable at 94%.

### For atrium-reversibility

**Leave as-is.** The DESIGN v0.3 deferral is correct. Reopen only if/when a genuine irreversible-op surface appears in Curator.

### Open question for The Log

Should the plugin coverage arcs run **per plugin** (safety arc → citation arc, sequential) or **combined** (one arc, both plugins, 4 sub-ships)? Recommend combined — they share the same patterns and Lesson #99 pragma audit can sweep both at close.

---

## See also

- **`..\AD_ASTRA_CONSTELLATION.md`** — constellation map (Curator's row updated at v1.7.209 to reflect v2.0-ready state)
- **`..\Atrium\CONSTITUTION.md`** v0.3 — Principles 1–4 that the plugins enforce
- **`Curator/docs/V2_RELEASE_COVERAGE_AUDIT.md`** — the 99.76% benchmark
- **`Curator/CLAUDE.md`** Doctrine #1 — the apex-accuracy doctrine these plugins would adopt
- **`Curator/docs/CLI_COVERAGE_ARC_SCOPE.md`** — the model for the plugin coverage arcs (CliRunner-based test patterns port directly to atrium-citation's `cli.py`)
