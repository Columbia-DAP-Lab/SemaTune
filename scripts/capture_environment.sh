#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="$REPO_ROOT/.venv-functional"

section() {
  printf '\n## %s\n' "$1"
}

version_or_missing() {
  local label="$1" output
  shift
  printf '%s: ' "$label"
  if command -v "$1" >/dev/null 2>&1; then
    if output="$("$@" 2>&1)"; then
      :
    fi
    printf '%s\n' "$output" | head -n 1
  else
    echo 'not installed'
  fi
}

echo '# SemaTune environment capture'
echo 'Identity, addresses, MACs, serial numbers, and credentials are omitted.'

section 'Operating system'
uname -srmo
if [[ -r /etc/os-release ]]; then
  grep -E '^(PRETTY_NAME|VERSION_ID)=' /etc/os-release
fi

section 'CPU topology'
lscpu | grep -E '^(Architecture|CPU\(s\)|On-line CPU\(s\) list|Vendor ID|Model name|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|CPU max MHz|CPU min MHz|NUMA node\(s\)|NUMA node[0-9]+ CPU\(s\)):'

section 'Memory and storage'
free -h
lsblk -d -o NAME,MODEL,SIZE,ROTA,TYPE,TRAN
df -hT "$REPO_ROOT"

section 'Network controller classes'
if command -v lspci >/dev/null 2>&1; then
  lspci -nn | grep -Ei 'ethernet|network' || true
else
  echo 'lspci: not installed'
fi

section 'Software versions'
if [[ -x "$VENV/bin/python" ]]; then
  "$VENV/bin/python" --version
  "$VENV/bin/python" -m pip --version
else
  echo 'Artifact Python environment: not installed'
fi
version_or_missing 'Java' java -version
version_or_missing 'PostgreSQL' psql --version
version_or_missing 'Sysbench' sysbench --version
version_or_missing 'perf' perf --version

section 'Pinned dependency commits'
git -C "$REPO_ROOT" submodule status

section 'Artifact footprints'
for path in .venv-functional deps/benchbase all_results/paper_evaluation; do
  if [[ -e "$REPO_ROOT/$path" ]]; then
    du -sh "$REPO_ROOT/$path" | awk -v prefix="$REPO_ROOT/" '{sub(prefix, "", $2); print $1, $2}'
  fi
done
