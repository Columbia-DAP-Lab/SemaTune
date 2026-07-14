from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_full_setup_delegates_to_standalone_spark_setup():
    setup = (ROOT / "scripts" / "setup.sh").read_text(encoding="utf-8")
    spark = (ROOT / "scripts" / "setup_sparkbench.sh").read_text(encoding="utf-8")
    assert '"$SCRIPT_DIR/setup_sparkbench.sh"' in setup
    assert "spark-2.4.5-hadoop-2.7" in spark
    assert "benchpress_cli.py install spark_standalone_local" in spark
    assert '"$SCRIPT_DIR/populate_sparkbench.sh"' in spark


def test_dataset_download_is_explicit_and_pinned():
    spark = (ROOT / "scripts" / "setup_sparkbench.sh").read_text(encoding="utf-8")
    verifier = (ROOT / "scripts" / "verify_spark_dataset.py").read_text(encoding="utf-8")
    assert "--with-dataset" in spark
    assert "--data-root" in spark
    assert "/mydata/TuxBot-sparkbench" in spark
    assert "afbc2c250aebb0c18e65a685f2b5e454e7d0c03b" in spark
    assert 'OBJECTS = 979' in verifier
    assert 'BYTES = 109486252337' in verifier
    assert "sparkbench.env" in spark


def test_population_is_bounded_accelerated_and_completion_marked():
    population = (ROOT / "scripts" / "populate_sparkbench.sh").read_text(encoding="utf-8")
    configurator = (ROOT / "scripts" / "configure_sparkbench_population.py").read_text(encoding="utf-8")
    assert "SEMATUNE_SPARKBENCH_POPULATE_TIMEOUT_SECONDS" in population
    assert "population_cores" in population
    assert "stop_spark" in population
    assert ".sematune-sparkbench-warehouse-complete.json" in population
    assert 'spark.default.parallelism' in configurator
    assert 'fileoutputcommitter.algorithm.version' in configurator


def test_smoke_requires_positive_spark_metrics_and_restoration():
    runner = (ROOT / "scripts" / "run_sparkbench_smoke.sh").read_text(encoding="utf-8")
    validator = (ROOT / "scripts" / "validate_sparkbench_smoke.py").read_text(encoding="utf-8")
    assert "-m optimizer.main" in runner
    assert "host_state_guard.py" in runner
    assert "/usr/lib/jvm/java-8-openjdk-amd64" in runner
    assert "dcperf_work" in runner
    assert ".sematune-sparkbench-warehouse-complete.json" in runner
    assert '"dcperf_benchmark_name": "spark_standalone_local"' in validator
    assert '"max_iterations": 1' in validator
    assert "queries_per_hour <= 0" in validator
    assert 'restoration.get("verify_status") != "PASS"' in validator
    assert 'version "1.8.0_' in validator
    assert 'release_test_93586.log' in validator
