#!/usr/bin/env python3
"""
Create a Parallel Coordinates plot for high-dimensional parameter tuning visualization.

This script visualizes the relationship between multiple tuned parameters and the optimization metric.
Each trial is represented as a line traversing vertical axes (one for each parameter + the metric).
Lines are colored by Tuner Type to compare search strategies.
"""

import json
import os
import glob
import argparse
import matplotlib.pyplot as plt
from matplotlib.path import Path
import matplotlib.patches as patches
import numpy as np
from collections import defaultdict
from typing import Dict, List, Tuple, Any, Optional

def load_history_file(filepath: str) -> Dict[str, Any]:
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading {filepath}: {e}")
        return None

def _format_model_name(model_name: str) -> str:
    if not model_name:
        return 'LLM'
    name_lower = model_name.lower().replace('_', '-')
    if 'gemini-2.5-flash-lite' in name_lower: return '2.5 Flash Lite'
    if 'gemini-2.5-flash' in name_lower: return '2.5 Flash'
    if 'gemini-2.5-pro' in name_lower: return '2.5 Pro'
    if 'gemini-' in name_lower: return model_name.replace('gemini-', '').replace('-', ' ').title()
    return model_name

def _get_tuner_key(config: Dict[str, Any]) -> str:
    tuner_type = config.get('tuner_type', 'unknown')
    if tuner_type.lower() == 'llm':
        model_name = config.get('llm_model_name') or config.get('llm_actor_model')
        if model_name:
            return f'llm:{_format_model_name(model_name)}'
    return tuner_type

def extract_tunable_parameters(config: Dict[str, Any]) -> List[str]:
    parameter_ranges = config.get('parameter_ranges', {})
    return sorted(list(parameter_ranges.keys()))

def _format_axis_label(name: str) -> str:
    if name == 'metric': return 'Metric'
    s = name.strip()
    if s.endswith('_ns'): s = s[:-3].replace('_', ' ').title() + '\n(ns)'
    elif s.endswith('_pct'): s = s[:-4].replace('_', ' ').title() + '\n(%)'
    else: s = s.replace('_', ' ').title()
    return s

