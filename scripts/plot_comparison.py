#!/usr/bin/env python3
"""
Plot parameter values over iterations, grouped by tuner type.

This script parses optimization history JSON files and creates a plot
showing how parameter values change over iterations for different tuner types.
"""

import json
import os
import glob
import argparse
import matplotlib.pyplot as plt
from collections import defaultdict
from typing import Dict, List, Tuple, Any
import numpy as np


def load_history_file(filepath: str) -> Dict[str, Any]:
    """Load and parse an optimization history JSON file."""
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading {filepath}: {e}")
        return None


def _format_model_name(model_name: str) -> str:
    """
    Format LLM model name for concise display labels.
    
    Args:
        model_name: Raw model name (e.g., 'gemini-2.5-flash', 'gemini-2.5-flash-lite')
        
    Returns:
        Concise label (e.g., '2.5 Flash', '2.5 Flash Lite')
    """
    if not model_name:
        return 'LLM'
    
    name_lower = model_name.lower().replace('_', '-')
    
    # Gemini models
    if 'gemini-2.5-flash-lite' in name_lower:
        return '2.5 Flash Lite'
    elif 'gemini-2.5-flash' in name_lower:
        return '2.5 Flash'
    elif 'gemini-2.5-pro' in name_lower:
        return '2.5 Pro'
    elif 'gemini-2.0-flash-lite' in name_lower:
        return '2.0 Flash Lite'
    elif 'gemini-2.0-flash' in name_lower:
        return '2.0 Flash'
    elif 'gemini-' in name_lower:
        return model_name.replace('gemini-', '').replace('-', ' ').title()
    else:
        return model_name


def _get_tuner_key(config: Dict[str, Any]) -> str:
    """
    Get a tuner key that differentiates LLM runs by model name.
    
    Returns:
        For LLM tuners: 'llm:<model_label>' (e.g. 'llm:2.5 Flash')
        For others: the raw tuner_type (e.g. 'fixed', 'mlos')
    """
    tuner_type = config.get('tuner_type', 'unknown')
    if tuner_type.lower() == 'llm':
        model_name = config.get('llm_model_name') or config.get('llm_actor_model')
        if model_name:
            return f'llm:{_format_model_name(model_name)}'
    return tuner_type


def _format_param_label(param_name: str) -> str:
    """Format parameter name for axis/subplot (e.g. min_granularity_ns -> Min granularity (ns))."""
    if not param_name:
        return param_name
    s = param_name.strip()
    if s.endswith('_ns'):
        s = s[:-3].replace('_', ' ').title() + ' (ns)'
    elif s.endswith('_pct'):
        s = s[:-4].replace('_', ' ').title() + ' (%)'
    else:
        s = s.replace('_', ' ').title()
    return s


def extract_tunable_parameters(config: Dict[str, Any]) -> List[str]:
    """Extract list of tunable parameter names from config."""
    parameter_ranges = config.get('parameter_ranges', {})
    return list(parameter_ranges.keys())


def extract_parameter_data(history_data: Dict[str, Any], start_iter: int = None, 
                          end_iter: int = None, requested_params: List[str] = None) -> Tuple[str, List[str], List[Tuple[int, Dict[str, float], bool]]]:
    """
    Extract tuner type, tunable parameters, and parameter values over iterations.
    
    Returns:
        (tuner_type, tunable_params, [(iteration, {param_name: value}, is_violated)])
    """
    if not history_data:
        return None, [], []
    
    config = history_data.get('config', {})
    tuner_type = config.get('tuner_type', 'unknown')
    tunable_params = extract_tunable_parameters(config)
    
    # Combine tunable params with requested params (if any)
    params_to_extract = set(tunable_params)
    if requested_params:
        params_to_extract.update(requested_params)
    
    history = history_data.get('history', [])
    data_points = []
    
    for entry in history:
        iteration = entry.get('iteration', 0)
        
        # Filter by iteration range
        if start_iter is not None and iteration < start_iter:
            continue
        if end_iter is not None and iteration > end_iter:
            continue
        
        parameters = entry.get('parameters', {})
        metrics = entry.get('metrics', {})
        is_violated = entry.get('constraint_violated', False)
        
        # Extract parameters/metrics
        # First check parameters, then fall back to metrics if not found
        param_values = {}
        for param_name in params_to_extract:
            if param_name in parameters:
                param_values[param_name] = parameters[param_name]
            elif param_name in metrics:
                param_values[param_name] = metrics[param_name]
        
        if param_values:  # Only add if we have at least one parameter
            data_points.append((iteration, param_values, is_violated))
    
    return tuner_type, tunable_params, data_points


