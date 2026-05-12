# Curator Engineering Doctrine

**Status:** v1.0 RATIFIED 2026-05-12 (v1.7.80)
**Scope:** Principles, patterns, and standing decisions for Curator development.
**Audience:** Contributors, future maintainers, future-Jake returning after a break.

---

## Preamble

This document codifies engineering principles distilled from Curator's 21-ship post-arc
hygiene phase (v1.7.59-79). The arc began with a 20-ship silent CI-red period
(v1.7.42-63) that surfaced gaps in tooling, hooks, automation, and documentation. Each
ship that followed closed one gap with a specific, reusable pattern.

The principles below aren't theoretical. Every one of them was learned the expensive
way — by shipping code that broke in CI, or shipping code without the infrastructure to
catch breakage, or carrying a backlog item across multiple ships because closure felt
optional. They are the rules the codebase wants us to follow.

These principles also apply across the Ad Astra constellation (Atrium, RCS, FAP Engine,
APEX, etc.) wherever the constraints are similar. Curator is the most CI-mature of the
constellation as of v1.7.79 (see `docs/AD_ASTRA_CI_AUDIT.md`); the patterns below are
the recommended starting point for sibling-repo CI adoption.

---

## Part I — Principles of building

### Principle 1: Empirical CI evidence beats theoretical reasoning

**Learned from:** v1.7.67 → v1.7.77

When v1.7.67 bumped GitHub Actions to Node.js 24, the conservative choice was
`actions/checkout@v5` over `@v6`, based on changelog notes about "credential persistence
breaking changes." Ten ships later, Dependabot's PR #1 ran v6 through the 9-cell matrix
and came back 9/9 GREEN. The theoretical breaking change didn't affect Curator at all
(no submodules, no post-checkout auth, no push). v1.7.77 accepted the v6 upgrade.

**Rule:** When CI gives you empirical evidence, it overrides theoretical reasoning,
including your own. Read the changelogs to inform hypotheses, then test the hypotheses
in the matrix.

### Principle 2: Conservative defaults are options, not commitments

**Learned from:** v1.7.67 (conservatism), v1.7.77 (reversal)

The v1.7.67 choice of `checkout@v5` cost nothing at the time. The v1.7.77 reversal also
cost nothing — when better evidence arrived, the previous decision was simply replaced.
Defaults that lock in costly migrations later are bad defaults; defaults that can be
revisited with one ship are fine.

**Rule:** Choose the conservative option when in doubt. Document why. Be ready to
reverse it when new evidence justifies the reversal.

### Principle 3: Functional parity > code parity (cross-platform)

**STATUS: SUSPENDED as of v1.7.84.** Scope narrowed to Windows-only; see
`docs/PLATFORM_SCOPE.md` for the resume path. The principle text below remains
as-written because the bash variants of tooling are preserved on disk; the
principle will reactivate if/when macOS / Linux support resumes. Until then,
new tooling does not need a POSIX sibling.

**Learned from:** v1.7.76 (`setup_dev_hooks.sh`), v1.7.78 (`ci_diag.sh`)

The PowerShell and bash variants of `setup_dev_hooks` and `ci_diag` are functionally
identical but share no code. Each uses its host shell's idioms — `SecureString` vs
`stty -echo`, Hidden attribute vs `chmod 600`, `Write-Host -ForegroundColor` vs ANSI
escapes. Forcing identical implementations would create a third dependency or unnatural
compromises in both.

**Rule:** When porting tooling across platforms, port the behavior. Use each platform's
native idioms. Verify equivalence by behavior, not by code structure.

### Principle 4: Minimal-scope credentials force good infrastructure

**Learned from:** v1.7.74 (`actions:read` PAT scope), v1.7.77 (manual merge instead of API)

The PAT used for autonomous ships has only `actions:read`. When Dependabot's PR #1
needed merging, the absence of `pull_requests:write` meant the bump landed via a
manual `test.yml` edit instead of a one-click API merge. The manual path required
reading the diff, understanding it, and documenting the decision — a better outcome
than a one-click merge would have been.

**Rule:** Grant credentials only the scope they need today. If broader scope would
make a process faster, that's usually a sign the process should be more deliberate.

### Principle 5: Defense-in-depth via fallbacks

**Learned from:** v1.7.70 (pre-push hook), v1.7.78 (`ci_diag.sh`)

