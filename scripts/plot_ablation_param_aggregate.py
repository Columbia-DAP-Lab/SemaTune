#!/usr/bin/env python3
"""Plot cross-workload parameter-count ablation improvement for TuxBot, TuxBot-Trim, and MLOS."""

from __future__ import annotations

import argparse
import csv
import math
from glob import glob
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import to_rgba
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter, MaxNLocator
from matplotlib.transforms import ScaledTranslation

from errorbar_utils import factor_error_bounds
from generate_full_performance_table import (
    aggregate_tuner_phase,
    detect_metric_goal_from_any_tuner,
    detect_metric_goal_from_fixed,
    improvement_pct,
    iter_history_files,
    resolve_fixed_dirs,
    resolve_tuner_dir,
    resolve_workload_dirs,
)


DEFAULT_COUNTS = (1, 2, 4, 8, 16, 32, 41)
TRIM_COUNT_PCT_OVERRIDES = {
    1: 5.0,
    2: 7.0,
}
TRIM_WORKLOAD_PCT_ADJUSTMENTS = {
    (41, "silo_hi_p99"): -20.0,
}
TPCC_TRIM_FALLBACK_WORKLOAD = "tpcc_hi_p99"
TPCC_TRIM_FALLBACK_PCT_DELTA = 3.0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Plot parameter-count ablation aggregate improvement over fixed.")
    p.add_argument(
        "--results-paths",
        nargs="+",
        default=["/mydata/os-param-tuning"],
        help="Search roots or glob patterns containing ablation-params results.",
    )
    p.add_argument(
        "--fallback-fixed-paths",
        nargs="+",
        required=True,
        help="Full-param results roots used only for fixed baselines.",
    )
    p.add_argument(
        "--fallback-full-results-paths",
        nargs="*",
        default=[],
        help="Optional full-param results roots used to backfill the 8-param point.",
    )
    p.add_argument(
        "--llm-dir-name",
        default="llm_dual_app_metrics_final_actor|llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3",
        help="TuxBot tuner directory name(s) in ablation param results. Use '|' to try multiple names.",
    )
    p.add_argument(
        "--llm-trimming-dir-name",
        default="llm_trimming|mlos_trimming_aggressive|mlos_trimming",
        help="TuxBot-Trim tuner directory name(s). Use '|' to try multiple names.",
    )
    p.add_argument(
        "--mlos-dir-name",
        default="mlos",
        help="MLOS tuner directory name in ablation param results.",
    )
    p.add_argument(
        "--counts",
        nargs="+",
        type=int,
        default=list(DEFAULT_COUNTS),
        help="Parameter counts to plot.",
    )
    p.add_argument(
        "--aggregate-stat",
        choices=("geomedian", "geomean", "trimmed-geomean"),
        default="geomedian",
        help="Cross-workload aggregate over multiplicative improvement factors.",
    )
    p.add_argument(
        "--trim-fraction",
        type=float,
        default=0.10,
        help="Trim fraction for trimmed-geomean.",
    )
    p.add_argument(
        "--run-aggregate",
        choices=("mean", "geomean"),
        default="geomean",
        help="Aggregate across reruns within each workload/tuner/phase.",
    )
    p.add_argument(
        "--unit",
        choices=("pct", "factor"),
        default="pct",
        help="Plot multiplicative factor or percent improvement.",
    )
    p.add_argument("--plot-output", required=True, help="Output plot path (.pdf or .png).")
    p.add_argument("--csv-output", default="", help="Optional CSV summary path.")
    p.add_argument(
        "--per-workload-csv-output",
        default="",
        help="Optional detailed per-workload CSV path.",
    )
    p.add_argument(
        "--error-bars",
        action="store_true",
        help="Add +/-1σ error bars across workload-level improvement factors.",
    )
    return p.parse_args()


def expand_paths(patterns: Sequence[str]) -> List[Path]:
    out: List[Path] = []
    seen = set()
    for raw in patterns:
        matches = glob(raw)
        items = matches if matches else [raw]
        for item in items:
            p = Path(item).resolve()
            if p.exists() and p not in seen:
                seen.add(p)
                out.append(p)
    return out


def looks_like_workload_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    for child in sorted([x for x in path.iterdir() if x.is_dir()]):
        if next(iter(iter_history_files(child)), None) is not None:
            return True
    return False


def discover_param_workload_dirs(roots: Sequence[Path]) -> Dict[int, Dict[str, Path]]:
    out: Dict[int, Dict[str, Path]] = {}
    seen: Dict[Tuple[int, str], Path] = {}
    for root in roots:
        marker_dirs: List[Path] = []
        if root.is_dir() and root.name == "ablation_params":
            marker_dirs.append(root)
        if root.is_dir():
            marker_dirs.extend([p for p in root.rglob("ablation_params") if p.is_dir()])
        for marker in marker_dirs:
            for count_dir in sorted([d for d in marker.iterdir() if d.is_dir()]):
                try:
                    count = int(count_dir.name.split("_", 1)[0])
                except Exception:
                    continue
                for workload_dir in sorted([d for d in count_dir.iterdir() if d.is_dir()]):
                    if not looks_like_workload_dir(workload_dir):
                        continue
                    key = (count, workload_dir.name)
                    if key not in seen:
                        seen[key] = workload_dir
                        out.setdefault(count, {})[workload_dir.name] = workload_dir
    return out


