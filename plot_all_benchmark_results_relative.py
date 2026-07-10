#!/usr/bin/env python3
"""
Comprehensive benchmark results plotting script with relative improvement.
Automatically discovers and compares all available tuners against Fixed baseline.
"""

import os
import sys
import json
import glob
import argparse
import numpy as np
import matplotlib.pyplot as plt
from collections import defaultdict
from pathlib import Path

# Benchmark subdirectory names to look for (used for ordering when applicable)
BENCHMARK_NAMES = [
    "masstree_hi_p99",
    "masstree_lo_pwr_wrt_p99",
    "silo_hi_p99",
    "silo_lo_pwr_wrt_p99",
    "sphinx_hi_p99",
    "sphinx_tput_max",
    "sysbench_oltp_rw_hi_p99",
    "sysbench_oltp_rw_lo_pwr_wrt_p99",
    "tpcc_hi_p99",
    "tpcc_lo_pwr_wrt_p99",
    "ycsb_hi_p99",
]

# Will be populated dynamically based on available tuners
TUNER_METHODS = []
# Extended color palette for all tuners
COLOR_PALETTE = {
    'fixed': '#3498db',
    'llm': '#e74c3c', 
    'mlos': '#2ecc71',
    'bayesian': '#9b59b6',
    'dqn': '#f39c12',
    'qlearning': '#1abc9c',
    'reasoning': '#e67e22',
}
COLORS = {}

def load_optimization_history(json_file):
    """Load and parse optimization history JSON file."""
    try:
        with open(json_file, 'r') as f:
            data = json.load(f)
        return data
    except Exception as e:
        print(f"Error loading {json_file}: {e}")
        return None