def collect_data_from_files(filepaths: List[str], start_iter: int = None, 
                           end_iter: int = None, requested_params: List[str] = None) -> Dict[str, Dict[str, List[Tuple[int, float, bool]]]]:
    """
    Collect parameter data from multiple history files, grouped by tuner type.
    
    Returns:
        {tuner_type: {param_name: [(iteration, value, is_violated), ...]}}
    """
    all_data = defaultdict(lambda: defaultdict(list))
    
    for filepath in filepaths:
        history_data = load_history_file(filepath)
        if not history_data:
            continue
        
        tuner_type, tunable_params, data_points = extract_parameter_data(
            history_data, start_iter, end_iter, requested_params)
        
        if not data_points:
            print(f"Warning: No data points found in {filepath}")
            continue
        
        # Use model-aware key for LLM tuners
        config = history_data.get('config', {})
        tuner_key = _get_tuner_key(config)
        
        # Group data by parameter name
        for iteration, param_values, is_violated in data_points:
            for param_name, value in param_values.items():
                all_data[tuner_key][param_name].append((iteration, value, is_violated))
    
    return all_data


def plot_parameter_comparison(data: Dict[str, Dict[str, List[Tuple[int, float]]]],
                            output_file: str = None,
                            parameter_name: str = None,
                            title: str = None,
                            ylim: Tuple[float, float] = None,
                            xlim: Tuple[float, float] = None):
    """
    Plot parameter values over iterations for different tuner types.
    
    Args:
        data: {tuner_type: {param_name: [(iteration, value), ...]}}
        output_file: Output file path (if None, shows plot)
        parameter_name: Specific parameter to plot (if None, plots first available)
        title: Optional figure title
    """
    # Determine which parameter to plot
    if parameter_name is None:
        # Find the first parameter that exists across tuners
        all_params = set()
        for tuner_data in data.values():
            all_params.update(tuner_data.keys())
        
        if not all_params:
            print("Error: No tunable parameters found in any history file")
            return
        
        # Prefer common parameters
        preferred_params = ['min_granularity_ns', 'latency_ns', 'wakeup_granularity_ns']
        for pref_param in preferred_params:
            if pref_param in all_params:
                parameter_name = pref_param
                break
        
        if parameter_name is None:
            parameter_name = sorted(all_params)[0]
    
    # Check if parameter exists
    has_data = False
    for tuner_data in data.values():
        if parameter_name in tuner_data:
            has_data = True
            break
    
    if not has_data:
        print(f"Error: Parameter '{parameter_name}' not found in any history file")
        print(f"Available parameters: {sorted(set().union(*[list(td.keys()) for td in data.values()]))}")
        return
    
    # Create plot with clean styling (matching the reference image)
    fig, ax = plt.subplots(figsize=(8, 5))
    
    # Define base colors for tuner families
    base_colors = {
        'fixed': '#95A5A6',          # Gray
        'mlos': '#16A085',           # Teal/Green
        'bayesian': '#F39C12',       # Orange/Yellow
        'human': '#2ECC71',          # Green
        'dqn': '#9B59B6',            # Purple
        'qlearning': '#3498DB',      # Blue
    }
    
    # LLM model-specific colors (differentiate models visually)
    llm_model_colors = [
        '#E74C3C',   # Red
        '#E67E22',   # Orange
        '#8E44AD',   # Dark Purple
        '#2980B9',   # Medium Blue
        '#D35400',   # Burnt Orange
        '#C0392B',   # Dark Red
    ]
    
    # Marker styles for distinction
    base_markers = {
        'fixed': 'x',
        'mlos': 's',
        'bayesian': 'D',
        'human': '^',
        'dqn': 'v',
        'qlearning': 'p',
    }
    
    llm_model_markers = ['o', '^', 'D', 'v', 'p', 'h']
    
    # Base label map for non-LLM tuners
    label_map = {
        'fixed': 'FIXED',
        'mlos': 'MLOS',
        'bayesian': 'BAYESIAN',
        'human': 'HUMAN EXPERT',
        'dqn': 'DQN',
        'qlearning': 'Q-LEARNING',
    }
    
    # Assign colors/markers to LLM model keys
    llm_keys = sorted([k for k in data.keys() if k.startswith('llm:')])
    llm_color_map = {}
    llm_marker_map = {}
    for i, key in enumerate(llm_keys):
        llm_color_map[key] = llm_model_colors[i % len(llm_model_colors)]
        llm_marker_map[key] = llm_model_markers[i % len(llm_model_markers)]
    
    # Plot one line per tuner key
    for tuner_key in sorted(data.keys()):
        if parameter_name not in data[tuner_key]:
            continue
        
        iterations_values = data[tuner_key][parameter_name]
        if not iterations_values:
            continue
        
        # Sort by iteration
        iterations_values.sort(key=lambda x: x[0])
        
        iterations = [x[0] for x in iterations_values]
        values = [x[1] for x in iterations_values]
        
        if tuner_key.startswith('llm:'):
            model_label = tuner_key.split(':', 1)[1]
            color = llm_color_map.get(tuner_key, '#E74C3C')
            marker = llm_marker_map.get(tuner_key, 'o')
            label = f'LLM ({model_label})'
        else:
            color = base_colors.get(tuner_key, '#000000')
            marker = base_markers.get(tuner_key, 'o')
            label = label_map.get(tuner_key, tuner_key.upper())
        
        ax.plot(iterations, values, marker=marker, label=label, color=color, 
                linewidth=2, markersize=6, alpha=0.85, linestyle='-')
    
    # Format parameter name for y-axis
    param_display = parameter_name.replace('_', ' ')
    if 'ns' in param_display.lower():
        param_display = param_display.replace(' ns', '').strip()
        ylabel = f'{param_display} (ms)'
    else:
        ylabel = param_display
    
    # Simple, clean labels matching reference
    ax.set_xlabel('Tuning Cycle', fontsize=11)
    ax.set_ylabel(ylabel, fontsize=11)
    
    # Always use log scale for y-axis (matching reference)
    ax.set_yscale('log')
    
    # Set axis limits
    if ylim is not None:
        ax.set_ylim(ylim)
    
    if xlim is not None:
        ax.set_xlim(xlim)
    
    # Legend inside plot area (matching reference position)
    ax.legend(loc='upper right', fontsize=10, frameon=True, 
              fancybox=False, shadow=False, framealpha=1.0,
              edgecolor='black', facecolor='white')
    
    if title:
        ax.set_title(title, fontsize=12, fontweight='bold', pad=10)
    
    # Grid styling - simple and subtle
    ax.grid(True, alpha=0.3, linestyle='-', linewidth=0.5, color='gray')
    ax.set_axisbelow(True)
    
    # Clean white background
    ax.set_facecolor('white')
    fig.patch.set_facecolor('white')
    
    # Spine styling
    for spine in ax.spines.values():
        spine.set_edgecolor('black')
        spine.set_linewidth(0.8)
    
    # Tight layout
    plt.tight_layout()
    
    if output_file:
        plt.savefig(output_file, dpi=300, bbox_inches='tight', facecolor='white')
        print(f"Plot saved to {output_file}")
    else:
        plt.show()


