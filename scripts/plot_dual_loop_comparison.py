#!/usr/bin/env python3
"""
Plot dual-loop optimization results showing per-agent proposed parameter values.

For dual-loop JSON files, each iteration stores what the Actor and Speculator
individually proposed (in ``tuner_timing.proposed_parameters`` and
``reasoning_tuner_timing.proposed_parameters``).  This script extracts those
per-agent proposals and plots them as separate lines, so you can see how each
agent explores the parameter space.

Also supports comparing across multiple files (dual-loop vs actor-only vs
speculator-only vs Bayesian, etc.) and marking an optimal region.
"""

import json
import os
import glob
import argparse
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from collections import OrderedDict
from typing import Dict, List, Tuple, Any, Optional


# ── Style constants ──────────────────────────────────────────────────────────

COLORS = OrderedDict([
    ('actor',               '#3498DB'),   # Blue
    ('speculator',          '#E74C3C'),   # Red
    ('applied',             '#2C3E50'),   # Dark gray
    ('actor + speculator',  '#9B59B6'),   # Purple  (whole-file comparison)
    ('actor only',          '#3498DB'),   # Blue
    ('speculator only',     '#E74C3C'),   # Red
    ('bayesian',            '#2ECC71'),   # Green
    ('fixed',               '#95A5A6'),   # Gray
    ('dqn',                 '#F39C12'),   # Orange
    ('qlearning',           '#1ABC9C'),   # Teal
    ('mlos',                '#E67E22'),   # Dark orange
    ('human',               '#16A085'),   # Dark teal
])

MARKERS = OrderedDict([
    ('actor',               's'),
    ('speculator',          '^'),
    ('applied',             'o'),
    ('actor + speculator',  'o'),
    ('actor only',          's'),
    ('speculator only',     '^'),
    ('bayesian',            'D'),
    ('fixed',               'x'),
    ('dqn',                 'v'),
    ('qlearning',           'P'),
    ('mlos',                'H'),
    ('human',               '*'),
])

LABEL_DISPLAY = OrderedDict([
    ('actor',               'ACTOR'),
    ('speculator',          'SPECULATOR'),
    ('applied',             'APPLIED'),
    ('actor + speculator',  'ACTOR + SPECULATOR'),
    ('actor only',          'ACTOR ONLY'),
    ('speculator only',     'SPECULATOR ONLY'),
    ('bayesian',            'BAYESIAN'),
    ('fixed',               'FIXED'),
    ('dqn',                 'DQN'),
    ('qlearning',           'Q-LEARNING'),
    ('mlos',                'MLOS'),
    ('human',               'HUMAN EXPERT'),
])


# ── Data loading ─────────────────────────────────────────────────────────────

def load_json(filepath: str) -> Optional[Dict[str, Any]]:
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading {filepath}: {e}")
        return None


def is_dual_loop(data: Dict[str, Any], filepath: str) -> bool:
    """Return True if this result file is from a dual-loop run."""
    if data.get('mode') == 'actor-speculator':
        return True
    if 'dual_loop' in os.path.basename(filepath).lower():
        return True
    return False


def detect_single_loop_label(data: Dict[str, Any], filepath: str) -> str:
    """Determine a label for a single-loop result file."""
    config = data.get('config', {})
    tuner_type = config.get('tuner_type', 'unknown')
    basename = os.path.basename(filepath).lower()

    if any(tag in basename for tag in ('reasoning', 'actor_only', 'actor-only')):
        return 'actor only'
    if any(tag in basename for tag in ('speculator_only', 'speculator-only', 'quick_only')):
        return 'speculator only'

    if tuner_type != 'llm':
        return tuner_type.lower()

    model = config.get('llm_model_name', '')
    if 'lite' in model:
        return 'speculator only'
    return 'actor only'


