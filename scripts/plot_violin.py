#!/usr/bin/env python3
"""
Create boxplot comparison plots across different tuner types and configurations.

This script parses optimization history JSON files and creates boxplots showing
the distribution of a specified metric for different tuner configurations, grouped by:
- Tuner type (FIXED, LLM, MLOS, BAYESIAN, QLEARNING, DQN)
- Number of parameters being tuned (1 or 2)
- Whether IPC optimization was used
"""

import json
import os
import glob
import argparse
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from collections import defaultdict
from typing import Dict, List, Tuple, Any, Optional


def load_history_file(filepath: str) -> Optional[Dict[str, Any]]:
    """Load and parse an optimization history JSON file."""
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
            if isinstance(data, dict):
                data['_filepath'] = filepath
            return data
    except Exception as e:
        print(f"Error loading {filepath}: {e}")
        return None


def _format_metric_label(metric_name: str) -> str:
    """
    Format metric name for display in axis labels.
    
    Args:
        metric_name: Raw metric name (e.g., 'latency_p95', 'p_99_latency', 'throughput')
        
    Returns:
        Formatted label (e.g., 'P95 Latency (ms)', 'Throughput (req/s)')
    """
    # Handle latency metrics
    if metric_name == 'latency_avg':
        return 'Average Latency (ms)'
    elif metric_name == 'latency_p95':
        return 'P95 Latency (ms)'
    elif metric_name == 'p_99_latency':
        return 'P99 Latency (ms)'
    elif metric_name == 'p_95_latency':
        return 'P95 Latency (ms)'
    elif metric_name == 'p_90_latency':
        return 'P90 Latency (ms)'
    elif metric_name == 'p_75_latency':
        return 'P75 Latency (ms)'
    elif metric_name == 'p_25_latency':
        return 'P25 Latency (ms)'
    elif metric_name in ['latency_median', 'latency_min', 'latency_max']:
        return metric_name.replace('_', ' ').title() + ' (ms)'
    # Handle throughput/goodput
    elif metric_name == 'throughput':
        return 'Throughput (req/s)'
    elif metric_name == 'goodput':
        return 'Goodput (req/s)'
    elif metric_name == 'queries_per_hour':
        return 'Queries per hour'
    # Handle power metrics
    elif metric_name == 'power_socket0_watts':
        return 'Power Socket 0 (W)'
    elif metric_name == 'power_ram_watts':
        return 'Power RAM (W)'
    # Handle CPU metrics
    elif 'cpu_load' in metric_name:
        return metric_name.replace('_', ' ').title() + ' (%)'
    # Handle C-state metrics
    elif 'cstate' in metric_name:
        return metric_name.replace('_', ' ').title() + ' (%)'
    # Default: format the name nicely
    else:
        return metric_name.replace('_', ' ').title()


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
        # Generic gemini: strip "gemini-" prefix
        return model_name.replace('gemini-', '').replace('-', ' ').title()
    else:
        # Generic fallback: use full name
        return model_name


def get_configuration_label(config: Dict[str, Any], tuner_type: str) -> str:
    """
    Determine configuration label based on:
    - Number of parameters being tuned
    - Whether IPC optimization was used
    - Tuner type (FIXED always gets "default")
    - For LLM tuners, uses the model name to differentiate runs
    """
    tuner_type_upper = tuner_type.upper()
    
    # FIXED tuner always uses "default"
    if tuner_type_upper == 'FIXED':
        return 'default'
    
    # LLM tuner: differentiate by model name
    if tuner_type_upper == 'LLM':
        model_name = config.get('llm_model_name') or config.get('llm_actor_model')
        if model_name:
            return _format_model_name(model_name)
    
    parameter_ranges = config.get('parameter_ranges', {})
    num_params = len(parameter_ranges)
    
    optimization_metric = config.get('optimization_metric', '').lower()
    is_ipc = 'ipc' in optimization_metric or 'perf_ipc' in optimization_metric
    
    if is_ipc:
        return 'IPC'
    elif num_params == 1:
        return '1'
    elif num_params == 2:
        return '2'
    else:
        return str(num_params)


