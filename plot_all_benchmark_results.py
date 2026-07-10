#!/usr/bin/env python3
"""
Comprehensive benchmark results plotting script.
Generates bar plots with clusters for each benchmark scenario.
"""

import os
import json
import glob
import numpy as np
import matplotlib.pyplot as plt
from collections import defaultdict
from pathlib import Path

# Benchmark directories to process
RESULT_DIRS = [
    "results/masstree_hi_p99",
    "results/masstree_lo_pwr_wrt_p99",
    "results/silo_hi_p99",
    "results/silo_lo_pwr_wrt_p99",
    "results/sphinx_hi_p99",
    "results/sphinx_tput_max",
    "results/sysbench_oltp_rw_hi_p99",
    "results/sysbench_oltp_rw_lo_pwr_wrt_p99",
    "results/tpcc_hi_p99",
    "results/tpcc_lo_pwr_wrt_p99",
]

TUNER_METHODS = ['fixed', 'llm', 'mlos']
COLORS = {'fixed': '#3498db', 'llm': '#e74c3c', 'mlos': '#2ecc71'}

def load_optimization_history(json_file):
    """Load and parse optimization history JSON file."""
    try:
        with open(json_file, 'r') as f:
            data = json.load(f)
        return data
    except Exception as e:
        print(f"Error loading {json_file}: {e}")
        return None

def extract_best_metrics(history_data):
    """Extract best metrics from optimization history."""
    if not history_data or 'history' not in history_data:
        return None
    
    history = history_data['history']
    if not history:
        return None
    
    # Get optimization metric and goal
    config = history_data.get('config', {})
    opt_metric = config.get('optimization_metric', 'reward')
    opt_goal = config.get('optimization_goal', 'minimize')
    
    # Find best iteration based on optimization goal
    best_entry = None
    best_value = float('inf') if opt_goal == 'minimize' else float('-inf')
    
    for entry in history:
        metrics = entry.get('metrics', {})
        reward = entry.get('reward', 0)
        
        # Try to get the optimization metric value
        value = metrics.get(opt_metric, reward)
        
        if opt_goal == 'minimize':
            if value < best_value:
                best_value = value
                best_entry = entry
        else:  # maximize
            if value > best_value:
                best_value = value
                best_entry = entry
    
    if not best_entry:
        return None
    
    metrics = best_entry['metrics']
    
    # Extract common metrics
    result = {
        'optimization_metric': opt_metric,
        'optimization_goal': opt_goal,
        'best_value': best_value,
        'throughput': metrics.get('throughput', 0),
        'goodput': metrics.get('goodput', 0),
        'latency_avg': metrics.get('latency_avg', 0),
        'latency_p95': metrics.get('latency_p95', 0),
        'latency_p99': metrics.get('latency_p99', metrics.get('p_99_latency', 0) / 1000.0),  # Convert to ms if needed
        'power_socket0_watts': metrics.get('power_socket0_watts', 0),
        'power_ram_watts': metrics.get('power_ram_watts', 0),
        'iterations': len(history),
        'total_time': history_data.get('total_time', 0),
    }
    
    return result

def collect_all_results():
    """Collect results from all benchmark directories."""
    results = defaultdict(lambda: defaultdict(list))
    
    for result_dir in RESULT_DIRS:
        if not os.path.exists(result_dir):
            print(f"Skipping {result_dir} - directory not found")
            continue
        
        scenario_name = os.path.basename(result_dir)
        
        for tuner in TUNER_METHODS:
            tuner_dir = os.path.join(result_dir, tuner)
            if not os.path.exists(tuner_dir):
                print(f"Skipping {tuner_dir} - directory not found")
                continue
            
            # Find all optimization history files
            history_files = glob.glob(os.path.join(tuner_dir, "optimization_history_*.json"))
            
            for history_file in history_files:
                data = load_optimization_history(history_file)
                if data:
                    metrics = extract_best_metrics(data)
                    if metrics:
                        results[scenario_name][tuner].append(metrics)
    
    return results

