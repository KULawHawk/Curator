#requires -Version 5.1
<#
.SYNOPSIS
    Set up a Curator dev environment for the current clone (v1.7.74).

.DESCRIPTION
    One-stop setup script for new Curator clones. Configures:
      1. git core.hooksPath to .githooks (activates pre-commit + pre-push)
      2. ~/.curator/github_pat file (for ci_diag.ps1 + pre-push hook)

    Idempotent: safe to run repeatedly. Skips steps that are already done.
    Verifies each step succeeded before moving on.

.PARAMETER Token
    GitHub Personal Access Token (PAT) for CI status queries. If not
    provided, the script will prompt unless ~/.curator/github_pat exists.

.PARAMETER SkipPat
    Skip the PAT setup step. Useful for contributors who don't need CI
    status queries (the hook + ci_diag.ps1 silently skip if no token).

.EXAMPLE
    .\scripts\setup_dev_hooks.ps1
    # Activates hooks. Prompts for PAT if not already configured.

.EXAMPLE
    .\scripts\setup_dev_hooks.ps1 -Token "github_pat_..."
    # Activates hooks AND saves the provided PAT.

.EXAMPLE
    .\scripts\setup_dev_hooks.ps1 -SkipPat
    # Activates hooks only; no PAT.

.NOTES
    v1.7.74: codifies the manual setup steps documented in
      - .githooks/pre-commit's header comment (v1.7.34, expanded v1.7.72/73)
      - .githooks/pre-push's header comment (v1.7.70)
      - scripts/ci_diag.ps1's token-discovery section (v1.7.65)

    Token storage: ~/.curator/github_pat (single line, no trailing
    whitespace, plain text). The PAT only needs the
    `actions:read` scope.
#>
[CmdletBinding()]
param(
    [string]$Token = "",
    [switch]$SkipPat
)

# Color helpers for output legibility.
function Write-Step($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "    OK: $msg" -ForegroundColor Green }
function Write-Skip($msg)  { Write-Host "    SKIP: $msg" -ForegroundColor Yellow }
function Write-Warn($msg)  { Write-Host "    WARN: $msg" -ForegroundColor Yellow }
function Write-Fail($msg)  { Write-Host "    FAIL: $msg" -ForegroundColor Red }

# Resolve repo root: this script lives at scripts/setup_dev_hooks.ps1,
# so the repo root is one level up.
$RepoRoot = Split-Path (Split-Path $PSCommandPath -Parent) -Parent