def _extract_dual_timing_metric_values(entry: Dict[str, Any], metric_name: str) -> List[float]:
    """
    Extract ALL dual-loop timing values for a metric from one history entry.

    Includes:
    - Speculator quick timing(s): all responses from ``metrics.all_quick_tuner_timings`` when present,
      otherwise ``tuner_timing.quick`` / legacy ``tuner_timing``.
    - Actor timing: ``tuner_timing.reasoning`` and legacy ``reasoning_tuner_timing``.
    """
    values: List[float] = []
    metrics = entry.get('metrics', {}) or {}
    timing = entry.get('tuner_timing', {}) or {}

    quick_timing_entries: List[Dict[str, Any]] = []
    reasoning_timing_entries: List[Dict[str, Any]] = []

    # Prefer all quick timings when available (in-window mode can have multiple).
    all_quick = metrics.get('all_quick_tuner_timings', [])
    if isinstance(all_quick, list):
        for item in all_quick:
            if isinstance(item, dict):
                quick_timing_entries.append(item)

    # Fallback to single quick timing when all_quick_tuner_timings is absent.
    if not quick_timing_entries and isinstance(timing, dict):
        if isinstance(timing.get('quick'), dict):
            quick_timing_entries.append(timing['quick'])
        elif timing.get('tuner_type') == 'speculator_quick':
            quick_timing_entries.append(timing)

    # Reasoning timing (new nested format or old flat format).
    if isinstance(timing, dict):
        if isinstance(timing.get('reasoning'), dict):
            reasoning_timing_entries.append(timing['reasoning'])
        elif timing.get('tuner_type') == 'actor_reasoning':
            reasoning_timing_entries.append(timing)

    legacy_reasoning = entry.get('reasoning_tuner_timing')
    if isinstance(legacy_reasoning, dict):
        reasoning_timing_entries.append(legacy_reasoning)

    for timing_entry in quick_timing_entries + reasoning_timing_entries:
        timing_value = timing_entry.get(metric_name)
        if timing_value is None:
            continue
        try:
            values.append(float(timing_value))
        except (TypeError, ValueError):
            continue

    return values


def _extract_dual_timing_metric_values_by_role(entry: Dict[str, Any], metric_name: str) -> Tuple[List[float], List[float]]:
    """
    Extract dual-loop timing values split by role.

    Returns:
        (speculator_values, actor_values)
    """
    metrics = entry.get('metrics', {}) or {}
    timing = entry.get('tuner_timing', {}) or {}

    quick_timing_entries: List[Dict[str, Any]] = []
    reasoning_timing_entries: List[Dict[str, Any]] = []

    all_quick = metrics.get('all_quick_tuner_timings', [])
    if isinstance(all_quick, list):
        for item in all_quick:
            if isinstance(item, dict):
                quick_timing_entries.append(item)

    if not quick_timing_entries and isinstance(timing, dict):
        if isinstance(timing.get('quick'), dict):
            quick_timing_entries.append(timing['quick'])
        elif timing.get('tuner_type') == 'speculator_quick':
            quick_timing_entries.append(timing)

    if isinstance(timing, dict):
        if isinstance(timing.get('reasoning'), dict):
            reasoning_timing_entries.append(timing['reasoning'])
        elif timing.get('tuner_type') == 'actor_reasoning':
            reasoning_timing_entries.append(timing)

    legacy_reasoning = entry.get('reasoning_tuner_timing')
    if isinstance(legacy_reasoning, dict):
        reasoning_timing_entries.append(legacy_reasoning)

    spec_values: List[float] = []
    actor_values: List[float] = []

    for timing_entry in quick_timing_entries:
        timing_value = timing_entry.get(metric_name)
        if timing_value is None:
            continue
        try:
            spec_values.append(float(timing_value))
        except (TypeError, ValueError):
            continue

    for timing_entry in reasoning_timing_entries:
        timing_value = timing_entry.get(metric_name)
        if timing_value is None:
            continue
        try:
            actor_values.append(float(timing_value))
        except (TypeError, ValueError):
            continue

    return spec_values, actor_values


def _count_dual_agent_contributions_by_role(entry: Dict[str, Any]) -> Tuple[int, int]:
    """
    Count dual-loop agent contributions represented in one history entry.

    Returns:
        (speculator_count, actor_count)
    """
    metrics = entry.get('metrics', {}) or {}
    timing = entry.get('tuner_timing', {}) or {}

    quick_count = 0
    all_quick = metrics.get('all_quick_tuner_timings', [])
    if isinstance(all_quick, list):
        quick_count = sum(1 for item in all_quick if isinstance(item, dict))

    if quick_count == 0 and isinstance(timing, dict):
        if isinstance(timing.get('quick'), dict):
            quick_count = 1
        elif timing.get('tuner_type') == 'speculator_quick':
            quick_count = 1

    actor_count = 0
    if isinstance(timing, dict):
        if isinstance(timing.get('reasoning'), dict):
            actor_count = 1
        elif timing.get('tuner_type') == 'actor_reasoning':
            actor_count = 1

    legacy_reasoning = entry.get('reasoning_tuner_timing')
    if actor_count == 0 and isinstance(legacy_reasoning, dict):
        actor_count = 1

    return quick_count, actor_count


def _count_dual_agent_contributions(entry: Dict[str, Any]) -> int:
    """
    Count dual-loop agent contributions represented in one history entry.

    For regular dual-loop runs we treat each quick/actor response as one contribution.
    """
    quick_count, actor_count = _count_dual_agent_contributions_by_role(entry)
    return quick_count + actor_count


