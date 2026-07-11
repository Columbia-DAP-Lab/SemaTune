#!/usr/bin/env bash

set -euo pipefail
script_dir="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
manifest="$repo_root/artifact/paper_plot_inputs.sha256"

if [[ ! -f "$manifest" ]]; then
  echo "Missing paper-input checksum manifest: $manifest" >&2
  exit 2
fi

cd "$repo_root"
sha256sum --check --quiet "$manifest"
echo "PASS: paper plot input checksums verified"
