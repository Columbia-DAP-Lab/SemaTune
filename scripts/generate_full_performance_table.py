#!/usr/bin/env python3
"""
Generate the full-performance LaTeX table from result directories.

Outputs a complete table* block compatible with sections/5_evaluation.tex.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

HISTORY_PATTERNS = ("optimization_history_*.json", "dual_loop_*.json")
SUCCESSFUL_TERMINATION_REASONS = frozenset({
    "complete",
    "completed",
    "completed_early",
})


@dataclass
class PhaseStats:
    mean: Optional[float]
    runs: int


@dataclass
class WorkloadStats:
    workload: str
    benchmark_label: str
    obj_label: str
    goal: str
    default_tuning: Optional[float]
    default_stable: Optional[float]
    by_tuner: Dict[str, Dict[str, Optional[float]]]  # tuner -> {"tuning": v, "stable": v}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate full-performance LaTeX table from result dirs.")
    p.add_argument(
        "--result-paths",
        nargs="+",
        required=True,
        help="List of results root paths (all_results/results_config_* or workload dir path).",
    )
    p.add_argument(
        "--output",
        default="/tmp/full_performance_table.tex",
        help="Output path for generated LaTeX table block.",
    )
    p.add_argument(
        "--patch-file",
        default=None,
        help="Optional TeX file to patch in-place (replaces table labeled tab:full_performance).",
    )
    p.add_argument(
        "--fallback-fixed-paths",
        nargs="*",
        default=[],
        help="Optional results roots/workload dirs used only to locate matching fixed baselines by workload name.",
    )
    p.add_argument(
        "--fallback-tuner-paths",
        nargs="*",
        default=[],
        help="Optional results roots/workload dirs used to locate tuner dirs missing from --result-paths by workload name.",
    )
    p.add_argument(
        "--tuners",
        default="sys,mlos,bo,dqn,ql",
        help="Comma-separated tuner buckets to include in the table (default: sys,mlos,bo,dqn,ql).",
    )
    p.add_argument(
        "--tuning-window",
        default="1-30",
        help="Inclusive tuning window as START-END (default: 1-30).",
    )
    p.add_argument(
        "--stable-window",
        default="31-50",
        help="Inclusive stable window as START-END (default: 31-50).",
    )
    p.add_argument(
        "--label",
        default="tab:full_performance",
        help="LaTeX label for the generated table (default: tab:full_performance).",
    )
    p.add_argument(
        "--custom-columns",
        default="",
        help=(
            "Optional comma-separated LABEL:DIRNAME list for exact tuner columns, "
            'e.g. "Tuxbot-App:llm_dual_app_metrics_final_actor,MLOS:mlos_50_tuning_only". '
            "When set, this overrides --tuners."
        ),
    )
    p.add_argument(
        "--run-aggregate",
        default="mean",
        choices=("mean", "geomean"),
        help="How to aggregate across reruns within each tuner/phase (default: mean).",
    )
    p.add_argument(
        "--sys-dir-name",
        default="",
        help="Optional exact workload subdirectory name to use for the sys bucket.",
    )
    p.add_argument(
        "--mlos-dir-name",
        default="",
        help="Optional exact workload subdirectory name to use for the mlos bucket.",
    )
    p.add_argument(
        "--workloads",
        default="",
        help="Optional comma-separated workload names to include, in output order.",
    )
    return p.parse_args()


def parse_window(spec: str, arg_name: str) -> Tuple[int, int]:
    m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", spec or "")
    if not m:
        raise SystemExit(f"{arg_name} must be START-END, got: {spec!r}")
    lo = int(m.group(1))
    hi = int(m.group(2))
    if lo <= 0 or hi < lo:
        raise SystemExit(f"{arg_name} must satisfy 1 <= START <= END, got: {spec!r}")
    return lo, hi


def canonical_goal(raw: Optional[str]) -> str:
    s = (raw or "").strip().lower()
    if s in {"maximize", "max", "higher_is_better"}:
        return "maximize"
    return "minimize"


def pick_iteration(entry: Dict[str, Any], fallback: int) -> int:
    for k in ("iteration", "window_number", "index"):
        if k in entry:
            try:
                return int(entry[k])
            except Exception:
                pass
    return fallback


def to_float(v: Any) -> Optional[float]:
    try:
        out = float(v)
    except Exception:
        return None
    if not math.isfinite(out):
        return None
    return out


def metric_value(entry: Dict[str, Any], metric_name: Optional[str]) -> Optional[float]:
    metrics = entry.get("metrics", {}) or {}
    sysm = entry.get("system_metrics", {}) or {}
    candidates: List[Any] = [entry.get("raw_metric_value")]
    if metric_name:
        candidates.append(metrics.get(metric_name))
        candidates.append(sysm.get(metric_name))
        # Common fallback aliases for p99 metrics.
        if metric_name == "latency_p99":
            candidates.append(metrics.get("p_99_latency"))
    candidates.append(entry.get("reward"))
    for c in candidates:
        parsed = to_float(c)
        if parsed is not None:
            return parsed
    return None


def iter_history_files(tuner_dir: Path) -> Iterable[Path]:
    for pat in HISTORY_PATTERNS:
        for fp in sorted(tuner_dir.glob(pat)):
            if fp.is_file():
                yield fp


def load_json(fp: Path) -> Optional[Dict[str, Any]]:
    try:
        with fp.open("r") as f:
            data = json.load(f)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def history_run_completed_successfully(data: Dict[str, Any]) -> bool:
    """Return True for complete runs, False for explicit failures/incomplete runs.

    Older result files often omit a completion marker entirely; keep those for
    backward compatibility. Newer files write either ``reason`` or
    ``terminated_reason``.
    """
    markers: List[str] = []
    for key in ("reason", "terminated_reason"):
        raw_value = data.get(key)
        if raw_value is None:
            continue
        marker = str(raw_value).strip().lower()
        if not marker or marker == "none":
            continue
        markers.append(marker)
    if not markers:
        return True
    return all(marker in SUCCESSFUL_TERMINATION_REASONS for marker in markers)


def phase_mean_for_run(
    data: Dict[str, Any], metric_name: Optional[str], window: Tuple[int, int]
) -> Optional[float]:
    lo, hi = window
    hist = data.get("history", [])
    if not isinstance(hist, list):
        return None
    vals: List[float] = []
    for pos, e in enumerate(hist, start=1):
        if not isinstance(e, dict):
            continue
        it = pick_iteration(e, pos)
        if it < lo or it > hi:
            continue
        v = metric_value(e, metric_name)
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    return statistics.fmean(vals)


def final_aggregate_value_for_run(
    data: Dict[str, Any], metric_name: Optional[str]
) -> Optional[float]:
    hist = data.get("history", [])
    if not isinstance(hist, list) or not hist:
        return None
    rows = [e for e in hist if isinstance(e, dict)]
    if not rows:
        return None
    cfg = data.get("config", {}) or {}
    benchmark_name = str(cfg.get("benchmark") or "")
    last_metrics = rows[-1].get("metrics", {}) or {}
    is_final_aggregate_only = (
        "dcperf_spark_online" in benchmark_name
        or "dcperf_mediawiki_online" in benchmark_name
        or any(str(k).startswith("aggregate_") for k in last_metrics.keys())
    )
    if not is_final_aggregate_only:
        return None
    return metric_value(rows[-1], metric_name)


def tuner_dir_uses_final_aggregate_only(
    tuner_dir: Path,
    metric_name: Optional[str],
) -> bool:
    for fp in iter_history_files(tuner_dir):
        data = load_json(fp)
        if data is None:
            continue
        if not history_run_completed_successfully(data):
            continue
        file_metric = metric_name
        if file_metric is None:
            cfg = data.get("config", {}) or {}
            m = cfg.get("optimization_metric")
            file_metric = str(m).strip() if m else None
        if final_aggregate_value_for_run(data, file_metric) is not None:
            return True
    return False


def aggregate_run_values(values: Sequence[float], method: str) -> Optional[float]:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not vals:
        return None
    if method == "geomean":
        positive = [v for v in vals if v > 0]
        if len(positive) == len(vals):
            return statistics.geometric_mean(positive)
    return statistics.fmean(vals)


def aggregate_tuner_phase(
    tuner_dir: Path,
    metric_name: Optional[str],
    tuning_window: Tuple[int, int],
    stable_window: Tuple[int, int],
    run_aggregate: str,
) -> Dict[str, PhaseStats]:
    tuning_runs: List[float] = []
    stable_runs: List[float] = []
    for fp in iter_history_files(tuner_dir):
        data = load_json(fp)
        if data is None:
            continue
        if not history_run_completed_successfully(data):
            continue
        # Prefer caller-provided metric; fallback to per-file config.
        file_metric = metric_name
        if file_metric is None:
            cfg = data.get("config", {}) or {}
            m = cfg.get("optimization_metric")
            file_metric = str(m).strip() if m else None
        final_aggregate = final_aggregate_value_for_run(data, file_metric)
        if final_aggregate is not None:
            stable_runs.append(final_aggregate)
            continue
        t = phase_mean_for_run(data, file_metric, tuning_window)
        s = phase_mean_for_run(data, file_metric, stable_window)
        if t is not None:
            tuning_runs.append(t)
        if s is not None:
            stable_runs.append(s)
    return {
        "tuning": PhaseStats(mean=aggregate_run_values(tuning_runs, run_aggregate), runs=len(tuning_runs)),
        "stable": PhaseStats(mean=aggregate_run_values(stable_runs, run_aggregate), runs=len(stable_runs)),
    }


def improvement_pct(default: Optional[float], candidate: Optional[float], goal: str) -> Optional[float]:
    if default is None or candidate is None:
        return None
    if default == 0 or candidate == 0:
        return None
    if goal == "maximize":
        return ((candidate / default) - 1.0) * 100.0
    return ((default / candidate) - 1.0) * 100.0


def classify_tuner_dir(name: str, tuner_type: Optional[str]) -> Optional[str]:
    n = name.lower()
    t = (tuner_type or "").lower()
    if n == "fixed" or t == "fixed":
        return "fixed"
    if n == "mlos" or t == "mlos":
        return "mlos"
    if n == "bayesian" or t in {"bayesian", "bo", "bayesopt"}:
        return "bo"
    if n == "dqn" or t == "dqn":
        return "dqn"
    if n in {"qlearning", "q_learning"} or t in {"qlearning", "q_learning"}:
        return "ql"
    if "llm_dual_full_metrics_mode3" in n:
        return "sys"
    if t == "llm":
        return "sys"
    return None


def detect_metric_goal_from_fixed(fixed_dir: Path, workload: str) -> Tuple[Optional[str], str]:
    for fp in iter_history_files(fixed_dir):
        data = load_json(fp)
        if data is None:
            continue
        if not history_run_completed_successfully(data):
            continue
        cfg = data.get("config", {}) or {}
        metric = cfg.get("optimization_metric")
        metric_name = str(metric).strip() if metric else None
        goal = canonical_goal(cfg.get("optimization_goal"))
        # Throughput objectives should be maximize, even if config says minimize.
        if metric_name and ("throughput" in metric_name.lower() or "tput" in workload.lower()):
            goal = "maximize"
        return metric_name, goal
    return None, "minimize"


def detect_metric_goal_from_any_tuner(workload_dir: Path, workload: str) -> Tuple[Optional[str], str]:
    for tuner_dir in sorted([d for d in workload_dir.iterdir() if d.is_dir()]):
        for fp in iter_history_files(tuner_dir):
            data = load_json(fp)
            if data is None:
                continue
            if not history_run_completed_successfully(data):
                continue
            cfg = data.get("config", {}) or {}
            metric = cfg.get("optimization_metric")
            metric_name = str(metric).strip() if metric else None
            goal = canonical_goal(cfg.get("optimization_goal"))
            if metric_name and ("throughput" in metric_name.lower() or "tput" in workload.lower()):
                goal = "maximize"
            return metric_name, goal
    return None, "minimize"


def resolve_workload_dirs(paths: Sequence[str]) -> List[Path]:
    tuner_dir_markers = ("fixed", "mlos", "bayesian", "dqn", "qlearning")

    def looks_like_workload_dir(p: Path) -> bool:
        if not p.is_dir():
            return False
        for child in sorted([x for x in p.iterdir() if x.is_dir()]):
            if next(iter(iter_history_files(child)), None) is not None:
                return True
        return False

    def child_workload_dirs(p: Path) -> List[Path]:
        if not p.is_dir():
            return []
        out: List[Path] = []
        for d in sorted([x for x in p.iterdir() if x.is_dir()]):
            if looks_like_workload_dir(d) or any((d / x).is_dir() for x in tuner_dir_markers):
                out.append(d)
        return out

    out: List[Path] = []
    seen = set()
    for raw in paths:
        p = Path(raw).resolve()
        if not p.exists():
            continue
        children = child_workload_dirs(p)
        # Results root path; prefer immediate child workload dirs when they exist.
        if children:
            for d in children:
                key = d.name
                if key not in seen:
                    seen.add(key)
                    out.append(d)
            continue
        # Workload dir directly provided.
        if looks_like_workload_dir(p) or (p.is_dir() and any((p / x).is_dir() for x in tuner_dir_markers)):
            key = p.name
            if key not in seen:
                seen.add(key)
                out.append(p)
    return out


def parse_custom_columns(spec: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    if not spec.strip():
        return out
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise SystemExit(f"--custom-columns entry must be LABEL:DIRNAME, got: {chunk!r}")
        label, dirname = chunk.split(":", 1)
        label = label.strip()
        dirname = dirname.strip()
        if not label or not dirname:
            raise SystemExit(f"--custom-columns entry must be LABEL:DIRNAME, got: {chunk!r}")
        out.append((label, dirname))
    return out


def resolve_tuner_dir(workload_dir: Path, dir_spec: str) -> Optional[Path]:
    for candidate in [chunk.strip() for chunk in dir_spec.split("|") if chunk.strip()]:
        # Prefer the explicitly requested result directory. Directories ending
        # in " copy" are historical backups and can contain misplaced runs;
        # consult them only when the canonical directory is absent.
        names = [candidate] if candidate.endswith(" copy") else [candidate, f"{candidate} copy"]
        for name in names:
            path = workload_dir / name
            if path.is_dir():
                return path
    return None


def resolve_fixed_dirs(paths: Sequence[str]) -> Dict[str, Path]:
    out: Dict[str, Path] = {}
    for raw in paths:
        p = Path(raw).resolve()
        if not p.exists():
            continue
        if p.is_dir() and (p / "fixed").is_dir():
            out.setdefault(p.name, p / "fixed")
            continue
        if p.is_dir():
            for d in sorted([x for x in p.iterdir() if x.is_dir()]):
                fixed_dir = d / "fixed"
                if fixed_dir.is_dir():
                    out.setdefault(d.name, fixed_dir)
    return out


def resolve_workload_dir_map(paths: Sequence[str]) -> Dict[str, Path]:
    return {wd.name: wd for wd in resolve_workload_dirs(paths)}


def benchmark_label(workload: str) -> str:
    mapping = {
        "dcperf_spark_online_tput": "Spark-Online",
        "dcperf_mediawiki_online_tput": "MediaWiki-Online",
        "masstree_hi_p99": "Masstree",
        "otmetrics_p99": "OTMetrics",
        "sibench_hi_p99": "Sibench",
        "silo_hi_p99": "Silo",
        "sphinx_tput_max": "Sphinx",
        "sysbench_cpu_tput": "Sys-CPU",
        "sysbench_oltp_rw_hi_p99": "Sys-OLTP-RW",
        "tpcc_hi_p99": "TPCC",
        "twitter_p99": "Twitter",
        "wikipedia_p99": "Wikipedia",
        "xapian_hi_p99": "Xapian",
        "ycsb_hi_p99": "YCSB",
    }
    return mapping.get(workload, workload)


def objective_label(workload: str, metric_name: Optional[str], goal: str) -> str:
    m = (metric_name or "").lower()
    if "throughput" in m or "tput" in workload.lower():
        return r"tput $\uparrow$"
    if "latency" in m or "p99" in workload.lower():
        return r"p99 $\downarrow$"
    return r"obj $\uparrow$" if goal == "maximize" else r"obj $\downarrow$"


def fmt_value(v: Optional[float], huge_bad: bool) -> str:
    if v is None:
        return "XXX"
    if huge_bad:
        return r"\texttt{>>}"
    return f"{v:.1f}"


def fmt_pct(v: Optional[float], bold: bool) -> str:
    if v is None:
        return "XXX"
    color = "green!60!black" if v >= 0 else "red!70!black"
    txt = rf"\textcolor{{{color}}}{{{v:+.1f}}}"
    if bold:
        return rf"\textbf{{{txt}}}"
    return txt


def is_huge_harmful(default: Optional[float], candidate: Optional[float], goal: str) -> bool:
    if default is None or candidate is None or default <= 0 or candidate <= 0:
        return False
    if goal == "minimize":
        return (candidate / default) >= 100.0
    # For maximize objectives, do not collapse values to >>.
    return False


def best_tuner_for_phase(
    values: Dict[str, Optional[float]], goal: str
) -> Optional[str]:
    candidates = {k: v for k, v in values.items() if v is not None}
    if not candidates:
        return None
    if goal == "maximize":
        return max(candidates.items(), key=lambda kv: kv[1])[0]
    return min(candidates.items(), key=lambda kv: kv[1])[0]


def gather_workload_stats(
    workload_dir: Path,
    tuning_window: Tuple[int, int],
    stable_window: Tuple[int, int],
    fallback_fixed_dir: Optional[Path] = None,
    fallback_tuner_dir: Optional[Path] = None,
    preferred_dirs: Optional[Dict[str, str]] = None,
    custom_columns: Optional[List[Tuple[str, str]]] = None,
    run_aggregate: str = "mean",
) -> Optional[WorkloadStats]:
    tuner_dirs = [d for d in sorted(workload_dir.iterdir()) if d.is_dir()]
    bucket_to_dir: Dict[str, Path] = {}
    preferred_dirs = preferred_dirs or {}
    custom_columns = custom_columns or []

    for bucket, exact_name in preferred_dirs.items():
        if not exact_name:
            continue
        exact_dir = resolve_tuner_dir(workload_dir, exact_name)
        if exact_dir is None and fallback_tuner_dir is not None:
            exact_dir = resolve_tuner_dir(fallback_tuner_dir, exact_name)
        if exact_dir is not None:
            bucket_to_dir[bucket] = exact_dir

    for label, exact_name in custom_columns:
        exact_dir = resolve_tuner_dir(workload_dir, exact_name)
        if exact_dir is None and fallback_tuner_dir is not None:
            exact_dir = resolve_tuner_dir(fallback_tuner_dir, exact_name)
        if exact_dir is not None:
            bucket_to_dir[label] = exact_dir

    for td in tuner_dirs:
        tuner_type = None
        # Peek one file for tuner_type.
        sample = next(iter(iter_history_files(td)), None)
        if sample:
            data = load_json(sample)
            if data:
                tuner_type = str((data.get("config", {}) or {}).get("tuner_type", "")).strip()
        bucket = classify_tuner_dir(td.name, tuner_type)
        if bucket == "fixed":
            bucket_to_dir.setdefault("fixed", td)
        elif bucket and bucket not in bucket_to_dir and not custom_columns:
            bucket_to_dir[bucket] = td

    if fallback_tuner_dir is not None:
        for td in sorted([d for d in fallback_tuner_dir.iterdir() if d.is_dir()]):
            tuner_type = None
            sample = next(iter(iter_history_files(td)), None)
            if sample:
                data = load_json(sample)
                if data:
                    tuner_type = str((data.get("config", {}) or {}).get("tuner_type", "")).strip()
            bucket = classify_tuner_dir(td.name, tuner_type)
            if bucket and bucket not in bucket_to_dir and not custom_columns:
                bucket_to_dir[bucket] = td

    if fallback_fixed_dir is not None:
        bucket_to_dir["fixed"] = fallback_fixed_dir

    if "fixed" in bucket_to_dir:
        metric_name, goal = detect_metric_goal_from_fixed(bucket_to_dir["fixed"], workload_dir.name)
    else:
        metric_name, goal = detect_metric_goal_from_any_tuner(workload_dir, workload_dir.name)

    by_tuner: Dict[str, Dict[str, Optional[float]]] = {}
    ordered_keys = [label for label, _ in custom_columns] if custom_columns else ["sys", "mlos", "bo", "dqn", "ql"]
    for bucket in ordered_keys + ["fixed"]:
        td = bucket_to_dir.get(bucket)
        if td is None:
            by_tuner[bucket] = {"tuning": None, "stable": None}
            continue
        phase = aggregate_tuner_phase(td, metric_name, tuning_window, stable_window, run_aggregate)
        by_tuner[bucket] = {
            "tuning": phase["tuning"].mean,
            "stable": phase["stable"].mean,
        }

    default_tuning = by_tuner["fixed"]["tuning"]
    default_stable = by_tuner["fixed"]["stable"]
    fixed_dir = bucket_to_dir.get("fixed")
    if (
        default_tuning is None
        and default_stable is not None
        and fixed_dir is not None
        and tuner_dir_uses_final_aggregate_only(fixed_dir, metric_name)
    ):
        # Final-aggregate-only workloads (e.g., Spark-online) do not have a
        # meaningful tuning-phase series; show the fixed aggregate in the
        # visible Default column so the row still exposes the baseline value.
        default_tuning = default_stable

    return WorkloadStats(
        workload=workload_dir.name,
        benchmark_label=benchmark_label(workload_dir.name),
        obj_label=objective_label(workload_dir.name, metric_name, goal),
        goal=goal,
        default_tuning=default_tuning,
        default_stable=default_stable,
        by_tuner=by_tuner,
    )


def latex_row(stat: WorkloadStats, tuner_order: Sequence[str]) -> str:
    # Determine best in each phase.
    tuning_values = {k: stat.by_tuner[k]["tuning"] for k in tuner_order}
    stable_values = {k: stat.by_tuner[k]["stable"] for k in tuner_order}
    best_tuning = best_tuner_for_phase(tuning_values, stat.goal)
    best_stable = best_tuner_for_phase(stable_values, stat.goal)

    parts = [stat.benchmark_label, stat.obj_label, (f"{stat.default_tuning:.1f}" if stat.default_tuning is not None else "XXX")]

    def append_phase(tuner: str, phase: str, default_val: Optional[float], best: Optional[str]) -> None:
        v = stat.by_tuner[tuner][phase]
        pct = improvement_pct(default_val, v, stat.goal)
        huge_bad = is_huge_harmful(default_val, v, stat.goal)
        value_text = fmt_value(v, huge_bad=huge_bad)
        if tuner == best and v is not None and not huge_bad:
            value_text = rf"\textbf{{{value_text}}}"
        pct_text = fmt_pct(pct, bold=(tuner == best and pct is not None and not huge_bad))
        parts.extend([value_text, pct_text])

    for tuner in tuner_order:
        append_phase(tuner, "tuning", stat.default_tuning, best_tuning)
    for tuner in tuner_order:
        append_phase(tuner, "stable", stat.default_stable, best_stable)

    return " & ".join(parts) + r" \\"


def tuner_label(bucket: str) -> str:
    mapping = {
        "sys": r"\sys",
        "mlos": "MLOS",
        "bo": "BO",
        "dqn": "DQN",
        "ql": "QL",
    }
    return mapping.get(bucket, bucket)


def build_table_block(
    rows: List[str],
    tuner_order: Sequence[str],
    tuner_labels: Dict[str, str],
    tuning_window: Tuple[int, int],
    stable_window: Tuple[int, int],
    label: str,
    run_aggregate: str,
) -> str:
    row_blob = "\n".join(rows)
    during_cols = 1 + (2 * len(tuner_order))
    stable_cols = 2 * len(tuner_order)
    tabular_spec = "ll|" + ("r" * during_cols) + "|" + ("r" * stable_cols)
    header_labels = ["Benchmark", "Obj", "Default"]
    for tuner in tuner_order:
        header_labels.extend([tuner_labels.get(tuner, tuner_label(tuner)), r"(\%)"])
    stable_labels = []
    for tuner in tuner_order:
        stable_labels.extend([tuner_labels.get(tuner, tuner_label(tuner)), r"(\%)"])
    header_line = " & ".join(header_labels + stable_labels) + r" \\"
    cmid_left_end = 2 + during_cols
    cmid_right_start = cmid_left_end + 1
    cmid_right_end = cmid_right_start + stable_cols - 1
    tuning_label = f"{tuning_window[0]}--{tuning_window[1]}"
    stable_label = f"{stable_window[0]}--{stable_window[1]}"
    agg_text = "geometric mean" if run_aggregate == "geomean" else "mean"
    return rf"""\begin{{table*}}[t]
    \centering
    \scriptsize
    \setlength{{\tabcolsep}}{{3pt}}
    \renewcommand{{\arraystretch}}{{1.12}}
    \resizebox{{\textwidth}}{{!}}{{%
    \begin{{tabular}}{{{tabular_spec}}}
    \toprule
    ~ & ~ & \multicolumn{{{during_cols}}}{{c|}}{{During tuning ({tuning_label})}} & \multicolumn{{{stable_cols}}}{{c}}{{Stable phase ({stable_label})}} \\
    \cmidrule(lr){{3-{cmid_left_end}}}\cmidrule(lr){{{cmid_right_start}-{cmid_right_end}}}
    {header_line}
    \midrule
{row_blob}
    \bottomrule
    \end{{tabular}}
    }}
    \caption{{
        Relative improvement in objective metric over the default OS configuration during the tuning phase ({tuning_label}) and the stable phase ({stable_label}).
        \ttt{{Default}} reports the baseline {agg_text} metric value in the tuning phase.
        Each tuner reports absolute {agg_text} value across reruns and relative improvement \texttt{{(\%)}} computed phase-wise against Fixed.
        \texttt{{>>}} denotes a very large harmful increase.
        In the stable phase, no further tuning actions are applied and performance reflects the last configuration set at the end of tuning.
    }}
    \label{{{label}}}