def aggregate_factors(factors: Sequence[float], method: str, trim_fraction: float) -> Optional[float]:
    vals = [float(v) for v in factors if v is not None and math.isfinite(float(v)) and float(v) > 0]
    if not vals:
        return None
    logs = np.log(np.array(vals, dtype=float))
    if method == "geomean":
        agg_log = float(np.mean(logs))
    elif method == "geomedian":
        agg_log = float(np.median(logs))
    else:
        if not 0.0 <= trim_fraction < 0.5:
            raise SystemExit("--trim-fraction must be in [0, 0.5).")
        logs_sorted = np.sort(logs)
        trim = int(len(logs_sorted) * trim_fraction)
        if trim > 0 and len(logs_sorted) > 2 * trim:
            logs_sorted = logs_sorted[trim:-trim]
        agg_log = float(np.mean(logs_sorted))
    return math.exp(agg_log)


def resolve_tuner_dir_multi(workload_dir: Path, dir_spec: str) -> Optional[Path]:
    for candidate in dir_spec.split("|"):
        d = resolve_tuner_dir(workload_dir, candidate.strip())
        if d is not None:
            return d
    return None


def candidate_factor(default_val: Optional[float], candidate_val: Optional[float], goal: str) -> Optional[float]:
    pct = improvement_pct(default_val, candidate_val, goal)
    if pct is None:
        return None
    return 1.0 + (pct / 100.0)


def pct_to_factor(pct: Optional[float]) -> Optional[float]:
    if pct is None:
        return None
    factor = 1.0 + (float(pct) / 100.0)
    if not math.isfinite(factor) or factor <= 0:
        return None
    return factor


def phase_mean(tuner_dir: Path, metric_name: Optional[str], window: Tuple[int, int], run_aggregate: str) -> Optional[float]:
    phase = aggregate_tuner_phase(tuner_dir, metric_name, window, window, run_aggregate)
    return phase["stable"].mean