def extract_dual_loop_series(data: Dict[str, Any],
                             param_key: str,
                             start_iter: Optional[int] = None,
                             end_iter: Optional[int] = None,
                             show_applied: bool = False
                             ) -> Dict[str, List[Tuple[int, float]]]:
    """Extract per-agent proposed parameter values from a dual-loop result.

    Returns up to 3 series: 'actor', 'speculator', and optionally 'applied'.
    Falls back to showing only 'applied' for older JSON files that lack
    ``proposed_parameters`` in their timing dicts.
    """
    history = data.get('history', [])
    actor_pts: List[Tuple[int, float]] = []
    spec_pts:  List[Tuple[int, float]] = []
    applied_pts: List[Tuple[int, float]] = []
    has_proposed = False  # Track if ANY entry has proposed_parameters

    for entry in history:
        it = entry.get('iteration', 0)
        if start_iter is not None and it < start_iter:
            continue
        if end_iter is not None and it > end_iter:
            continue

        # --- Speculator proposal ---
        spec_timing = entry.get('tuner_timing') or {}
        spec_params = spec_timing.get('proposed_parameters') or {}
        if spec_params:
            has_proposed = True
        if param_key in spec_params:
            try:
                spec_pts.append((it, float(spec_params[param_key])))
            except (TypeError, ValueError):
                pass

        # Also check all_quick_tuner_timings (in-window mode may store multiple)
        if not spec_params.get(param_key):
            all_timings = entry.get('metrics', {}).get('all_quick_tuner_timings', [])
            if all_timings:
                last_timing = all_timings[-1]
                alt_params = last_timing.get('proposed_parameters') or {}
                if alt_params:
                    has_proposed = True
                if param_key in alt_params:
                    try:
                        spec_pts.append((it, float(alt_params[param_key])))
                    except (TypeError, ValueError):
                        pass

        # --- Actor proposal ---
        actor_timing = entry.get('reasoning_tuner_timing') or {}
        actor_params = actor_timing.get('proposed_parameters') or {}
        if actor_params:
            has_proposed = True
        if param_key in actor_params:
            try:
                actor_pts.append((it, float(actor_params[param_key])))
            except (TypeError, ValueError):
                pass

        # --- Applied (final) --- always collect for fallback
        applied_val = entry.get('parameters', {}).get(param_key)
        if applied_val is not None:
            try:
                applied_pts.append((it, float(applied_val)))
            except (TypeError, ValueError):
                pass

    result: Dict[str, List[Tuple[int, float]]] = OrderedDict()

    if has_proposed:
        # New format: show per-agent proposals
        if spec_pts:
            result['speculator'] = spec_pts
        if actor_pts:
            result['actor'] = actor_pts
        if show_applied and applied_pts:
            result['applied'] = applied_pts
    else:
        # Old format fallback: no proposed_parameters, show applied as single line
        if applied_pts:
            print(f"  (Note: old JSON format without per-agent proposals, "
                  f"showing applied values)")
            result['applied'] = applied_pts

    return result


def extract_single_loop_series(data: Dict[str, Any],
                               param_key: str,
                               start_iter: Optional[int] = None,
                               end_iter: Optional[int] = None
                               ) -> List[Tuple[int, float]]:
    """Extract parameter values from a single-loop result."""
    history = data.get('history', [])
    pts: List[Tuple[int, float]] = []
    for entry in history:
        it = entry.get('iteration', 0)
        if start_iter is not None and it < start_iter:
            continue
        if end_iter is not None and it > end_iter:
            continue

        # Check proposed_parameters in tuner_timing first
        timing = entry.get('tuner_timing') or {}
        proposed = timing.get('proposed_parameters') or {}
        val = proposed.get(param_key)

        # Fall back to applied parameters
        if val is None:
            val = entry.get('parameters', {}).get(param_key)

        if val is not None:
            try:
                pts.append((it, float(val)))
            except (TypeError, ValueError):
                pass
    return pts


def extract_metric_series(data: Dict[str, Any],
                          metric_key: str,
                          start_iter: Optional[int] = None,
                          end_iter: Optional[int] = None
                          ) -> List[Tuple[int, float]]:
    """Extract a metric value over iterations (single line, any file type)."""
    history = data.get('history', [])
    pts: List[Tuple[int, float]] = []
    for entry in history:
        it = entry.get('iteration', 0)
        if start_iter is not None and it < start_iter:
            continue
        if end_iter is not None and it > end_iter:
            continue

        val = entry.get('metrics', {}).get(metric_key)
        if val is None:
            val = entry.get('system_metrics', {}).get(metric_key)
        if val is None and metric_key == 'reward':
            val = entry.get('reward')
        if val is not None:
            try:
                pts.append((it, float(val)))
            except (TypeError, ValueError):
                pass
    return pts