\end{{table*}}
"""


def patch_tex_file(tex_path: Path, new_block: str) -> None:
    text = tex_path.read_text()
    pattern = re.compile(
        r"\\begin\{table\*\}\[t\].*?\\label\{tab:full_performance\}.*?\\end\{table\*\}",
        re.DOTALL,
    )
    if not pattern.search(text):
        raise SystemExit("Could not find table block with label tab:full_performance to patch.")
    updated = pattern.sub(lambda _m: new_block.strip(), text, count=1)
    tex_path.write_text(updated)


def main() -> None:
    args = parse_args()
    tuning_window = parse_window(args.tuning_window, "--tuning-window")
    stable_window = parse_window(args.stable_window, "--stable-window")
    workload_dirs = resolve_workload_dirs(args.result_paths)
    if not workload_dirs:
        raise SystemExit("No workload dirs resolved from --result-paths.")
    workload_order = [w.strip() for w in args.workloads.split(",") if w.strip()]
    if workload_order:
        workload_map = {wd.name: wd for wd in workload_dirs}
        workload_dirs = [workload_map[name] for name in workload_order if name in workload_map]
        if not workload_dirs:
            raise SystemExit("No workload dirs remain after applying --workloads.")

    custom_columns = parse_custom_columns(args.custom_columns)
    if custom_columns:
        tuner_order = [label for label, _ in custom_columns]
        tuner_labels = {label: label for label, _ in custom_columns}
    else:
        tuner_order = [t.strip().lower() for t in args.tuners.split(",") if t.strip()]
        valid_tuners = {"sys", "mlos", "bo", "dqn", "ql"}
        if not tuner_order or any(t not in valid_tuners for t in tuner_order):
            raise SystemExit(f"--tuners must be a comma-separated subset of: {sorted(valid_tuners)}")
        tuner_labels = {t: tuner_label(t) for t in tuner_order}

    fallback_fixed_map = resolve_fixed_dirs(args.fallback_fixed_paths)
    fallback_tuner_map = resolve_workload_dir_map(args.fallback_tuner_paths)
    preferred_dirs = {
        "sys": args.sys_dir_name.strip(),
        "mlos": args.mlos_dir_name.strip(),
    }

    stats: List[WorkloadStats] = []
    for wd in workload_dirs:
        s = gather_workload_stats(
            wd,
            tuning_window=tuning_window,
            stable_window=stable_window,
            fallback_fixed_dir=fallback_fixed_map.get(wd.name),
            fallback_tuner_dir=fallback_tuner_map.get(wd.name),
            preferred_dirs=preferred_dirs,
            custom_columns=custom_columns,
            run_aggregate=args.run_aggregate,
        )
        if s is not None:
            stats.append(s)

    if not stats:
        raise SystemExit("No workload stats generated.")

    rows = [latex_row(s, tuner_order) for s in stats]
    block = build_table_block(rows, tuner_order, tuner_labels, tuning_window, stable_window, args.label, args.run_aggregate)

    out_path = Path(args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(block)
    print(f"Wrote table block: {out_path}")
    print("Rows:")
    for s in stats:
        print(f" - {s.workload}")

    if args.patch_file:
        patch_tex_file(Path(args.patch_file).resolve(), block)
        print(f"Patched file: {Path(args.patch_file).resolve()}")


if __name__ == "__main__":
    main()
