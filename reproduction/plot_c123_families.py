#!/usr/bin/env python3
"""Aggregate and plot the live-provider C1--C3 family-workload extension."""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
from pathlib import Path
from statistics import fmean
from typing import Any

import suite


REPRO_ROOT = Path(__file__).resolve().parent
REPO_ROOT = REPRO_ROOT.parent
DEFAULT_MANIFEST = REPRO_ROOT / "c123_family_manifest.json"
PLOT_FILES = {
    "plot6": "retry_aggregate_improvement_geomean_with_and_without_xapian.pdf",
    "plot7": "retry_indirect_aggregate_improvement_geomean_with_and_without_xapian.pdf",
}
PLOT6_COLUMNS = (
    "TuxBot App Metrics Dual Loop:llm_dual_app_metrics_final_actor,"
    "TuxBot App Metrics Dual Loop no Catastrophic:llm_dual_app_metrics_final_actor,"
    "MLOS + TuxBot:__missing_tuxbot_trim__,"
    "MLOS + TuxBot no Catastrophic:__missing_tuxbot_trim__,"
    "MLOS:mlos_50_tuning_only|mlos,"
    "MLOS no Catastrophic:mlos_50_tuning_only|mlos,"
    "Bayesian:__missing_bayesian__,"
    "Bayesian no Catastrophic:__missing_bayesian__,"
    "DQN:__missing_dqn__,"
    "DQN no Catastrophic:__missing_dqn__,"
    "Q-Learning:__missing_qlearning__,"
    "Q-Learning no Catastrophic:__missing_qlearning__"
)
PLOT7_COLUMNS = (
    "TuxBot App Only Dual:llm_dual_app_metrics_final_actor,"
    "TuxBot App Only Dual no Catastrophic:llm_dual_app_metrics_final_actor,"
    "TuxBot Indirect Dump Dual:llm_dual_system_metrics_plain_final_actor,"
    "TuxBot Indirect Dump Dual no Catastrophic:llm_dual_system_metrics_plain_final_actor,"
    "TuxBot IPC Dual:__missing_tuxbot_ipc__,"
    "TuxBot IPC Dual no Catastrophic:__missing_tuxbot_ipc__,"
    "TuxBot Trim App:__missing_tuxbot_trim_app__,"
    "TuxBot Trim App no Catastrophic:__missing_tuxbot_trim_app__,"
    "TuxBot Trim IPC:__missing_tuxbot_trim_ipc__,"
    "TuxBot Trim IPC no Catastrophic:__missing_tuxbot_trim_ipc__,"
    "TuxBot Trim Cache:__missing_tuxbot_trim_cache__,"
    "TuxBot Trim Cache no Catastrophic:__missing_tuxbot_trim_cache__,"
    "MLOS App Metrics:mlos_50_tuning_only|mlos,"
    "MLOS App Metrics no Catastrophic:mlos_50_tuning_only|mlos,"
    "MLOS IPC:__missing_mlos_ipc__,"
    "MLOS IPC no Catastrophic:__missing_mlos_ipc__,"
    "MLOS Cache Misses:__missing_mlos_cache__,"
    "MLOS Cache Misses no Catastrophic:__missing_mlos_cache__"
)


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def geometric_mean(values: list[float]) -> float:
    if not values or any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("geometric mean requires finite positive factors")
    return math.exp(fmean(math.log(value) for value in values))


def job_identity(job_id: str) -> tuple[str, str]:
    fields = job_id.split(":")
    if len(fields) != 3 or fields[0] != "common":
        raise ValueError(f"unsupported C1-C3 family job id: {job_id}")
    return fields[1], fields[2]


