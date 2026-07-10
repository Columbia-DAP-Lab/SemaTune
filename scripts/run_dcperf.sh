#!/usr/bin/env bash
# Run DCPerf benchpress CLI with console output visible.
# Usage: ./scripts/run_dcperf.sh [install|run] <job_name> [benchpress args...]
#   e.g.  ./scripts/run_dcperf.sh install django_workload_default
#         ./scripts/run_dcperf.sh run django_workload_default -r standalone
#         ./scripts/run_dcperf.sh run django_workload_default -r db
#         ./scripts/run_dcperf.sh run django_workload_default -r clientserver -i '{"db_addr":"host:port"}'
set -e
DCPERF_ROOT="${DCPERF_ROOT:-/mydata/os-param-tuning/deps/DCPerf}"
cd "$DCPERF_ROOT"
# Force benchpress to log at INFO to console (default is file-only)
exec sudo python3 -c "
import sys
sys.path.insert(0, '.')
import logging
import benchpress.logging_config
benchpress.logging_config.create_logger()
# Ensure stream handler shows INFO (default in repo is WARNING)
for h in logging.getLogger().handlers:
    if getattr(h, 'stream', None) is not None:
        h.setLevel(logging.INFO)
        break
sys.argv = ['benchpress'] + sys.argv[1:]
from benchpress.cli.main import main
main()
" "$@"
