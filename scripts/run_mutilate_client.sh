#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_ROOT/functional_example/mutilate.env"

[[ -r "$ENV_FILE" ]] || {
  echo "Missing $ENV_FILE; run scripts/setup.sh --memcached-client first." >&2
  exit 1
}
# shellcheck disable=SC1090
source "$ENV_FILE"
[[ "${SEMATUNE_MUTILATE_ROLE:-}" == client ]] || {
  echo "$ENV_FILE does not describe a memcached-client installation." >&2
  exit 1
}

export PYTHONPATH="$REPO_ROOT/src"
exec /usr/bin/python3 -m optimizer.benchmarks.mutilate_client \
  --server-host "${SEMATUNE_MUTILATE_SERVER_IP:?}" \
  --client-host "${SEMATUNE_MUTILATE_CLIENT_IP:?}" \
  --control-port "${SEMATUNE_MUTILATE_CONTROL_PORT:-19876}" \
  --mutilate-bin "${SEMATUNE_MUTILATE_BIN:?}"
