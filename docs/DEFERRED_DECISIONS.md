# Deferred decisions

**Owner:** Jake Leese · **Updated:** 2026-05-13 (v1.7.181, Round 4 Tier 1 ship 2)

This file is the canonical index of decisions that **Code surfaced but
deliberately did not act on** while waiting for Jake's call. Per Lesson
#100 (Doctrine #18 in `CLAUDE.md`), Curator policy is: **never
auto-delete or auto-modify code with unclear provenance.** Document the
finding, list options, recommend one, and defer.

This file is the single index. Cross-references to specific decision
docs and to release notes / CHANGELOG entries where each item was first
raised are kept current here. When a decision is resolved, move its
entry to the **Resolved** section at the bottom rather than deleting it
— the resolution record is part of the project's memory.

---

## How to add a new deferred decision

Follow the pattern from Lesson #100 (CLAUDE.md Doctrine #18). Either:

- **In-source comment block** at the affected location, *and* an entry
  here pointing back, **or**
- **A standalone doc** under `docs/` named for the topic (e.g.
  `docs/PLATFORM_SCOPE.md`, `docs/MUTATION_TESTING_DEFERRED.md`), and
  an entry here pointing to it.

Use this template for each entry below:

```markdown
### NN. <one-line topic>

- **Location:** `<file:line_range or area>`
- **Surfaced:** `v1.7.NN` (`<release-notes link>`, `<CHANGELOG link>`)
- **Found:** <what makes this suspicious / why it needs a decision>
- **Options:**
  - **(a)** <action with consequence>
  - **(b)** <action with consequence>
  - **(c)** <action with consequence (optional)>
- **Recommendation:** <option letter> — <1-line rationale>
- **Decision:** **PENDING** — awaiting Jake's call
- **Cost of waiting:** <small / medium / large + what happens if it keeps
  waiting>
- **Detail doc (optional):** `docs/<topic>.md`
```

Resolution: when Jake calls it, move the entry to **Resolved** below,
add `### Resolution` with the decision date, ship that closed it,
and a 1-line outcome summary.

---

## Pending decisions

### 1. Sandbox-fragile trash-flow tests (recycle-bin enumeration hangs)

- **Location:** `tests/gui/test_gui_mutations.py::TestTrashMutations::test_trash_moves_file_and_marks_deleted` plus one or more `tests/integration/` tests that invoke `cli/main.py:464` (the `scan group --apply` flow → `rt.trash.send_to_trash(...)`)
- **Surfaced:** v1.7.180 (Round 4 Tier 1 ship 1) — the standard shipping invocation per Lesson #96 (`pytest tests/ --cov=curator.cli.main`) hung in this sandbox environment; cli/main.py coverage had to be measured with `tests/unit/` only
- **Found:** the vendored `send2trash` module at `src/curator/_vendored/send2trash/win/recycle_bin.py:111` performs blocking `pathlib.Path.read_bytes()` on every `$I*` entry in `$Recycle.Bin` across every drive. In real Windows / Jake's dev environment / CI, this completes in seconds. In sandbox containers without a real recycle bin, it blocks indefinitely (timeout-killed by pytest-timeout, but only after consuming the timeout budget — affects 2+ tests on every full-suite run).
- **Options:**
  - **(a)** Add `@pytest.mark.integration` to the affected tests, then add a CI config that runs `tests/unit/` + `tests/integration/ -m "not integration"` in fast jobs and the full suite in slower nightly jobs
  - **(b)** Add a `send2trash_mock` pytest fixture that activates when an env-var like `CURATOR_SANDBOX=1` is set, replacing `find_in_recycle_bin` with a no-op stub
  - **(c)** Both — marker for clean fast/slow separation, env-var-fixture for sandbox runs that need to exercise the full suite
