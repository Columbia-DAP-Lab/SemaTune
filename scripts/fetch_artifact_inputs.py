#!/usr/bin/env python3
"""Fetch and verify external artifact inputs from a lock file.

Known inputs are accepted only after their byte size and cryptographic digest
match ``artifact/downloads.lock.json``.  ``record`` is intentionally separate:
it downloads an input whose publisher did not provide a digest and prints the
candidate SHA-256 without modifying the lock file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCK = REPO_ROOT / "artifact" / "downloads.lock.json"
CHUNK_SIZE = 8 * 1024 * 1024


def load_lock(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("schema_version") != 1:
        raise ValueError(f"unsupported lock schema in {path}")
    validate_lock(payload)
    return payload


def validate_lock(lock: Dict[str, Any]) -> None:
    files = lock.get("files")
    if not isinstance(files, list):
        raise ValueError("download lock 'files' must be a list")
    ids = set()
    destinations = set()
    for record in files:
        for field in ("id", "url", "destination", "size", "purpose", "license"):
            if field not in record:
                raise ValueError(f"download record is missing {field!r}")
        identifier = str(record["id"])
        destination = str(record["destination"])
        relative_destination = Path(destination)
        if (
            not re.fullmatch(r"[A-Za-z0-9._-]+", identifier)
            or not destination
            or relative_destination.is_absolute()
            or any(part in ("", ".", "..") for part in relative_destination.parts)
            or relative_destination.as_posix() != destination
        ):
            raise ValueError(f"unsafe id or destination in download lock: {identifier!r}")
        if identifier in ids:
            raise ValueError(f"duplicate file id in download lock: {identifier}")
        if destination in destinations:
            raise ValueError(f"duplicate destination in download lock: {destination}")
        ids.add(identifier)
        destinations.add(destination)
        if urllib.parse.urlparse(str(record["url"])).scheme != "https":
            raise ValueError(f"{identifier}: download URL must use HTTPS")
        if not isinstance(record["size"], int) or record["size"] <= 0:
            raise ValueError(f"{identifier}: invalid byte size")
        for algorithm, length in (("sha256", 64), ("sha512", 128)):
            value = record.get(algorithm)
            if value is None:
                continue
            value = str(value).lower()
            if len(value) != length or any(c not in "0123456789abcdef" for c in value):
                raise ValueError(f"{identifier}: invalid {algorithm}")

    git_records = lock.get("git", [])
    if not isinstance(git_records, list):
        raise ValueError("download lock 'git' must be a list")
    git_ids = set()
    for record in git_records:
        identifier = str(record.get("id", ""))
        if not re.fullmatch(r"[A-Za-z0-9._-]+", identifier) or identifier in git_ids:
            raise ValueError(f"missing or duplicate git input id: {identifier!r}")
        git_ids.add(identifier)
        for field in ("purpose", "license"):
            if not record.get(field):
                raise ValueError(f"{identifier}: git input is missing {field}")
        if urllib.parse.urlparse(str(record.get("url", ""))).scheme != "https":
            raise ValueError(f"{identifier}: git URL must use HTTPS")
        for field in ("commit", "tree"):
            value = str(record.get(field, "")).lower()
            if len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError(f"{identifier}: invalid Git {field}")
        lfs_digest = record.get("lfs_manifest_sha256")
        if lfs_digest is not None:
            value = str(lfs_digest).lower()
            if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError(f"{identifier}: invalid LFS manifest SHA-256")


def file_records(lock: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    records = {str(item["id"]): item for item in lock.get("files", [])}
    if len(records) != len(lock.get("files", [])):
        raise ValueError("duplicate file id in download lock")
    return records


def expected_digest(record: Dict[str, Any]) -> Tuple[str, str] | None:
    for algorithm in ("sha512", "sha256"):
        value = record.get(algorithm)
        if value:
            return algorithm, str(value).lower()
    return None


def hash_file(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(path: Path, record: Dict[str, Any], *, allow_missing_digest: bool = False) -> Dict[str, Any]:
    expected_size = int(record["size"])
    actual_size = path.stat().st_size
    if actual_size != expected_size:
        raise ValueError(f"{path}: size {actual_size} != expected {expected_size}")

    expected = expected_digest(record)
    if expected is None:
        if not allow_missing_digest:
            raise ValueError(f"{record['id']}: lock has no cryptographic digest")
        algorithm = "sha256"
        expected_value = None
    else:
        algorithm, expected_value = expected

    actual_digest = hash_file(path, algorithm)
    if expected_value is not None and actual_digest != expected_value:
        raise ValueError(
            f"{path}: {algorithm} {actual_digest} != expected {expected_value}"
        )
    return {
        "id": record["id"],
        "path": str(path),
        "size": actual_size,
        algorithm: actual_digest,
    }


def destination_path(root: Path, record: Dict[str, Any]) -> Path:
    root = root.resolve()
    requested = root / str(record["destination"])
    resolved_parent = requested.parent.resolve()
    if resolved_parent != root and root not in resolved_parent.parents:
        raise ValueError(f"destination escapes root: {requested}")
    destination = resolved_parent / requested.name
    if destination.is_symlink():
        raise ValueError(f"refusing symbolic-link destination: {destination}")
    return destination


def validate_response_entity(record: Dict[str, Any], response: Any) -> None:
    """For un-hashed inputs, require the upstream entity recorded in the lock."""

    if expected_digest(record) is not None:
        return
    expected_etag = record.get("etag")
    actual_etag = response.headers.get("ETag")
    if not expected_etag:
        raise ValueError(f"{record['id']}: un-hashed input has no recorded ETag")
    if actual_etag != expected_etag:
        raise ValueError(
            f"{record['id']}: upstream ETag {actual_etag!r} != recorded {expected_etag!r}"
        )


def validate_remote_entity(record: Dict[str, Any]) -> None:
    request = urllib.request.Request(
        str(record["url"]),
        headers={"User-Agent": "SemaTune-artifact-fetch/1"},
        method="HEAD",
    )
    try:
        response = urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"entity check failed for {record['id']}: HTTP {exc.code}") from exc
    with response:
        validate_response_entity(record, response)


def stream_download(record: Dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    if partial.is_symlink():
        raise ValueError(f"refusing symbolic-link partial download: {partial}")
    if partial.exists() and partial.stat().st_nlink != 1:
        raise ValueError(f"refusing multiply linked partial download: {partial}")
    offset = partial.stat().st_size if partial.exists() else 0
    expected_size = int(record["size"])
    if offset > expected_size:
        partial.unlink()
        offset = 0
    elif offset == expected_size:
        try:
            validate(
                partial,
                record,
                allow_missing_digest=expected_digest(record) is None,
            )
            if expected_digest(record) is None:
                validate_remote_entity(record)
        except ValueError:
            partial.unlink()
            offset = 0
        else:
            os.replace(partial, destination)
            return

    # A digest will detect a spliced resume. For the one-time record path,
    # resume only when an upstream entity validator is available.
    if offset and expected_digest(record) is None and not record.get("etag"):
        partial.unlink()
        offset = 0

    headers = {"User-Agent": "SemaTune-artifact-fetch/1"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
        if record.get("etag"):
            headers["If-Range"] = str(record["etag"])

    request = urllib.request.Request(str(record["url"]), headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"download failed for {record['id']}: HTTP {exc.code}") from exc

    status = getattr(response, "status", response.getcode())
    try:
        validate_response_entity(record, response)
    except ValueError:
        response.close()
        raise
    append = offset > 0 and status == 206
    if append:
        content_range = response.headers.get("Content-Range", "")
        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", content_range)
        if (
            match is None
            or int(match.group(1)) != offset
            or (match.group(3) != "*" and int(match.group(3)) != expected_size)
        ):
            response.close()
            partial.unlink()
            return stream_download(record, destination)
    if offset and not append:
        offset = 0

    mode = "ab" if append else "wb"
    written = offset
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    flags |= os.O_APPEND if append else os.O_TRUNC
    try:
        descriptor = os.open(partial, flags, 0o600)
    except OSError:
        response.close()
        raise
    with response, os.fdopen(descriptor, mode) as handle:
        while True:
            chunk = response.read(CHUNK_SIZE)
            if not chunk:
                break
            if written + len(chunk) > expected_size:
                raise ValueError(
                    f"{record['id']}: download exceeded expected size {expected_size}"
                )
            handle.write(chunk)
            written += len(chunk)
            print(
                f"\r{record['id']}: {written:,}/{expected_size:,} bytes",
                end="",
                file=sys.stderr,
                flush=True,
            )
    print(file=sys.stderr)
    validate(partial, record, allow_missing_digest=expected_digest(record) is None)
    os.replace(partial, destination)


def choose_record(args: argparse.Namespace, lock: Dict[str, Any]) -> Dict[str, Any]:
    records = file_records(lock)
    try:
        return records[args.id]
    except KeyError as exc:
        raise ValueError(f"unknown input id {args.id!r}") from exc


def command_list(lock: Dict[str, Any]) -> int:
    for record in lock.get("files", []):
        digest = expected_digest(record)
        digest_text = f"{digest[0]}:{digest[1]}" if digest else "UNRECORDED"
        print(f"{record['id']}\t{record['size']}\t{digest_text}\t{record['url']}")
    return 0


def command_fetch(args: argparse.Namespace, lock: Dict[str, Any]) -> int:
    record = choose_record(args, lock)
    if expected_digest(record) is None:
        raise ValueError(
            f"{record['id']}: no digest is recorded; use 'record' once, review the output, "
            "and update the lock before normal fetching"
        )
    destination = destination_path(args.root, record)
    if not destination.exists():
        stream_download(record, destination)
    print(json.dumps(validate(destination, record), indent=2, sort_keys=True))
    return 0


def command_record(args: argparse.Namespace, lock: Dict[str, Any]) -> int:
    record = choose_record(args, lock)
    if expected_digest(record) is not None:
        raise ValueError(f"{record['id']}: digest is already recorded; use 'fetch'")
    destination = destination_path(args.root, record)
    if not destination.exists():
        stream_download(record, destination)
    candidate = validate(destination, record, allow_missing_digest=True)
    candidate["url"] = record["url"]
    print(json.dumps(candidate, indent=2, sort_keys=True))
    return 0


def command_verify(args: argparse.Namespace, lock: Dict[str, Any]) -> int:
    record = choose_record(args, lock)
    destination = destination_path(args.root, record)
    print(json.dumps(validate(destination, record), indent=2, sort_keys=True))
    return 0


def command_verify_all(args: argparse.Namespace, lock: Dict[str, Any]) -> int:
    failures = []
    for record in lock.get("files", []):
        destination = destination_path(args.root, record)
        try:
            result = validate(destination, record)
            print(f"OK {result['id']}: {destination}")
        except (OSError, ValueError) as exc:
            failures.append(str(exc))
            print(f"FAIL {record['id']}: {exc}", file=sys.stderr)
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list")
    for name in ("fetch", "record", "verify"):
        child = subparsers.add_parser(name)
        child.add_argument("id")
        child.add_argument("--root", type=Path, default=REPO_ROOT)
    child = subparsers.add_parser("verify-all")
    child.add_argument("--root", type=Path, default=REPO_ROOT)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        lock = load_lock(args.lock)
        if args.command == "list":
            return command_list(lock)
        if args.command == "fetch":
            return command_fetch(args, lock)
        if args.command == "record":
            return command_record(args, lock)
        if args.command == "verify":
            return command_verify(args, lock)
        if args.command == "verify-all":
            return command_verify_all(args, lock)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
