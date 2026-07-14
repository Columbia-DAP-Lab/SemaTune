#!/usr/bin/env bash

set -euo pipefail
script_dir="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
manifest="$repo_root/artifact/paper_plot_inputs.sha256"

if [[ ! -f "$manifest" ]]; then
  echo "Missing paper-input checksum manifest: $manifest" >&2
  exit 2
fi

python3 "$repo_root/tools/maintenance/build_paper_input_manifest.py" --check
