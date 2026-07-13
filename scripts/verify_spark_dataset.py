#!/usr/bin/env python3
"""Verify the pinned DCPerf SparkBench Git/LFS dataset checkout."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path


COMMIT = "afbc2c250aebb0c18e65a685f2b5e454e7d0c03b"
TREE = "1446cd86ca4a9e7d7351b7724adb8a9a423470b5"
SUBPATH = "bpc_t93586_s2_synthetic"
OBJECTS = 979
BYTES = 109486252337
MANIFEST_SHA256 = "22f05c05f5e30f3b3edc0e0b861431bf5a551068a9db07fd5ac7e6bfc19d12eb"


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} DATASET_REPOSITORY", file=sys.stderr)
        return 2
    repo = Path(sys.argv[1]).resolve()
    if git(repo, "rev-parse", "HEAD") != COMMIT or git(repo, "rev-parse", "HEAD^{tree}") != TREE:
        raise ValueError("dataset Git commit/tree does not match artifact/downloads.lock.json")
    output = git(repo, "lfs", "ls-files", "-l")
    records = []
    for line in output.splitlines():
        oid, remainder = line.split(maxsplit=1)
        path = remainder[2:] if remainder[:2] in {"* ", "- "} else remainder
        if not path.startswith(SUBPATH + "/"):
            continue
        payload = repo / path
        if not payload.is_file():
            raise ValueError(f"missing LFS payload: {path}")
        size = payload.stat().st_size
        records.append((path, oid.lower(), size))
    if len(records) != OBJECTS or sum(item[2] for item in records) != BYTES:
        raise ValueError("dataset LFS object count/byte size does not match the lock")
    lines = [f"{path} {oid} {size}" for path, oid, size in sorted(records)]
    digest = hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest()
    if digest != MANIFEST_SHA256:
        raise ValueError(f"dataset LFS manifest SHA-256 {digest} != {MANIFEST_SHA256}")
    print(f"SPARK_DATASET_VERIFY: PASS ({OBJECTS} objects, {BYTES} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
