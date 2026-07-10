import argparse
import hashlib
import io
import json
from pathlib import Path

import pytest

from scripts import artifact_checksums, fetch_artifact_inputs, manifest_apt_packages


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, *, status: int, headers: dict[str, str]):
        super().__init__(payload)
        self.status = status
        self.headers = headers

    def getcode(self) -> int:
        return self.status


def test_payload_checksum_round_trip_covers_symlinks_and_extra_files(tmp_path: Path):
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "data.txt").write_text("artifact\n", encoding="utf-8")
    (payload / "data-link").symlink_to("data.txt")

    artifact_checksums.command_generate(
        argparse.Namespace(
            root=payload,
            output=Path("SHA256SUMS"),
            exclude=[],
        )
    )
    assert artifact_checksums.command_verify(
        argparse.Namespace(
            root=payload,
            manifest=Path("SHA256SUMS"),
            exclude=[],
        )
    ) == 0

    (payload / "unrecorded.txt").write_text("extra\n", encoding="utf-8")
    with pytest.raises(ValueError, match="payload paths are unrecorded"):
        artifact_checksums.command_verify(
            argparse.Namespace(
                root=payload,
                manifest=Path("SHA256SUMS"),
                exclude=[],
            )
        )


def test_download_lock_and_local_stream_validation(tmp_path: Path, monkeypatch):
    lock = fetch_artifact_inputs.load_lock(Path("artifact/downloads.lock.json"))
    assert set(fetch_artifact_inputs.file_records(lock)) == {
        "apache-maven-3.8.4",
        "spark-2.4.5-hadoop-2.7",
        "tailbench-inputs",
    }

    payload = b"abcd"
    record = {
        "id": "fixture",
        "url": "https://example.invalid/fixture",
        "destination": "fixture.bin",
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "purpose": "test",
        "license": "MIT",
    }
    monkeypatch.setattr(
        fetch_artifact_inputs.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(payload, status=200, headers={}),
    )
    destination = tmp_path / "fixture.bin"
    fetch_artifact_inputs.stream_download(record, destination)
    assert destination.read_bytes() == payload


def test_download_rejects_oversized_response(tmp_path: Path, monkeypatch):
    record = {
        "id": "fixture",
        "url": "https://example.invalid/fixture",
        "destination": "fixture.bin",
        "size": 3,
        "sha256": hashlib.sha256(b"abc").hexdigest(),
        "purpose": "test",
        "license": "MIT",
    }
    monkeypatch.setattr(
        fetch_artifact_inputs.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(b"abcd", status=200, headers={}),
    )
    with pytest.raises(ValueError, match="exceeded expected size"):
        fetch_artifact_inputs.stream_download(record, tmp_path / "fixture.bin")


def test_download_resumes_only_from_the_recorded_range(tmp_path: Path, monkeypatch):
    payload = b"abcd"
    record = {
        "id": "fixture",
        "url": "https://example.invalid/fixture",
        "destination": "fixture.bin",
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "purpose": "test",
        "license": "MIT",
    }
    destination = tmp_path / "fixture.bin"
    destination.with_name("fixture.bin.part").write_bytes(payload[:2])

    def open_range(request, timeout):
        assert request.get_header("Range") == "bytes=2-"
        return FakeResponse(
            payload[2:],
            status=206,
            headers={"Content-Range": "bytes 2-3/4"},
        )

    monkeypatch.setattr(fetch_artifact_inputs.urllib.request, "urlopen", open_range)
    fetch_artifact_inputs.stream_download(record, destination)
    assert destination.read_bytes() == payload


def test_apt_spec_and_lock_validation_reject_unsafe_paths():
    spec = manifest_apt_packages.load_spec(Path("artifact/apt-packages.in.json"))
    assert spec["architecture"] == "amd64"
    assert spec["suites"] == ["jammy", "jammy-updates", "jammy-security"]

    bad_lock = {
        "schema_version": 1,
        "package_count": 1,
        "packages": [
            {
                "package": "memcached",
                "version": "1.6.14-1ubuntu0.1",
                "architecture": "amd64",
                "filename": "../memcached.deb",
                "size": 1,
                "sha256": "0" * 64,
            }
        ],
    }
    with pytest.raises(ValueError, match="unsafe or non-canonical"):
        manifest_apt_packages.validate_lock_packages(bad_lock)


def test_machine_readable_manifests_are_json():
    for path in Path("artifact").glob("*.json"):
        assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1