# ── Plotting ─────────────────────────────────────────────────────────────────

def _style_for(label: str, idx: int):
    key = label.lower()
    fallback_colors = ['#2C3E50', '#C0392B', '#27AE60', '#8E44AD',
                       '#D35400', '#2980B9', '#7F8C8D', '#F1C40F']
    fallback_markers = ['o', 's', '^', 'D', 'v', 'P', 'H', '*']
    color = COLORS.get(key, fallback_colors[idx % len(fallback_colors)])
    marker = MARKERS.get(key, fallback_markers[idx % len(fallback_markers)])
    display = LABEL_DISPLAY.get(key, label.upper())
    return color, marker, display


def plot_comparison(series_map: Dict[str, List[Tuple[int, float]]],
                    ylabel: str,
                    output_file: Optional[str] = None,
                    title: Optional[str] = None,
                    ylim: Optional[Tuple[float, float]] = None,
                    xlim: Optional[Tuple[float, float]] = None,
                    log_y: bool = False,
                    opt_start: Optional[float] = None,
                    opt_end: Optional[float] = None):
    fig, ax = plt.subplots(figsize=(9, 5.5))

    for idx, (label, points) in enumerate(series_map.items()):
        if not points:
            continue
        points.sort(key=lambda p: p[0])
        iters = [p[0] for p in points]
        vals  = [p[1] for p in points]

        color, marker, display = _style_for(label, idx)

        # Use dashed line for 'applied'
        ls = '--' if label.lower() == 'applied' else '-'
        alpha = 0.5 if label.lower() == 'applied' else 0.85

        ax.plot(iters, vals,
                marker=marker, label=display, color=color,
                linewidth=2, markersize=6, alpha=alpha, linestyle=ls)

    # Optimal region
    handles, labels = ax.get_legend_handles_labels()
    if opt_start is not None and opt_end is not None:
        ax.axhspan(opt_start, opt_end, color='#2ECC71', alpha=0.13, zorder=0)
        ax.axhline(opt_start, color='#27AE60', lw=1, ls='--', alpha=0.5)
        ax.axhline(opt_end,   color='#27AE60', lw=1, ls='--', alpha=0.5)
        opt_patch = mpatches.Patch(facecolor='#2ECC71', alpha=0.25,
                                   edgecolor='#27AE60', linestyle='--',
                                   label='OPTIMAL REGION')
        handles.append(opt_patch)
        labels.append('OPTIMAL REGION')
    elif opt_start is not None:
        ax.axhline(opt_start, color='#27AE60', lw=1.5, ls='--', alpha=0.7)
    elif opt_end is not None:
        ax.axhline(opt_end, color='#27AE60', lw=1.5, ls='--', alpha=0.7)

    ax.legend(handles, labels, loc='best', fontsize=9, frameon=True,
              fancybox=False, shadow=False, framealpha=1.0,
              edgecolor='black', facecolor='white')

    ax.set_xlabel('Tuning Cycle', fontsize=11)
    ax.set_ylabel(ylabel, fontsize=11)

    if log_y:
        ax.set_yscale('log')
    if ylim:
        ax.set_ylim(ylim)
    if xlim:
        ax.set_xlim(xlim)
    if title:
        ax.set_title(title, fontsize=13, fontweight='bold', pad=10)

    ax.grid(True, alpha=0.3, linestyle='-', linewidth=0.5, color='gray')
    ax.set_axisbelow(True)
    ax.set_facecolor('white')
    fig.patch.set_facecolor('white')
    for spine in ax.spines.values():
        spine.set_edgecolor('black')
        spine.set_linewidth(0.8)

    plt.tight_layout()

    if output_file:
        plt.savefig(output_file, dpi=300, bbox_inches='tight', facecolor='white')
        print(f"Plot saved to {output_file}")
    else:
        plt.show()


