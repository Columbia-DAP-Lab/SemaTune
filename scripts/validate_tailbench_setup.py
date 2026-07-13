#!/usr/bin/env python3
"""Validate the four TailBench workloads used by the SemaTune artifact."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


BINARIES = {
    "masstree": Path("masstree/mttest_integrated"),
    "silo": Path("silo/out-perf.masstree/benchmarks/dbtest_integrated"),
    "sphinx": Path("sphinx/decoder_integrated"),
    "xapian": Path("xapian/xapian_integrated"),
}


def require_file(path: Path, description: str, *, executable: bool = False) -> None:
    if not path.is_file():
        raise ValueError(f"missing {description}: {path}")
    if executable and not os.access(path, os.X_OK):
        raise ValueError(f"{description} is not executable: {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tailbench-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--require-inputs", action="store_true")
    args = parser.parse_args()
    root = args.tailbench_root.resolve()
    data = args.data_root.resolve()

    for workload, relative in BINARIES.items():
        require_file(root / relative, f"{workload} integrated binary", executable=True)
    require_file(root / "harness/client.o", "TailBench client harness")
    require_file(root / "harness/tbench_server_integrated.o", "TailBench integrated server harness")
    require_file(
        root / "sphinx/sphinx-install/lib/libsphinxbase.so",
        "local SphinxBase library",
    )
    require_file(
        root / "sphinx/sphinx-install/lib/libpocketsphinx.so",
        "local PocketSphinx library",
    )
    require_file(
        root / "sphinx/sphinx-install/lib/pkgconfig/sphinxbase.pc",
        "local SphinxBase pkg-config metadata",
    )
    require_file(
        root / "sphinx/sphinx-install/lib/pkgconfig/pocketsphinx.pc",
        "local PocketSphinx pkg-config metadata",
    )
    require_file(
        root / "xapian/xapian-core-1.2.13/install/bin/xapian-config",
        "local Xapian configuration tool",
        executable=True,
    )
    require_file(
        root / "xapian/xapian-core-1.2.13/install/lib/libxapian.so.22",
        "local Xapian runtime library",
    )

    for workload, relative in BINARIES.items():
        linked = subprocess.run(
            ["ldd", str(root / relative)], check=True, text=True, capture_output=True
        ).stdout
        if "not found" in linked:
            raise ValueError(
                f"{workload} has unresolved shared libraries:\n{linked}"
            )

    if args.require_inputs:
        if not (data / "sphinx/wav").is_dir():
            raise ValueError(f"missing Sphinx corpus: {data / 'sphinx/wav'}")
        require_file(data / "xapian/terms.in", "Xapian terms")
        require_file(data / "xapian/wiki/iamchert", "Xapian Chert database marker")

    print("TAILBENCH_VALIDATE: PASS")
    for workload, relative in BINARIES.items():
        print(f"  {workload}: {root / relative}")
    print(f"  inputs: {'PASS' if args.require_inputs else 'not requested'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