def create_bar_plot(results, metric_key, metric_label, filename, goal='minimize'):
    """Create a grouped bar plot for a specific metric."""
    scenarios = sorted(results.keys())
    
    if not scenarios:
        print(f"No data to plot for {metric_label}")
        return
    
    # Prepare data
    tuner_data = {tuner: [] for tuner in TUNER_METHODS}
    tuner_errors = {tuner: [] for tuner in TUNER_METHODS}
    
    for scenario in scenarios:
        for tuner in TUNER_METHODS:
            values = [r[metric_key] for r in results[scenario].get(tuner, []) if r.get(metric_key, 0) > 0]
            if values:
                tuner_data[tuner].append(np.mean(values))
                tuner_errors[tuner].append(np.std(values) if len(values) > 1 else 0)
            else:
                tuner_data[tuner].append(0)
                tuner_errors[tuner].append(0)
    
    # Create the plot
    fig, ax = plt.subplots(figsize=(16, 8))
    
    x = np.arange(len(scenarios))
    width = 0.25
    
    # Plot bars for each tuner
    for i, tuner in enumerate(TUNER_METHODS):
        offset = (i - 1) * width
        bars = ax.bar(x + offset, tuner_data[tuner], width, 
                      label=tuner.upper(), color=COLORS[tuner],
                      yerr=tuner_errors[tuner], capsize=5, alpha=0.8)
        
        # Add value labels on bars
        for bar in bars:
            height = bar.get_height()
            if height > 0:
                ax.text(bar.get_x() + bar.get_width()/2., height,
                       f'{height:.2f}',
                       ha='center', va='bottom', fontsize=8, rotation=0)
    
    # Customize plot
    ax.set_xlabel('Benchmark Scenario', fontsize=12, fontweight='bold')
    ax.set_ylabel(metric_label, fontsize=12, fontweight='bold')
    ax.set_title(f'{metric_label} Across All Benchmarks', fontsize=14, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels([s.replace('_', '\n') for s in scenarios], rotation=0, ha='center', fontsize=9)
    ax.legend(loc='upper right', fontsize=10)
    ax.grid(axis='y', alpha=0.3, linestyle='--')
    
    # Add a note about optimization goal
    goal_note = f"Lower is better" if goal == 'minimize' else "Higher is better"
    ax.text(0.02, 0.98, goal_note, transform=ax.transAxes, 
            fontsize=10, verticalalignment='top', 
            bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.3))
    
    plt.tight_layout()
    plt.savefig(filename, dpi=300, bbox_inches='tight')
    print(f"Saved: {filename}")
    plt.close()

def create_summary_table(results):
    """Create a summary table of all results."""
    print("\n" + "="*120)
    print("BENCHMARK RESULTS SUMMARY")
    print("="*120)
    
    for scenario in sorted(results.keys()):
        print(f"\n{scenario.upper().replace('_', ' ')}")
        print("-" * 120)
        
        for tuner in TUNER_METHODS:
            runs = results[scenario].get(tuner, [])
            if not runs:
                print(f"  {tuner.upper():8s}: No data")
                continue
            
            # Calculate statistics
            opt_metric = runs[0]['optimization_metric']
            best_values = [r['best_value'] for r in runs]
            
            print(f"  {tuner.upper():8s}: "
                  f"{opt_metric}={np.mean(best_values):.3f}±{np.std(best_values):.3f} | "
                  f"Runs: {len(runs)} | "
                  f"Avg Iterations: {np.mean([r['iterations'] for r in runs]):.1f}")

def main():
    print("Collecting benchmark results...")
    results = collect_all_results()
    
    if not results:
        print("No results found!")
        return
    
    # Create plots for different metrics
    print("\nGenerating plots...")
    
    # Latency P99 (minimize)
    create_bar_plot(results, 'latency_p99', 'P99 Latency (ms)', 
                    'all_benchmarks_p99_latency.png', goal='minimize')
    
    # Throughput (maximize)
    create_bar_plot(results, 'throughput', 'Throughput (req/s)', 
                    'all_benchmarks_throughput.png', goal='maximize')
    
    # Power (minimize)
    create_bar_plot(results, 'power_socket0_watts', 'Power Consumption (Watts)', 
                    'all_benchmarks_power.png', goal='minimize')
    
    # Latency Average (minimize)
    create_bar_plot(results, 'latency_avg', 'Average Latency (ms)', 
                    'all_benchmarks_avg_latency.png', goal='minimize')
    
    # Create summary table
    create_summary_table(results)
    
    print("\n" + "="*120)
    print("PLOTTING COMPLETE!")
    print("="*120)
    print("\nGenerated plots:")
    print("  - all_benchmarks_p99_latency.png")
    print("  - all_benchmarks_throughput.png")
    print("  - all_benchmarks_power.png")
    print("  - all_benchmarks_avg_latency.png")

if __name__ == "__main__":
    main()