# ── CLI ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Plot per-agent proposed parameter values (Actor vs Speculator)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Show Actor vs Speculator proposals for a single dual-loop run
  python scripts/plot_dual_loop_comparison.py \\
      -f dual_loop_result.json \\
      -p min_granularity_ns --log-y

  # Same, also show the actually-applied value
  python scripts/plot_dual_loop_comparison.py \\
      -f dual_loop_result.json \\
      -p min_granularity_ns --log-y --show-applied

  # Compare dual-loop vs actor-only vs speculator-only
  python scripts/plot_dual_loop_comparison.py \\
      -f dual.json actor.json speculator.json \\
      -p min_granularity_ns --log-y

  # Mark an optimal region
  python scripts/plot_dual_loop_comparison.py \\
      -f dual.json \\
      -p min_granularity_ns --log-y \\
      --opt-start 1000 --opt-end 100000

  # Plot a metric instead of a parameter
  python scripts/plot_dual_loop_comparison.py \\
      -f dual.json actor.json \\
      --metric latency_p99

  # Directory scan
  python scripts/plot_dual_loop_comparison.py \\
      -d results/sphinx_hi_p99 \\
      -p min_granularity_ns --log-y -o comparison.png
        """)

    parser.add_argument('-f', '--files', nargs='+',
                        help='Result JSON files to plot')
    parser.add_argument('-d', '--directory', type=str,
                        help='Directory to scan for result JSON files (recursive)')
    parser.add_argument('--labels', nargs='+',
                        help='Explicit label per file (for non-dual-loop files)')

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--parameter', '-p', type=str,
                       help='Parameter to plot (e.g. min_granularity_ns). '
                            'For dual-loop files this shows per-agent proposals.')
    group.add_argument('--metric', '-m', type=str,
                       help='Metric to plot (one line per file, no per-agent split)')

    parser.add_argument('--show-applied', action='store_true',
                        help='Also plot the actually-applied parameter value '
                             '(dashed line) for dual-loop files')

    parser.add_argument('-o', '--output', type=str,
                        help='Save plot to file (default: show interactively)')
    parser.add_argument('-t', '--title', type=str, help='Figure title')

    parser.add_argument('--ylim', nargs=2, type=float, metavar=('MIN', 'MAX'))
    parser.add_argument('--xlim', nargs=2, type=float, metavar=('MIN', 'MAX'))
    parser.add_argument('--log-y', action='store_true',
                        help='Log scale on Y-axis')

    parser.add_argument('--opt-start', type=float, default=None,
                        help='Lower bound of optimal region (horizontal band)')
    parser.add_argument('--opt-end', type=float, default=None,
                        help='Upper bound of optimal region (horizontal band)')

    parser.add_argument('--start', type=int, help='Start iteration (inclusive)')
    parser.add_argument('--end', type=int, help='End iteration (inclusive)')

    return parser


def collect_filepaths(args) -> List[str]:
    paths: List[str] = []
    if args.files:
        for pattern in args.files:
            expanded = glob.glob(pattern)
            paths.extend(expanded if expanded else [pattern])
    if args.directory:
        for pattern in (
            os.path.join(args.directory, '*.json'),
            os.path.join(args.directory, '**', '*.json'),
        ):
            paths.extend(glob.glob(pattern, recursive=True))

    result = []
    for p in paths:
        base = os.path.basename(p).lower()
        if base.startswith('window_') or base.startswith('llm_api_'):
            continue
        if 'perf_stat' in base or 'perf_info' in base or 'metrics.json' in base:
            continue
        result.append(p)

    seen = set()
    unique = []
    for p in result:
        rp = os.path.realpath(p)
        if rp not in seen:
            seen.add(rp)
            unique.append(p)
    return unique


def determine_param_key(args, first_data: Dict[str, Any]) -> Optional[str]:
    """Return the parameter key to plot, or None if --metric mode."""
    if args.parameter:
        return args.parameter
    if args.metric:
        return None
    # Default: first tunable parameter
    config = first_data.get('config', {})
    params_to_tune = config.get('parameters_to_tune', [])
    if params_to_tune:
        return params_to_tune[0]
    param_ranges = config.get('parameter_ranges', {})
    if param_ranges:
        return list(param_ranges.keys())[0]
    return None


def make_ylabel(key: str, is_param: bool) -> str:
    display = key.replace('_', ' ').title()
    if key.endswith('_ns'):
        display = display.rsplit(' Ns', 1)[0] + ' (ns)'
    elif key.endswith('_ms'):
        display = display.rsplit(' Ms', 1)[0] + ' (ms)'
    elif key.endswith('_pct'):
        display = display.rsplit(' Pct', 1)[0] + ' (%)'
    elif key.endswith('_watts'):
        display = display.rsplit(' Watts', 1)[0] + ' (W)'
    return display


def main():
    parser = build_parser()
    args = parser.parse_args()

    filepaths = collect_filepaths(args)
    if not filepaths:
        parser.error('No result JSON files found. Use -f or -d.')

    print(f"Found {len(filepaths)} result file(s):")
    for fp in filepaths:
        print(f"  {fp}")

    # Load all
    loaded: List[Tuple[str, Dict[str, Any]]] = []
    for fp in filepaths:
        data = load_json(fp)
        if data:
            loaded.append((fp, data))

    if not loaded:
        print("Error: no valid result files loaded.")
        return

    # Determine what to plot
    param_key = determine_param_key(args, loaded[0][1])
    metric_key = args.metric
    plotting_param = param_key is not None

    if not plotting_param and not metric_key:
        # Fallback: optimization metric
        metric_key = loaded[0][1].get('config', {}).get('optimization_metric', 'reward')
        plotting_param = False

    value_key = param_key if plotting_param else metric_key
    print(f"Plotting: {value_key} ({'parameter' if plotting_param else 'metric'})")

    # Build series
    series_map: Dict[str, List[Tuple[int, float]]] = OrderedDict()

    # Track how many dual-loop files we have (for prefixing when >1)
    dual_files = [(fp, d) for fp, d in loaded if is_dual_loop(d, fp)]
    multi_dual = len(dual_files) > 1

    label_idx = 0
    for fp, data in loaded:
        dual = is_dual_loop(data, fp)

        if plotting_param and dual:
            # Per-agent series from dual-loop file
            prefix = ""
            if multi_dual:
                stem = os.path.splitext(os.path.basename(fp))[0]
                prefix = f"{stem}: "

            agent_series = extract_dual_loop_series(
                data, param_key,
                start_iter=args.start, end_iter=args.end,
                show_applied=args.show_applied)

            for agent_key, pts in agent_series.items():
                full_label = f"{prefix}{agent_key}" if prefix else agent_key
                series_map[full_label] = pts
                print(f"  {full_label}: {len(pts)} points "
                      f"[iter {pts[0][0]}–{pts[-1][0]}]")
        elif plotting_param and not dual:
            # Single-loop file – one line
            if args.labels and label_idx < len(args.labels):
                lbl = args.labels[label_idx]
            else:
                lbl = detect_single_loop_label(data, fp)
            label_idx += 1

            pts = extract_single_loop_series(
                data, param_key,
                start_iter=args.start, end_iter=args.end)
            if pts:
                series_map[lbl] = pts
                print(f"  {lbl}: {len(pts)} points "
                      f"[iter {pts[0][0]}–{pts[-1][0]}]")
            else:
                print(f"  {lbl}: no data for '{param_key}'")
        else:
            # Metric mode – one line per file
            if dual:
                lbl = 'actor + speculator'
            elif args.labels and label_idx < len(args.labels):
                lbl = args.labels[label_idx]
                label_idx += 1
            else:
                lbl = detect_single_loop_label(data, fp)
                label_idx += 1

            if multi_dual and dual:
                stem = os.path.splitext(os.path.basename(fp))[0]
                lbl = f"{lbl} ({stem})"

            pts = extract_metric_series(
                data, metric_key,
                start_iter=args.start, end_iter=args.end)
            if pts:
                series_map[lbl] = pts
                print(f"  {lbl}: {len(pts)} points "
                      f"[iter {pts[0][0]}–{pts[-1][0]}]")
            else:
                print(f"  {lbl}: no data for '{metric_key}'")

    if not series_map:
        print("Error: no plottable data found.")
        return

    ylabel = make_ylabel(value_key, plotting_param)

    plot_comparison(
        series_map=series_map,
        ylabel=ylabel,
        output_file=args.output,
        title=args.title,
        ylim=tuple(args.ylim) if args.ylim else None,
        xlim=tuple(args.xlim) if args.xlim else None,
        log_y=args.log_y,
        opt_start=args.opt_start,
        opt_end=args.opt_end,
    )


if __name__ == '__main__':
    main()