def extract_window_metrics(history_data, start_window=None, end_window=None):
    """Extract metrics averaged across windows from optimization history.
    
    Args:
        history_data: The optimization history data
        start_window: Optional 1-indexed start window (inclusive)
        end_window: Optional 1-indexed end window (inclusive)
    """
    if not history_data or 'history' not in history_data:
        return None
    
    history = history_data['history']
    if not history:
        return None
    
    # Get optimization metric and goal
    config = history_data.get('config', {})
    opt_metric = config.get('optimization_metric', 'reward')
    opt_goal = config.get('optimization_goal', 'minimize')
    
    # Determine window range (1-indexed, inclusive)
    total_windows = len(history)
    start_idx = (start_window - 1) if start_window else 0
    end_idx = (end_window) if end_window else total_windows
    
    # Clamp to valid range
    start_idx = max(0, min(start_idx, total_windows - 1))
    end_idx = max(1, min(end_idx, total_windows))
    
    if start_idx >= end_idx:
        return None
    
    # Extract objective values from the specified window range
    objective_values = []
    all_metrics = []
    
    # Extract objective values from the specified window range
    objective_values = []
    all_metrics = []
    constraint_violations = 0
    
    for idx in range(start_idx, end_idx):
        entry = history[idx]
        
        # Try to get raw_metric_value first (new format), fall back to metrics dict
        metric_value = entry.get('raw_metric_value')
        if metric_value is None:
            # Old format - extract from metrics
            metrics = entry.get('metrics', {})
            metric_value = metrics.get(opt_metric)
            
            # If not in metrics, try system_metrics (for power, etc.)
            if metric_value is None:
                system_metrics = entry.get('system_metrics', {})
                metric_value = system_metrics.get(opt_metric)
        
        if metric_value is not None and metric_value != 0:
            objective_values.append(metric_value)
            # Merge both metrics and system_metrics
            combined_metrics = {**entry.get('metrics', {}), **entry.get('system_metrics', {})}
            all_metrics.append(combined_metrics)
            
            # Check for constraint violation
            if entry.get('constraint_violated', False):
                constraint_violations += 1
    
    if not objective_values:
        return None
    
    # Calculate average objective value across windows
    avg_objective_value = np.mean(objective_values)
    
    # Average all other metrics across the windows as well
    avg_throughput = np.mean([m.get('throughput', 0) for m in all_metrics if m.get('throughput', 0) > 0]) if any(m.get('throughput', 0) > 0 for m in all_metrics) else 0
    avg_goodput = np.mean([m.get('goodput', 0) for m in all_metrics if m.get('goodput', 0) > 0]) if any(m.get('goodput', 0) > 0 for m in all_metrics) else 0
    avg_latency_avg = np.mean([m.get('latency_avg', 0) for m in all_metrics if m.get('latency_avg', 0) > 0]) if any(m.get('latency_avg', 0) > 0 for m in all_metrics) else 0
    avg_latency_p95 = np.mean([m.get('latency_p95', 0) for m in all_metrics if m.get('latency_p95', 0) > 0]) if any(m.get('latency_p95', 0) > 0 for m in all_metrics) else 0
    
    # Handle both latency_p99 and p_99_latency formats
    p99_values = []
    for m in all_metrics:
        p99 = m.get('latency_p99')
        if p99 is None or p99 == 0:
            p99 = m.get('p_99_latency', 0) / 1000.0 if m.get('p_99_latency', 0) > 0 else 0
        if p99 > 0:
            p99_values.append(p99)
    avg_latency_p99 = np.mean(p99_values) if p99_values else 0
    
    avg_power_socket0 = np.mean([m.get('power_socket0_watts', 0) for m in all_metrics if m.get('power_socket0_watts', 0) > 0]) if any(m.get('power_socket0_watts', 0) > 0 for m in all_metrics) else 0
    avg_power_ram = np.mean([m.get('power_ram_watts', 0) for m in all_metrics if m.get('power_ram_watts', 0) > 0]) if any(m.get('power_ram_watts', 0) > 0 for m in all_metrics) else 0
    
    # Check if this benchmark has an SLO constraint
    has_slo = 'constraint_metric' in config
    
    # Extract common metrics
    result = {
        'optimization_metric': opt_metric,
        'optimization_goal': opt_goal,
        'avg_value': avg_objective_value,  # Average objective value across windows
        'throughput': avg_throughput,
        'goodput': avg_goodput,
        'latency_avg': avg_latency_avg,
        'latency_p95': avg_latency_p95,
        'latency_p99': avg_latency_p99,
        'power_socket0_watts': avg_power_socket0,
        'power_ram_watts': avg_power_ram,
        'iterations': total_windows,
        'windows_used': f"{start_idx + 1}-{end_idx}",
        'total_time': history_data.get('total_time', 0),
        'constraint_violations': constraint_violations,
        'has_slo': has_slo,
    }
    
    return result

