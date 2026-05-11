# .githooks/

Curator's optional git hooks. Activate with one command:

```bash
git config core.hooksPath .githooks
```

After activation, the hooks here run automatically at the corresponding git lifecycle events for this clone only. Other clones / collaborators aren't affected unless they run the same command.

## Hooks provided

### `pre-commit` (v1.7.34)

Runs the lesson #50 lint test (`test_no_literal_glyphs_in_cli_outside_util`) before allowing a commit. Blocks commits that introduce a literal Unicode glyph from `_GLYPH_FALLBACKS` into any file under `src/curator/cli/` outside `util.py`.

**Why this exists:** v1.7.32 codified the lint at the pytest level (catches regressions when someone runs `pytest tests/`). v1.7.34 closes the gap for developers who push without running pytest. Together they form layer 4 of the lesson #50 defense:

| Layer | Mechanism | When it fires |
|---|---|---|
| 1. Code | `curator.cli.util` constants (9 codepoints) | Compile time (the safe path is the easy path) |
| 2. Tests | `test_no_literal_glyphs_in_cli_outside_util` | Test time (regressions caught when pytest runs) |
| 3. Docs | CHANGELOG v1.7.30 / v1.7.32 / v1.7.33 / v1.7.34 release notes | Code review / archaeology |
| 4. Hook  | `.githooks/pre-commit` | Commit time (mandatory unless `--no-verify`) |

**Cost:** ~600ms per commit. The hook runs a single pytest function, not the whole suite. Slowest part is Python startup + Qt import; the actual file scan completes in microseconds.

**Bypass when needed** (emergency hotfix, broken local environment):
```bash
git commit --no-verify
```

**Compatibility:** POSIX shell; works under Git for Windows' bundled `sh.exe`, macOS, and Linux. Tries `.venv/Scripts/python.exe` (Windows venv), then `.venv/bin/python` (Unix venv), then system `python` / `python3`.

## Why `.githooks/` instead of `.git/hooks/`

`.git/hooks/` lives inside the local `.git/` directory, which isn't tracked by git itself. Hooks placed there don't propagate to other clones.

`.githooks/` IS tracked. With `git config core.hooksPath .githooks`, this clone uses the tracked hooks instead. New clones get the hooks on the same one-command activation. Updates to hooks ship through normal `git pull`.

## Adding new hooks

1. Drop the script (no extension required) into `.githooks/` with a name matching the git lifecycle event (`pre-commit`, `commit-msg`, `pre-push`, `post-checkout`, etc.)
2. Make it executable (`chmod +x .githooks/your-hook`)
3. Document it in this README

On Windows, `chmod +x` is a no-op — git tracks the executable bit separately. Use `git update-index --chmod=+x .githooks/your-hook` to set it if needed.

## Opt-in by design

These hooks are NOT auto-activated when someone clones the repo. The user must explicitly run the `git config` command. This matches Curator's broader philosophy: defenses are layered, but the developer controls when each layer engages.
