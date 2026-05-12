#!/usr/bin/env bash
# ============================================================================
# scripts/ci_diag.sh   (v1.7.78)
#
# CI diagnostic helper -- one-command access to the latest GitHub Actions run.
# Bash variant of ci_diag.ps1 (v1.7.65). Functionally equivalent: same modes,
# same token-discovery, same output style. Implementation uses host-shell
# idioms (curl, jq, awk, sed) instead of PowerShell cmdlets.
#
# Three modes:
#   ./scripts/ci_diag.sh status              # Show all 9 cells' pass/fail
#   ./scripts/ci_diag.sh logs <name-pattern> # Download log for failing cell
#   ./scripts/ci_diag.sh summary             # Failing tests across all cells
#
# Token discovery (in priority order):
#   1. $GH_TOKEN env var
#   2. $GITHUB_TOKEN env var
#   3. Stored at ~/.curator/github_pat (single-line file, chmod 600)
#
# Dependencies: curl + jq. Both ubiquitous on modern Linux/macOS; included
# in Git for Windows' bundled MINGW64 environment. If jq is missing, the
# script falls back to Python for JSON parsing (same fallback chain as
# .githooks/pre-push).
#
# Scope: read-only Actions API access. Public repo KULawHawk/Curator.
# ============================================================================

set -e

# ----------------------------------------------------------------------------
# Defaults + argument parsing
# ----------------------------------------------------------------------------

REPO="KULawHawk/Curator"
OUT_DIR="$HOME/.curator/logs"
MODE="status"
NAME_PATTERN=""

# First positional arg = mode; second = name pattern
if [ $# -gt 0 ]; then
    case "$1" in
        status|logs|summary)
            MODE="$1"
            shift
            ;;
        --help|-h)
            sed -n '4,22p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown mode: $1" >&2
            echo "Usage: $0 {status|logs <pattern>|summary} [--help]" >&2
            exit 1
            ;;
    esac
fi