def collect_all_results(results_base_dirs, start_window=None, end_window=None):
    """Collect results from all benchmark directories across multiple base directories.
    
    Args:
        results_base_dirs: List of base directories containing benchmark results
        start_window: Optional 1-indexed start window for averaging
        end_window: Optional 1-indexed end window for averaging
    """
    global TUNER_METHODS, COLORS
    
    results = defaultdict(lambda: defaultdict(list))
    discovered_tuners = set()
    
    # Ensure results_base_dirs is a list
    if isinstance(results_base_dirs, str):
        results_base_dirs = [results_base_dirs]
    
    # Process each base directory
    for results_base_dir in results_base_dirs:
        if not os.path.exists(results_base_dir):
            print(f"Warning: Directory '{results_base_dir}' not found, skipping...")
            continue
        
        print(f"Processing: {results_base_dir}")
        
        # Automatically discover all benchmark directories (exclude common non-benchmark dirs)
        exclude_dirs = {'logs', '.git', '__pycache__', 'venv', 'env'}
        try:
            all_dirs = [d for d in os.listdir(results_base_dir) 
                       if os.path.isdir(os.path.join(results_base_dir, d)) 
                       and d not in exclude_dirs]
            
            print(f"  Found {len(all_dirs)} potential benchmark directories")
            
            for benchmark_name in all_dirs:
                result_dir = os.path.join(results_base_dir, benchmark_name)
                
                # Discover all tuner subdirectories dynamically
                try:
                    tuner_dirs = [d for d in os.listdir(result_dir) 
                                 if os.path.isdir(os.path.join(result_dir, d))]
                    
                    for tuner in tuner_dirs:
                        discovered_tuners.add(tuner)
                        tuner_dir = os.path.join(result_dir, tuner)
                        
                        # Find all optimization history files
                        history_files = glob.glob(os.path.join(tuner_dir, "optimization_history_*.json"))
                        
                        for history_file in history_files:
                            data = load_optimization_history(history_file)
                            if data:
                                metrics = extract_window_metrics(data, start_window, end_window)
                                if metrics:
                                    results[benchmark_name][tuner].append(metrics)
                                else:
                                    # Log why extraction failed
                                    config = data.get('config', {})
                                    opt_metric = config.get('optimization_metric', 'unknown')
                                    print(f"  Warning: No valid metrics in {benchmark_name}/{tuner} (optimization_metric: {opt_metric})")
                except Exception as e:
                    # Skip directories that don't have the expected structure
                    continue
                    
        except Exception as e:
            print(f"Error processing {results_base_dir}: {e}")
            continue
    
    # Update global TUNER_METHODS with discovered tuners
    # Put 'fixed' first if it exists, then sort the rest
    if discovered_tuners:
        if 'fixed' in discovered_tuners:
            TUNER_METHODS = ['fixed'] + sorted([t for t in discovered_tuners if t != 'fixed'])
        else:
            TUNER_METHODS = sorted(discovered_tuners)
        
        # Assign colors to tuners
        for tuner in TUNER_METHODS:
            COLORS[tuner] = COLOR_PALETTE.get(tuner, f'#{hash(tuner) % 0xFFFFFF:06x}')
        
        print(f"Discovered tuners: {', '.join(TUNER_METHODS)}")
    
    return results

def calculate_improvement(value, baseline, goal):
    """Calculate relative improvement compared to baseline.
    
    For minimize goals: Lower value = better = multiplier >1.0
                       Higher value = worse = multiplier <1.0
    For maximize goals: Higher value = better = multiplier >1.0
                       Lower value = worse = multiplier <1.0
    
    Returns multiplier where 1.0 = baseline performance.
    """
    if baseline == 0 or value == 0:
        return 0
    
    if goal == 'minimize':
        # Lower is better: baseline / value
        # If value < baseline: multiplier > 1.0 (better)
        # If value > baseline: multiplier < 1.0 (worse)
        return baseline / value
    else:  # maximize
        # Higher is better: value / baseline
        # If value > baseline: multiplier > 1.0 (better)
        # If value < baseline: multiplier < 1.0 (worse)
        return value / baseline