def plot_parallel_coordinates(data_points: List[Dict[str, Any]], 
                            params: List[str], 
                            metric_name: str,
                            output_file: str = None,
                            title: str = None):
    """
    Create a parallel coordinates plot using matplotlib.
    
    Args:
        data_points: List of dicts with 'tuner', 'metric', and param values
        params: List of parameter names
        metric_name: Name of optimization metric
    """
    if not data_points:
        print("No data points to plot")
        return

    # Add metric to dimensions to plot (as the last axis)
    dims = params + ['metric']
    
    # Calculate min/max for normalization (and handle categorical)
    ranges = {}
    dim_mappings = {} # Store mapping for categorical dims
    
    for dim in dims:
        values = [d.get(dim) for d in data_points if d.get(dim) is not None]
        if not values:
            ranges[dim] = (0, 1)
            continue
            
        # Check if numeric
        is_numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)
        
        if is_numeric:
            mn, mx = min(values), max(values)
            if mn == mx:
                ranges[dim] = (mn - 0.5, mx + 0.5)
            else:
                ranges[dim] = (mn, mx)
        else:
            # Categorical - map to 0..N-1
            unique_vals = sorted(list(set(values)))
            mapping = {v: i for i, v in enumerate(unique_vals)}
            dim_mappings[dim] = mapping
            ranges[dim] = (0, max(1, len(unique_vals) - 1))

    # Normalize data
    normalized_data = []
    for d in data_points:
        norm_d = {'tuner': d['tuner']}
        valid = True
        for dim in dims:
            val = d.get(dim)
            if val is None:
                valid = False
                break
            
            mn, mx = ranges[dim]
            
            # Map if categorical
            if dim in dim_mappings:
                if val not in dim_mappings[dim]:
                    valid = False
                    break
                numeric_val = dim_mappings[dim][val]
            else:
                numeric_val = val
                
            norm_d[dim] = (numeric_val - mn) / (mx - mn)
            
        if valid:
            normalized_data.append(norm_d)

    if not normalized_data:
        print("No valid data after normalization")
        return

    # Setup plot
    fig, host = plt.subplots(figsize=(15, 8))
    
    # Create twin axes for each dimension
    axes = [host] + [host.twinx() for i in range(len(dims) - 1)]
    
    for i, ax in enumerate(axes):
        ax.set_ylim(0, 1)
        ax.spines['top'].set_visible(False)
        ax.spines['bottom'].set_visible(False)
        if i > 0:
            ax.spines['left'].set_visible(False)
            ax.yaxis.set_ticks_position('right')
            ax.spines['right'].set_position(('axes', i / (len(dims) - 1)))
        else:
            ax.spines['right'].set_visible(False)
            ax.yaxis.set_ticks_position('left')

    # Set axis limits and ticks
    for i, dim in enumerate(dims):
        mn, mx = ranges[dim]
        ax = axes[i]
        
        if dim in dim_mappings:
            # Categorical ticks
            mapping = dim_mappings[dim]
            # Invert mapping to get label from index
            inv_map = {v: k for k, v in mapping.items()}
            n_cats = len(mapping)
            if n_cats > 1:
                # Place ticks at mapped positions normalized
                tick_vals = sorted(mapping.values())
                norm_ticks = [(v - mn) / (mx - mn) for v in tick_vals]
                tick_labels = [str(inv_map[v]) for v in tick_vals]
                ax.set_yticks(norm_ticks)
                ax.set_yticklabels(tick_labels, fontsize=8)
            else:
                ax.set_yticks([0.5])
                ax.set_yticklabels([str(list(mapping.keys())[0])], fontsize=8)
        else:
            # Numeric ticks
            # Set 5 ticks
            ticks = np.linspace(0, 1, 5)
            tick_labels = [f"{mn + t * (mx - mn):.2g}" for t in ticks]
            ax.set_yticks(ticks)
            ax.set_yticklabels(tick_labels, fontsize=9)
            
        # Add axis label at bottom
        ax.text(i / (len(dims) - 1), -0.08, _format_axis_label(dim), 
               transform=host.transAxes, ha='center', va='top', fontsize=10, fontweight='bold')

    host.set_xlim(0, len(dims) - 1)
    host.set_xticks([]) # Hide x ticks
    host.spines['top'].set_visible(False)
    host.spines['bottom'].set_visible(False)
    host.spines['left'].set_visible(False)
    host.spines['right'].set_visible(False)

    # Define colors
    base_colors = {
        'fixed': '#95A5A6', 'mlos': '#16A085', 'bayesian': '#F39C12',
        'human': '#2ECC71', 'dqn': '#9B59B6', 'qlearning': '#3498DB',
    }
    llm_model_colors = ['#E74C3C', '#E67E22', '#8E44AD', '#2980B9', '#D35400', '#C0392B']
    
    # Assign colors to tuners
    tuners = sorted(list(set(d['tuner'] for d in normalized_data)))
    llm_keys = [t for t in tuners if t.startswith('llm:')]
    llm_map = {k: llm_model_colors[i % len(llm_model_colors)] for i, k in enumerate(llm_keys)}
    
    def get_color(tuner):
        if tuner.startswith('llm:'): return llm_map.get(tuner, '#E74C3C')
        return base_colors.get(tuner, '#000000')

    # Plot lines
    # Shuffle to avoid z-order bias
    import random
    random.seed(42)
    random.shuffle(normalized_data)

    for d in normalized_data:
        verts = list(zip([x for x in range(len(dims))], [d[dim] for dim in dims]))
        codes = [Path.MOVETO] + [Path.LINETO for _ in range(len(dims) - 1)]
        path = Path(verts, codes)
        patch = patches.PathPatch(path, facecolor='none', lw=1.5, 
                                 edgecolor=get_color(d['tuner']), alpha=0.6)
        host.add_patch(patch)

    # Legend
    from matplotlib.lines import Line2D
    legend_elements = []
    for tuner in tuners:
        label = tuner.split(':', 1)[1] if tuner.startswith('llm:') else tuner.upper()
        if tuner.startswith('llm:'): label = f"LLM ({label})"
        legend_elements.append(Line2D([0], [0], color=get_color(tuner), lw=2, label=label))
    
    host.legend(handles=legend_elements, loc='upper center', bbox_to_anchor=(0.5, 1.15), 
               ncol=min(len(tuners), 4), frameon=False, fontsize=10)

    if title:
        plt.suptitle(title, fontsize=14, fontweight='bold', y=0.98)

    plt.tight_layout(rect=[0, 0.05, 1, 0.95])
    
    if output_file:
        plt.savefig(output_file, dpi=300, bbox_inches='tight', facecolor='white')
        print(f"Parallel Coordinates plot saved to {output_file}")
    else:
        plt.show()
    plt.close(fig)

def main():
    parser = argparse.ArgumentParser(description='Create Parallel Coordinates plot')
    parser.add_argument('-d', '--directory', required=True, help='Results directory')
    parser.add_argument('-o', '--output', help='Output file')
    parser.add_argument('-t', '--title', help='Plot title')
    args = parser.parse_args()

    filepaths = []
    for pattern in (os.path.join(args.directory, 'optimization_history_*.json'),
                   os.path.join(args.directory, '*', 'optimization_history_*.json')):
        filepaths.extend(glob.glob(pattern))
    
    if not filepaths:
        print("No history files found")
        return

    data_points = []
    params = set()
    metric_name = None

    for fp in filepaths:
        hist = load_history_file(fp)
        if not hist: continue
        
        config = hist.get('config', {})
        tuner_key = _get_tuner_key(config)
        tunable = extract_tunable_parameters(config)
        params.update(tunable)
        
        # Detect metric name
        mn = config.get('optimization_metric')
        if not metric_name and mn: metric_name = mn
        
        for entry in hist.get('history', []):
            # Skip violations? Or maybe plot them? 
            # Parallel coords usually shows all points. We can filter if needed.
            # Let's include all points.
            
            # Extract metric
            val = None
            if metric_name:
                val = entry.get('metrics', {}).get(metric_name) or entry.get('system_metrics', {}).get(metric_name)
            
            # Extract params
            p_vals = {}
            for p in tunable:
                p_vals[p] = entry.get('parameters', {}).get(p)
            
            if val is not None:
                dp = {'tuner': tuner_key, 'metric': float(val)}
                dp.update(p_vals)
                data_points.append(dp)

    if not data_points:
        print("No valid data points found")
        return

    if not metric_name: metric_name = "Metric"
    
    plot_parallel_coordinates(data_points, sorted(list(params)), metric_name, 
                            output_file=args.output, title=args.title)

if __name__ == '__main__':
    main()