def _get_plot_style_maps(data: Dict[str, Any]) -> Tuple[Dict, Dict, Dict, Dict]:
    """Build color/marker/label maps for tuner keys. Returns (base_colors, llm_color_map, base_markers, llm_marker_map, label_map)."""
    base_colors = {
        'fixed': '#95A5A6', 'mlos': '#16A085', 'bayesian': '#F39C12',
        'human': '#2ECC71', 'dqn': '#9B59B6', 'qlearning': '#3498DB',
    }
    llm_model_colors = ['#E74C3C', '#E67E22', '#8E44AD', '#2980B9', '#D35400', '#C0392B']
    base_markers = {'fixed': 'x', 'mlos': 's', 'bayesian': 'D', 'human': '^', 'dqn': 'v', 'qlearning': 'p'}
    llm_model_markers = ['o', '^', 'D', 'v', 'p', 'h']
    label_map = {
        'fixed': 'FIXED', 'mlos': 'MLOS', 'bayesian': 'BAYESIAN',
        'human': 'HUMAN EXPERT', 'dqn': 'DQN', 'qlearning': 'Q-LEARNING',
    }
    llm_keys = sorted([k for k in data.keys() if k.startswith('llm:')])
    llm_color_map = {k: llm_model_colors[i % len(llm_model_colors)] for i, k in enumerate(llm_keys)}
    llm_marker_map = {k: llm_model_markers[i % len(llm_model_markers)] for i, k in enumerate(llm_keys)}
    return base_colors, llm_color_map, base_markers, llm_marker_map, label_map