if [ $# -gt 0 ]; then
    NAME_PATTERN="$1"
fi

# ----------------------------------------------------------------------------
# Color helpers (TTY-aware)
# ----------------------------------------------------------------------------

if [ -t 1 ]; then
    C_CYAN="\033[36m"
    C_GREEN="\033[32m"
    C_YELLOW="\033[33m"
    C_RED="\033[31m"
    C_RESET="\033[0m"
else
    C_CYAN=""
    C_GREEN=""
    C_YELLOW=""
    C_RED=""
    C_RESET=""
fi

# ----------------------------------------------------------------------------
# Token discovery
# ----------------------------------------------------------------------------

GH_TOKEN_VALUE=""
if [ -n "$GH_TOKEN" ]; then
    GH_TOKEN_VALUE="$GH_TOKEN"
elif [ -n "$GITHUB_TOKEN" ]; then
    GH_TOKEN_VALUE="$GITHUB_TOKEN"
elif [ -f "$HOME/.curator/github_pat" ]; then
    GH_TOKEN_VALUE=$(tr -d '\n\r ' < "$HOME/.curator/github_pat")
fi

if [ -z "$GH_TOKEN_VALUE" ]; then
    printf "${C_RED}No GitHub token found.${C_RESET}\n" >&2
    echo "Set \$GH_TOKEN, or store at ~/.curator/github_pat (single line, chmod 600)." >&2
    exit 1
fi

# ----------------------------------------------------------------------------
# JSON parsing: prefer jq, fall back to Python
# ----------------------------------------------------------------------------

if command -v jq >/dev/null 2>&1; then
    JSON_PARSER="jq"
elif command -v python3 >/dev/null 2>&1; then
    JSON_PARSER="python3"
elif command -v python >/dev/null 2>&1; then
    JSON_PARSER="python"
else
    printf "${C_RED}No jq or python found for JSON parsing.${C_RESET}\n" >&2
    echo "Install jq (brew install jq / apt install jq) or ensure python is on PATH." >&2
    exit 1
fi

# Run a jq-style query against stdin. Uses jq if available; otherwise Python.
json_query() {
    local query="$1"
    if [ "$JSON_PARSER" = "jq" ]; then
        jq -r "$query"
    else
        # Translate a minimal subset of jq syntax to Python. This handles the
        # specific queries this script uses; not a general jq replacement.
        $JSON_PARSER -c "
import json, sys
d = json.load(sys.stdin)
q = '''$query'''

def get_path(obj, path):
    for k in path:
        if k.isdigit():
            obj = obj[int(k)]
        else:
            obj = obj.get(k, '')
    return obj

if q == '.workflow_runs[0].id':
    print(d['workflow_runs'][0]['id'])
elif q == '.workflow_runs[0].head_sha':
    print(d['workflow_runs'][0]['head_sha'])
elif q == '.workflow_runs[0].display_title':
    print(d['workflow_runs'][0].get('display_title', ''))
elif q == '.workflow_runs[0].status':
    print(d['workflow_runs'][0].get('status', ''))
elif q == '.workflow_runs[0].conclusion':
    print(d['workflow_runs'][0].get('conclusion') or '')
elif q == '.workflow_runs[0].html_url':
    print(d['workflow_runs'][0].get('html_url', ''))
elif q == '.jobs[] | .name + \"\\t\" + .status + \"\\t\" + (.conclusion // \"\") + \"\\t\" + (.id|tostring)':
    for j in d['jobs']:
        print(j['name'] + '\t' + j['status'] + '\t' + (j.get('conclusion') or '') + '\t' + str(j['id']))
else:
    print('UNSUPPORTED_QUERY: ' + q, file=sys.stderr)
    sys.exit(2)
"
    fi
}

# ----------------------------------------------------------------------------
# API helpers
# ----------------------------------------------------------------------------

api_get() {
    local url="$1"
    curl -sf \
        -H "Accept: application/vnd.github+json" \
        -H "Authorization: Bearer $GH_TOKEN_VALUE" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        -H "User-Agent: Curator-CIDiag-Bash/v1.7.78" \
        "$url"
}

api_download() {
    local url="$1"
    local out_path="$2"
    curl -sfL \
        -H "Accept: application/vnd.github+json" \
        -H "Authorization: Bearer $GH_TOKEN_VALUE" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        -H "User-Agent: Curator-CIDiag-Bash/v1.7.78" \
        -o "$out_path" \
        "$url"
}

get_latest_run_json() {
    api_get "https://api.github.com/repos/$REPO/actions/runs?per_page=1"
}

get_run_jobs_json() {
    local run_id="$1"
    api_get "https://api.github.com/repos/$REPO/actions/runs/$run_id/jobs"
}

# ----------------------------------------------------------------------------
# Mode: status
# ----------------------------------------------------------------------------

show_status() {
    local run_json
    run_json=$(get_latest_run_json)

    local run_id title sha status conclusion html_url short_sha
    run_id=$(printf '%s' "$run_json" | json_query '.workflow_runs[0].id')
    title=$(printf '%s' "$run_json" | json_query '.workflow_runs[0].display_title')
    sha=$(printf '%s' "$run_json" | json_query '.workflow_runs[0].head_sha')
    status=$(printf '%s' "$run_json" | json_query '.workflow_runs[0].status')
    conclusion=$(printf '%s' "$run_json" | json_query '.workflow_runs[0].conclusion')
    html_url=$(printf '%s' "$run_json" | json_query '.workflow_runs[0].html_url')
    short_sha=$(printf '%s' "$sha" | cut -c1-7)

    echo
    printf "${C_CYAN}=== Latest run: %s ===${C_RESET}\n" "$title"
    printf "SHA:    %s\n" "$short_sha"
    printf "Status: %s / %s\n" "$status" "$conclusion"
    printf "URL:    %s\n" "$html_url"
    echo

    local jobs_json
    jobs_json=$(get_run_jobs_json "$run_id")

    local success=0 failure=0 running=0
    local jobs_data
    jobs_data=$(printf '%s' "$jobs_json" | json_query '.jobs[] | .name + "\t" + .status + "\t" + (.conclusion // "") + "\t" + (.id|tostring)')

    # Sort by name and print
    while IFS=$'\t' read -r name jstatus jconc jid; do
        case "$jconc" in
            success) marker="[OK]  "; color="$C_GREEN"; success=$((success+1)) ;;
            failure) marker="[FAIL]"; color="$C_RED"; failure=$((failure+1)) ;;
            *)
                if [ "$jstatus" = "in_progress" ]; then
                    marker="..."
                    running=$((running+1))
                elif [ "$jstatus" = "queued" ]; then
                    marker="?  "
                    running=$((running+1))
                else
                    marker="?  "
                fi
                color="$C_YELLOW"
                ;;
        esac
        printf "${color}%s %-50s %-13s %s${C_RESET}\n" "$marker" "$name" "$jstatus" "$jconc"
    done <<< "$(printf '%s\n' "$jobs_data" | sort)"

    echo
    printf "${C_CYAN}=== TALLY: success=%d | failure=%d | running/queued=%d ===${C_RESET}\n" \
        "$success" "$failure" "$running"
}

# ----------------------------------------------------------------------------
# Mode: logs
# ----------------------------------------------------------------------------