def extract_metric_values(history_data: Dict[str, Any], metric_name: str,
                          start_iter: int = None, end_iter: int = None, 
                          trim: bool = False) -> Tuple[str, str, List[float], int, List[float], List[float]]:
    """
    Extract metric values from history data.
    
    Checks both 'metrics' and 'system_metrics' sections for the metric.
    
    Returns:
        (
            tuner_type,
            config_label,
            [metric_values, ...],
            violation_count,
            [actor_metric_values, ...],
            [speculator_metric_values, ...],
        )
    """
    if not history_data:
        return None, None, [], 0, [], []
    
    config = history_data.get('config', {})
    tuner_type = config.get('tuner_type', 'unknown')
    config_label = get_configuration_label(config, tuner_type)
    
    # Customize label based on filepath/mode for variant runs.
    # Use filepath/mode only for "(Dual)" so we don't merge single-loop runs that
    # happen to have actor+speculator in config (e.g. llm_gemini_2_5_flash) with
    # actual dual-loop runs (e.g. llm_gemini_2_5_flash_lite_llm_dual).
    filepath = history_data.get('_filepath', '')
    filepath_lower = str(filepath).lower()
    results_dir_lower = str(config.get('results_dir', '')).lower()
    mode = history_data.get('mode', '')
    is_tuxbot = ('tuxbot' in filepath_lower) or ('tuxbot' in results_dir_lower)
    is_dual = (
        'llm_dual' in filepath_lower or
        'dual_loop' in filepath_lower or
        mode == 'actor-speculator'
    )
    is_full_metrics = bool(config.get('llm_full_metrics_prompt_mode', False))
    is_app_metrics_variant = (
        'app_metrics' in filepath_lower or
        'app_metrics' in results_dir_lower
    )
    is_full_metrics_mode3 = bool(
        config.get('llm_full_metrics_explicit_signature_compare', False)
        or 'app_metrics_mode3' in filepath_lower
        or 'full_metrics_mode3' in filepath_lower
    )
    use_indirect = bool(config.get('use_indirect_optimization', False))
    raw_indirect_style = str(config.get('llm_indirect_prompt_style', 'signature_compare') or 'signature_compare').strip().lower()
    indirect_style = {
        'signature': 'signature_compare',
        'signature_explicit': 'signature_compare',
        'plain': 'all_metrics_plain',
        'all_metrics': 'all_metrics_plain',
    }.get(raw_indirect_style, raw_indirect_style)
    raw_omit_pairwise = config.get('omit_explicit_pairwise_comparison_instruction', 0)
    try:
        omit_pairwise_mode = int(raw_omit_pairwise)
    except (TypeError, ValueError):
        omit_pairwise_mode = 0
    is_indirect_mode2 = use_indirect and (omit_pairwise_mode == 2 or indirect_style == 'all_metrics_plain')
    indirect_mode_label = 'Mode2' if is_indirect_mode2 else 'Mode3'
    spec_metrics = config.get('llm_speculator_additional_metrics') or []
    primary_metric = config.get('optimization_metric')
    spec_hides_primary = bool(config.get('llm_speculator_hide_primary_metric', False))
    primary_absent_in_spec = (
        bool(primary_metric) and
        isinstance(spec_metrics, list) and
        primary_metric not in spec_metrics
    )
    is_perf_only_speculator_dual = (
        is_dual and (
            'perf_only_speculator' in filepath_lower or
            (spec_hides_primary and primary_absent_in_spec)
        )
    )

    if is_tuxbot:
        config_label = 'Tuxbot'
    elif is_full_metrics:
        # Keep full-metrics variants as distinct columns from baseline LLM runs.
        if is_dual:
            if is_app_metrics_variant and is_full_metrics_mode3:
                config_label += ' (Dual App Metrics Mode3)'
            elif is_app_metrics_variant:
                config_label += ' (Dual App Metrics Mode2)'
            elif is_full_metrics_mode3:
                config_label += ' (Dual Full Metrics Mode3)'
            elif is_perf_only_speculator_dual:
                config_label += ' (Dual Perf-Only Speculator)'
            else:
                config_label += ' (Dual Full Metrics Mode2)'
        else:
            model_name = (config.get('llm_model_name') or '').lower()
            if is_app_metrics_variant and is_full_metrics_mode3:
                config_label += ' (App Metrics Mode3)'
            elif is_app_metrics_variant:
                config_label += ' (App Metrics Mode2)'
            elif 'flash-lite' in model_name or 'lite' in model_name:
                if is_full_metrics_mode3:
                    config_label += ' (Full Metrics Mode3)'
                else:
                    config_label += ' (Full Metrics Mode2)'
            else:
                if is_full_metrics_mode3:
                    config_label += ' (Reasoning Full Metrics Mode3)'
                else:
                    config_label += ' (Reasoning Full Metrics Mode2)'
    elif is_dual:
        # Show both models for dual-loop runs when available and keep mode-specific
        # variants as separate columns.
        actor_model = config.get('llm_actor_model') or config.get('llm_model_name')
        spec_model = config.get('llm_speculator_model') or config.get('llm_secondary_model')
        if actor_model and spec_model:
            config_label = f"{_format_model_name(actor_model)} + {_format_model_name(spec_model)}"

        if 'indirect_all' in filepath_lower:
            config_label += f' (Dual Indirect All {indirect_mode_label})'
        elif 'indirect' in filepath_lower:
            config_label += f' (Dual Indirect {indirect_mode_label})'
        elif 'ipc' in filepath_lower:
            config_label += ' (Dual IPC)'
        elif is_perf_only_speculator_dual:
            config_label += ' (Dual Perf-Only Speculator)'
        else:
            config_label += ' (Dual)'
    elif 'indirect' in filepath_lower:
        if 'indirect_all' in filepath_lower:
            config_label += f' (Indirect All {indirect_mode_label})'
        else:
            config_label += f' (Indirect {indirect_mode_label})'
    elif 'ipc' in filepath_lower:
        config_label += ' (IPC)'
    elif 'mlos_trimming' in filepath_lower:
        config_label += ' (MLOS Trimming)'
            
    tuner_type = tuner_type.upper()
    
    history = history_data.get('history', [])
    metric_values = []
    actor_metric_values = []
    speculator_metric_values = []
    violation_count = 0
    
    for pos, entry in enumerate(history, start=1):
        # Normalize iteration/window index so --start/--end always works,
        # even for histories that don't store "iteration" explicitly.
        iteration = (
            entry.get('iteration')
            or entry.get('window_number')
            or entry.get('index')
            or pos
        )
        try:
            iteration = int(iteration)
        except (TypeError, ValueError):
            iteration = pos
        
        # Filter by iteration range
        if start_iter is not None and iteration < start_iter:
            continue
        if end_iter is not None and iteration > end_iter:
            continue
        
        if entry.get('constraint_violated', False):
            violation_count += 1
        
        metrics = entry.get('metrics', {})
        system_metrics = entry.get('system_metrics', {})
        
        # Get metric value - check both metrics and system_metrics
        value = metrics.get(metric_name)
        if value is None:
            value = system_metrics.get(metric_name)

        dual_timing_values: List[float] = []
        dual_timing_spec_values: List[float] = []
        dual_timing_actor_values: List[float] = []
        if is_dual:
            dual_timing_spec_values, dual_timing_actor_values = _extract_dual_timing_metric_values_by_role(entry, metric_name)
            dual_timing_values = dual_timing_spec_values + dual_timing_actor_values

        if value is not None:
            # Convert to float if needed
            value = float(value)
            
            # Handle unit conversion for latency metrics stored in microseconds
            if metric_name in ['p_99_latency', 'p_95_latency', 'p_90_latency', 
                              'p_75_latency', 'p_25_latency', 'latency_median',
                              'latency_min', 'latency_max']:
                # Convert from microseconds to milliseconds
                value = value / 1000.0

            # For regular dual-loop runs, always include all agent contributions.
            # This expands each window metric by the number of dual responses (quick + actor).
            if is_dual and not dual_timing_values:
                quick_count, actor_count = _count_dual_agent_contributions_by_role(entry)
                contribution_count = quick_count + actor_count
                if contribution_count > 0:
                    metric_values.extend([value] * contribution_count)
                    if quick_count > 0:
                        speculator_metric_values.extend([value] * quick_count)
                    if actor_count > 0:
                        actor_metric_values.extend([value] * actor_count)
                else:
                    metric_values.append(value)
            else:
                metric_values.append(value)

        # For dual-loop runs, always include ALL available agent timing values
        # for metrics that live under tuner timing blocks.
        if is_dual and dual_timing_values:
            metric_values.extend(dual_timing_values)
            speculator_metric_values.extend(dual_timing_spec_values)
            actor_metric_values.extend(dual_timing_actor_values)
    
    # Trim first and last values if requested
    if trim and len(metric_values) > 2:
        metric_values = metric_values[1:-1]
    
    return tuner_type, config_label, metric_values, violation_count, actor_metric_values, speculator_metric_values


