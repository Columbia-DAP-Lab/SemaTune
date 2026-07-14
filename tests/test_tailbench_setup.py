from pathlib import Path
import tarfile

from scripts.install_tailbench_inputs import validate_member


ROOT = Path(__file__).resolve().parents[1]


def test_full_setup_delegates_to_standalone_tailbench_setup():
    setup = (ROOT / "scripts/setup.sh").read_text(encoding="utf-8")
    tailbench = (ROOT / "scripts/setup_tailbench.sh").read_text(encoding="utf-8")
    assert '"$SCRIPT_DIR/setup_tailbench.sh"' in setup
    assert "2f3098b539a9a3413086fc77e29637937bafd116" in tailbench


def test_only_retained_tailbench_workloads_are_built_and_validated():
    setup = (ROOT / "scripts/setup_tailbench.sh").read_text(encoding="utf-8")
    validator = (ROOT / "scripts/validate_tailbench_setup.py").read_text(encoding="utf-8")
    for workload in ("masstree", "silo", "sphinx", "xapian"):
        assert workload in setup
        assert workload in validator
    for excluded in ("img-dnn", "moses", "shore", "specjbb"):
        assert excluded not in setup
        assert excluded not in validator
    assert "libssl-dev" in setup
    assert "liblz4-dev" in setup
    assert "uuid-dev" in setup
    assert 'sphinxbase.pc' in setup
    assert 'pocketsphinx.pc' in setup
    assert '--without-python' in setup


def test_legacy_tailbench_builds_are_serialized_and_use_host_automake():
    setup = (ROOT / "scripts/setup_tailbench.sh").read_text(encoding="utf-8")
    silo_config = 'make -C "$SUITE_DIR/silo" MODE=perf masstree/config.h'
    silo_binary = 'make -C "$SUITE_DIR/silo" MODE=perf -j"$jobs"'
    assert silo_config in setup
    assert setup.index(silo_config) < setup.index(silo_binary)
    assert setup.count("autoreconf --force --install") == 2


def test_inputs_default_under_mydata_with_explicit_software_only_mode():
    setup = (ROOT / "scripts/setup_tailbench.sh").read_text(encoding="utf-8")
    installer = (ROOT / "scripts/install_tailbench_inputs.py").read_text(encoding="utf-8")
    assert "/mydata/TuxBot-tailbench/tailbench.inputs" in setup
    assert "--without-inputs" in setup
    assert "already installed" in setup
    assert 'config_data_path="$TAILBENCH_DIR/tailbench.inputs"' in setup
    assert 'ln -s "$DATA_ROOT" "$config_data_path"' in setup
    assert "EXPECTED_SIZE = 10_230_769_002" in installer
    assert 'TOP_LEVEL = "tailbench.inputs"' in installer
    assert 'member.isdev() or member.isfifo()' in installer
    assert 'mode="r|*"' in installer


def test_input_extractor_rejects_unsafe_members():
    cases = [
        ("../escape", ""),
        ("other-root/file", ""),
        ("tailbench.inputs/link", "/etc/passwd"),
        ("tailbench.inputs/dir/link", "../../escape"),
    ]
    for name, linkname in cases:
        member = tarfile.TarInfo(name)
        if linkname:
            member.type = tarfile.SYMTYPE
            member.linkname = linkname
        try:
            validate_member(member)
        except ValueError:
            continue
        raise AssertionError(f"unsafe member was accepted: {name!r} -> {linkname!r}")
