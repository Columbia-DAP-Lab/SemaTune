#!/usr/bin/env python3
"""Generate and validate paper Plots 6 and 7 for Silo, TPC-C, and Sysbench."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import suite


REPRO_ROOT = Path(__file__).resolve().parent
REPO_ROOT = REPRO_ROOT.parent
DEFAULT_MANIFEST = REPRO_ROOT / "three_app_plots_6_7_manifest.json"
WORKLOADS = "silo_hi_p99,tpcc_hi_p99,sysbench_oltp_rw_hi_p99"
PLOT6_NAME = "retry_aggregate_improvement_geomean_with_and_without_xapian"
PLOT7_NAME = "retry_indirect_aggregate_improvement_geomean_with_and_without_xapian"
PLOT6_COLUMNS = (
    "TuxBot App Metrics Dual Loop:llm_dual_app_metrics_final_actor,"
    "TuxBot App Metrics Dual Loop no Catastrophic:llm_dual_app_metrics_final_actor,"
    "MLOS + TuxBot:mlos_trimming_aggressive|mlos_trimming,"
    "MLOS + TuxBot no Catastrophic:mlos_trimming_aggressive|mlos_trimming,"
    "MLOS:mlos_50_tuning_only|mlos,"
    "MLOS no Catastrophic:mlos_50_tuning_only|mlos,"
    "Bayesian:__missing_bayesian__,"
    "Bayesian no Catastrophic:__missing_bayesian__,"
    "DQN:__missing_dqn__,"
    "DQN no Catastrophic:__missing_dqn__,"
    "Q-Learning:__missing_qlearning__,"
    "Q-Learning no Catastrophic:__missing_qlearning__"
)
PLOT6_POPULATED_COLUMNS = PLOT6_COLUMNS.replace(
    "Bayesian:__missing_bayesian__,Bayesian no Catastrophic:__missing_bayesian__,",
    "Bayesian:bayesian,Bayesian no Catastrophic:bayesian,",
).replace(
    "DQN:__missing_dqn__,DQN no Catastrophic:__missing_dqn__,",
    "DQN:dqn,DQN no Catastrophic:dqn,",
).replace(
    "Q-Learning:__missing_qlearning__,Q-Learning no Catastrophic:__missing_qlearning__",
    "Q-Learning:qlearning,Q-Learning no Catastrophic:qlearning",
)
PLOT7_COLUMNS = (
    "TuxBot App Only Dual:llm_dual_app_metrics_final_actor,"
    "TuxBot App Only Dual no Catastrophic:llm_dual_app_metrics_final_actor,"
    "TuxBot Indirect Dump Dual:llm_dual_system_metrics_plain_final_actor,"
    "TuxBot Indirect Dump Dual no Catastrophic:llm_dual_system_metrics_plain_final_actor,"
    "TuxBot IPC Dual:llm_dual_ipc_final_actor,"
    "TuxBot IPC Dual no Catastrophic:llm_dual_ipc_final_actor,"
    "TuxBot Trim App:mlos_trimming_aggressive|mlos_trimming,"
    "TuxBot Trim App no Catastrophic:mlos_trimming_aggressive|mlos_trimming,"
    "TuxBot Trim IPC:mlos_trimming_aggressive_ipc|mlos_trimming_ipc,"
    "TuxBot Trim IPC no Catastrophic:mlos_trimming_aggressive_ipc|mlos_trimming_ipc,"
    "TuxBot Trim Cache:mlos_trimming_aggressive_cache_misses_max|mlos_trimming_cache_misses_max,"
    "TuxBot Trim Cache no Catastrophic:mlos_trimming_aggressive_cache_misses_max|mlos_trimming_cache_misses_max,"
    "MLOS App Metrics:mlos_50_tuning_only|mlos,"
    "MLOS App Metrics no Catastrophic:mlos_50_tuning_only|mlos,"
    "MLOS IPC:mlos_ipc_50_tuning_only|mlos_ipc,"
    "MLOS IPC no Catastrophic:mlos_ipc_50_tuning_only|mlos_ipc,"
    "MLOS Cache Misses:mlos_cache_misses_50_tuning_only|mlos_cache_misses,"
    "MLOS Cache Misses no Catastrophic:mlos_cache_misses_50_tuning_only|mlos_cache_misses"
)
PLOT6_METHOD_LABELS = {
    "sematune_app": {
        "TuxBot App Metrics Dual Loop",
        "TuxBot App Metrics Dual Loop no Catastrophic",
    },
    "sematune_trim_app": {"MLOS + TuxBot", "MLOS + TuxBot no Catastrophic"},
    "mlos_app": {"MLOS", "MLOS no Catastrophic"},
    "bayesian": {"Bayesian", "Bayesian no Catastrophic"},
    "dqn": {"DQN", "DQN no Catastrophic"},
    "qlearning": {"Q-Learning", "Q-Learning no Catastrophic"},
}
PLOT7_METHOD_LABELS = {
    "sematune_app": {"TuxBot App Only Dual", "TuxBot App Only Dual no Catastrophic"},
    "sematune_system": {
        "TuxBot Indirect Dump Dual",
        "TuxBot Indirect Dump Dual no Catastrophic",
    },
    "sematune_ipc": {"TuxBot IPC Dual", "TuxBot IPC Dual no Catastrophic"},
    "sematune_trim_app": {"TuxBot Trim App", "TuxBot Trim App no Catastrophic"},
    "sematune_trim_ipc": {"TuxBot Trim IPC", "TuxBot Trim IPC no Catastrophic"},
    "sematune_trim_cache": {
        "TuxBot Trim Cache",
        "TuxBot Trim Cache no Catastrophic",
    },
    "mlos_app": {"MLOS App Metrics", "MLOS App Metrics no Catastrophic"},
    "mlos_ipc": {"MLOS IPC", "MLOS IPC no Catastrophic"},
    "mlos_cache": {"MLOS Cache Misses", "MLOS Cache Misses no Catastrophic"},
}


def roots(results: Path, manifest: dict[str, Any], *, fixed: bool) -> list[str]:
    found: list[str] = []
    for job in manifest["jobs"]:
        if not job["id"].startswith("common:"):
            continue
        is_fixed = job["id"].endswith(":fixed")
        if is_fixed != fixed:
            continue
        root = str(results / Path(job["target_results_dir"]).parts[0])
        if root not in found:
            found.append(root)
    return found


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def pdf_page_size(path: Path) -> tuple[float, float] | None:
    match = re.search(
        rb"/MediaBox\s*\[\s*0(?:\.0+)?\s+0(?:\.0+)?\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\]",
        path.read_bytes(),
    )
    return None if match is None else (float(match.group(1)), float(match.group(2)))


def run_plot(
    *,
    results: Path,
    output: Path,
    manifest: dict[str, Any],
    name: str,
    columns: str,
    extra_args: list[str],
) -> tuple[Path, Path]:
    plot_dir = output / "plots"
    plot_dir.mkdir(parents=True, exist_ok=True)
    plot_path = plot_dir / f"{name}.pdf"
    csv_path = plot_dir / f"{name}.csv"
    result_roots = roots(results, manifest, fixed=False)
    fixed_roots = roots(results, manifest, fixed=True)
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "plot_retry_aggregate_improvement.py"),
        "--result-paths", *result_roots,
        "--fallback-fixed-paths", *fixed_roots,
        "--fallback-tuner-paths", *result_roots,
        "--workloads", ",".join(manifest["workloads"]),
        "--custom-columns", columns,
        "--aggregate-stat", "geomean",
        "--subset", "union",
        "--tuning-window", "1-30",
        "--stable-window", "31-50",
        "--error-bars",
        "--preserve-empty-methods",
        "--plot-output", str(plot_path),
        "--csv-output", str(csv_path),
        *extra_args,
    ]
    subprocess.run(command, cwd=REPO_ROOT, check=True)
    return plot_path, csv_path


def require_complete_results(
    results: Path, manifest: dict[str, Any], *, allow_replay: bool
) -> None:
    for job in manifest["jobs"]:
        history_path = suite.completed_result(results / job["target_results_dir"], job)
        if history_path is None:
            raise ValueError(f"{job['id']}: missing strict complete history")
        history = json.loads(history_path.read_text(encoding="utf-8"))
        config = history.get("config")
        if not isinstance(config, dict):
            raise ValueError(f"{job['id']}: history lacks its materialized config")
        if config.get("llm_replay_file") and not allow_replay:
            raise ValueError(f"{job['id']}: trace replay is not valid live-provider evidence")


def selected_common_methods(manifest: dict[str, Any]) -> set[str]:
    return {
        job["id"].rsplit(":", 1)[-1]
        for job in manifest["jobs"]
        if job["id"].startswith("common:")
    }


def labels_for_selected_methods(
    selected: set[str], mapping: dict[str, set[str]]
) -> tuple[set[str], set[str]]:
    all_labels = set().union(*mapping.values())
    actual = set().union(*(mapping[method] for method in selected if method in mapping))
    return actual, all_labels - actual


def columns_for_selected_methods(
    columns: str, selected: set[str], mapping: dict[str, set[str]]
) -> str:
    label_methods = {
        label: method for method, labels in mapping.items() for label in labels
    }
    scoped: list[str] = []
    for item in columns.split(","):
        label, directory_spec = item.split(":", 1)
        method = label_methods[label]
        if method not in selected:
            directory_spec = f"__missing_{method}__"
        scoped.append(f"{label}:{directory_spec}")
    return ",".join(scoped)


def require_grid(
    rows: list[dict[str, str]],
    *,
    expected_rows: int,
    actual_methods: set[str],
    empty_methods: set[str] | None = None,
    expected_workloads: set[str] | None = None,
) -> None:
    if len(rows) != expected_rows:
        raise ValueError(f"expected {expected_rows} paper-grid rows, found {len(rows)}")
    keyed = {(row["phase"], row["method"]): row for row in rows}
    for phase in ("tuning", "stable"):
        for method in actual_methods:
            row = keyed.get((phase, method))
            method_workloads = set(expected_workloads or ())
            if method.endswith(" no Catastrophic"):
                method_workloads.discard("xapian_hi_p99")
                method_workloads.discard("mutilate_high")
            expected_count = len(method_workloads) if expected_workloads is not None else 3
            if (
                row is None
                or int(row["n_workloads"]) != expected_count
                or int(row["run_count"]) != 1
            ):
                raise ValueError(
                    f"{method}/{phase}: expected all {expected_count} one-run workloads"
                )
            factor = float(row["aggregate_factor"])
            if not math.isfinite(factor) or factor <= 0:
                raise ValueError(f"{method}/{phase}: invalid aggregate factor")
        for method in empty_methods or set():
            row = keyed.get((phase, method))
            if row is None or int(row["n_workloads"]) != 0 or row["aggregate_factor"]:
                raise ValueError(f"{method}/{phase}: expected a preserved empty paper slot")


def stable_factor(
    rows: list[dict[str, str]], method: str, *, required: bool = True
) -> float | None:
    for row in rows:
        if row["phase"] == "stable" and row["method"] == method:
            value = row["aggregate_factor"]
            if value:
                return float(value)
            break
    if required:
        raise ValueError(f"missing stable aggregate for {method}")
    return None


def write_report(
    report_dir: Path,
    manifest: dict[str, Any],
    plot6_rows: list[dict[str, str]],
    plot7_rows: list[dict[str, str]],
    plot6: Path,
    plot7: Path,
    execution_mode: str,
) -> None:
    app = stable_factor(plot6_rows, "TuxBot App Metrics Dual Loop")
    trim = stable_factor(plot6_rows, "MLOS + TuxBot", required=False)
    mlos = stable_factor(plot6_rows, "MLOS")
    system = stable_factor(plot7_rows, "TuxBot Indirect Dump Dual")
    assert app is not None and mlos is not None and system is not None
    claims = [
        {"claim": "C1", "comparison": "TuxBot App / Fixed", "factor": app, "consistent": app > 1.0},
        {"claim": "C2", "comparison": "TuxBot App / MLOS App", "factor": app / mlos, "consistent": app > mlos},
        {"claim": "C3", "comparison": "TuxBot System / MLOS App", "factor": system / mlos, "consistent": system > mlos},
    ]
    for row in claims:
        row["status"] = "CONSISTENT" if row.pop("consistent") else "DIVERGENT"
        row["pct"] = (float(row["factor"]) - 1.0) * 100.0
    payload = {
        "schema_version": 1,
        "workflow": manifest["workflow"],
        "execution_mode": execution_mode,
        "workloads": manifest["workloads"],
        "configurations": len(manifest["jobs"]),
        "fresh_repetitions": 1,
        "claims": claims,
        "claim_plot_map": {"C1": "plot_6", "C2": "plot_6", "C3": "plot_7"},
        "plot_6": str(plot6),
        "plot_7": str(plot7),
        "plot_6_stable_factors": {"TuxBot App": app, "TuxBot-Trim App": trim, "MLOS App": mlos},
        "plot_7_system_stable_factor": system,
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "three_app_plots_6_7_report.json"
    temporary = json_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(json_path)
    workload_count = len(manifest["workloads"])
    lines = [
        f"# {workload_count}-workload paper Plots 6 and 7",
        "",
        f"One {execution_mode} repetition over {workload_count} workloads: "
        + ", ".join(manifest["workloads"])
        + ".",
        "",
        "| Claim | Comparison | Observation | Factor |",
        "|---|---|---|---:|",
    ]
    for row in claims:
        lines.append(
            f"| {row['claim']} | {row['comparison']} | {row['status']} | "
            f"{row['factor']:.4f}x ({row['pct']:+.2f}%) |"
        )
    lines.extend([
        "",
        (
            "Plots use the paper method grid; the canvas expands when necessary to avoid clipping."
            if manifest.get("tier") in {"extended", "full"}
            else "Plots use the paper method grid. Methods outside the fast base scope remain empty."
        ),
        "",
        f"Plot 6: [`fresh/plots/{plot6.name}`](fresh/plots/{plot6.name}).",
        "",
        f"Plot 7: [`fresh/plots/{plot7.name}`](fresh/plots/{plot7.name}).",
        "",
    ])
    (report_dir / "three_app_plots_6_7_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--allow-replay",
        action="store_true",
        help="Accept validated replay histories for the provider-free base tier.",
    )
    args = parser.parse_args()

    results = args.results_dir.resolve()
    output = args.output_dir.resolve()
    report_dir = args.report_dir.resolve()
    manifest = suite.load_manifest(args.manifest.resolve())
    populate_baselines = manifest.get("tier") in {"extended", "full"}
    require_complete_results(results, manifest, allow_replay=args.allow_replay)
    selected_methods = selected_common_methods(manifest)
    plot6_columns = columns_for_selected_methods(
        PLOT6_POPULATED_COLUMNS if populate_baselines else PLOT6_COLUMNS,
        selected_methods,
        PLOT6_METHOD_LABELS,
    )
    plot7_columns = columns_for_selected_methods(
        PLOT7_COLUMNS, selected_methods, PLOT7_METHOD_LABELS
    )

    plot6, csv6 = run_plot(
        results=results,
        output=output,
        manifest=manifest,
        name=PLOT6_NAME,
        columns=plot6_columns,
        extra_args=[
            "--preserve-empty-methods", "--auto-y", "--right-trim-pts", "0",
            "--output-width-pts", "564.1350915211",
            "--output-height-pts", "226.18",
        ],
    )
    plot7, csv7 = run_plot(
        results=results,
        output=output,
        manifest=manifest,
        name=PLOT7_NAME,
        columns=plot7_columns,
        extra_args=[
            "--preserve-empty-methods", "--auto-y",
            "--x-label-map",
            "TuxBot App Only Dual:App,TuxBot Indirect Dump Dual:System,TuxBot IPC Dual:IPC,"
            "TuxBot Trim App:App,TuxBot Trim IPC:IPC,TuxBot Trim Cache:Cache,"
            "MLOS App Metrics:App,MLOS IPC:IPC,MLOS Cache Misses:Cache",
            "--x-group-map",
            "TuxBot App Only Dual:TuxBot,TuxBot Indirect Dump Dual:TuxBot,TuxBot IPC Dual:TuxBot,"
            "TuxBot Trim App:TuxBot-trim,TuxBot Trim IPC:TuxBot-trim,TuxBot Trim Cache:TuxBot-trim,"
            "MLOS App Metrics:MLOS,MLOS IPC:MLOS,MLOS Cache Misses:MLOS",
            "--output-width-pts", "566",
            "--output-height-pts", "213.646144",
        ],
    )

    rows6 = csv_rows(csv6)
    rows7 = csv_rows(csv7)
    plot6_actual, plot6_empty = labels_for_selected_methods(
        selected_methods, PLOT6_METHOD_LABELS
    )
    plot7_actual, plot7_empty = labels_for_selected_methods(
        selected_methods, PLOT7_METHOD_LABELS
    )
    expected_workloads = set(manifest["workloads"])
    require_grid(
        rows6,
        expected_rows=24,
        actual_methods=plot6_actual,
        empty_methods=plot6_empty,
        expected_workloads=expected_workloads,
    )
    require_grid(
        rows7,
        expected_rows=36,
        actual_methods=plot7_actual,
        empty_methods=plot7_empty,
        expected_workloads=expected_workloads,
    )
    for path, expected in ((plot6, (564.1350915211, 226.18)), (plot7, (566.0, 213.646144))):
        size = pdf_page_size(path)
        if (
            size is None
            or not math.isclose(size[0], expected[0], abs_tol=0.01)
            or size[1] + 0.01 < expected[1]
        ):
            raise ValueError(
                f"{path.name}: expected paper width {expected[0]} and minimum "
                f"height {expected[1]}, found {size}"
            )

    write_report(
        report_dir,
        manifest,
        rows6,
        rows7,
        plot6,
        plot7,
        "trace-replay" if args.allow_replay else "real-provider",
    )
    print(f"THREE_APP_PLOTS_6_7: PASS ({plot6}; {plot7})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