def _tuner_label(tuner_key: str, llm_color_map: Dict, base_colors: Dict, label_map: Dict) -> str:
    if tuner_key.startswith('llm:'):
        return f'LLM ({tuner_key.split(":", 1)[1]})'
    return label_map.get(tuner_key, tuner_key.upper())


def plot_all_parameters_comparison(data: Dict[str, Dict[str, List[Tuple[int, float, bool]]]],
                                  output_file: str = None,
                                  title: str = None,
                                  param_order: List[str] = None,
                                  show_violations: bool = False):
    """
    Plot every tunable parameter in a grid of subplots (one subplot per parameter).
    
    Args:
        data: {tuner_key: {param_name: [(iteration, value, is_violated), ...]}}
        output_file: Output file path (if None, shows plot)
        title: Optional figure title
        param_order: Optional list of parameter names for subplot order (default: sorted union)
        show_violations: Whether to show constraint violations with different markers
    """
    all_params = set()
    for tuner_data in data.values():
        all_params.update(tuner_data.keys())
    if not all_params:
        print("Error: No tunable parameters found in any history file")
        return
    
    params = param_order if param_order else sorted(all_params)
    n_params = len(params)
    ncols = min(3, n_params)
    nrows = (n_params + ncols - 1) // ncols
    
    fig, axes = plt.subplots(nrows, ncols, figsize=(5 * ncols, 4 * nrows))
    if n_params == 1:
        axes = np.array([axes])
    axes_flat = axes.flatten() if n_params > 1 else [axes.item()]
    
    base_colors, llm_color_map, base_markers, llm_marker_map, label_map = _get_plot_style_maps(data)
    
    for idx, param_name in enumerate(params):
        ax = axes_flat[idx]
        for tuner_key in sorted(data.keys()):
            if param_name not in data[tuner_key]:
                continue
            iterations_values = data[tuner_key][param_name]
            if not iterations_values:
                continue
            iterations_values = sorted(iterations_values, key=lambda x: x[0])
            iterations = [x[0] for x in iterations_values]
            values = [x[1] for x in iterations_values]
            if tuner_key.startswith('llm:'):
                color = llm_color_map.get(tuner_key, '#E74C3C')
                marker = llm_marker_map.get(tuner_key, 'o')
            else:
                color = base_colors.get(tuner_key, '#000000')
                marker = base_markers.get(tuner_key, 'o')
            label = _tuner_label(tuner_key, llm_color_map, base_colors, label_map)
            ax.plot(iterations, values, marker=marker, label=label, color=color,
                    linewidth=1.5, markersize=4, alpha=0.85, linestyle='-')
        
        ax.set_yscale('log')
        ax.set_xlabel('Tuning Cycle', fontsize=9)
        ax.set_ylabel(_format_param_label(param_name), fontsize=9)
        ax.set_title(_format_param_label(param_name), fontsize=10, fontweight='bold')
        ax.grid(True, alpha=0.3, linestyle='-', linewidth=0.5, color='gray')
        ax.set_axisbelow(True)
        ax.set_facecolor('white')
    
    for j in range(n_params, len(axes_flat)):
        axes_flat[j].set_visible(False)
    
    handles, labels = axes_flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', ncol=min(len(labels), 6), fontsize=9,
               frameon=True, fancybox=False, framealpha=1.0, edgecolor='black', facecolor='white',
               bbox_to_anchor=(0.5, 0.02))
    
    if title:
        fig.suptitle(title, fontsize=12, fontweight='bold', y=1.02)
    fig.patch.set_facecolor('white')
    plt.tight_layout(rect=[0, 0.08, 1, 0.98])
    
    if output_file:
        plt.savefig(output_file, dpi=300, bbox_inches='tight', facecolor='white')
        print(f"Plot saved to {output_file}")
    else:
        plt.show()
    plt.close(fig)