def detect_optimization_metric(filepaths: List[str]) -> Optional[str]:
    """
    Detect the optimization metric from the config in history files.
    
    Args:
        filepaths: List of JSON file paths
        
    Returns:
        The optimization metric name if found, None otherwise.
        If multiple files have different metrics, returns the first one found.
    """
    for filepath in filepaths:
        history_data = load_history_file(filepath)
        if not history_data:
            continue
        
        config = history_data.get('config', {})
        optimization_metric = config.get('optimization_metric')
        if optimization_metric:
            return optimization_metric
    
    return None


def detect_benchmark_name(filepaths: List[str]) -> Optional[str]:
    """
    Detect the benchmark name from the config in history files.
    
    Args:
        filepaths: List of JSON file paths
        
    Returns:
        The benchmark name if found, None otherwise.
        If multiple files have different benchmarks, returns the first one found.
    """
    for filepath in filepaths:
        history_data = load_history_file(filepath)
        if not history_data:
            continue
        
        config = history_data.get('config', {})
        benchmark = config.get('benchmark')
        if benchmark:
            return benchmark
    
    return None


def format_benchmark_name(benchmark: str) -> str:
    """
    Format benchmark name for display (e.g., 'sysbench_oltp' -> 'Sysbench OLTP').
    
    Args:
        benchmark: Raw benchmark name
        
    Returns:
        Formatted benchmark name
    """
    # Replace underscores with spaces and title case
    formatted = benchmark.replace('_', ' ').title()
    return formatted