- **Recommendation:** **(c)** — they're complementary, not exclusive. Marker is the clean architectural separator; env-var fixture handles sandbox / containerized runs that still need full-suite coverage.
- **Decision:** **PENDING** — awaiting Jake's call
- **Cost of waiting:** **medium**. Every Round 4+ coverage measurement on `cli/main.py` (or any module exercised by the trash-flow tests) has to fall back to `tests/unit/` and document the sandbox limitation. Adds ~1 paragraph to every release notes file. Not blocking, but adding doctrine debt.

### 2. Dialogs.py decomposition strategy (~2234 lines)

- **Location:** `src/curator/gui/dialogs.py`
- **Surfaced:** `docs/ROUND_3_LESSONS_RETROSPECTIVE.md` Lesson #98 application notes + `..\.curator\CLAUDE_CODE_HANDOFF_ROUND4.md` Tier 4 description (Round 4 handoff scoping note: "may exceed one round's budget")
- **Found:** `gui/dialogs.py` is the largest GUI module by ~2× the next biggest (`main_window.py` at 1089 lines). Round 4 Tier 4 is opt-in and explicitly flagged as "may need to defer to Round 5." A decomposition strategy is needed before a coverage arc can be opened on the file — likely one ship per dialog class, with the dialog inventory itself being a separate ship.
- **Options:**
  - **(a)** Tackle in Round 4 Tier 4 with the decomposition-doc-first approach outlined in the handoff (v1.7.197 → docs/DIALOGS_DECOMPOSITION.md, then per-dialog ships)
  - **(b)** Defer entirely to Round 5 with a fresh handoff prompt focused on dialogs (the handoff doc already notes this is acceptable)
  - **(c)** Partial: cover the simpler dialogs (about, version, help) in Round 4 and defer the complex ones (migration confirmations, source configuration) to Round 5
- **Recommendation:** **defer to Tier 4 / Round 5 gate** — Round 4 Tier 1+2+3 already amounts to 11+ ships. Tier 4 should only proceed with Jake's explicit go-ahead after Tier 3 closes. Pre-committing the dialogs strategy here would violate Lesson #88 (split if scope > 1.5x typical).
- **Decision:** **PENDING** — awaiting Tier 3 close + Jake's call on whether to attempt Tier 4 in Round 4
- **Cost of waiting:** **none** — this is forward-looking strategy, not blocking current work

---

## Resolved decisions

### R1. Duplicate `_resolve_file` in `cli/main.py`

- **Location:** `src/curator/cli/main.py` lines 187-225 (dead) and 3721+ (live, since v1.7.3)
- **Surfaced:** v1.7.155 (Round 3 Tier 3 ship 1, [release notes](releases/v1.7.155.md))
- **Carried through:** 21 sub-ships of Round 3 Tier 3 (CLI Coverage Arc). Pragma'd at v1.7.175 ([release notes](releases/v1.7.175.md)). Re-flagged at Round 3 close-out (v1.7.179).
- **Resolution:** **v1.7.180** ([release notes](releases/v1.7.180.md), [CHANGELOG](../CHANGELOG.md#17180--2026-05-13--round-4-tier-1-ship-1-resolve-_resolve_file-duplicate-option-b--merge-prefix-match))
- **Decision:** **option (b) — merge prefix-match into the live definition**. Restores the path-prefix feature advertised in `inspect`'s help text and silently broken since v1.7.3.
- **Outcome:** −39 source lines dead code, +10 lines docstring, +1 pragma retired, +2 new tests, `cli/main.py` coverage 99.43% → 99.44%. Captured as **Lesson #102 / Doctrine #20** (shadowed definitions become silent regressions).

---

## See also

- **Doctrine #18 (Lesson #100):** `CLAUDE.md` — the rule that produced this index
- **Doctrine #20 (Lesson #102):** `CLAUDE.md` — the rule that explains why option (b) won R1
- **`docs/PLATFORM_SCOPE.md`** — the single-decision-doc model. Decisions large enough to warrant their own file follow that pattern; this index points to them.
- **`docs/ROUND_3_LESSONS_RETROSPECTIVE.md`** — the source of Lessons #96-101, several of which produce future entries here