def load_phase_means(results_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    labels = manifest["workload_labels"]
    goals = manifest["optimization_goals"]
    rows: list[dict[str, Any]] = []
    for job in manifest["jobs"]:
        history_path = suite.completed_result(results_dir / job["target_results_dir"], job)
        if history_path is None:
            raise ValueError(f"missing complete history for {job['id']}")
        data = json.loads(history_path.read_text(encoding="utf-8"))
        history = sorted(
            (
                row
                for row in data["history"]
                if isinstance(row, dict) and int(row.get("iteration", 0)) > 0
            ),
            key=lambda row: int(row["iteration"]),
        )
        if len(history) != 50:
            raise ValueError(f"{job['id']}: expected exactly 50 measurement windows")
        workload, method = job_identity(job["id"])
        metric = str(job["completion"]["optimization_metric"])
        goal = str(goals[workload])
        for phase, selected in (("tuning", history[:30]), ("stable", history[30:50])):
            value = fmean(float(row["metrics"][metric]) for row in selected)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{job['id']}: {phase} {metric} is not finite and positive")
            rows.append(
                {
                    "job_id": job["id"],
                    "workload": workload,
                    "workload_label": labels[workload],
                    "method": method,
                    "phase": phase,
                    "metric": metric,
                    "optimization_goal": goal,
                    "mean_metric": value,
                    "history": str(history_path),
                }
            )
    return rows


def factor(default: float, candidate: float, goal: str) -> float:
    return candidate / default if goal == "maximize" else default / candidate


def build_factors(
    phase_rows: list[dict[str, Any]], manifest: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[tuple[str, str, str], float]]:
    indexed = {
        (row["workload"], row["method"], row["phase"]): float(row["mean_metric"])
        for row in phase_rows
    }
    labels = manifest["workload_labels"]
    goals = manifest["optimization_goals"]
    metrics = {
        row["workload"]: row["metric"]
        for row in phase_rows
        if row["method"] == "fixed"
    }
    rows: list[dict[str, Any]] = []
    factors: dict[tuple[str, str, str], float] = {}
    for workload in manifest["workloads"]:
        for phase in ("tuning", "stable"):
            default = indexed[(workload, "fixed", phase)]
            for method in ("sematune_app", "sematune_system", "mlos_app"):
                candidate = indexed[(workload, method, phase)]
                value = factor(default, candidate, goals[workload])
                if not math.isfinite(value) or value <= 0:
                    raise ValueError(f"{workload}/{method}/{phase}: invalid improvement factor")
                factors[(method, phase, workload)] = value
                rows.append(
                    {
                        "workload": workload,
                        "workload_label": labels[workload],
                        "method": method,
                        "phase": phase,
                        "metric": metrics[workload],
                        "optimization_goal": goals[workload],
                        "fixed_mean": default,
                        "method_mean": candidate,
                        "improvement_factor": value,
                        "improvement_pct": (value - 1.0) * 100.0,
                    }
                )
    return rows, factors


def aggregate(
    factors: dict[tuple[str, str, str], float], workloads: list[str], method: str, phase: str
) -> float:
    return geometric_mean([factors[(method, phase, workload)] for workload in workloads])


def build_aggregates_and_claims(
    factors: dict[tuple[str, str, str], float], manifest: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    aggregate_rows: list[dict[str, Any]] = []
    claim_rows: list[dict[str, Any]] = []
    for cohort, info in manifest["cohorts"].items():
        workloads = list(info["workloads"])
        values: dict[tuple[str, str], float] = {}
        for phase in ("tuning", "stable"):
            for method in ("sematune_app", "sematune_system", "mlos_app"):
                value = aggregate(factors, workloads, method, phase)
                values[(method, phase)] = value
                aggregate_rows.append(
                    {
                        "cohort": cohort,
                        "cohort_label": info["label"],
                        "n_workloads": len(workloads),
                        "workloads": ",".join(workloads),
                        "phase": phase,
                        "method": method,
                        "aggregate_factor": value,
                        "aggregate_pct": (value - 1.0) * 100.0,
                    }
                )
        app = values[("sematune_app", "stable")]
        system = values[("sematune_system", "stable")]
        mlos = values[("mlos_app", "stable")]
        comparisons = (
            ("C1", "TuxBot App / Default Parameters", app, app > 1.0),
            ("C2", "TuxBot App / MLOS App", app / mlos, app > mlos),
            ("C3", "TuxBot System / MLOS App", system / mlos, system > mlos),
        )
        for claim, comparison, value, consistent in comparisons:
            claim_rows.append(
                {
                    "claim": claim,
                    "cohort": cohort,
                    "cohort_label": info["label"],
                    "n_workloads": len(workloads),
                    "evidence_status": "COMPLETE",
                    "observation_status": "CONSISTENT" if consistent else "DIVERGENT",
                    "comparison": comparison,
                    "fresh_factor": value,
                    "fresh_pct": (value - 1.0) * 100.0,
                }
            )
    return aggregate_rows, claim_rows


def result_roots(results_dir: Path, manifest: dict[str, Any], method: str) -> list[str]:
    roots: list[str] = []
    for job in manifest["jobs"]:
        _, job_method = job_identity(job["id"])
        if job_method != method:
            continue
        root = str(results_dir / Path(job["target_results_dir"]).parts[0])
        if root not in roots:
            roots.append(root)
    return roots


def run_paper_plot(
    *,
    results_dir: Path,
    output: Path,
    manifest: dict[str, Any],
    plot_name: str,
    custom_columns: str,
    extra_args: list[str],
) -> None:
    """Use the paper plot implementation and styling on the fresh result roots."""

    plots = output / "plots"
    plots.mkdir(parents=True, exist_ok=True)
    retry_roots: list[str] = []
    for method in ("sematune_app", "sematune_system", "mlos_app"):
        for root in result_roots(results_dir, manifest, method):
            if root not in retry_roots:
                retry_roots.append(root)
    fixed_roots = result_roots(results_dir, manifest, "fixed")
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "plot_retry_aggregate_improvement.py"),
        "--result-paths",
        *retry_roots,
        "--fallback-fixed-paths",
        *fixed_roots,
        "--fallback-tuner-paths",
        *fixed_roots,
        "--workloads",
        ",".join(manifest["workloads"]),
        "--custom-columns",
        custom_columns,
        "--aggregate-stat",
        "geomean",
        "--subset",
        "union",
        "--tuning-window",
        "1-30",
        "--stable-window",
        "31-50",
        "--preserve-empty-methods",
        "--plot-output",
        str(plots / PLOT_FILES[plot_name]),
        "--csv-output",
        str(plots / PLOT_FILES[plot_name].replace(".pdf", ".csv")),
        *extra_args,
    ]
    subprocess.run(command, cwd=REPO_ROOT, check=True)


