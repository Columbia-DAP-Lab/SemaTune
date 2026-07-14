#!/usr/bin/env bash
set -euo pipefail

# Paper-numbered entry point; keep the delegated path for active queue compatibility.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/finish_c125_queue.sh" "$@"
