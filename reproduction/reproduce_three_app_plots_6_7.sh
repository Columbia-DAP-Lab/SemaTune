#!/usr/bin/env bash
set -euo pipefail

# Canonical paper-numbered entry point. The delegated filename is retained so
# already-running queues and external scripts remain compatible.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/reproduce_three_app_plot12.sh" "$@"
