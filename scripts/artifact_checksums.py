#!/usr/bin/env python3
"""Generate or verify deterministic SHA256SUMS for an artifact payload."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Iterable, List, Tuple

CHUNK_SIZE = 8 * 1024 * 1024
DEFAULT_EXCLUDES = {".git", "SHA256SUMS", "TuxBot.pdf", "ARTIFACT_RELEASE_CHECKLIST.md"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_symlink():
        digest.update(b"SYMLINK\0")
        digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
        return digest.hexdigest()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def included_files(root: Path, excludes: set[str]) -> Iterable[Path]:
    for directory, names, filenames in os.walk(root):
        relative_directory = Path(directory).relative_to(root)
        retained_directories = []
        for name in sorted(names):
            relative = relative_directory / name
            if name in excludes or relative.as_posix() in excludes:
                continue
            path = root / relative
            if path.is_symlink():
                yield path
            else:
                retained_directories.append(name)
        names[:] = retained_directories
        for filename in sorted(filenames):
            relative = relative_directory / filename
            if filename in excludes or relative.as_posix() in excludes:
                continue
            path = root / relative
            if path.is_symlink() or path.is_file():
                yield path


def manifest_lines(root: Path, excludes: set[str]) -> List[str]:
    lines = []
    for path in included_files(root, excludes):
        relative = path.relative_to(root).as_posix()
        if "\n" in relative or "\r" in relative:
            raise ValueError(f"cannot represent newline in manifest path: {relative!r}")
        lines.append(f"{sha256(path)}  {relative}")
    return lines


def parse_manifest(path: Path) -> List[Tuple[str, str]]:
    entries = []
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line:
            continue
        if len(raw_line) < 67 or raw_line[64:66] != "  ":
            raise ValueError(f"{path}:{number}: invalid SHA256SUMS line")
        digest, relative = raw_line[:64], raw_line[66:]
        if any(character not in "0123456789abcdef" for character in digest):
            raise ValueError(f"{path}:{number}: invalid SHA-256")
        relative_path = Path(relative)
        if (
            not relative
            or relative_path.is_absolute()
            or any(part in ("", ".", "..") for part in relative_path.parts)
            or relative_path.as_posix() != relative
        ):
            raise ValueError(f"{path}:{number}: unsafe or non-canonical path")
        entries.append((digest, relative))
    return entries


def payload_path(root: Path, relative: str) -> Path:
    """Return a safe payload path without dereferencing its final symlink."""

    path = root / relative
    resolved_parent = path.parent.resolve()
    if resolved_parent != root and root not in resolved_parent.parents:
        raise ValueError(f"manifest path escapes payload root: {relative}")
    return resolved_parent / path.name


def command_generate(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    temporary = output.with_name(output.name + ".tmp")
    excludes = DEFAULT_EXCLUDES | set(args.exclude)
    for generated_path in (output, temporary):
        try:
            lexical_path = Path(os.path.abspath(generated_path))
            excludes.add(lexical_path.relative_to(root).as_posix())
        except ValueError:
            pass
    lines = manifest_lines(root, excludes)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write("\n".join(lines) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, output)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    print(f"wrote {output} with {len(lines)} entries")
    return 0


def command_verify(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    manifest = args.manifest if args.manifest.is_absolute() else root / args.manifest
    excludes = DEFAULT_EXCLUDES | set(args.exclude)
    try:
        manifest_relative = manifest.resolve().relative_to(root).as_posix()
    except ValueError:
        manifest_relative = None
    if manifest_relative:
        excludes.add(manifest_relative)

    entries = parse_manifest(manifest)
    recorded = {relative for _, relative in entries}
    if len(recorded) != len(entries):
        raise ValueError("duplicate manifest path")
    present = {
        path.relative_to(root).as_posix()
        for path in included_files(root, excludes)
    }
    missing = sorted(recorded - present)
    unrecorded = sorted(present - recorded)
    if missing or unrecorded:
        details = []
        if missing:
            details.append(f"{len(missing)} recorded paths are missing")
        if unrecorded:
            details.append(f"{len(unrecorded)} payload paths are unrecorded")
        raise ValueError("manifest does not cover the payload: " + "; ".join(details))

    failures = []
    for expected, relative in entries:
        path = payload_path(root, relative)
        try:
            actual = sha256(path)
            if actual != expected:
                raise ValueError(f"SHA-256 {actual} != {expected}")
            print(f"OK {relative}")
        except (OSError, ValueError) as exc:
            failures.append(relative)
            print(f"FAIL {relative}: {exc}", file=sys.stderr)
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate")
    generate.add_argument("--root", type=Path, default=Path.cwd())
    generate.add_argument("--output", type=Path, default=Path("SHA256SUMS"))
    generate.add_argument("--exclude", action="append", default=[])

    verify = subparsers.add_parser("verify")
    verify.add_argument("--root", type=Path, default=Path.cwd())
    verify.add_argument("--manifest", type=Path, default=Path("SHA256SUMS"))
    verify.add_argument("--exclude", action="append", default=[])
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "generate":
            return command_generate(args)
        if args.command == "verify":
            return command_verify(args)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