def collect_data_from_files(filepaths: List[str], metric_name: str,
                             start_iter: int = None, end_iter: int = None, 
                             trim: bool = False) -> Tuple[
                                 Dict[str, Dict[str, List[float]]],
                                 Dict[str, Dict[str, int]],
                                 Dict[str, Dict[str, Dict[str, List[float]]]]
                             ]:
    """
    Collect metric data from multiple history files.
    
    Returns:
        (
            {tuner_type: {config_label: [metric_values, ...]}},
            {tuner_type: {config_label: violation_count}},
            {tuner_type: {config_label: {"actor": [...], "speculator": [...]}}}
        )
    """
    all_data = defaultdict(lambda: defaultdict(list))
    all_violations = defaultdict(lambda: defaultdict(int))
    dual_role_values = defaultdict(lambda: defaultdict(lambda: {"actor": [], "speculator": []}))
    
    for filepath in filepaths:
        history_data = load_history_file(filepath)
        if not history_data:
            continue
        
        tuner_type, config_label, metric_values, violations, actor_values, speculator_values = extract_metric_values(
            history_data, metric_name, start_iter, end_iter, trim)
        
        if not tuner_type or not config_label:
            continue
            
        # We need data to plot violin, but even if no data, we might have violations?
        # Violin plot requires data points. If empty, skip data addition.
        if metric_values:
            all_data[tuner_type][config_label].extend(metric_values)
        
        # Always accumulate violations if we processed the file
        all_violations[tuner_type][config_label] += violations

        if actor_values:
            dual_role_values[tuner_type][config_label]["actor"].extend(actor_values)
        if speculator_values:
            dual_role_values[tuner_type][config_label]["speculator"].extend(speculator_values)
    
    return all_data, all_violations, dual_role_values


def merge_trimming_labels(data: Dict[str, Dict[str, List[float]]],
                          violation_counts: Dict[str, Dict[str, int]]):
    """
    Merge fragmented numeric labels (e.g. "4", "5", "7") into the maximum value (e.g. "8")
    to group trimming runs together under the initial parameter count.
    
    Handles suffixes like " (MLOS Trimming)" so "4 (MLOS Trimming)" and "7 (MLOS Trimming)"
    are merged into "8 (MLOS Trimming)" (assuming 8 is the max).
    """
    import re
    
    for tuner_type, configs in data.items():
        # Group labels by suffix
        # suffix -> [(number, original_label), ...]
        groups = defaultdict(list)
        
        for label in list(configs.keys()):
            # Match number at start, optional suffix
            match = re.match(r'^(\d+)(.*)$', label)
            if match:
                val = int(match.group(1))
                suffix = match.group(2)
                groups[suffix].append((val, label))
        
        for suffix, items in groups.items():
            if len(items) <= 1:
                continue
                
            # Find max parameter count in this group
            max_val = max(item[0] for item in items)
            target_label = f"{max_val}{suffix}"
            
            # Merge all others into target_label
            for val, src_label in items:
                if src_label == target_label:
                    continue
                
                # Merge data
                if src_label in configs:
                    if target_label not in configs:
                        configs[target_label] = []
                    configs[target_label].extend(configs[src_label])
                    del configs[src_label]
                
                # Merge violations
                if tuner_type in violation_counts:
                    v_src = violation_counts[tuner_type].get(src_label, 0)
                    if v_src > 0:
                        violation_counts[tuner_type][target_label] += v_src
                        if src_label in violation_counts[tuner_type]:
                            del violation_counts[tuner_type][src_label]

    return data, violation_counts