def create_relative_improvement_plot(results, metric_key, metric_label, filename, goal='minimize'):
    """Create a relative improvement plot with Fixed as baseline."""
    scenarios = sorted(results.keys())
    
    if not scenarios:
        print(f"No data to plot for {metric_label}")
        return
    
    # Get non-fixed tuners for comparison
    comparison_tuners = [t for t in TUNER_METHODS if t != 'fixed']
    
    if not comparison_tuners:
        print(f"No comparison tuners found for {metric_label}")
        return
    
    # Prepare data - calculate relative improvement for each scenario
    tuner_improvements = {tuner: [] for tuner in comparison_tuners}
    tuner_errors = {tuner: [] for tuner in comparison_tuners}
    valid_scenarios = []
    
    for scenario in scenarios:
        # Get baseline (fixed) values
        fixed_values = [r[metric_key] for r in results[scenario].get('fixed', []) if r.get(metric_key, 0) > 0]
        
        if not fixed_values:
            continue  # Skip if no fixed baseline
        
        fixed_baseline = np.mean(fixed_values)
        
        # Get values for each comparison tuner and calculate improvement
        for tuner in comparison_tuners:
            tuner_values = [r[metric_key] for r in results[scenario].get(tuner, []) if r.get(metric_key, 0) > 0]
            if tuner_values:
                improvements_raw = [calculate_improvement(v, fixed_baseline, goal) for v in tuner_values]
                tuner_improvements[tuner].append(np.mean(improvements_raw))
                tuner_errors[tuner].append(np.std(improvements_raw) if len(improvements_raw) > 1 else 0)
            else:
                tuner_improvements[tuner].append(0)
                tuner_errors[tuner].append(0)
        
        valid_scenarios.append(scenario)
    
    if not valid_scenarios:
        print(f"No valid data for {metric_label}")
        return
    
    # Create the plot
    fig, ax = plt.subplots(figsize=(16, 8))
    
    x = np.arange(len(valid_scenarios))
    num_tuners = len(comparison_tuners)
    width = 0.8 / num_tuners  # Distribute available space among tuners
    
    # Plot baseline (Fixed) as horizontal dashed line at 1.0
    ax.axhline(y=1.0, color=COLORS.get('fixed', '#3498db'), linestyle='--', linewidth=2, 
               label='FIXED (Baseline)', zorder=1)
    
    # Plot bars for each tuner
    for i, tuner in enumerate(comparison_tuners):
        offset = (i - num_tuners/2 + 0.5) * width
        bars = ax.bar(x + offset, tuner_improvements[tuner], width,
                      label=tuner.upper(), color=COLORS.get(tuner, '#95a5a6'),
                      yerr=tuner_errors[tuner], capsize=5, alpha=0.8, zorder=2)
        
        # Add value labels on bars
        for bar in bars:
            height = bar.get_height()
            if height > 0:
                ax.text(bar.get_x() + bar.get_width()/2., height,
                       f'{height:.2f}x',
                       ha='center', va='bottom', fontsize=8, fontweight='bold')
    
    # Customize plot
    ax.set_xlabel('Benchmark Scenario', fontsize=13, fontweight='bold')
    ax.set_ylabel('Relative Performance (vs Fixed Baseline)', fontsize=13, fontweight='bold')
    
    title_suffix = "Lower is Better" if goal == 'minimize' else "Higher is Better"
    ax.set_title(f'{metric_label} - Relative Improvement\n({title_suffix})', 
                 fontsize=14, fontweight='bold')
    
    ax.set_xticks(x)
    ax.set_xticklabels([s.replace('_', '\n') for s in valid_scenarios], 
                       rotation=0, ha='center', fontsize=9)
    ax.legend(loc='upper right', fontsize=13, framealpha=0.9, ncol=5)
    ax.grid(axis='y', alpha=0.3, linestyle='--')
    
    # Add interpretation note
    note_text = ("Values >1.0x = Better than Fixed\n"
                 "Values <1.0x = Worse than Fixed\n"
                 "1.0x = Same as Fixed (Baseline)")
    ax.text(0.02, 0.98, note_text, transform=ax.transAxes,
            fontsize=9, verticalalignment='top',
            bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
    
    # Set y-axis to start slightly below 0.5 to show poor performance clearly
    all_improvements = [v for improvements in tuner_improvements.values() for v in improvements if v > 0]
    y_min = min(0.5, min(all_improvements, default=0.5) * 0.9)
    y_max = max(all_improvements, default=1.5) * 1.1
    ax.set_ylim(y_min, y_max)
    
    plt.tight_layout()
    plt.savefig(filename, dpi=300, bbox_inches='tight')
    print(f"Saved: {filename}")
    plt.close()

def create_objective_improvement_plot(results, filename, use_log_scale=False):
    """Create a relative improvement plot based on the objective metric for each scenario.
    
    This automatically detects the optimization metric and goal for each scenario
    and calculates improvement accordingly.
    """
    scenarios = sorted(results.keys())
    
    if not scenarios:
        print(f"No data to plot for objective improvement")
        return
    
    # Get non-fixed tuners for comparison
    comparison_tuners = [t for t in TUNER_METHODS if t != 'fixed']
    
    if not comparison_tuners:
        print(f"No comparison tuners found for objective improvement")
        return
    
    # Prepare data - calculate relative improvement for each scenario
    tuner_improvements = {tuner: [] for tuner in comparison_tuners}
    tuner_errors = {tuner: [] for tuner in comparison_tuners}
    tuner_violations = {tuner: [] for tuner in comparison_tuners}
    valid_scenarios = []
    
    for scenario in scenarios:
        # Get baseline (fixed) runs
        fixed_runs = results[scenario].get('fixed', [])
        
        if not fixed_runs:
            continue  # Skip if no fixed baseline
        
        # Get the optimization metric and goal from fixed runs
        opt_metric = fixed_runs[0]['optimization_metric']
        opt_goal = fixed_runs[0]['optimization_goal']
        
        # Get baseline values
        fixed_values = [r['avg_value'] for r in fixed_runs if r.get('avg_value', 0) > 0]
        
        if not fixed_values:
            continue
        
        fixed_baseline = np.mean(fixed_values)
        
        # Get values for each comparison tuner and calculate improvement
        for tuner in comparison_tuners:
            tuner_runs = results[scenario].get(tuner, [])
            tuner_values = [r['avg_value'] for r in tuner_runs if r.get('avg_value', 0) > 0]
            
            # Collect violation info
            violations = sum([r.get('constraint_violations', 0) for r in tuner_runs])
            has_slo = any([r.get('has_slo', False) for r in tuner_runs])
            tuner_violations[tuner].append({'count': violations, 'has_slo': has_slo})
            
            if tuner_values:
                improvements_raw = [calculate_improvement(v, fixed_baseline, opt_goal) for v in tuner_values]
                tuner_improvements[tuner].append(np.mean(improvements_raw))
                tuner_errors[tuner].append(np.std(improvements_raw) if len(improvements_raw) > 1 else 0)
            else:
                tuner_improvements[tuner].append(0)
                tuner_errors[tuner].append(0)
        
        valid_scenarios.append(scenario)
    
    if not valid_scenarios:
        print(f"No valid data for objective improvement")
        return
    
    # Create the plot - 3x width as requested (16 * 3 = 48)
    fig, ax = plt.subplots(figsize=(48, 12))
    
    x = np.arange(len(valid_scenarios))
    num_tuners = len(comparison_tuners)
    width = 0.8 / num_tuners  # Distribute available space among tuners
    
    # Plot baseline (Fixed) as horizontal dashed line at 1.0
    ax.axhline(y=1.0, color=COLORS.get('fixed', '#3498db'), linestyle='--', linewidth=2, 
               label='Baseline (sh)', zorder=1)
    
    # Plot bars for each tuner
    for i, tuner in enumerate(comparison_tuners):
        offset = (i - num_tuners/2 + 0.5) * width
        bars = ax.bar(x + offset, tuner_improvements[tuner], width,
                      label=tuner.upper(), color=COLORS.get(tuner, '#95a5a6'),
                      yerr=tuner_errors[tuner], capsize=5, alpha=0.8, zorder=2)
        
        # No value labels on bars as requested
    
    # Customize plot
    ax.set_xlabel('Benchmark Scenario', fontsize=32, fontweight='bold')
    ax.set_ylabel('Relative Objective Improvement', fontsize=32, fontweight='bold')
    
    # No title as requested
    
    ax.set_xticks(x)
    # No newline replacement to prevent folding
    ax.set_xticklabels(valid_scenarios, rotation=0, ha='center', fontsize=28)
    
    # Set y-axis tick label size
    ax.tick_params(axis='y', labelsize=28)
    
    ax.legend(loc='upper right', fontsize=32, framealpha=0.9, ncol=5)
    ax.grid(axis='y', alpha=0.3, linestyle='--', which='major')
    
    # Log2 scale option
    if use_log_scale:
        ax.set_yscale('log', base=2)
        ax.grid(axis='y', alpha=0.1, linestyle=':', which='minor')
        
        # Format y-axis ticks to show plain numbers (0.5, 1, 2) instead of 2^x
        from matplotlib.ticker import FuncFormatter
        ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: '{:g}'.format(y)))
    
    # Set y-axis limits to ensure good framing
    all_improvements = [v for improvements in tuner_improvements.values() for v in improvements if v > 0]
    if all_improvements:
        y_min = min(0.5, min(all_improvements) * 0.8)
        y_max = max(1.5, max(all_improvements) * 1.2)
        ax.set_ylim(y_min, y_max)
    
    plt.tight_layout()
    plt.savefig(filename, dpi=300, bbox_inches='tight')
    print(f"Saved: {filename}")
    plt.close()