def make_plots(output: Path, results_dir: Path, manifest: dict[str, Any]) -> None:
    # C1 and C2 share paper Plot 6. Preserve the complete original method grid so
    # unavailable methods remain empty instead of changing bar widths or spacing.
    run_paper_plot(
        results_dir=results_dir,
        output=output,
        manifest=manifest,
        plot_name="plot6",
        custom_columns=PLOT6_COLUMNS,
        extra_args=[
            "--error-bars", "--preserve-empty-methods", "--auto-y", "--right-trim-pts", "0",
            "--output-width-pts", "564.1350915211",
            "--output-height-pts", "226.18",
        ],
    )
    # C3 uses paper Plot 7 with the full App/System/IPC and tuner grouping.
    run_paper_plot(
        results_dir=results_dir,
        output=output,
        manifest=manifest,
        plot_name="plot7",
        custom_columns=PLOT7_COLUMNS,
        extra_args=[
            "--error-bars", "--preserve-empty-methods", "--auto-y",
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


def write_report(
    report_dir: Path,
    manifest: dict[str, Any],
    claims: list[dict[str, Any]],
    aggregate_rows: list[dict[str, Any]],
) -> None:
    payload = {
        "schema_version": 1,
        "workflow": manifest["workflow"],
        "execution_mode": "real-provider",
        "configurations": len(manifest["jobs"]),
        "measurement_windows": sum(
            int(job["completion"]["measurement_windows"])
            for job in manifest["jobs"]
        ),
        "llm_configurations": sum(
            suite.is_llm_config(suite.materialized_config(job))
            for job in manifest["jobs"]
        ),
        "claims": ["C1", "C2", "C3"],
        "c4_evaluated": False,
        "fresh_repetitions": 1,
        "workloads": [
            {"id": workload, "label": manifest["workload_labels"][workload]}
            for workload in manifest["workloads"]
        ],
        "cohorts": manifest["cohorts"],
        "fresh_claims": claims,
        "aggregate_improvement": aggregate_rows,
        "paper_equivalent_plots": {
            "plot_6": f"fresh/plots/{PLOT_FILES['plot6']}",
            "plot_7": f"fresh/plots/{PLOT_FILES['plot7']}",
        },
        "claim_plot_map": {
            "C1": "plot_6",
            "C2": "plot_6",
            "C3": "plot_7",
        },
        "interpretation": (
            "CONSISTENT validates the direction of the claim for one stochastic live-provider repetition; "
            "exact paper percentages are not acceptance thresholds."
        ),
    }
    write_json(report_dir / "c123_family_report.json", payload)
    lines = [
        "# Live-provider C1–C3 family-workload report",
        "",
        "One fresh provider-backed repetition covers the 11 paper workloads from BenchBase, "
        "Sysbench, and TailBench. C4 and trace replay are outside this run.",
        "The scoped evidence contains 44 configurations (22 live-LLM) and 2,200 "
        "measurement windows.",
        "",
        "The original with/without-Xapian convention is retained. Because Mutilate is outside "
        "this family-only scope, the second cohort excludes Xapian only.",
        "",
        "| Claim | Cohort | Workloads | Evidence | Observation | Fresh comparison |",
        "|---|---|---:|---|---|---:|",
    ]
    for row in claims:
        lines.append(
            f"| {row['claim']} | {row['cohort_label']} | {row['n_workloads']} | "
            f"{row['evidence_status']} | {row['observation_status']} | "
            f"{row['fresh_factor']:.4f}× ({row['fresh_pct']:+.2f}%) |"
        )
    lines.extend(
        [
            "",
            "`CONSISTENT` checks claim direction only. A `DIVERGENT` stochastic observation is "
            "reported rather than hidden or replaced.",
            "",
            "Paper-equivalent Plots 6 and 7 are under [`fresh/plots/`](fresh/plots/) and disaggregated values are under "
            "[`fresh/tables/`](fresh/tables/).",
            "",
            f"C1/C2: [`fresh/plots/{PLOT_FILES['plot6']}`](fresh/plots/{PLOT_FILES['plot6']}).",
            "",
            f"C3: [`fresh/plots/{PLOT_FILES['plot7']}`](fresh/plots/{PLOT_FILES['plot7']}).",
            "",
            "Machine-readable values are in [`c123_family_report.json`](c123_family_report.json).",
            "",
        ]
    )
    (report_dir / "c123_family_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()

    manifest = suite.load_manifest(args.manifest.resolve())
    output = args.output_dir.resolve()
    report_dir = args.report_dir.resolve()
    phase_rows = load_phase_means(args.results_dir.resolve(), manifest)
    factor_rows, factors = build_factors(phase_rows, manifest)
    aggregate_rows, claims = build_aggregates_and_claims(factors, manifest)
    make_plots(output, args.results_dir.resolve(), manifest)
    tables = output / "tables"
    write_csv(
        tables / "c123_phase_metrics.csv",
        phase_rows,
        [
            "job_id", "workload", "workload_label", "method", "phase", "metric",
            "optimization_goal", "mean_metric", "history",
        ],
    )
    write_csv(
        tables / "c123_improvement_factors.csv",
        factor_rows,
        [
            "workload", "workload_label", "method", "phase", "metric",
            "optimization_goal", "fixed_mean", "method_mean", "improvement_factor",
            "improvement_pct",
        ],
    )
    write_csv(
        tables / "c123_aggregate_improvement.csv",
        aggregate_rows,
        [
            "cohort", "cohort_label", "n_workloads", "workloads", "phase", "method",
            "aggregate_factor", "aggregate_pct",
        ],
    )
    write_csv(
        tables / "c123_claim_summary.csv",
        claims,
        [
            "claim", "cohort", "cohort_label", "n_workloads", "evidence_status",
            "observation_status", "comparison", "fresh_factor", "fresh_pct",
        ],
    )
    write_report(report_dir, manifest, claims, aggregate_rows)
    print(f"C123_FAMILY_PLOTS: PASS ({output})")
    for row in claims:
        print(
            f"{row['claim']} [{row['cohort']}]: {row['evidence_status']} / "
            f"{row['observation_status']} ({row['fresh_pct']:+.2f}%)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
