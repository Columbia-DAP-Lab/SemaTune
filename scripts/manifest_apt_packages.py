#!/usr/bin/env python3
"""Create or verify a SHA-256 manifest for downloaded Ubuntu ``.deb`` files.

Resolve/download the requested packages and their dependency closure in a
clean Ubuntu 22.04 environment configured for the snapshot in
``artifact/apt-packages.in.json``.  This script then records the exact package
metadata and payload digest without trusting filenames.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO_ROOT / "artifact" / "apt-packages.in.json"
DEFAULT_OUTPUT = REPO_ROOT / "artifact" / "apt-packages.lock.json"
CHUNK_SIZE = 8 * 1024 * 1024


def load_json(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("schema_version") != 1:
        raise ValueError(f"unsupported schema in {path}")
    return payload


def load_spec(path: Path) -> Dict[str, Any]:
    spec = load_json(path)
    for field in (
        "snapshot_id",
        "snapshot_url",
        "release",
        "architecture",
        "suites",
        "components",
        "direct_packages",
    ):
        if field not in spec:
            raise ValueError(f"package specification is missing {field!r}")
    if not str(spec["snapshot_url"]).startswith("https://"):
        raise ValueError("snapshot URL must use HTTPS")
    if not isinstance(spec["suites"], list) or not spec["suites"]:
        raise ValueError("package specification has no Ubuntu suites")
    if not isinstance(spec["components"], list) or not spec["components"]:
        raise ValueError("package specification has no Ubuntu components")
    if not isinstance(spec["direct_packages"], list) or not spec["direct_packages"]:
        raise ValueError("package specification has no direct packages")
    requested = set()
    for item in spec["direct_packages"]:
        key = (item.get("name"), item.get("version"))
        if None in key or key in requested:
            raise ValueError(f"missing or duplicate direct package: {key}")
        requested.add(key)
    return spec


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def deb_fields(path: Path) -> Dict[str, str]:
    values = {}
    for field in ("Package", "Version", "Architecture"):
        result = subprocess.run(
            ["dpkg-deb", "--field", str(path), field],
            check=True,
            text=True,
            capture_output=True,
        )
        values[field.lower()] = result.stdout.strip()
    return {
        "package": values["package"],
        "version": values["version"],
        "architecture": values["architecture"],
    }


def iter_debs(directory: Path) -> Iterable[Path]:
    root = directory.resolve()
    for path in sorted(directory.rglob("*.deb")):
        if path.is_symlink():
            raise ValueError(f"symbolic-link .deb is not allowed: {path}")
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved.parent != root and root not in resolved.parents:
            raise ValueError(f".deb path escapes download directory: {path}")
        yield path


def package_entries(directory: Path) -> List[Dict[str, Any]]:
    entries = []
    for path in iter_debs(directory):
        metadata = deb_fields(path)
        entries.append(
            {
                **metadata,
                "filename": path.relative_to(directory).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    if not entries:
        raise ValueError(f"no .deb files found under {directory}")
    return entries


def validate_direct_packages(spec: Dict[str, Any], entries: List[Dict[str, Any]]) -> None:
    target_architecture = str(spec["architecture"])
    allowed_architectures = {target_architecture, "all"}
    missing = []
    for requested in spec.get("direct_packages", []):
        key = (requested["name"], requested["version"])
        package_matches = [
            item
            for item in entries
            if (item["package"], item["version"]) == key
            and item["architecture"] in allowed_architectures
        ]
        if not package_matches:
            missing.append(f"{key[0]}={key[1]}")
            continue
        if requested.get("sha256"):
            matches = [
                item
                for item in package_matches
                if item["sha256"] == requested["sha256"]
                and item["size"] == requested["size"]
            ]
            if not matches:
                raise ValueError(f"publisher checksum mismatch for {key[0]}={key[1]}")
    if missing:
        raise ValueError("download directory lacks direct packages: " + ", ".join(missing))


def validate_architectures(spec: Dict[str, Any], entries: List[Dict[str, Any]]) -> None:
    allowed = {str(spec["architecture"]), "all"}
    unexpected = sorted({item["architecture"] for item in entries} - allowed)
    if unexpected:
        raise ValueError("unexpected package architectures: " + ", ".join(unexpected))


def validate_lock_packages(lock: Dict[str, Any]) -> List[Dict[str, Any]]:
    entries = lock.get("packages", [])
    if not isinstance(entries, list) or not entries:
        raise ValueError("package lock contains no packages")
    if lock.get("package_count") != len(entries):
        raise ValueError("package_count does not match package entries")

    filenames = set()
    identities = set()
    for item in entries:
        filename = str(item.get("filename", ""))
        relative = Path(filename)
        if (
            not filename
            or relative.is_absolute()
            or any(part in ("", ".", "..") for part in relative.parts)
            or relative.as_posix() != filename
        ):
            raise ValueError(f"unsafe or non-canonical package filename: {filename!r}")
        if filename in filenames:
            raise ValueError(f"duplicate package filename: {filename}")
        filenames.add(filename)

        identity = (item.get("package"), item.get("version"), item.get("architecture"))
        if None in identity or identity in identities:
            raise ValueError(f"missing or duplicate package identity: {identity}")
        identities.add(identity)

        digest = str(item.get("sha256", ""))
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError(f"invalid SHA-256 for {filename}")
        if not isinstance(item.get("size"), int) or item["size"] < 0:
            raise ValueError(f"invalid size for {filename}")
    return entries


def validate_lock_context(
    spec: Dict[str, Any],
    lock: Dict[str, Any],
    *,
    input_sha256: str,
) -> None:
    for field in (
        "snapshot_id",
        "snapshot_url",
        "release",
        "architecture",
        "suites",
        "components",
    ):
        if lock.get(field) != spec.get(field):
            raise ValueError(f"package lock {field} does not match input specification")
    if lock.get("input_sha256") != input_sha256:
        raise ValueError("package lock was generated from a different input specification")


def write_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def command_create(args: argparse.Namespace) -> int:
    spec = load_spec(args.input)
    entries = package_entries(args.deb_dir)
    validate_architectures(spec, entries)
    validate_direct_packages(spec, entries)
    payload = {
        "schema_version": 1,
        "snapshot_id": spec["snapshot_id"],
        "snapshot_url": spec["snapshot_url"],
        "release": spec["release"],
        "architecture": spec["architecture"],
        "suites": spec["suites"],
        "components": spec["components"],
        "input_sha256": sha256(args.input),
        "package_count": len(entries),
        "packages": entries,
    }
    write_atomic(args.output, payload)
    print(f"wrote {args.output} with {len(entries)} packages")
    return 0


def command_verify(args: argparse.Namespace) -> int:
    spec = load_spec(args.input)
    lock = load_json(args.lock)
    entries = validate_lock_packages(lock)
    validate_lock_context(spec, lock, input_sha256=sha256(args.input))
    validate_architectures(spec, entries)
    validate_direct_packages(spec, entries)
    expected_filenames = {item["filename"] for item in entries}
    actual_filenames = {
        path.relative_to(args.deb_dir).as_posix()
        for path in iter_debs(args.deb_dir)
    }
    failures = []
    missing = sorted(expected_filenames - actual_filenames)
    unrecorded = sorted(actual_filenames - expected_filenames)
    if missing:
        failures.extend(missing)
        print(f"FAIL package directory is missing {len(missing)} locked .deb files", file=sys.stderr)
    if unrecorded:
        failures.extend(unrecorded)
        print(f"FAIL package directory has {len(unrecorded)} unrecorded .deb files", file=sys.stderr)

    for item in entries:
        path = args.deb_dir / item["filename"]
        try:
            if path.stat().st_size != item["size"]:
                raise ValueError("size mismatch")
            if sha256(path) != item["sha256"]:
                raise ValueError("SHA-256 mismatch")
            metadata = deb_fields(path)
            for key in ("package", "version", "architecture"):
                if metadata[key] != item[key]:
                    raise ValueError(f"{key} mismatch")
            print(f"OK {item['package']}={item['version']} ({item['architecture']})")
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            failures.append(str(path))
            print(f"FAIL {path}: {exc}", file=sys.stderr)
    return 1 if failures else 0


def command_install_args(args: argparse.Namespace) -> int:
    spec = load_spec(args.input)
    print(" ".join(f"{item['name']}={item['version']}" for item in spec["direct_packages"]))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("deb_dir", type=Path)
    create.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    create.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)

    verify = subparsers.add_parser("verify")
    verify.add_argument("deb_dir", type=Path)
    verify.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    verify.add_argument("--lock", type=Path, default=DEFAULT_OUTPUT)

    install_args = subparsers.add_parser("install-args")
    install_args.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "create":
            return command_create(args)
        if args.command == "verify":
            return command_verify(args)
        if args.command == "install-args":
            return command_install_args(args)
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