get_failing_logs() {
    local pattern="$1"
    local run_json run_id sha short_sha
    run_json=$(get_latest_run_json)
    run_id=$(printf '%s' "$run_json" | json_query '.workflow_runs[0].id')
    sha=$(printf '%s' "$run_json" | json_query '.workflow_runs[0].head_sha')
    short_sha=$(printf '%s' "$sha" | cut -c1-7)

    local jobs_json jobs_data
    jobs_json=$(get_run_jobs_json "$run_id")
    jobs_data=$(printf '%s' "$jobs_json" | json_query '.jobs[] | .name + "\t" + .status + "\t" + (.conclusion // "") + "\t" + (.id|tostring)')

    mkdir -p "$OUT_DIR"

    local count=0
    local downloaded=0
    while IFS=$'\t' read -r name jstatus jconc jid; do
        if [ "$jconc" != "failure" ]; then continue; fi
        if [ -n "$pattern" ] && ! printf '%s' "$name" | grep -qE "$pattern"; then continue; fi
        count=$((count+1))
    done <<< "$jobs_data"

    if [ "$count" = "0" ]; then
        printf "${C_YELLOW}No failing jobs match pattern '%s' in run %s.${C_RESET}\n" "$pattern" "$short_sha"
        return
    fi

    printf "${C_CYAN}=== Downloading %d failing job log(s) ===${C_RESET}\n" "$count"
    while IFS=$'\t' read -r name jstatus jconc jid; do
        if [ "$jconc" != "failure" ]; then continue; fi
        if [ -n "$pattern" ] && ! printf '%s' "$name" | grep -qE "$pattern"; then continue; fi
        local safe_name out_path
        safe_name=$(printf '%s' "$name" | tr -c 'a-zA-Z0-9' '_')
        out_path="$OUT_DIR/ci_${short_sha}_${safe_name}.log"
        if api_download "https://api.github.com/repos/$REPO/actions/jobs/$jid/logs" "$out_path"; then
            local size
            size=$(wc -c < "$out_path")
            printf "  ${C_GREEN}[OK]${C_RESET} %s -> %s (%s bytes)\n" "$name" "$out_path" "$size"
            downloaded=$((downloaded+1))
        else
            printf "  ${C_RED}[FAIL]${C_RESET} %s\n" "$name"
        fi
    done <<< "$jobs_data"
}

# ----------------------------------------------------------------------------
# Mode: summary
# ----------------------------------------------------------------------------

show_failing_summary() {
    local run_json run_id sha short_sha
    run_json=$(get_latest_run_json)
    run_id=$(printf '%s' "$run_json" | json_query '.workflow_runs[0].id')
    sha=$(printf '%s' "$run_json" | json_query '.workflow_runs[0].head_sha')
    short_sha=$(printf '%s' "$sha" | cut -c1-7)

    local jobs_json jobs_data
    jobs_json=$(get_run_jobs_json "$run_id")
    jobs_data=$(printf '%s' "$jobs_json" | json_query '.jobs[] | .name + "\t" + .status + "\t" + (.conclusion // "") + "\t" + (.id|tostring)')

    local failing_count=0
    while IFS=$'\t' read -r name jstatus jconc jid; do
        if [ "$jconc" = "failure" ]; then failing_count=$((failing_count+1)); fi
    done <<< "$jobs_data"

    if [ "$failing_count" = "0" ]; then
        printf "${C_GREEN}All jobs passing in run %s. Nothing to summarize.${C_RESET}\n" "$short_sha"
        return
    fi

    mkdir -p "$OUT_DIR"

    printf "${C_CYAN}=== Failing tests across %d cell(s) ===${C_RESET}\n" "$failing_count"
    while IFS=$'\t' read -r name jstatus jconc jid; do
        if [ "$jconc" != "failure" ]; then continue; fi
        local safe_name out_path
        safe_name=$(printf '%s' "$name" | tr -c 'a-zA-Z0-9' '_')
        out_path="$OUT_DIR/ci_${short_sha}_${safe_name}.log"
        if [ ! -f "$out_path" ]; then
            api_download "https://api.github.com/repos/$REPO/actions/jobs/$jid/logs" "$out_path" 2>/dev/null || continue
        fi
        echo
        printf "${C_YELLOW}--- %s ---${C_RESET}\n" "$name"
        # Print FAILED lines
        grep -E "^FAILED tests/" "$out_path" 2>/dev/null | while read -r line; do
            # Strip leading timestamp like "2026-05-12T..."
            line=$(printf '%s' "$line" | sed -E 's/^[0-9]+-[0-9]+-[0-9]+T[^ ]+ //')
            echo "  ${line:0:180}"
        done
        # Print test summary line
        local summary
        summary=$(grep -E "passed.*failed|failed.*passed" "$out_path" 2>/dev/null | head -1)
        if [ -n "$summary" ]; then
            summary=$(printf '%s' "$summary" | sed -E 's/^[0-9]+-[0-9]+-[0-9]+T[^ ]+ //')
            printf "  ${C_CYAN}SUMMARY: %s${C_RESET}\n" "$summary"
        fi
    done <<< "$jobs_data"
}

# ----------------------------------------------------------------------------
# Main dispatch
# ----------------------------------------------------------------------------

case "$MODE" in
    status)  show_status ;;
    logs)    get_failing_logs "$NAME_PATTERN" ;;
    summary) show_failing_summary ;;
    *)       show_status ;;
esac