def plot_violin_comparison(data: Dict[str, Dict[str, List[float]]], 
                           metric_name: str,
                           benchmark_name: str = None,
                           title: str = None,
                           output_file: str = None,
                           ylim: Tuple[float, float] = None,
                           xlim: Tuple[float, float] = None,
                           violation_counts: Dict[str, Dict[str, int]] = None,
                           dual_role_values: Dict[str, Dict[str, Dict[str, List[float]]]] = None,
                           show_counts: bool = False):
    """
    Create boxplot + mean-line comparison plots across tuner types and configurations.
    
    Args:
        data: {tuner_type: {config_label: [metric_values, ...]}}
        metric_name: Name of the metric being plotted (for axis labels)
        benchmark_name: Name of the benchmark (for title, optional)
        title: Override title (if set, used instead of formatted benchmark_name)
        output_file: Output file path (if None, shows plot)
        ylim: Y-axis limits as (min, max)
        xlim: X-axis limits as (min, max)
        violation_counts: Optional {tuner_type: {config_label: count}}
        dual_role_values: Optional {tuner_type: {config_label: {"actor": [...], "speculator": [...]}}}
        show_counts: Whether to show violation and observation counts in x-axis labels
    """
    # Define order of tuner types.
    tuner_order = ['FIXED', 'LLM', 'MLOS', 'BAYESIAN', 'QLEARNING', 'DQN']

    def _config_metric_family_rank(config_label: str) -> int:
        """
        Rank by metric family for display order:
        app metrics -> IPC -> sys metrics -> full metrics.
        """
        label_lower = str(config_label or "").lower()
        if "full metrics" in label_lower:
            return 3
        if "indirect" in label_lower or "sys metrics" in label_lower or "perf-only speculator" in label_lower:
            return 2
        if "ipc" in label_lower:
            return 1
        return 0

    def _background_group_from_label(config_label: str) -> str:
        """
        Classify config label into background color families.
        """
        label = str(config_label or "")
        label_lower = label.lower()

        if '(dual' in label_lower:
            return 'dual'
        if 'flash lite' in label_lower:
            return 'flash_lite'
        if 'flash' in label_lower and 'lite' not in label_lower:
            return 'flash'
        return 'other'
    
    # Prepare data in the correct order
    plot_data = []
    plot_labels = []
    plot_actor_values = []
    plot_speculator_values = []
    plot_bg_groups = []
    tuner_positions = {}  # Track where each tuner starts
    current_pos = 0
    
    for tuner_type in tuner_order:
        if tuner_type not in data:
            continue
        
        tuner_start = current_pos
        configs = data[tuner_type]
        violations_map = violation_counts.get(tuner_type, {}) if violation_counts else {}
        
        # Build effective config order:
        # app metrics first, then IPC, then sys metrics, then full metrics.
        effective_order = sorted(
            [cl for cl in configs.keys() if configs[cl]],
            key=lambda cl: (_config_metric_family_rank(cl), str(cl).lower()),
        )
        
        for config_label in effective_order:
            values = configs[config_label]
            plot_data.append(values)
            role_entry = {}
            if dual_role_values:
                role_entry = dual_role_values.get(tuner_type, {}).get(config_label, {})
            plot_actor_values.append(role_entry.get("actor", []) if role_entry else [])
            plot_speculator_values.append(role_entry.get("speculator", []) if role_entry else [])
            plot_bg_groups.append(_background_group_from_label(config_label))
            n_obs = len(values)
            
            base_label = config_label
            label = base_label
            if show_counts:
                v_count = violations_map.get(config_label, 0) if violations_map else 0
                if v_count > 0:
                    label = f"{base_label}\n{v_count} viol\n{n_obs} obs"
                else:
                    label = f"{base_label}\n{n_obs} obs"
            
            plot_labels.append(label)
            current_pos += 1
        
        if current_pos > tuner_start:
            tuner_positions[tuner_type] = (tuner_start, current_pos - 1)
    
    if not plot_data:
        print("Error: No data to plot")
        return
    
    # Create figure with clean styling
    fig, ax = plt.subplots(figsize=(10, 6))
    has_actor_mean = False
    has_speculator_mean = False

    # Add family background shading for Dual/Flash/Flash Lite columns.
    bg_colors = {
        'dual': '#DDEEFF',
        'flash': '#FFE9D6',
        'flash_lite': '#E6F6E6',
    }
    bg_alpha = 0.38
    for i, bg_group in enumerate(plot_bg_groups):
        if bg_group in bg_colors:
            ax.axvspan(i - 0.5, i + 0.5, color=bg_colors[bg_group], alpha=bg_alpha, zorder=0)
    
    # Draw box plot elements and mean line (no violin bodies).
    for i, values in enumerate(plot_data):
        mean = np.mean(values)
        median = np.median(values)
        q1, q3 = np.percentile(values, [25, 75])
        iqr = q3 - q1
        
        # Calculate whisker limits (1.5 * IQR)
        lower_whisker = max(q1 - 1.5 * iqr, np.min(values))
        upper_whisker = min(q3 + 1.5 * iqr, np.max(values))
        
        # Draw red horizontal line for mean
        line_width = 0.23
        ax.plot([i - line_width, i + line_width], [mean, mean],
               color='red', linewidth=2.5, zorder=5)

        # For dual runs, draw per-agent mean lines.
        actor_vals = plot_actor_values[i]
        spec_vals = plot_speculator_values[i]
        dual_line_width = 0.16
        if actor_vals:
            actor_mean = float(np.mean(actor_vals))
            ax.plot([i - dual_line_width, i + dual_line_width], [actor_mean, actor_mean],
                    color='#1f77b4', linewidth=2.0, linestyle='--', zorder=6)
            has_actor_mean = True
        if spec_vals:
            spec_mean = float(np.mean(spec_vals))
            ax.plot([i - dual_line_width, i + dual_line_width], [spec_mean, spec_mean],
                    color='#ff7f0e', linewidth=2.0, linestyle='-.', zorder=6)
            has_speculator_mean = True
        
        # Draw box for quartiles (Q1 to Q3)
        box_width = 0.13
        box = plt.Rectangle((i - box_width/2, q1), box_width, q3 - q1,
                           fill=False, edgecolor='black', linewidth=1.5, zorder=4)
        ax.add_patch(box)
        
        # Draw median line (black horizontal line inside box)
        ax.plot([i - box_width/2, i + box_width/2], [median, median],
               color='black', linewidth=2, zorder=5)
        
        # Draw whiskers (vertical lines)
        ax.plot([i, i], [q3, upper_whisker], color='black', linewidth=1.5, zorder=4)
        ax.plot([i, i], [q1, lower_whisker], color='black', linewidth=1.5, zorder=4)
        
        # Draw whisker caps (horizontal lines at the ends)
        cap_width = 0.1
        ax.plot([i - cap_width, i + cap_width], [upper_whisker, upper_whisker],
               color='black', linewidth=1.5, zorder=4)
        ax.plot([i - cap_width, i + cap_width], [lower_whisker, lower_whisker],
               color='black', linewidth=1.5, zorder=4)
    
    # Add vertical separator lines between tuner types
    for tuner_type, (start_pos, end_pos) in tuner_positions.items():
        if end_pos < len(plot_data) - 1:
            sep_pos = end_pos + 0.5
            ax.axvline(x=sep_pos, color='black', linewidth=1.5, linestyle='-', 
                      ymin=0, ymax=1, zorder=1)
    
    # Set x-axis labels - configuration labels only
    ax.set_xticks(range(len(plot_labels)))
    ax.set_xticklabels(plot_labels, fontsize=10, rotation=90)
    
    # Dynamic axis label based on metric name
    metric_label = _format_metric_label(metric_name)
    ax.set_ylabel(metric_label, fontsize=11)
    
    # Set axis limits
    if ylim is not None:
        ax.set_ylim(ylim)
    else:
        # Set y-axis range automatically
        all_values = [v for values in plot_data for v in values]
        if all_values:
            y_min = max(0, np.min(all_values) - 5)
            y_max = np.max(all_values) + 5
            ax.set_ylim(y_min, y_max)
    
    if xlim is not None:
        ax.set_xlim(xlim)
    
    # Grid styling - horizontal only, subtle
    ax.grid(True, alpha=0.3, linestyle='-', linewidth=0.5, axis='y')
    ax.set_axisbelow(True)
    
    # Clean white background
    ax.set_facecolor('white')
    fig.patch.set_facecolor('white')
    
    # Spine styling
    for spine in ['top', 'right']:
        ax.spines[spine].set_visible(True)
    for spine in ax.spines.values():
        spine.set_edgecolor('black')
        spine.set_linewidth(0.8)
    
    # Add tuner type labels below the x-axis (outside plot area)
    for tuner_type, (start_pos, end_pos) in tuner_positions.items():
        mid_pos = (start_pos + end_pos) / 2
        # Use data coordinates for x, axes coordinates for y (below the plot)
        ax.text(mid_pos, -0.15, tuner_type, ha='center', va='top', 
               fontsize=11, fontweight='bold', 
               transform=ax.get_xaxis_transform())
    
    # Add title: explicit title overrides benchmark name
    title_text = title if title else (format_benchmark_name(benchmark_name) if benchmark_name else None)
    if title_text:
        ax.set_title(title_text, fontsize=12, fontweight='bold', pad=10)

    # Legend for means and background families.
    legend_handles = []
    bg_group_order = ['dual', 'flash', 'flash_lite']
    bg_group_labels = {
        'dual': 'Dual',
        'flash': 'Flash',
        'flash_lite': 'Flash Lite',
    }
    for bg_group in bg_group_order:
        if bg_group in plot_bg_groups:
            legend_handles.append(
                Patch(facecolor=bg_colors[bg_group], edgecolor='none', alpha=bg_alpha, label=bg_group_labels[bg_group])
            )

    legend_handles.append(Line2D([0], [0], color='red', linewidth=2.5, label='Overall Mean'))
    if has_actor_mean:
        legend_handles.append(Line2D([0], [0], color='#1f77b4', linewidth=2.0, linestyle='--', label='Actor Mean'))
    if has_speculator_mean:
        legend_handles.append(Line2D([0], [0], color='#ff7f0e', linewidth=2.0, linestyle='-.', label='Speculator Mean'))

    if legend_handles:
        ax.legend(handles=legend_handles, loc='upper right', fontsize=9, frameon=True)
    
    # Adjust layout to make room for labels below
    plt.subplots_adjust(bottom=0.18, top=0.95 if benchmark_name else 0.95)
    
    if output_file:
        plt.savefig(output_file, dpi=300, bbox_inches='tight', facecolor='white')
        print(f"Plot saved to {output_file}")
    else:
        plt.show()