Push-Location $RepoRoot
try {
    Write-Host ""
    Write-Host "Curator dev hooks setup (v1.7.74)" -ForegroundColor White
    Write-Host "Repo root: $RepoRoot"
    Write-Host ""

    # -----------------------------------------------------------------------
    # Step 1: git core.hooksPath -> .githooks
    # -----------------------------------------------------------------------
    Write-Step "Configuring git core.hooksPath..."

    $currentHooksPath = git config --get core.hooksPath 2>$null
    if ($currentHooksPath -eq ".githooks") {
        Write-Skip "core.hooksPath already set to .githooks"
    } else {
        if ($currentHooksPath) {
            Write-Warn "core.hooksPath was '$currentHooksPath'; updating to .githooks"
        }
        git config core.hooksPath .githooks
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "git config command failed"
            exit 1
        }
        Write-Ok "core.hooksPath set to .githooks"
    }

    # Verify the hooks exist
    if (-not (Test-Path ".githooks\pre-commit")) {
        Write-Fail ".githooks/pre-commit not found! Are you in the Curator repo?"
        exit 1
    }
    if (-not (Test-Path ".githooks\pre-push")) {
        Write-Fail ".githooks/pre-push not found! Are you in the Curator repo?"
        exit 1
    }
    Write-Ok "Pre-commit + pre-push hooks found and activated"

    # -----------------------------------------------------------------------
    # Step 2: PAT file at ~/.curator/github_pat
    # -----------------------------------------------------------------------
    if ($SkipPat) {
        Write-Step "Skipping PAT setup (--SkipPat)"
        Write-Skip "ci_diag.ps1 and pre-push hook will silently skip without a token"
    } else {
        Write-Step "Configuring GitHub PAT for CI tooling..."

        $patDir  = Join-Path $HOME ".curator"
        $patFile = Join-Path $patDir "github_pat"

        # Ensure ~/.curator/ exists
        if (-not (Test-Path $patDir)) {
            New-Item -Path $patDir -ItemType Directory -Force | Out-Null
            Write-Ok "Created $patDir"
        }

        # If PAT not provided as parameter, check if file already exists
        if ($Token -eq "" -and (Test-Path $patFile)) {
            $existingPat = (Get-Content $patFile -Raw -ErrorAction SilentlyContinue).Trim()
            if ($existingPat -match "^(github_pat_|ghp_)") {
                Write-Skip "PAT file already exists at $patFile (length=$($existingPat.Length))"
                $Token = ""  # don't overwrite
            } else {
                Write-Warn "PAT file exists but content doesn't look like a GitHub PAT"
                Write-Warn "Existing first 10 chars: $($existingPat.Substring(0, [Math]::Min(10, $existingPat.Length)))"
            }
        }

        # If no PAT yet, prompt
        if ($Token -eq "" -and -not (Test-Path $patFile)) {
            Write-Host ""
            Write-Host "    No PAT configured. To create one:" -ForegroundColor White
            Write-Host "      1. Visit https://github.com/settings/personal-access-tokens/new"
            Write-Host "      2. Select 'Fine-grained tokens', set expiration"
            Write-Host "      3. Repository access: 'Selected repositories' -> Curator"
            Write-Host "      4. Permissions -> Repository -> Actions: 'Read-only'"
            Write-Host "      5. Generate the token (starts with 'github_pat_...')"
            Write-Host ""
            $secureToken = Read-Host -AsSecureString "    Paste PAT (or press Enter to skip)"
            $Token = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
                [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
            )
        }

        # Save the PAT if we have one and the file doesn't already exist
        if ($Token -and $Token.Trim() -ne "") {
            $Token = $Token.Trim()
            if ($Token -notmatch "^(github_pat_|ghp_)") {
                Write-Fail "Provided token doesn't look like a GitHub PAT (must start with 'github_pat_' or 'ghp_')"
                exit 1
            }
            Set-Content -Path $patFile -Value $Token -NoNewline -Encoding ascii
            Write-Ok "PAT saved to $patFile"

            # Restrict file permissions on Windows (best-effort; ACLs are
            # complicated, but at least mark hidden)
            try {
                (Get-Item $patFile).Attributes = "Hidden"
                Write-Ok "PAT file marked hidden"
            } catch {
                Write-Warn "Couldn't mark PAT file as hidden (non-fatal)"
            }
        } elseif (-not (Test-Path $patFile)) {
            Write-Skip "No PAT provided; ci_diag.ps1 + pre-push hook will silently skip"
        }
    }

    # -----------------------------------------------------------------------
    # Step 3: Verify setup
    # -----------------------------------------------------------------------
    Write-Step "Verifying setup..."

    $hooksPath = git config --get core.hooksPath
    if ($hooksPath -eq ".githooks") {
        Write-Ok "core.hooksPath: $hooksPath"
    } else {
        Write-Fail "core.hooksPath is '$hooksPath' (expected '.githooks')"
        exit 1
    }

    if (Test-Path ".githooks\pre-commit") {
        Write-Ok "Pre-commit hook present (runs 3 lints: glyph, ORDER BY, ANSI regex)"
    }
    if (Test-Path ".githooks\pre-push") {
        Write-Ok "Pre-push hook present (warns when CI is red)"
    }

    if (-not $SkipPat) {
        $patFile = Join-Path $HOME ".curator\github_pat"
        if (Test-Path $patFile) {
            Write-Ok "PAT file present (used by ci_diag.ps1 and pre-push hook)"
        } else {
            Write-Skip "PAT file not present; ci_diag.ps1 needs -Token param or env var"
        }
    }

    # -----------------------------------------------------------------------
    # Done
    # -----------------------------------------------------------------------
    Write-Host ""
    Write-Host "Setup complete." -ForegroundColor Green
    Write-Host ""
    Write-Host "Quick reference:" -ForegroundColor White
    Write-Host "  CI status:       .\scripts\ci_diag.ps1 status"
    Write-Host "  Failing tests:   .\scripts\ci_diag.ps1 summary"
    Write-Host "  Bypass hook:     git commit --no-verify  /  git push --no-verify"
    Write-Host ""

} finally {
    Pop-Location
}