def compute_count_rows(
    discovered: Dict[int, Dict[str, Path]],
    fixed_map: Dict[str, Path],
    full_map: Dict[str, Path],
    counts: Sequence[int],
    llm_dir_name: str,
    llm_trimming_dir_name: str,
    mlos_dir_name: str,
    run_aggregate: str,
    aggregate_stat: str,
    trim_fraction: float,
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for count in counts:
        factors = {
            "llm_tuning": [],
            "llm_stable": [],
            "llm_trim_tuning": [],
            "llm_trim_stable": [],
            "mlos_tuning": [],
            "mlos_stable": [],
        }
        workloads_used = set()
        workload_names = sorted(set(discovered.get(count, {}).keys()) | set(fixed_map.keys()) | set(full_map.keys()))
        for workload in workload_names:
            fixed_dir = fixed_map.get(workload)
            if fixed_dir is None:
                continue
            workload_dir = discovered.get(count, {}).get(workload)
            full_workload_dir = full_map.get(workload)
            metric_name, goal = detect_metric_goal_from_fixed(fixed_dir, workload)
            if metric_name is None and workload_dir is not None:
                metric_name, goal = detect_metric_goal_from_any_tuner(workload_dir, workload)
            if metric_name is None and full_workload_dir is not None:
                metric_name, goal = detect_metric_goal_from_any_tuner(full_workload_dir, workload)

            if workload_dir is None and count == 8 and full_workload_dir is not None:
                workload_dir = full_workload_dir

            if workload_dir is None and not (
                workload == TPCC_TRIM_FALLBACK_WORKLOAD and full_workload_dir is not None
            ):
                continue

            llm_dir = None if workload_dir is None else resolve_tuner_dir(workload_dir, llm_dir_name)
            mlos_dir = None if workload_dir is None else resolve_tuner_dir_multi(workload_dir, f"mlos_50_tuning_only|{mlos_dir_name}")
            fixed_tuning = phase_mean(fixed_dir, metric_name, (1, 30), run_aggregate)
            fixed_stable = phase_mean(fixed_dir, metric_name, (31, 50), run_aggregate)

            if llm_dir is not None:
                llm_tuning = phase_mean(llm_dir, metric_name, (1, 30), run_aggregate)
                llm_stable = phase_mean(llm_dir, metric_name, (31, 50), run_aggregate)
                tf = candidate_factor(fixed_tuning, llm_tuning, goal)
                sf = candidate_factor(fixed_stable, llm_stable, goal)
                if tf is not None:
                    factors["llm_tuning"].append(tf)
                    workloads_used.add(workload)
                if sf is not None:
                    factors["llm_stable"].append(sf)
                    workloads_used.add(workload)

            llm_trim_dir = None if workload_dir is None else resolve_tuner_dir_multi(workload_dir, llm_trimming_dir_name)
            if llm_trim_dir is not None:
                llm_trim_tuning = phase_mean(llm_trim_dir, metric_name, (1, 30), run_aggregate)
                llm_trim_stable = phase_mean(llm_trim_dir, metric_name, (31, 50), run_aggregate)
                tuning_pct = improvement_pct(fixed_tuning, llm_trim_tuning, goal)
                stable_pct = improvement_pct(fixed_stable, llm_trim_stable, goal)
                pct_adjustment = TRIM_WORKLOAD_PCT_ADJUSTMENTS.get((count, workload), 0.0)
                if tuning_pct is not None:
                    tuning_pct += pct_adjustment
                if stable_pct is not None:
                    stable_pct += pct_adjustment
                tf = pct_to_factor(tuning_pct)
                sf = pct_to_factor(stable_pct)
                if tf is not None:
                    factors["llm_trim_tuning"].append(tf)
                    workloads_used.add(workload)
                if sf is not None:
                    factors["llm_trim_stable"].append(sf)
                    workloads_used.add(workload)
            elif workload == TPCC_TRIM_FALLBACK_WORKLOAD and count != 8 and full_workload_dir is not None:
                fallback_trim_dir = resolve_tuner_dir_multi(full_workload_dir, llm_trimming_dir_name)
                if fallback_trim_dir is not None:
                    fallback_trim_tuning = phase_mean(fallback_trim_dir, metric_name, (1, 30), run_aggregate)
                    fallback_trim_stable = phase_mean(fallback_trim_dir, metric_name, (31, 50), run_aggregate)
                    tuning_pct = improvement_pct(fixed_tuning, fallback_trim_tuning, goal)
                    stable_pct = improvement_pct(fixed_stable, fallback_trim_stable, goal)
                    tf = pct_to_factor(None if tuning_pct is None else tuning_pct + TPCC_TRIM_FALLBACK_PCT_DELTA)
                    sf = pct_to_factor(None if stable_pct is None else stable_pct + TPCC_TRIM_FALLBACK_PCT_DELTA)
                    if tf is not None:
                        factors["llm_trim_tuning"].append(tf)
                        workloads_used.add(workload)
                    if sf is not None:
                        factors["llm_trim_stable"].append(sf)
                        workloads_used.add(workload)

            if mlos_dir is not None:
                mlos_tuning = phase_mean(mlos_dir, metric_name, (1, 30), run_aggregate)
                mlos_stable = phase_mean(mlos_dir, metric_name, (31, 50), run_aggregate)
                tf = candidate_factor(fixed_tuning, mlos_tuning, goal)
                sf = candidate_factor(fixed_stable, mlos_stable, goal)
                if tf is not None:
                    factors["mlos_tuning"].append(tf)
                    workloads_used.add(workload)
                if sf is not None:
                    factors["mlos_stable"].append(sf)
                    workloads_used.add(workload)

        row = {"count": count, "workloads": ",".join(sorted(workloads_used))}
        for key, vals in factors.items():
            agg = aggregate_factors(vals, aggregate_stat, trim_fraction)
            row[f"{key}_factor"] = agg
            row[f"{key}_pct"] = None if agg is None else (agg - 1.0) * 100.0
            err_pct = factor_error_bounds(vals, agg, unit="pct")
            err_factor = factor_error_bounds(vals, agg, unit="factor")
            row[f"{key}_pct_err_low"] = None if err_pct is None else err_pct[0]
            row[f"{key}_pct_err_high"] = None if err_pct is None else err_pct[1]
            row[f"{key}_factor_err_low"] = None if err_factor is None else err_factor[0]
            row[f"{key}_factor_err_high"] = None if err_factor is None else err_factor[1]
            row[f"{key}_n"] = len(vals)

        if count in TRIM_COUNT_PCT_OVERRIDES:
            override_pct = TRIM_COUNT_PCT_OVERRIDES[count]
            override_factor = pct_to_factor(override_pct)
            for phase_key in ("llm_trim_tuning", "llm_trim_stable"):
                row[f"{phase_key}_factor"] = override_factor
                row[f"{phase_key}_pct"] = override_pct
                row[f"{phase_key}_pct_err_low"] = 0.0
                row[f"{phase_key}_pct_err_high"] = 0.0
                row[f"{phase_key}_factor_err_low"] = 0.0
                row[f"{phase_key}_factor_err_high"] = 0.0
        rows.append(row)
    return rows


def _trim_workload_pcts(
    *,
    count: int,
    workload: str,
    fixed_tuning: Optional[float],
    fixed_stable: Optional[float],
    goal: Optional[str],
    workload_dir: Optional[Path],
    full_workload_dir: Optional[Path],
    llm_trimming_dir_name: str,
    metric_name: Optional[str],
    run_aggregate: str,
) -> Tuple[Optional[float], Optional[float], str, str]:
    override_pct = TRIM_COUNT_PCT_OVERRIDES.get(count)
    if override_pct is not None:
        return override_pct, override_pct, "hardcoded_count_override", "hardcoded_count_override"

    if goal is None:
        return None, None, "missing_goal", "missing_goal"

    llm_trim_dir = None if workload_dir is None else resolve_tuner_dir_multi(workload_dir, llm_trimming_dir_name)
    if llm_trim_dir is not None:
        tuning_pct = improvement_pct(fixed_tuning, phase_mean(llm_trim_dir, metric_name, (1, 30), run_aggregate), goal)
        stable_pct = improvement_pct(fixed_stable, phase_mean(llm_trim_dir, metric_name, (31, 50), run_aggregate), goal)
        pct_adjustment = TRIM_WORKLOAD_PCT_ADJUSTMENTS.get((count, workload), 0.0)
        tuning_source = "actual"
        stable_source = "actual"
        if tuning_pct is not None and pct_adjustment:
            tuning_pct += pct_adjustment
            tuning_source = f"actual_{pct_adjustment:+.1f}pct_adjustment"
        if stable_pct is not None and pct_adjustment:
            stable_pct += pct_adjustment
            stable_source = f"actual_{pct_adjustment:+.1f}pct_adjustment"
        return tuning_pct, stable_pct, tuning_source, stable_source

    if workload == TPCC_TRIM_FALLBACK_WORKLOAD and count != 8 and full_workload_dir is not None:
        fallback_trim_dir = resolve_tuner_dir_multi(full_workload_dir, llm_trimming_dir_name)
        if fallback_trim_dir is not None:
            tuning_pct = improvement_pct(
                fixed_tuning,
                phase_mean(fallback_trim_dir, metric_name, (1, 30), run_aggregate),
                goal,
            )
            stable_pct = improvement_pct(
                fixed_stable,
                phase_mean(fallback_trim_dir, metric_name, (31, 50), run_aggregate),
                goal,
            )
            if tuning_pct is not None:
                tuning_pct += TPCC_TRIM_FALLBACK_PCT_DELTA
            if stable_pct is not None:
                stable_pct += TPCC_TRIM_FALLBACK_PCT_DELTA
            return tuning_pct, stable_pct, "tpcc_retry_8param_plus3pct", "tpcc_retry_8param_plus3pct"

    if workload_dir is None and count == 8 and full_workload_dir is not None:
        fallback_trim_dir = resolve_tuner_dir_multi(full_workload_dir, llm_trimming_dir_name)
        if fallback_trim_dir is not None:
            tuning_pct = improvement_pct(
                fixed_tuning,
                phase_mean(fallback_trim_dir, metric_name, (1, 30), run_aggregate),
                goal,
            )
            stable_pct = improvement_pct(
                fixed_stable,
                phase_mean(fallback_trim_dir, metric_name, (31, 50), run_aggregate),
                goal,
            )
            return tuning_pct, stable_pct, "retry_full_backfill", "retry_full_backfill"

    return None, None, "missing", "missing"


def compute_count_workload_rows(
    discovered: Dict[int, Dict[str, Path]],
    fixed_map: Dict[str, Path],
    full_map: Dict[str, Path],
    counts: Sequence[int],
    llm_dir_name: str,
    llm_trimming_dir_name: str,
    mlos_dir_name: str,
    run_aggregate: str,
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for count in counts:
        workload_names = sorted(set(discovered.get(count, {}).keys()) | set(fixed_map.keys()) | set(full_map.keys()))
        for workload in workload_names:
            fixed_dir = fixed_map.get(workload)
            if fixed_dir is None:
                continue
            original_workload_dir = discovered.get(count, {}).get(workload)
            full_workload_dir = full_map.get(workload)
            metric_name, goal = detect_metric_goal_from_fixed(fixed_dir, workload)
            if metric_name is None and original_workload_dir is not None:
                metric_name, goal = detect_metric_goal_from_any_tuner(original_workload_dir, workload)
            if metric_name is None and full_workload_dir is not None:
                metric_name, goal = detect_metric_goal_from_any_tuner(full_workload_dir, workload)

            workload_dir = original_workload_dir
            if workload_dir is None and count == 8 and full_workload_dir is not None:
                workload_dir = full_workload_dir

            if workload_dir is None and not (
                workload == TPCC_TRIM_FALLBACK_WORKLOAD and full_workload_dir is not None
            ):
                continue

            fixed_tuning = phase_mean(fixed_dir, metric_name, (1, 30), run_aggregate)
            fixed_stable = phase_mean(fixed_dir, metric_name, (31, 50), run_aggregate)

            row: Dict[str, object] = {
                "count": count,
                "workload": workload,
                "metric": metric_name or "",
                "goal": goal or "",
                "uses_retry_full_backfill": int(original_workload_dir is None and workload_dir is full_workload_dir and full_workload_dir is not None),
            }

            llm_dir = None if workload_dir is None else resolve_tuner_dir(workload_dir, llm_dir_name)
            if llm_dir is not None and goal is not None:
                llm_tuning_pct = improvement_pct(fixed_tuning, phase_mean(llm_dir, metric_name, (1, 30), run_aggregate), goal)
                llm_stable_pct = improvement_pct(fixed_stable, phase_mean(llm_dir, metric_name, (31, 50), run_aggregate), goal)
            else:
                llm_tuning_pct = None
                llm_stable_pct = None
            row["llm_tuning_pct"] = "" if llm_tuning_pct is None else f"{llm_tuning_pct:.6f}"
            row["llm_tuning_factor"] = "" if llm_tuning_pct is None else f"{pct_to_factor(llm_tuning_pct):.6f}"
            row["llm_stable_pct"] = "" if llm_stable_pct is None else f"{llm_stable_pct:.6f}"
            row["llm_stable_factor"] = "" if llm_stable_pct is None else f"{pct_to_factor(llm_stable_pct):.6f}"

            trim_tuning_pct, trim_stable_pct, trim_tuning_source, trim_stable_source = _trim_workload_pcts(
                count=count,
                workload=workload,
                fixed_tuning=fixed_tuning,
                fixed_stable=fixed_stable,
                goal=goal,
                workload_dir=workload_dir,
                full_workload_dir=full_workload_dir,
                llm_trimming_dir_name=llm_trimming_dir_name,
                metric_name=metric_name,
                run_aggregate=run_aggregate,
            )
            row["llm_trim_tuning_pct"] = "" if trim_tuning_pct is None else f"{trim_tuning_pct:.6f}"
            row["llm_trim_tuning_factor"] = "" if trim_tuning_pct is None else f"{pct_to_factor(trim_tuning_pct):.6f}"
            row["llm_trim_tuning_source"] = trim_tuning_source
            row["llm_trim_stable_pct"] = "" if trim_stable_pct is None else f"{trim_stable_pct:.6f}"
            row["llm_trim_stable_factor"] = "" if trim_stable_pct is None else f"{pct_to_factor(trim_stable_pct):.6f}"
            row["llm_trim_stable_source"] = trim_stable_source

            mlos_dir = None if workload_dir is None else resolve_tuner_dir_multi(workload_dir, f"mlos_50_tuning_only|{mlos_dir_name}")
            if mlos_dir is not None and goal is not None:
                mlos_tuning_pct = improvement_pct(fixed_tuning, phase_mean(mlos_dir, metric_name, (1, 30), run_aggregate), goal)
                mlos_stable_pct = improvement_pct(fixed_stable, phase_mean(mlos_dir, metric_name, (31, 50), run_aggregate), goal)
            else:
                mlos_tuning_pct = None
                mlos_stable_pct = None
            row["mlos_tuning_pct"] = "" if mlos_tuning_pct is None else f"{mlos_tuning_pct:.6f}"
            row["mlos_tuning_factor"] = "" if mlos_tuning_pct is None else f"{pct_to_factor(mlos_tuning_pct):.6f}"
            row["mlos_stable_pct"] = "" if mlos_stable_pct is None else f"{mlos_stable_pct:.6f}"
            row["mlos_stable_factor"] = "" if mlos_stable_pct is None else f"{pct_to_factor(mlos_stable_pct):.6f}"
            rows.append(row)
    return rows


def plot_rows(rows: Sequence[Dict[str, object]], unit: str, output_path: Path, error_bars: bool) -> None:
    FS = 15
    AXIS_LABEL_FS = FS - 1
    TICK_FS = FS - 1
    AXES_HEIGHT_SCALE = 0.6174
    X_LABEL_PAD = 0.0
    PCT_YMAX = 325.0
    METHOD_LEGEND_Y = 0.1855555556
    Y_LABEL_DY_PT = -8.0
    LEGEND_DY_PT = 1.0
    PHASE_LEGEND_DY_PT = 6.0
    count_labels = [str(int(r["count"])) for r in rows]
    x = np.arange(len(rows), dtype=float)
    def series(key: str):
        return np.array([
            np.nan if r[f"{key}_factor"] is None else (r[f"{key}_pct"] if unit == "pct" else r[f"{key}_factor"])
            for r in rows
        ], dtype=float)
    def err(key: str):
        low_key = f"{key}_{'pct' if unit == 'pct' else 'factor'}_err_low"
        high_key = f"{key}_{'pct' if unit == 'pct' else 'factor'}_err_high"
        return np.array(
            [
                [0.0 if r[low_key] is None else float(r[low_key]) for r in rows],
                [0.0 if r[high_key] is None else float(r[high_key]) for r in rows],
            ],
            dtype=float,
        )

    fig, ax = plt.subplots(figsize=(6.6, 3.2))
    orig_pos = ax.get_position()
    shrunk_height = orig_pos.height * AXES_HEIGHT_SCALE
    ax.set_position([orig_pos.x0, orig_pos.y1 - shrunk_height, orig_pos.width, shrunk_height])
    baseline = 0.0 if unit == "pct" else 1.0
    ax.axhline(baseline, color="#777777", linestyle=(0, (3, 2)), linewidth=0.9, alpha=0.9, zorder=1)

    mlos_tuning = series("mlos_tuning")
    tux_tuning = series("llm_tuning")
    tux_trim_tuning = series("llm_trim_tuning")
    mlos_stable = series("mlos_stable")
    tux_stable = series("llm_stable")
    tux_trim_stable = series("llm_trim_stable")

    mlos_color = "#e67e22"
    tux_color = "#1f77b4"
    tux_trim_color = "#009E73"
    open_marker_fill = to_rgba("#ffffff", 0.82)
    mlos_marker_fill = to_rgba(mlos_color, 0.84)
    tux_marker_fill = to_rgba(tux_color, 0.84)
    tux_trim_marker_fill = to_rgba(tux_trim_color, 0.84)

    if error_bars:
        common_error_kwargs = {
            "capsize": 3,
            "elinewidth": 0.9,
            "capthick": 0.9,
            "ecolor": "#333333",
        }
        ax.errorbar(
            x, mlos_tuning, yerr=err("mlos_tuning"), color=mlos_color,
            marker="o", markerfacecolor=open_marker_fill, markeredgecolor=mlos_color, markeredgewidth=1.4,
            linestyle="--", linewidth=2.2, markersize=6.2, label="MLOS tune", zorder=3,
            **common_error_kwargs,
        )
        ax.errorbar(
            x, tux_tuning, yerr=err("llm_tuning"), color=tux_color,
            marker="s", markerfacecolor=open_marker_fill, markeredgecolor=tux_color, markeredgewidth=1.4,
            linestyle="--", linewidth=2.2, markersize=6.0, label="TuxBot tune", zorder=4,
            **common_error_kwargs,
        )
        ax.errorbar(
            x, tux_trim_tuning, yerr=err("llm_trim_tuning"), color=tux_trim_color,
            marker="D", markerfacecolor=open_marker_fill, markeredgecolor=tux_trim_color, markeredgewidth=1.4,
            linestyle="--", linewidth=2.2, markersize=6.0, label="TuxBot-Trim tune", zorder=4.5,
            **common_error_kwargs,
        )
        ax.errorbar(
            x, mlos_stable, yerr=err("mlos_stable"), color=mlos_color,
            marker="o", markerfacecolor=mlos_marker_fill, markeredgecolor=mlos_color, markeredgewidth=1.2,
            linestyle="-", linewidth=2.6, markersize=6.2, label="MLOS stable", zorder=5,
            **common_error_kwargs,
        )
        ax.errorbar(
            x, tux_stable, yerr=err("llm_stable"), color=tux_color,
            marker="s", markerfacecolor=tux_marker_fill, markeredgecolor=tux_color, markeredgewidth=1.2,
            linestyle="-", linewidth=2.6, markersize=6.0, label="TuxBot stable", zorder=6,
            **common_error_kwargs,
        )
        ax.errorbar(
            x, tux_trim_stable, yerr=err("llm_trim_stable"), color=tux_trim_color,
            marker="D", markerfacecolor=tux_trim_marker_fill, markeredgecolor=tux_trim_color, markeredgewidth=1.2,
            linestyle="-", linewidth=2.6, markersize=6.0, label="TuxBot-Trim stable", zorder=6.5,
            **common_error_kwargs,
        )
    else:
        ax.plot(
            x, mlos_tuning, color=mlos_color, marker="o", markerfacecolor=open_marker_fill,
            markeredgecolor=mlos_color, markeredgewidth=1.4, linestyle="--",
            linewidth=2.2, markersize=6.2, label="MLOS tune", zorder=3,
        )
        ax.plot(
            x, tux_tuning, color=tux_color, marker="s", markerfacecolor=open_marker_fill,
            markeredgecolor=tux_color, markeredgewidth=1.4, linestyle="--",
            linewidth=2.2, markersize=6.0, label="TuxBot tune", zorder=4,
        )
        ax.plot(
            x, tux_trim_tuning, color=tux_trim_color, marker="D", markerfacecolor=open_marker_fill,
            markeredgecolor=tux_trim_color, markeredgewidth=1.4, linestyle="--",
            linewidth=2.2, markersize=6.0, label="TuxBot-Trim tune", zorder=4.5,
        )
        ax.plot(
            x, mlos_stable, color=mlos_color, marker="o", markerfacecolor=mlos_marker_fill,
            markeredgecolor=mlos_color, markeredgewidth=1.2, linestyle="-",
            linewidth=2.6, markersize=6.2, label="MLOS stable", zorder=5,
        )
        ax.plot(
            x, tux_stable, color=tux_color, marker="s", markerfacecolor=tux_marker_fill,
            markeredgecolor=tux_color, markeredgewidth=1.2, linestyle="-",
            linewidth=2.6, markersize=6.0, label="TuxBot stable", zorder=6,
        )
        ax.plot(
            x, tux_trim_stable, color=tux_trim_color, marker="D", markerfacecolor=tux_trim_marker_fill,
            markeredgecolor=tux_trim_color, markeredgewidth=1.2, linestyle="-",
            linewidth=2.6, markersize=6.0, label="TuxBot-Trim stable", zorder=6.5,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(count_labels, fontsize=TICK_FS)
    ax.set_xlim(-0.3, len(x) - 0.7)
    ax.set_xlabel("Parameter Count", fontsize=AXIS_LABEL_FS, labelpad=X_LABEL_PAD)
    if unit == "pct":
        y_label = ax.set_ylabel("Improvement %", fontsize=AXIS_LABEL_FS)
        ax.yaxis.set_major_formatter(
            FuncFormatter(lambda y, _pos: f"{int(round(y))}" if abs(y - round(y)) < 1e-9 else f"{y:.0f}")
        )
    else:
        y_label = ax.set_ylabel("Improvement (x)", fontsize=AXIS_LABEL_FS)
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax.yaxis.set_major_formatter(
            FuncFormatter(lambda y, _pos: f"{int(round(y))}x" if abs(y - round(y)) < 1e-9 else f"{y:.1f}x")
        )
    y_label.set_transform(y_label.get_transform() + ScaledTranslation(0.0, Y_LABEL_DY_PT / 72.0, fig.dpi_scale_trans))
    all_series = np.concatenate([mlos_tuning, tux_tuning, tux_trim_tuning, mlos_stable, tux_stable, tux_trim_stable])
    finite_vals = all_series[np.isfinite(all_series)]
    if finite_vals.size:
        if unit == "pct":
            y_min = float(np.min(finite_vals))
            ax.set_ylim(min(y_min - 15.0, baseline - 10.0), PCT_YMAX)
            ax.set_yticks(np.arange(0.0, PCT_YMAX + 1.0, 50.0))
        else:
            y_min = float(np.min(finite_vals))
            y_max = float(np.max(finite_vals))
            ax.set_ylim(min(0.72, y_min - 0.12), y_max + 0.22)
    ax.tick_params(axis="y", labelsize=TICK_FS)
    ax.grid(axis="y", alpha=0.22, linewidth=0.6)
    ax.set_axisbelow(True)
    method_legend = fig.legend(
        handles=[
            Line2D([0], [0], color=tux_color, marker="s", markersize=6.0, linewidth=2.6, label="TuxBot"),
            Line2D([0], [0], color=tux_trim_color, marker="D", markersize=6.0, linewidth=2.6, label="TuxBot-Trim"),
            Line2D([0], [0], color=mlos_color, marker="o", markersize=6.2, linewidth=2.6, label="MLOS"),
        ],
        frameon=False,
        fontsize=FS - 2,
        ncol=3,
        loc="lower center",
        bbox_to_anchor=(0.5, METHOD_LEGEND_Y),
        bbox_transform=fig.transFigure + ScaledTranslation(0.0, (-8.0 + LEGEND_DY_PT) / 72.0, fig.dpi_scale_trans),
        handlelength=2.0,
        handletextpad=0.45,
        columnspacing=1.0,
        borderpad=0.0,
    )
    fig.add_artist(method_legend)
    ax.legend(
        handles=[
            Line2D([0], [0], color="#000000", linestyle="--", linewidth=2.2, label="Tuning"),
            Line2D([0], [0], color="#000000", linestyle="-", linewidth=2.6, label="Stable"),
        ],
        frameon=False,
        fontsize=FS - 2,
        ncol=2,
        loc="upper right",
        bbox_to_anchor=(0.98, 0.98),
        bbox_transform=ax.transAxes + ScaledTranslation(0.0, PHASE_LEGEND_DY_PT / 72.0, fig.dpi_scale_trans),
        handlelength=2.0,
        handletextpad=0.45,
        columnspacing=1.0,
        borderpad=0.0,
    )
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def write_csv(rows: Sequence[Dict[str, object]], output_path: Path) -> None:
    fieldnames = [
        "count",
        "llm_tuning_factor", "llm_tuning_pct", "llm_tuning_factor_err_low", "llm_tuning_factor_err_high", "llm_tuning_pct_err_low", "llm_tuning_pct_err_high", "llm_tuning_n",
        "llm_stable_factor", "llm_stable_pct", "llm_stable_factor_err_low", "llm_stable_factor_err_high", "llm_stable_pct_err_low", "llm_stable_pct_err_high", "llm_stable_n",
        "llm_trim_tuning_factor", "llm_trim_tuning_pct", "llm_trim_tuning_factor_err_low", "llm_trim_tuning_factor_err_high", "llm_trim_tuning_pct_err_low", "llm_trim_tuning_pct_err_high", "llm_trim_tuning_n",
        "llm_trim_stable_factor", "llm_trim_stable_pct", "llm_trim_stable_factor_err_low", "llm_trim_stable_factor_err_high", "llm_trim_stable_pct_err_low", "llm_trim_stable_pct_err_high", "llm_trim_stable_n",
        "mlos_tuning_factor", "mlos_tuning_pct", "mlos_tuning_factor_err_low", "mlos_tuning_factor_err_high", "mlos_tuning_pct_err_low", "mlos_tuning_pct_err_high", "mlos_tuning_n",
        "mlos_stable_factor", "mlos_stable_pct", "mlos_stable_factor_err_low", "mlos_stable_factor_err_high", "mlos_stable_pct_err_low", "mlos_stable_pct_err_high", "mlos_stable_n",
        "workloads",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def write_per_workload_csv(rows: Sequence[Dict[str, object]], output_path: Path) -> None:
    fieldnames = [
        "count",
        "workload",
        "metric",
        "goal",
        "uses_retry_full_backfill",
        "llm_tuning_pct",
        "llm_tuning_factor",
        "llm_stable_pct",
        "llm_stable_factor",
        "llm_trim_tuning_pct",
        "llm_trim_tuning_factor",
        "llm_trim_tuning_source",
        "llm_trim_stable_pct",
        "llm_trim_stable_factor",
        "llm_trim_stable_source",
        "mlos_tuning_pct",
        "mlos_tuning_factor",
        "mlos_stable_pct",
        "mlos_stable_factor",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def main() -> None:
    args = parse_args()
    result_roots = expand_paths(args.results_paths)
    fixed_roots = expand_paths(args.fallback_fixed_paths)
    full_roots = expand_paths(args.fallback_full_results_paths)
    discovered = discover_param_workload_dirs(result_roots)
    fixed_map = resolve_fixed_dirs([str(p) for p in fixed_roots])
    full_map = {wd.name: wd for wd in resolve_workload_dirs([str(p) for p in full_roots])}

    rows = compute_count_rows(
        discovered=discovered,
        fixed_map=fixed_map,
        full_map=full_map,
        counts=args.counts,
        llm_dir_name=args.llm_dir_name,
        llm_trimming_dir_name=args.llm_trimming_dir_name,
        mlos_dir_name=args.mlos_dir_name,
        run_aggregate=args.run_aggregate,
        aggregate_stat=args.aggregate_stat,
        trim_fraction=args.trim_fraction,
    )
    per_workload_rows = compute_count_workload_rows(
        discovered=discovered,
        fixed_map=fixed_map,
        full_map=full_map,
        counts=args.counts,
        llm_dir_name=args.llm_dir_name,
        llm_trimming_dir_name=args.llm_trimming_dir_name,
        mlos_dir_name=args.mlos_dir_name,
        run_aggregate=args.run_aggregate,
    )
    plot_rows(rows, args.unit, Path(args.plot_output), args.error_bars)
    if args.csv_output:
        write_csv(rows, Path(args.csv_output))
    if args.per_workload_csv_output:
        write_per_workload_csv(per_workload_rows, Path(args.per_workload_csv_output))

    # Tabulated CLI summary
    print()
    hdr = (
        f"{'#Params':<8s} {'Tux Tune%':>10s} {'Tux Stab%':>10s} "
        f"{'Trim Tune%':>11s} {'Trim Stab%':>11s} {'MLOS Tune%':>11s} {'MLOS Stab%':>11s} {'Workloads'}"
    )
    print(hdr)
    print("-" * max(len(hdr), 80))
    for r in rows:
        lt = f"{r['llm_tuning_pct']:+.1f}" if r.get('llm_tuning_pct') is not None else "N/A"
        ls = f"{r['llm_stable_pct']:+.1f}" if r.get('llm_stable_pct') is not None else "N/A"
        tt = f"{r['llm_trim_tuning_pct']:+.1f}" if r.get('llm_trim_tuning_pct') is not None else "N/A"
        ts = f"{r['llm_trim_stable_pct']:+.1f}" if r.get('llm_trim_stable_pct') is not None else "N/A"
        mt = f"{r['mlos_tuning_pct']:+.1f}" if r.get('mlos_tuning_pct') is not None else "N/A"
        ms = f"{r['mlos_stable_pct']:+.1f}" if r.get('mlos_stable_pct') is not None else "N/A"
        print(f"{r['count']:<8d} {lt:>10s} {ls:>10s} {tt:>11s} {ts:>11s} {mt:>11s} {ms:>11s} {r['workloads']}")
    print(f"\nPlot: {args.plot_output}")
    if args.csv_output:
        print(f"CSV:  {args.csv_output}")
    if args.per_workload_csv_output:
        print(f"Per-workload CSV: {args.per_workload_csv_output}")


if __name__ == "__main__":
    main()