def collect_violation_data_from_files(filepaths: List[str],
                                     start_iter: int = None,
                                     end_iter: int = None) -> Dict[str, List[Tuple[int, int]]]:
    """
    Collect cumulative constraint violation counts per iteration from history files.
    
    Returns:
        {tuner_key: [(iteration, cumulative_violation_count), ...]}
    """
    result = defaultdict(list)  # tuner_key -> [(iter, cumulative), ...]
    
    for filepath in filepaths:
        history_data = load_history_file(filepath)
        if not history_data:
            continue
        config = history_data.get('config', {})
        tuner_key = _get_tuner_key(config)
        history = history_data.get('history', [])
        cumulative = 0
        for entry in history:
            iteration = entry.get('iteration', 0)
            if start_iter is not None and iteration < start_iter:
                continue
            if end_iter is not None and iteration > end_iter:
                continue
            if entry.get('constraint_violated', False):
                cumulative += 1
            result[tuner_key].append((iteration, cumulative))
    
    return dict(result)


def plot_violations(violation_data: Dict[str, List[Tuple[int, int]]],
                    output_file: str = None,
                    title: str = None):
    """
    Plot cumulative constraint violations over tuning cycle (one line per tuner).
    
    Args:
        violation_data: {tuner_key: [(iteration, cumulative_count), ...]}
        output_file: Output file path (if None, shows plot)
        title: Optional figure title
    """
    if not violation_data:
        print("No violation data to plot")
        return
    
    base_colors, llm_color_map, base_markers, llm_marker_map, label_map = _get_plot_style_maps(violation_data)
    
    fig, ax = plt.subplots(figsize=(8, 5))
    
    for tuner_key in sorted(violation_data.keys()):
        points = violation_data[tuner_key]
        if not points:
            continue
        points = sorted(points, key=lambda x: x[0])
        iterations = [x[0] for x in points]
        cumulative = [x[1] for x in points]
        if tuner_key.startswith('llm:'):
            color = llm_color_map.get(tuner_key, '#E74C3C')
            marker = llm_marker_map.get(tuner_key, 'o')
        else:
            color = base_colors.get(tuner_key, '#000000')
            marker = base_markers.get(tuner_key, 'o')
        label = _tuner_label(tuner_key, llm_color_map, base_colors, label_map)
        ax.plot(iterations, cumulative, marker=marker, label=label, color=color,
                linewidth=2, markersize=6, alpha=0.85, linestyle='-')
    
    ax.set_xlabel('Tuning Cycle', fontsize=11)
    ax.set_ylabel('Cumulative constraint violations', fontsize=11)
    if title:
        ax.set_title(title, fontsize=12, fontweight='bold', pad=10)
    ax.legend(loc='upper right', fontsize=10, frameon=True, fancybox=False, framealpha=1.0,
              edgecolor='black', facecolor='white')
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
        print(f"Violations plot saved to {output_file}")
    else:
        plt.show()
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(
        description='Plot parameter values over iterations for different tuner types',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Plot from all history files in results directory
  python scripts/plot_tuner_comparison.py -d results

  # Plot specific files
  python scripts/plot_tuner_comparison.py -f results/history1.json results/history2.json

  # Plot specific parameter
  python scripts/plot_parameter_comparison.py -d results -p min_granularity_ns

  # Save to file
  python scripts/plot_parameter_comparison.py -d results -o parameter_comparison.png
  
  # Filter by iteration range
  python scripts/plot_parameter_comparison.py -d results --start 10 --end 100
  
  # Set custom axis limits
  python scripts/plot_parameter_comparison.py -d results --ylim 0.1 100 --xlim 0 200
        """
    )
    
    parser.add_argument(
        '-d', '--directory',
        type=str,
        help='Directory containing optimization history JSON files'
    )
    
    parser.add_argument(
        '-f', '--files',
        nargs='+',
        help='Specific history JSON files to plot'
    )
    
    parser.add_argument(
        '-p', '--parameter',
        type=str,
        help='Parameter name to plot (default: auto-detect)'
    )
    
    parser.add_argument(
        '-o', '--output',
        type=str,
        help='Output file path (default: show plot)'
    )
    
    parser.add_argument(
        '-t', '--title',
        type=str,
        help='Figure title (e.g., "Sysbench CPU (P99 Latency)")'
    )
    
    parser.add_argument(
        '--ylim',
        nargs=2,
        type=float,
        metavar=('MIN', 'MAX'),
        help='Y-axis limits (e.g., --ylim 0.1 100)'
    )
    
    parser.add_argument(
        '--xlim',
        nargs=2,
        type=float,
        metavar=('MIN', 'MAX'),
        help='X-axis limits (e.g., --xlim 0 200)'
    )
    
    parser.add_argument(
        '--start',
        type=int,
        help='Starting iteration to include (inclusive)'
    )
    
    parser.add_argument(
        '--end',
        type=int,
        help='Ending iteration to include (inclusive)'
    )
    
    parser.add_argument(
        '--all-params',
        action='store_true',
        help='Plot every tunable parameter in one figure (one subplot per parameter)'
    )
    
    parser.add_argument(
        '--violations-output',
        type=str,
        metavar='FILE',
        help='Also plot cumulative constraint violations to this file'
    )
    
    parser.add_argument(
        '--show-violations',
        action='store_true',
        help='Show constraint violations with different markers (x) on comparison plots'
    )
    
    args = parser.parse_args()
    
    # Collect file paths
    filepaths = []
    
    if args.files:
        filepaths.extend(args.files)
    
    if args.directory:
        # Look in directory and one level down (e.g. results/bench/fixed/optimization_history_*.json)
        for pattern in (
            os.path.join(args.directory, 'optimization_history_*.json'),
            os.path.join(args.directory, '*', 'optimization_history_*.json'),
        ):
            filepaths.extend(glob.glob(pattern))
    
    if not filepaths:
        print("Error: No history files found. Use -d or -f to specify files.")
        return
    
    # Remove duplicates
    filepaths = list(set(filepaths))
    
    print(f"Found {len(filepaths)} history file(s)")
    
    # Collect data with filtering options
    # If a specific parameter is requested, pass it so we can extract it even if not tunable
    requested_params = [args.parameter] if args.parameter else None
    data = collect_data_from_files(filepaths, 
                                   start_iter=args.start, 
                                   end_iter=args.end,
                                   requested_params=requested_params)
    
    if not data:
        print("Error: No valid data found in history files")
        return
    
    print(f"Found data for tuner types: {', '.join(sorted(data.keys()))}")
    
    # Prepare axis limits
    ylim = tuple(args.ylim) if args.ylim else None
    xlim = tuple(args.xlim) if args.xlim else None
    
    # Parameter comparison: single-parameter or all-params grid
    if args.all_params:
        all_params = set()
        for tuner_data in data.values():
            all_params.update(tuner_data.keys())
        param_order = sorted(all_params)
        print(f"Plotting all {len(param_order)} parameters: {param_order}")
        plot_all_parameters_comparison(data, output_file=args.output, title=args.title,
                                      param_order=param_order, show_violations=args.show_violations)
    else:
        plot_parameter_comparison(data, output_file=args.output, parameter_name=args.parameter,
                                 title=args.title, ylim=ylim, xlim=xlim, show_violations=args.show_violations)
    
    # Optional: constraint violations plot
    if args.violations_output:
        violation_data = collect_violation_data_from_files(
            filepaths, start_iter=args.start, end_iter=args.end)
        if violation_data:
            print(f"Violation data for tuners: {', '.join(sorted(violation_data.keys()))}")
            plot_violations(violation_data, output_file=args.violations_output,
                            title=(args.title + ' – Constraint violations') if args.title else 'Cumulative constraint violations')
        else:
            print("No constraint violation data found in history entries (constraint_violated field).")
            print("Skipping violations plot.")


if __name__ == '__main__':
    main()