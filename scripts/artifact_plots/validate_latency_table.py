#!/usr/bin/env python3
"""Validate the reused latency samples against tab:latency_by_params."""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path


EXPECTED = {
    1: (8.0758, 1.0183, 0.4900),
    2: (13.5308, 1.0114, 0.6300),
    4: (10.0658, 1.3367, 0.7300),
    8: (9.7104, 1.2226, 2.4200),
    16: (11.5330, 1.2675, 4.3600),
    32: (15.8032, 2.3639, 4.0500),
    41: (12.2178, 2.5543, 5.7300),
}


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_latency_table.py LATENCY.csv", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    with path.open(newline="") as handle:
        data = {int(row["n_params"]): row for row in csv.DictReader(handle)}
    errors = []
    for count, expected in EXPECTED.items():
        row = data.get(count)
        if row is None:
            errors.append(f"missing {count}-parameter row")
            continue
        actual = tuple(float(row[key]) for key in ("actor_s", "speculator_s", "mlos_s"))
        if any(not math.isclose(a, e, abs_tol=0.0001) for a, e in zip(actual, expected)):
            errors.append(f"{count}-parameter values differ: {actual} != {expected}")
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("PASS: reused latency table matches tab:latency_by_params")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