def main():
    parser = argparse.ArgumentParser(
        description='Create boxplots comparing metrics across tuner types',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-detect optimization metric from config and plot
  python scripts/plot_violin.py -d results

  # Explicitly specify metric to plot
  python scripts/plot_violin.py -d results -m latency_p95

  # Plot throughput (overrides auto-detected metric)
  python scripts/plot_violin.py -d results -m throughput

  # Plot specific files (auto-detects metric)
  python scripts/plot_violin.py -f results/history1.json results/history2.json

  # Save to file
  python scripts/plot_violin.py -d results -o boxplot_comparison.png
  
  # Trim first and last values from each dataset
  python scripts/plot_violin.py -d results --trim
  
  # Filter by iteration range
  python scripts/plot_violin.py -d results --start 10 --end 100
  
  # Set custom axis limits
  python scripts/plot_violin.py -d results --ylim 20 60 --xlim -0.5 10.5
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
        '-o', '--output',
        type=str,
        help='Output file path (default: show plot)'
    )
    
    parser.add_argument(
        '--trim',
        action='store_true',
        help='Trim first and last values from each dataset'
    )
    
    parser.add_argument(
        '--ylim',
        nargs=2,
        type=float,
        metavar=('MIN', 'MAX'),
        help='Y-axis limits (e.g., --ylim 0 100)'
    )
    
    parser.add_argument(
        '--xlim',
        nargs=2,
        type=float,
        metavar=('MIN', 'MAX'),
        help='X-axis limits (e.g., --xlim -0.5 10.5)'
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
        '-m', '--metric',
        type=str,
        default=None,
        help='Metric name to plot (e.g., latency_p95, throughput, goodput, p_99_latency). If not specified, will auto-detect from optimization_metric in config.'
    )
    
    parser.add_argument(
        '-t', '--title',
        type=str,
        default=None,
        help='Figure title (e.g., "Sysbench CPU (P99 Latency)"). If not set, uses formatted benchmark name from config.'
    )
    
    parser.add_argument(
        '--counts',
        action='store_true',
        help='Show violation and observation counts in x-axis labels'
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
            os.path.join(args.directory, 'dual_loop_*.json'),
            os.path.join(args.directory, '*', 'dual_loop_*.json'),
        ):
            filepaths.extend(glob.glob(pattern))
    
    if not filepaths:
        print("Error: No history files found. Use -d or -f to specify files.")
        return
    
    # Remove duplicates
    filepaths = list(set(filepaths))
    
    print(f"Found {len(filepaths)} history file(s)")
    
    # Auto-detect optimization metric if not specified
    metric_name = args.metric
    if metric_name is None:
        detected_metric = detect_optimization_metric(filepaths)
        if detected_metric:
            metric_name = detected_metric
            print(f"Auto-detected optimization metric: {metric_name}")
        else:
            # Fallback to p_99_latency if not found
            metric_name = 'p_99_latency'
            print(f"Warning: Could not detect optimization_metric from config. Using default: {metric_name}")
    else:
        print(f"Using specified metric: {metric_name}")
    
    # Auto-detect benchmark name
    benchmark_name = detect_benchmark_name(filepaths)
    if benchmark_name:
        print(f"Auto-detected benchmark: {benchmark_name}")
    
    # Collect data with filtering options
    data, violations, dual_role_values = collect_data_from_files(filepaths, 
                                   metric_name=metric_name,
                                   start_iter=args.start, 
                                   end_iter=args.end, 
                                   trim=args.trim)
    
    if not data:
        print(f"Error: No valid {metric_name} data found in history files")
        return
    
    # Merge trimming labels (e.g. 4, 5, 7 -> 8)
    data, violations = merge_trimming_labels(data, violations)
    
    print(f"Found {metric_name} data for tuner types: {', '.join(sorted(data.keys()))}")
    for tuner_type, configs in data.items():
        print(f"  {tuner_type}: {', '.join(sorted(configs.keys()))}")
    
    # Prepare axis limits
    ylim = tuple(args.ylim) if args.ylim else None
    xlim = tuple(args.xlim) if args.xlim else None
    
    # Plot (use --title to override benchmark name in title)
    plot_violin_comparison(data, metric_name=metric_name, benchmark_name=benchmark_name,
                          title=args.title, output_file=args.output, ylim=ylim, xlim=xlim,
                          violation_counts=violations, dual_role_values=dual_role_values, show_counts=args.counts)


if __name__ == '__main__':
    main()
