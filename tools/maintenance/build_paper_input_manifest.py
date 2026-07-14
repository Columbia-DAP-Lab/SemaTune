#!/usr/bin/env python3
"""Generate or strictly verify the archived paper-input SHA-256 manifest."""

from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = ROOT / "all_results" / "paper_evaluation"
MANIFEST = ROOT / "artifact" / "paper_plot_inputs.sha256"
CHUNK_SIZE = 8 * 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_text() -> str:
    lines = []
    for path in sorted(candidate for candidate in DATA_ROOT.rglob("*") if candidate.is_file()):
        relative = path.relative_to(ROOT).as_posix()
        lines.append(f"{sha256(path)}  {relative}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail unless the manifest exactly covers and matches the retained files.",
    )
    args = parser.parse_args()
    expected = expected_text()
    if args.check:
        actual = MANIFEST.read_text(encoding="utf-8")
        if actual != expected:
            raise SystemExit("PAPER_INPUT_MANIFEST: FAIL: manifest is stale or incomplete")
        print(f"PAPER_INPUT_MANIFEST: PASS ({expected.count(chr(10))} files)")
        return 0

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=MANIFEST.parent,
            prefix=f".{MANIFEST.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(expected)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, MANIFEST)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    print(f"wrote {MANIFEST} with {expected.count(chr(10))} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