def create_summary_table(results):
    """Create a summary table showing relative improvements."""
    print("\n" + "="*120)
    print("RELATIVE IMPROVEMENT SUMMARY (vs Fixed Baseline)")
    print("="*120)
    
    # Get non-fixed tuners
    comparison_tuners = [t for t in TUNER_METHODS if t != 'fixed']
    
    for scenario in sorted(results.keys()):
        print(f"\n{scenario.upper().replace('_', ' ')}")
        print("-" * 120)
        
        # Get Fixed baseline
        fixed_runs = results[scenario].get('fixed', [])
        if not fixed_runs:
            print("  No Fixed baseline available")
            continue
        
        opt_metric = fixed_runs[0]['optimization_metric']
        opt_goal = fixed_runs[0]['optimization_goal']
        fixed_values = [r['avg_value'] for r in fixed_runs]
        fixed_baseline = np.mean(fixed_values)
        
        print(f"  FIXED (Baseline): {opt_metric}={fixed_baseline:.3f} | Runs: {len(fixed_runs)}")
        
        # Calculate improvements for all comparison tuners
        for tuner in comparison_tuners:
            runs = results[scenario].get(tuner, [])
            if not runs:
                print(f"  {tuner.upper():8s}: No data")
                continue
            
            values = [r['avg_value'] for r in runs]
            improvements = [calculate_improvement(v, fixed_baseline, opt_goal) for v in values]
            
            avg_improvement = np.mean(improvements)
            std_improvement = np.std(improvements) if len(improvements) > 1 else 0
            
            better_or_worse = "BETTER" if avg_improvement > 1.0 else "WORSE" if avg_improvement < 1.0 else "SAME"
            improvement_pct = (avg_improvement - 1.0) * 100
            
            print(f"  {tuner.upper():8s}: {avg_improvement:.3f}x ± {std_improvement:.3f} "
                  f"({improvement_pct:+.1f}%) - {better_or_worse} | "
                  f"{opt_metric}={np.mean(values):.3f} | Runs: {len(runs)}")

