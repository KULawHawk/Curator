#!/usr/bin/env bash
# scripts/setup_dev_hooks.sh   (v1.7.76)
#
# Bash/POSIX variant of setup_dev_hooks.ps1 (v1.7.74). One-stop dev
# environment installer for Curator clones on macOS, Linux, or
# Windows-WSL2/git-bash.
#
# Configures:
#   1. git core.hooksPath to .githooks (activates pre-commit + pre-push)
#   2. ~/.curator/github_pat file (for ci_diag.ps1 + pre-push hook)
#
# Idempotent: safe to run repeatedly. Skips steps that are already done.
#
# Usage:
#   ./scripts/setup_dev_hooks.sh           # default; prompts for PAT
#   ./scripts/setup_dev_hooks.sh --token <PAT>     # save the provided PAT
#   ./scripts/setup_dev_hooks.sh --skip-pat        # skip PAT step
#   ./scripts/setup_dev_hooks.sh --help            # show usage
#
# Token: ~/.curator/github_pat (single line, no trailing whitespace,
# plain text). Only needs the actions:read scope.

set -e

# ---------------------------------------------------------------------------
# Color helpers (TTY-aware; falls back to plain text in pipes/redirects)
# ---------------------------------------------------------------------------

if [ -t 1 ]; then
    C_CYAN="\033[36m"
    C_GREEN="\033[32m"
    C_YELLOW="\033[33m"
    C_RED="\033[31m"
    C_WHITE="\033[37m"
    C_RESET="\033[0m"
else
    C_CYAN=""
    C_GREEN=""
    C_YELLOW=""
    C_RED=""
    C_WHITE=""
    C_RESET=""
fi

write_step() { printf "${C_CYAN}==> %s${C_RESET}\n" "$1"; }
write_ok()   { printf "    ${C_GREEN}OK: %s${C_RESET}\n" "$1"; }
write_skip() { printf "    ${C_YELLOW}SKIP: %s${C_RESET}\n" "$1"; }
write_warn() { printf "    ${C_YELLOW}WARN: %s${C_RESET}\n" "$1"; }
write_fail() { printf "    ${C_RED}FAIL: %s${C_RESET}\n" "$1"; }

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

TOKEN=""
SKIP_PAT=0

while [ $# -gt 0 ]; do
    case "$1" in
        --token)
            TOKEN="$2"
            shift 2
            ;;
        --skip-pat)
            SKIP_PAT=1
            shift
            ;;
        --help|-h)
            sed -n '4,21p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            echo "Usage: $0 [--token <PAT>] [--skip-pat] [--help]" >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Resolve repo root (script lives at scripts/setup_dev_hooks.sh, root is ..)
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

echo
printf "${C_WHITE}Curator dev hooks setup (v1.7.76 / bash)${C_RESET}\n"
echo "Repo root: $REPO_ROOT"
echo

# ---------------------------------------------------------------------------
# Step 1: git core.hooksPath -> .githooks
# ---------------------------------------------------------------------------

write_step "Configuring git core.hooksPath..."

CURRENT_HOOKS_PATH=$(git config --get core.hooksPath 2>/dev/null || echo "")
if [ "$CURRENT_HOOKS_PATH" = ".githooks" ]; then
    write_skip "core.hooksPath already set to .githooks"
else
    if [ -n "$CURRENT_HOOKS_PATH" ]; then
        write_warn "core.hooksPath was '$CURRENT_HOOKS_PATH'; updating to .githooks"
    fi
    git config core.hooksPath .githooks
    write_ok "core.hooksPath set to .githooks"
fi

# Verify hooks exist
if [ ! -f ".githooks/pre-commit" ]; then
    write_fail ".githooks/pre-commit not found! Are you in the Curator repo?"
    exit 1
fi
if [ ! -f ".githooks/pre-push" ]; then
    write_fail ".githooks/pre-push not found! Are you in the Curator repo?"
    exit 1
fi

# Make hooks executable (in case they aren't on this clone)
chmod +x .githooks/pre-commit .githooks/pre-push 2>/dev/null || true

write_ok "Pre-commit + pre-push hooks found and activated"

# ---------------------------------------------------------------------------
# Step 2: PAT file at ~/.curator/github_pat
# ---------------------------------------------------------------------------

if [ "$SKIP_PAT" = "1" ]; then
    write_step "Skipping PAT setup (--skip-pat)"
    write_skip "ci_diag.ps1 and pre-push hook will silently skip without a token"
