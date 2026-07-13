#!/usr/bin/env python3
"""Safely extract the explicitly accepted TailBench input archive."""

from __future__ import annotations

import argparse
import os
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


EXPECTED_SIZE = 10_230_769_002
TOP_LEVEL = "tailbench.inputs"


def validate_member(member: tarfile.TarInfo) -> None:
    path = PurePosixPath(member.name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe archive member: {member.name!r}")
    if path.parts[0] != TOP_LEVEL:
        raise ValueError(f"archive member is outside {TOP_LEVEL}/: {member.name!r}")
    if member.isdev() or member.isfifo():
        raise ValueError(f"unsupported special file: {member.name!r}")
    if member.issym() or member.islnk():
        target = PurePosixPath(member.linkname)
        resolved = path.parent.joinpath(target)
        if target.is_absolute() or ".." in resolved.parts:
            raise ValueError(f"unsafe archive link: {member.name!r} -> {member.linkname!r}")


def ready(data_root: Path) -> bool:
    return (
        (data_root / "sphinx" / "wav").is_dir()
        and (data_root / "xapian" / "terms.in").is_file()
        and (data_root / "xapian" / "wiki" / "iamchert").is_file()
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    archive_path = args.archive.resolve()
    data_root = args.data_root.resolve()

    if ready(data_root):
        print(f"TAILBENCH_INPUTS: already installed ({data_root})")
        return 0
    if archive_path.stat().st_size != EXPECTED_SIZE:
        raise ValueError(
            f"archive size {archive_path.stat().st_size} != locked size {EXPECTED_SIZE}"
        )
    if data_root.exists() and any(data_root.iterdir()):
        raise ValueError(f"refusing to replace non-empty input directory: {data_root}")

    data_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tailbench-inputs-", dir=data_root.parent) as tmp:
        staging_parent = Path(tmp)
        with tarfile.open(archive_path, mode="r|*") as archive:
            # Python 3.10 on the artifact host predates tarfile's filter=
            # argument. Validate and extract one member at a time into an
            # isolated staging directory, avoiding two scans of the 10 GB file.
            for member in archive:
                validate_member(member)
                archive.extract(member, staging_parent)
        extracted = staging_parent / TOP_LEVEL
        if not ready(extracted):
            raise ValueError("archive lacks the required Sphinx/Xapian input layout")
        if data_root.exists():
            data_root.rmdir()
        os.replace(extracted, data_root)

    print(f"TAILBENCH_INPUTS: PASS ({data_root})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
