import stat
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_unified_setup_entrypoint_is_self_contained():
    setup = ROOT / "scripts" / "setup.sh"
    source = setup.read_text(encoding="utf-8")

    assert setup.stat().st_mode & stat.S_IXUSR
    assert "--base" in source
    assert "--full" in source
    assert "requirements-bootstrap.lock" in source
    assert '"$REPO_ROOT/requirements.txt"' in source
    assert "functional_example/requirements.txt" not in source
    assert "deps/benchbase" in source
    assert "deps/mutilate" in source
    assert '"$SCRIPT_DIR/setup_tailbench.sh"' in source
    assert '"$SCRIPT_DIR/setup_sparkbench.sh"' in source
    assert "libssl-dev" in source
    assert "liblz4-dev" in source
    assert "uuid-dev" in source
    assert "SETUP: WARNING: continuing without perf hardware-counter samples." in source
    assert "perf remains unusable" not in source


def test_obsolete_functional_installers_are_removed():
    assert not (ROOT / "functional_example" / "install.sh").exists()
    assert not (ROOT / "functional_example" / "install_tpcc.sh").exists()


def test_documentation_uses_unified_setup_entrypoint():
    paths = [
        ROOT / "README.md",
        ROOT / "functional_example" / "README.md",
        ROOT / "deps" / "README.md",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    assert "scripts/setup.sh --base" in combined
    assert "functional_example/install.sh" not in combined
    assert "functional_example/install_tpcc.sh" not in combined