def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='Plot benchmark results with relative improvement analysis',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                      # Use default 'results/' directory
  %(prog)s results_new                          # Use 'results_new/' directory
  %(prog)s results_20251124_120000              # Use timestamped archived results
  %(prog)s results_dir1 results_dir2            # Process multiple directories
  %(prog)s results_*                            # Process all matching directories
        """
    )
    parser.add_argument(
        'results_dir',
        nargs='+',
        default=['results'],
        help='One or more directories containing benchmark results (default: results)'
    )
    parser.add_argument(
        '--log-scale',
        action='store_true',
        help='Use log2 scale for the y-axis (default: False)'
    )
    parser.add_argument(
        '--start',
        type=int,
        default=None,
        help='Start window number (1-indexed, inclusive) for averaging metrics'
    )
    parser.add_argument(
        '--end',
        type=int,
        default=None,
        help='End window number (1-indexed, inclusive) for averaging metrics'
    )
    
    args = parser.parse_args()
    results_dirs = args.results_dir
    use_log_scale = args.log_scale
    start_window = args.start
    end_window = args.end
    
    # Validate window arguments
    if start_window is not None and start_window < 1:
        print(f"Error: --start must be >= 1")
        sys.exit(1)
    if end_window is not None and end_window < 1:
        print(f"Error: --end must be >= 1")
        sys.exit(1)
    if start_window is not None and end_window is not None and start_window > end_window:
        print(f"Error: --start must be <= --end")
        sys.exit(1)
    
    # Validate that at least one results directory exists
    valid_dirs = [d for d in results_dirs if os.path.exists(d)]
    invalid_dirs = [d for d in results_dirs if not os.path.exists(d)]
    
    if invalid_dirs:
        print(f"Warning: The following directories were not found and will be skipped:")
        for d in invalid_dirs:
            print(f"  - {d}")
    
    if not valid_dirs:
        print(f"Error: No valid results directories found!")
        print(f"Searched for: {', '.join(results_dirs)}")
        print(f"Please specify valid results directories.")
        sys.exit(1)
    
    print(f"Collecting benchmark results from {len(valid_dirs)} director{'y' if len(valid_dirs) == 1 else 'ies'}:")
    for d in valid_dirs:
        print(f"  - {d}")
    
    if start_window or end_window:
        window_range = f"windows {start_window or 1} to {end_window or 'end'}"
        print(f"Using {window_range} for averaging metrics")
    
    results = collect_all_results(valid_dirs, start_window, end_window)
    
    if not results:
        print("No results found!")
        print(f"Make sure the specified directories contain benchmark subdirectories with tuner results.")
        print(f"Expected structure: results_dir/benchmark_name/tuner_name/optimization_history_*.json")
        return
    
    print(f"Found results for {len(results)} benchmark scenarios")

    
    # Create relative improvement plots for different metrics
    print("\nGenerating relative improvement plots...")
    
    # Objective improvement (automatic detection of metric and goal)
    create_objective_improvement_plot(results, 'relative_objective_improvement.png', use_log_scale=use_log_scale)
    
    # Latency P99 (minimize - higher multiplier is better)
    create_relative_improvement_plot(results, 'latency_p99', 'P99 Latency', 
                                    'relative_improvement_p99_latency.png', goal='minimize')
    
    # Throughput (maximize - higher multiplier is better)
    create_relative_improvement_plot(results, 'throughput', 'Throughput', 
                                    'relative_improvement_throughput.png', goal='maximize')
    
    # Power (minimize - higher multiplier is better)
    create_relative_improvement_plot(results, 'power_socket0_watts', 'Power Consumption', 
                                    'relative_improvement_power.png', goal='minimize')
    
    # Latency Average (minimize - higher multiplier is better)
    create_relative_improvement_plot(results, 'latency_avg', 'Average Latency', 
                                    'relative_improvement_avg_latency.png', goal='minimize')
    
    # Create summary table
    create_summary_table(results)
    
    print("\n" + "="*120)
    print("PLOTTING COMPLETE!")
    print("="*120)
    print("\nGenerated relative improvement plots:")
    print("  - relative_objective_improvement.png (Primary - All Metrics)")
    if use_log_scale:
        print("    (Using Log2 Scale)")
    print("  - relative_improvement_p99_latency.png")
    print("  - relative_improvement_throughput.png")
    print("  - relative_improvement_power.png")
    print("  - relative_improvement_avg_latency.png")
    print("\nInterpretation:")
    print("  - Values >1.0x = Better than Fixed baseline")
    print("  - Values <1.0x = Worse than Fixed baseline")
    print("  - 1.0x = Same as Fixed baseline")

if __name__ == "__main__":
    main()
