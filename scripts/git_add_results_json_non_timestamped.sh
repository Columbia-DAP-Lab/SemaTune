#!/usr/bin/env bash

set -euo pipefail

BRANCH_NAME="results"
DRY_RUN=0
ALLOW_EXISTING_BRANCH=0

usage() {
    cat <<'EOF'
Usage:
  scripts/git_add_results_json_non_timestamped.sh [options]

Stages history JSON files (`optimization_history*.json` and `dual_loop*.json`)
under top-level result/percentile directories, including timestamped ones.
Uses forced staging (`git add -f`) so matching files are added even if ignored.

By default, it also ensures work is on branch "results" created from the
current branch HEAD.

Options:
  -b, --branch NAME            Branch name to use (default: results)
  -n, --dry-run                Print actions/files without executing git add/switch
      --allow-existing-branch  Allow switching to an existing branch even if it
                               does not point at current HEAD
  -h, --help                   Show this help

Example:
  scripts/git_add_results_json_non_timestamped.sh
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -b|--branch)
            if [[ $# -lt 2 ]]; then
                echo "Error: missing value for $1" >&2
                exit 1
            fi
            BRANCH_NAME="$2"
            shift 2
            ;;
        -n|--dry-run)
            DRY_RUN=1
            shift
            ;;
        --allow-existing-branch)
            ALLOW_EXISTING_BRANCH=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Error: unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if ! git rev-parse --show-toplevel >/dev/null 2>&1; then
    echo "Error: not inside a git repository." >&2
    exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
CURRENT_HEAD="$(git rev-parse --verify HEAD)"

if [[ "$CURRENT_BRANCH" != "$BRANCH_NAME" ]]; then
    if git show-ref --verify --quiet "refs/heads/$BRANCH_NAME"; then
        EXISTING_BRANCH_HEAD="$(git rev-parse --verify "$BRANCH_NAME")"
        if [[ "$EXISTING_BRANCH_HEAD" != "$CURRENT_HEAD" && "$ALLOW_EXISTING_BRANCH" -ne 1 ]]; then
            echo "Error: branch '$BRANCH_NAME' already exists and is not at current HEAD." >&2
            echo "Current HEAD: $CURRENT_HEAD" >&2
            echo "Branch HEAD : $EXISTING_BRANCH_HEAD" >&2
            echo "Refusing to switch because '$BRANCH_NAME' would not branch off current branch." >&2
            echo "Use --allow-existing-branch to override." >&2
            exit 1
        fi

        if [[ "$DRY_RUN" -eq 1 ]]; then
            echo "[dry-run] git switch $BRANCH_NAME"
        else
            git switch "$BRANCH_NAME"
        fi
    else
        if [[ "$DRY_RUN" -eq 1 ]]; then
            echo "[dry-run] git switch -c $BRANCH_NAME"
        else
            git switch -c "$BRANCH_NAME"
        fi
    fi
fi

mapfile -t TARGET_DIRS < <(
    find . -maxdepth 1 -mindepth 1 -type d -printf '%P\n' \
    | sort \
    | while IFS= read -r d; do
        # Include:
        # 1) any top-level results dir (results, results_*, etc.)
        # 2) percentile-named dirs like *_hi_p99, *_p95_*
        if [[ "$d" == results* || "$d" =~ (^|_)p[0-9]{2}($|_) ]]; then
            printf '%s\n' "$d"
        fi
      done
)

if [[ "${#TARGET_DIRS[@]}" -eq 0 ]]; then
    echo "No matching results/percentile directories found."
    exit 0
fi

TMP_FILES="$(mktemp)"
trap 'rm -f "$TMP_FILES"' EXIT

for d in "${TARGET_DIRS[@]}"; do
    find "$d" -type f -name 'optimization_history*.json' -print0 >> "$TMP_FILES"
    find "$d" -type f -name 'dual_loop*.json' -print0 >> "$TMP_FILES"
done

FILE_COUNT="$(tr -cd '\0' < "$TMP_FILES" | wc -c | tr -d ' ')"

echo "Target directories (${#TARGET_DIRS[@]}):"
for d in "${TARGET_DIRS[@]}"; do
    echo "  $d"
done
echo "JSON files matched: $FILE_COUNT"

if [[ "$FILE_COUNT" -eq 0 ]]; then
    echo "Nothing to stage."
    exit 0
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] Files that would be force-staged (git add -f):"
    while IFS= read -r -d '' f; do
        echo "  $f"
    done < "$TMP_FILES"
else
    xargs -0 -r git add -f -- < "$TMP_FILES"
    echo "Force-staged optimization_history*.json and dual_loop*.json files from matching directories."
fi