The pre-push hook uses curl → wget fallback. `ci_diag.sh` uses jq → Python fallback.
Each layered fallback covers a class of stripped-down systems where the preferred tool
might be missing. The fallback path doesn't have to be elegant; it has to work.

**Rule:** For tooling that runs on contributor machines (not just CI), assume nothing
about installed tools beyond a POSIX shell. Layer fallbacks for everything else.

### Principle 6: TTY-aware output is the standard Unix convention

**Learned from:** v1.7.76, v1.7.78

Both bash variants detect `[ -t 1 ]` before emitting ANSI escapes. Interactive runs
get color; pipes and redirects get plain text. This isn't a feature — it's a baseline
expectation for any well-mannered Unix tool.

**Rule:** Color output requires TTY detection. Without it, log files and CI captures
fill with `\x1b[36m` garbage that breaks substring matching and irritates everyone.

### Principle 7: POSIX-canonical paths align with host conventions

**Learned from:** v1.7.74 vs v1.7.78 (output paths)

`setup_dev_hooks.ps1` writes to `~\Desktop\AL\.curator\`. `setup_dev_hooks.sh` and
`ci_diag.sh` write to `~/.curator/logs/`. PowerShell users expect Windows-style paths;
POSIX users expect dotfile-style locations. Forcing identical paths would be
unnatural in one or the other.

**Rule:** Each platform variant uses its host's path conventions. Document the
difference. Don't force harmonization.

---

## Part II — Principles of correctness

### Principle 8: Pre-commit lints turn invariants into laws

**Learned from:** v1.7.32 (glyph), v1.7.72 (ORDER BY), v1.7.73 (ANSI regex)

Three project invariants are now mandatory at commit time:

| Invariant | Lint | Catches |
|---|---|---|
| No literal Unicode in cp1252-risk paths | v1.7.32/34 | Lesson #50 regressions |
| `ORDER BY <timestamp>` requires `rowid` or unique tie-breaker | v1.7.72 | Lesson #67 regressions (20-ship CI-red arc) |
| No inline ANSI-strip regex outside `conftest.py` | v1.7.73 | v1.7.68 fixture-hoist regressions |

Each lint took one ship to write, but blocks every future commit that would
re-introduce the bug class. The asymmetry is everything.

**Rule:** When a bug class causes pain (especially repeat pain), write the lint
before writing the next fix. Lints scale; vigilance doesn't.

### Principle 9: Bug-class sweeps + regression lints prevent recurrence

**Learned from:** v1.7.66 → v1.7.72, v1.7.68 → v1.7.73

When v1.7.59-64 discovered that one missing `, rowid` in an `ORDER BY` clause caused
the CI-red arc, v1.7.66 swept all 13 ORDER BY sites across 7 repositories to add
deterministic tie-breakers. v1.7.72 then codified the pattern as a pre-commit lint.
Same pattern for ANSI regex: v1.7.68 hoisted three duplicates into a `strip_ansi`
fixture; v1.7.73 wrote the lint.

**Rule:** A bug-class fix is two ships, not one. First, sweep all instances and fix
them. Second, write the lint that prevents the next instance.

### Principle 10: Pre-push hooks are signals, not gates

**Learned from:** v1.7.70

The pre-push hook queries the GitHub Actions API for the latest CI run and warns
loudly when red. It never blocks the push. Two reasons: (a) sometimes pushing a fix
to a red CI is exactly what you want; (b) gates that block correct work breed
`--no-verify` reflexes that defeat the system.

**Rule:** When the right answer is sometimes "ignore the warning and proceed," make
it a warning, not a gate. Save gates for invariants that are never legitimately
violated.

### Principle 11: DRY refactors pair with regression lints

**Learned from:** v1.7.68 (refactor) → v1.7.73 (lint)

When v1.7.68 hoisted three inline `re.sub(r"\x1b\[…")` patterns into a `strip_ansi`
fixture, the cleanup felt complete. Five ships later, v1.7.73 wrote the lint that
prevents anyone from re-introducing the inline pattern. Without the lint, the next
contributor would copy-paste the same regex into a new test file.

**Rule:** When you DRY up duplicated code, the refactor only sticks if a lint prevents
re-duplication. Otherwise the regression is just deferred.

---

## Part III — Principles of communication

### Principle 12: Documentation follows tooling, not leads it

**Learned from:** v1.7.65 → v1.7.75 (10-ship gap)

The ten infrastructure ships from v1.7.65 (`ci_diag.ps1`) to v1.7.74 (`setup_dev_hooks.ps1`)
each carried inline header comments but no README presence. v1.7.75 finally consolidated
them into a "Contributing — dev setup" section. Documenting any individual ship earlier
would have required rewriting as the toolkit evolved.

**Rule:** Build the thing, then document it. If you document first, the document
shapes the thing, often badly.

### Principle 13: Workflow files accumulate decision history in comments

**Learned from:** v1.7.42 → v1.7.67 → v1.7.77

`.github/workflows/test.yml` carries comment blocks for every action-version decision.
v1.7.67's bump to `checkout@v5` is documented. v1.7.77's reversal to `checkout@v6` is
documented immediately above it. A future contributor reading the workflow sees the
full decision tree without leaving the file.

**Rule:** Every non-obvious decision in a config file deserves an inline comment with
the ship version that made the decision. Decision history co-located with the code is
worth more than decision history in a separate doc that someone has to find.

### Principle 14: Audit closure has value even when result is "no action needed"

**Learned from:** v1.7.79 (Ad Astra audit)

The Ad Astra sibling-repo audit took ~5 API queries and surfaced zero action items.
But documenting the result in `docs/AD_ASTRA_CI_AUDIT.md` prevents future-Jake from
re-running the audit, and provides a recommended-patterns list for the day sibling
repos do adopt CI.

**Rule:** When you finish an audit, write down what you found. Negative results
deserve the same documentation as positive ones.

### Principle 15: Backlog items deferred 3+ times must be closed or scheduled

**Learned from:** v1.7.79 (audit item carried for 5 ships)

The Ad Astra audit appeared on five consecutive backlog lists (v1.7.74-78) before
v1.7.79 closed it. Each deferral felt fine in isolation; cumulatively, it was a smell.

**Rule:** A backlog item that's been carried unchanged for three or more ships is
either (a) actually important and should be scheduled now, or (b) not important and
should be closed with a "won't do" note. Perpetual deferral is the worst outcome.

---

## Part IV — Principles of automation

### Principle 16: One-command setup is non-negotiable

**Learned from:** v1.7.74 (`setup_dev_hooks.ps1`), v1.7.76 (`setup_dev_hooks.sh`)

A new contributor (or future-Jake on a new machine) clones the repo and runs one
command: `.\scripts\setup_dev_hooks.ps1` (or `./scripts/setup_dev_hooks.sh`). That
command configures git hooks, prompts for the PAT, validates the install, and prints
a quick-reference. Anything more is friction.

**Rule:** If setting up the development environment requires more than one command,
the setup is incomplete. Make the script idempotent so re-running is safe.

### Principle 17: Dependabot is the change-detector, you're the change-acceptor

**Learned from:** v1.7.71 (config), v1.7.77 (first acceptance)

Dependabot's job is to notice when dependencies update and propose grouped bumps.
Your job is to read the PR, check CI, and accept or reject. The acceptance step is
manual and deliberate (especially when minimal-scope credentials prevent one-click
merges — see Principle 4). The detection is automated; the judgment is not.

**Rule:** Automated dependency tracking is mandatory; automated dependency merging
is not. Let the bot find the updates; you decide which ones land.

---

## Part V — Standing decisions

The following choices apply to all future Curator ships unless explicitly overridden:

| Decision | Source | Rationale |
|---|---|---|
| CI matrix is 3 cells: `windows-latest × {3.11, 3.12, 3.13}` | v1.7.84 | Scope narrowed from 9-cell to Windows-only; see `docs/PLATFORM_SCOPE.md` for the set-aside register and step-by-step resume path |
| GitHub Actions: `checkout@v6`, `setup-python@v6`, `upload-artifact@v7` | v1.7.77 | Node.js 24 native; CI-verified safe |
| Dependabot watches `github-actions` ecosystem only (for now) | v1.7.71 | Pip ecosystem added when 25+ consecutive green ships verify the github-actions cadence |
| **Coverage standard: 100% line + branch on Windows scope, or documented `# pragma: no cover`** | v1.7.84 | Accuracy is the apex principle; "diminishing returns" framing is corner-cutting. Pragma exceptions require an inline comment naming the reason (e.g. "set aside v1.7.84", "defensive code for impossible case") |
| Three pre-commit lints: glyph, ORDER BY, inline ANSI regex | v1.7.32/72/73 | One ship per regression bug class |
| Pre-push hook warns on red CI, never blocks | v1.7.70 | Signals, not gates (Principle 10) |
| PAT scope: `actions:read` only | v1.7.74 | Minimal-scope credentials (Principle 4) |
| Output paths: PS uses `~\Desktop\AL\.curator\`, bash uses `~/.curator/logs/` | v1.7.74/78 | POSIX-canonical paths (Principle 7) |
| Every ship gets a CHANGELOG entry + release notes file | All ships since v1.7.x | Decision history co-located with code |
| `--no-verify` is documented and acceptable for emergencies | v1.7.70/72/73 | Warnings not gates |

---

## Part VI — How to use this document

**When starting a new ship:** Skim Parts I-IV. If the work fits an existing principle,
reference it in the release notes. If it conflicts with a principle, either change
the work or amend the doctrine (with version bump and rationale).

**When writing release notes:** Cite the principle(s) the ship reinforces or extends.
Example: "This ship demonstrates Principle 9 (bug-class sweeps + regression lints)
by adding both the sweep (v1.X.X) and the lint (this ship)."

**When reviewing someone else's PR (or future-Jake reviewing his own):** Check that
the change doesn't silently violate Part V's standing decisions. If it does, ask why.

**When the doctrine is wrong:** It will be eventually. Open a PR that amends the
relevant principle, bumps this document's version (currently v1.0), and explains the
change in the release notes. The doctrine is a living document, not scripture.

---

## Appendix A: Lessons captured

The doctrine above is the distilled form. The raw lessons are #46-67, captured
across the v1.7.x release notes. Key entries:

| Lesson | Description | Doctrine principle |
|---|---|---|
| #46-49 | Various v1.7.x learnings (see release notes) | Various |
| #50 | Literal Unicode glyphs crash cp1252 capture | Principle 8 (lints) |
| #66 | Green local pytest does not imply green CI | Principles 1, 10 |
| #67 | Diagnose with logs, not with hypotheses | Principles 1, 5, 17 |

Lesson #67 is fully mitigated by v1.7.70 via three concrete mechanisms:
1. One-command CI log access (`ci_diag.{ps1,sh}`) — v1.7.65/78
2. Persistent PAT storage (`~/.curator/github_pat`) — v1.7.65
3. Pre-push warning when CI is red — v1.7.70

---

## Appendix B: The 21-ship hygiene arc (v1.7.59-79)

| Ship | Theme | Principle(s) demonstrated |
|---|---|---|
| v1.7.59-64 | CI-red arc closure | 1, 8 |
| v1.7.65 | `ci_diag.ps1` diagnostic tooling | 5, 17 |
| v1.7.66 | ORDER BY hardening sweep (13 sites) | 9 |
| v1.7.67 | Node.js 24 readiness | 2, 13 |
| v1.7.68 | `strip_ansi` fixture DRY refactor | 11 |
| v1.7.69 | Linux `/var` audit | 14 |
| v1.7.70 | Pre-push CI verification hook | 5, 10, 17 |
| v1.7.71 | Dependabot automation | 17 |
| v1.7.72 | Pre-commit ORDER BY regression lint | 8, 9 |
| v1.7.73 | Pre-commit inline ANSI regex lint | 8, 11 |
| v1.7.74 | Auto-install PowerShell installer | 16 |
| v1.7.75 | README "Contributing — dev setup" section | 12 |
| v1.7.76 | Auto-install bash installer | 3, 6, 7, 16 |
| v1.7.77 | Accept Dependabot PR #1 | 1, 2, 4, 13, 17 |
| v1.7.78 | `ci_diag.sh` bash variant | 3, 5, 6, 7 |
| v1.7.79 | Ad Astra constellation CI audit | 14, 15 |
| v1.7.80 | **Engineering Doctrine + self-audit test (this ship)** | All 17 |

---

## Document history

| Version | Date | Ship | Change |
|---|---|---|---|
| v1.0 | 2026-05-12 | v1.7.80 | Initial ratification |

---

*End of Curator Engineering Doctrine v1.0.*