else
    write_step "Configuring GitHub PAT for CI tooling..."

    PAT_DIR="$HOME/.curator"
    PAT_FILE="$PAT_DIR/github_pat"

    # Ensure ~/.curator/ exists
    if [ ! -d "$PAT_DIR" ]; then
        mkdir -p "$PAT_DIR"
        write_ok "Created $PAT_DIR"
    fi

    # If PAT not provided as arg, check if file already exists
    if [ -z "$TOKEN" ] && [ -f "$PAT_FILE" ]; then
        EXISTING_PAT=$(tr -d '[:space:]' < "$PAT_FILE" 2>/dev/null || echo "")
        if printf '%s' "$EXISTING_PAT" | grep -qE '^(github_pat_|ghp_)'; then
            write_skip "PAT file already exists at $PAT_FILE (length=${#EXISTING_PAT})"
            TOKEN=""  # don't overwrite
        else
            write_warn "PAT file exists but content doesn't look like a GitHub PAT"
        fi
    fi

    # If still no PAT, prompt
    if [ -z "$TOKEN" ] && [ ! -f "$PAT_FILE" ]; then
        echo
        printf "${C_WHITE}    No PAT configured. To create one:${C_RESET}\n"
        echo "      1. Visit https://github.com/settings/personal-access-tokens/new"
        echo "      2. Select 'Fine-grained tokens', set expiration"
        echo "      3. Repository access: 'Selected repositories' -> Curator"
        echo "      4. Permissions -> Repository -> Actions: 'Read-only'"
        echo "      5. Generate the token (starts with 'github_pat_...')"
        echo
        printf "    Paste PAT (or press Enter to skip): "

        # Read without echo (hide the token as it's typed)
        stty -echo 2>/dev/null || true
        read -r TOKEN
        stty echo 2>/dev/null || true
        echo
    fi

    # Save the PAT if we have one
    if [ -n "$TOKEN" ]; then
        # Trim whitespace
        TOKEN=$(printf '%s' "$TOKEN" | tr -d '[:space:]')
        if ! printf '%s' "$TOKEN" | grep -qE '^(github_pat_|ghp_)'; then
            write_fail "Provided token doesn't look like a GitHub PAT (must start with 'github_pat_' or 'ghp_')"
            exit 1
        fi
        printf '%s' "$TOKEN" > "$PAT_FILE"
        chmod 600 "$PAT_FILE" 2>/dev/null || true
        write_ok "PAT saved to $PAT_FILE (chmod 600)"
    elif [ ! -f "$PAT_FILE" ]; then
        write_skip "No PAT provided; ci_diag.ps1 + pre-push hook will silently skip"
    fi
fi

# ---------------------------------------------------------------------------
# Step 3: Verify setup
# ---------------------------------------------------------------------------

write_step "Verifying setup..."

HOOKS_PATH=$(git config --get core.hooksPath)
if [ "$HOOKS_PATH" = ".githooks" ]; then
    write_ok "core.hooksPath: $HOOKS_PATH"
else
    write_fail "core.hooksPath is '$HOOKS_PATH' (expected '.githooks')"
    exit 1
fi

if [ -f ".githooks/pre-commit" ]; then
    write_ok "Pre-commit hook present (runs 3 lints: glyph, ORDER BY, ANSI regex)"
fi
if [ -f ".githooks/pre-push" ]; then
    write_ok "Pre-push hook present (warns when CI is red)"
fi

if [ "$SKIP_PAT" != "1" ]; then
    PAT_FILE="$HOME/.curator/github_pat"
    if [ -f "$PAT_FILE" ]; then
        write_ok "PAT file present (used by ci_diag.ps1 and pre-push hook)"
    else
        write_skip "PAT file not present; ci_diag.ps1 needs env var or argument"
    fi
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

echo
printf "${C_GREEN}Setup complete.${C_RESET}\n"
echo
printf "${C_WHITE}Quick reference:${C_RESET}\n"
echo "  CI status (PowerShell only):   pwsh ./scripts/ci_diag.ps1 status"
echo "  Failing tests (PowerShell):    pwsh ./scripts/ci_diag.ps1 summary"
echo "  Bypass hook:                   git commit --no-verify  /  git push --no-verify"
echo

# Note: ci_diag.ps1 is PowerShell-only currently. macOS/Linux contributors
# need pwsh (PowerShell Core) installed: `brew install --cask powershell`
# or `apt install powershell`. A native bash variant of ci_diag would be a
# nice-to-have future ship; for now, the pre-push hook (POSIX sh) is the
# main daily-driver tool that this script targets.
