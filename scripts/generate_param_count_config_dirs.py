#!/usr/bin/env python3
"""
Generate parameter-count config families by cloning config/full_param.

For each generated directory, this script only updates:
- parameter_ranges
- parameters_to_tune

All other fields (including fixed_parameters) are preserved from full_param.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from barebones_optimizer.parameter_manager import PARAMETER_METADATA


BASE_EIGHT_REQUIRED: List[str] = [
    "min_granularity_ns",
    "latency_ns",
    "cstate_max",
    "napi_busy_poll",
    "wakeup_granularity_ns",
    "migration_cost_ns",
    "max_perf_pct",
    "min_perf_pct",
]


# Priority order used when extending to larger spaces (16/20/32/etc.).
EXTENSION_PRIORITY: List[str] = [
    "epp",
    "turbo",
    "pmqos",
    "busy_read",
    "netdev_budget",
    "netdev_budget_usecs",
    "vm_swappiness",
    "vm_dirty_ratio",
    "vm_dirty_background_ratio",
    "zone_reclaim_mode",
    "sched_autogroup_enabled",
    "sched_cfs_bandwidth_slice_us",
    "somaxconn",
    "netdev_max_backlog",
    "rmem_default",
    "wmem_default",
    "rmem_max",
    "wmem_max",
    "tcp_fin_timeout",
    "tcp_tw_reuse",
    "tcp_mtu_probing",
    "tcp_timestamps",
    "tcp_sack",
    "tcp_window_scaling",
    "tcp_fastopen",
    "tcp_congestion_control",
    "scaling_governor",
    "scaling_min_freq",
    "scaling_max_freq",
    "numa_balancing",
    "busy_poll",
]


# Fallback ranges for knobs not present in full_param configs.
FALLBACK_PARAMETER_RANGES: Dict[str, Any] = {
    "scaling_governor": ["performance", "powersave", "ondemand", "conservative", "userspace", "schedutil"],
    "scaling_min_freq": [0, 5000000],
    "scaling_max_freq": [0, 5000000],
    "epp": ["default", "performance", "balance_performance", "balance_power", "power"],
    "turbo": [True, False],
    "pmqos": [0, 5000],
    "busy_poll": [0, 1000],
    "busy_read": [0, 1000],
    "netdev_budget": [64, 5000],
    "netdev_budget_usecs": [0, 20000],
    "vm_swappiness": [0, 100],
    "vm_dirty_ratio": [1, 80],
    "vm_dirty_background_ratio": [1, 40],
    "vm_dirty_expire_centisecs": [100, 60000],
    "vm_dirty_writeback_centisecs": [100, 60000],
    "zone_reclaim_mode": [0, 7],
    "numa_balancing": [True, False],
    "sched_autogroup_enabled": [True, False],
    "sched_cfs_bandwidth_slice_us": [1000, 50000],
    "somaxconn": [128, 65535],
    "netdev_max_backlog": [1000, 100000],
    "rmem_default": [212992, 33554432],
    "wmem_default": [212992, 33554432],
    "rmem_max": [212992, 33554432],
    "wmem_max": [212992, 33554432],
    "tcp_fin_timeout": [10, 120],
    "tcp_tw_reuse": [0, 1, 2],
    "tcp_mtu_probing": [0, 1, 2],
    "tcp_timestamps": [0, 1],
    "tcp_sack": [0, 1],
    "tcp_window_scaling": [0, 1],
    "tcp_fastopen": [0, 1024],
    "tcp_congestion_control": ["reno", "cubic", "bbr"],
}


PROFILE_ALIASES: Dict[str, str] = {
    "single": "1",
    "1": "1",
    "one": "1",
    "two": "2",
    "2": "2",
    "four": "4",
    "4": "4",
    "8": "8",
    "16": "16",
    "20": "20",
    "32": "32",
    "all": "all",
}


LEGACY_FIXED_SOURCE_BY_DEST: Dict[str, str] = {
    "1_param": "single_param",
    "2_param": "two_param",
    "single_param": "single_param",
    "two_param": "two_param",
}

LEGACY_CLONE_SOURCE_BY_DEST: Dict[str, str] = {
    "1_param": "single_param",
    "2_param": "two_param",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Clone config/full_param into multiple param-count directories while changing only "
            "parameter_ranges and parameters_to_tune."
        )
    )
    parser.add_argument(
        "--source-root",
        default="config/full_param",
        help="Source full-param config root (default: config/full_param).",
    )
    parser.add_argument(
        "--output-parent",
        default="config",
        help="Parent directory for generated directories (default: config).",
    )
    parser.add_argument(
        "--profiles",
        default="1,2,4,8,16,32",
        help=(
            "Comma-separated profiles to generate. Supports: 1,2,4,8,16,20,32,all "
            "(aliases: single->1, two->2). Default: 1,2,4,8,16,32"
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite destination directories if they already exist.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan only; do not write files.",
    )
    return parser.parse_args()


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


def list_json_files(root: Path) -> List[Path]:
    return sorted(p for p in root.rglob("*.json") if p.is_file())


def collect_fixed_parameters_map(root: Path) -> Dict[str, Any]:
    """
    Build relpath -> fixed_parameters map for JSON files that define fixed_parameters.
    """
    out: Dict[str, Any] = {}
    if not root.exists() or not root.is_dir():
        return out

    for path in list_json_files(root):
        payload = load_json(path)
        if "fixed_parameters" not in payload:
            continue
        rel = str(path.relative_to(root))
        out[rel] = copy.deepcopy(payload.get("fixed_parameters"))
    return out


def merge_fixed_maps_prefer_non_empty(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge relpath->fixed_parameters maps, avoiding replacement of non-empty values by empty ones.
    """
    out = dict(base)
    for rel, value in incoming.items():
        if rel not in out:
            out[rel] = copy.deepcopy(value)
            continue

        existing = out[rel]
        existing_non_empty = isinstance(existing, dict) and len(existing) > 0
        incoming_non_empty = isinstance(value, dict) and len(value) > 0

        if existing_non_empty and not incoming_non_empty:
            continue
        out[rel] = copy.deepcopy(value)
    return out


def resolve_preserved_fixed_for_relpath(
    relpath: str,
    preserved_fixed_map: Optional[Dict[str, Any]],
) -> Optional[Any]:
    """
    Resolve preserved fixed_parameters with scenario-name fallback aliases.
    """
    if not preserved_fixed_map:
        return None

    if relpath in preserved_fixed_map:
        return copy.deepcopy(preserved_fixed_map[relpath])

    parts = relpath.split("/")
    if len(parts) < 2:
        return None
    scenario = parts[0]
    rest = "/".join(parts[1:])

    scenario_aliases = [scenario]
    if scenario.endswith("_p99"):
        scenario_aliases.append(scenario[: -len("_p99")] + "_hi_p99")
    if scenario.endswith("_tput"):
        scenario_aliases.append(scenario[: -len("_tput")] + "_hi_tput")

    for alias in scenario_aliases:
        candidate = f"{alias}/{rest}"
        if candidate in preserved_fixed_map:
            return copy.deepcopy(preserved_fixed_map[candidate])

    return None


def discover_full_param_order_and_canonical_ranges(
    json_files: Iterable[Path],
) -> Tuple[List[str], Dict[str, Any]]:
    first_seen_order: List[str] = []
    seen = set()
    value_counter: Dict[str, Counter] = defaultdict(Counter)

    for config_file in json_files:
        payload = load_json(config_file)
        ranges = payload.get("parameter_ranges")
        if not isinstance(ranges, dict):
            continue
        for key, value in ranges.items():
            if key not in seen:
                seen.add(key)
                first_seen_order.append(key)
            value_counter[key][json.dumps(value, sort_keys=True)] += 1

    canonical: Dict[str, Any] = {}
    for key in first_seen_order:
        if not value_counter[key]:
            continue
        canonical[key] = json.loads(value_counter[key].most_common(1)[0][0])

    return first_seen_order, canonical


def resolve_parameter_range(
    param_name: str,
    source_ranges: Dict[str, Any],
    canonical_full_ranges: Dict[str, Any],
) -> Any:
    if param_name in source_ranges:
        return copy.deepcopy(source_ranges[param_name])
    if param_name in canonical_full_ranges:
        return copy.deepcopy(canonical_full_ranges[param_name])
    if param_name in FALLBACK_PARAMETER_RANGES:
        return copy.deepcopy(FALLBACK_PARAMETER_RANGES[param_name])

    meta = PARAMETER_METADATA.get(param_name, {})
    valid_values = meta.get("valid_values")
    if isinstance(valid_values, list) and valid_values:
        return copy.deepcopy(valid_values)

    return [0, 100]


def clone_tree(source_root: Path, destination_root: Path, overwrite: bool, dry_run: bool) -> None:
    if destination_root.exists():
        if not overwrite:
            raise FileExistsError(
                f"Destination exists: {destination_root}. Use --overwrite to replace it."
            )
        if not dry_run:
            shutil.rmtree(destination_root)
    if not dry_run:
        shutil.copytree(source_root, destination_root)


def apply_variant(
    destination_root: Path,
    desired_param_names: Sequence[str],
    canonical_full_ranges: Dict[str, Any],
    preserved_fixed_map: Optional[Dict[str, Any]],
    enforce_nonfixed_latency_fixed_1000: bool,
    enforce_two_param_lo_pwr_pair: bool,
    enforce_four_param_lo_pwr_swap_wakeup: bool,
    dry_run: bool,
) -> Tuple[int, int]:
    json_files = list_json_files(destination_root)
    updated = 0
    skipped = 0

    for config_file in json_files:
        rel = str(config_file.relative_to(destination_root))
        payload = load_json(config_file)
        source_ranges = payload.get("parameter_ranges")
        if not isinstance(source_ranges, dict):
            skipped += 1
            continue

        effective_param_names = list(desired_param_names)
        if enforce_two_param_lo_pwr_pair and "lo_pwr_wrt_p99" in rel:
            effective_param_names = ["cstate_max", "max_perf_pct"]
        if enforce_four_param_lo_pwr_swap_wakeup and "lo_pwr_wrt_p99" in rel:
            effective_param_names = [
                "min_granularity_ns",
                "latency_ns",
                "cstate_max",
                "max_perf_pct",
            ]

        new_ranges: Dict[str, Any] = {}
        for name in effective_param_names:
            new_ranges[name] = resolve_parameter_range(name, source_ranges, canonical_full_ranges)

        payload["parameter_ranges"] = new_ranges
        payload["parameters_to_tune"] = list(effective_param_names)
        preserved_fixed = resolve_preserved_fixed_for_relpath(rel, preserved_fixed_map)
        if preserved_fixed is not None:
            payload["fixed_parameters"] = preserved_fixed
        if enforce_nonfixed_latency_fixed_1000:
            tuner_type = str(payload.get("tuner_type", "")).strip().lower()
            if tuner_type != "fixed":
                if "lo_pwr_wrt_p99" in rel:
                    payload["fixed_parameters"] = {"cstate_max": "POLL"}
                else:
                    fixed = payload.get("fixed_parameters")
                    if not isinstance(fixed, dict):
                        fixed = {}
                    fixed["latency_ns"] = 1000
                    payload["fixed_parameters"] = fixed

        if not dry_run:
            write_json(config_file, payload)
        updated += 1

    return updated, skipped


def build_count_params(
    target_count: int,
    base_eight: Sequence[str],
    manager_order: Sequence[str],
) -> List[str]:
    if target_count <= 0:
        raise ValueError(f"Invalid target count: {target_count}")
    if target_count <= len(base_eight):
        return list(base_eight[:target_count])

    selected = list(base_eight)
    selected_set = set(selected)

    for name in EXTENSION_PRIORITY:
        if len(selected) >= target_count:
            break
        if name in selected_set or name not in manager_order:
            continue
        selected.append(name)
        selected_set.add(name)

    for name in manager_order:
        if len(selected) >= target_count:
            break
        if name in selected_set:
            continue
        selected.append(name)
        selected_set.add(name)

    if len(selected) < target_count:
        raise ValueError(
            f"Unable to build {target_count} parameters, only resolved {len(selected)}."
        )
    return selected


def parse_profiles(raw: str) -> List[str]:
    out: List[str] = []
    for item in raw.split(","):
        token = item.strip().lower()
        if not token:
            continue
        out.append(PROFILE_ALIASES.get(token, token))
    if not out:
        raise ValueError("No profiles parsed from --profiles")
    return out


def build_variant_plan(
    profiles: Sequence[str],
    base_eight: Sequence[str],
    manager_order: Sequence[str],
) -> List[Tuple[str, List[str]]]:
    manager_count = len(manager_order)
    plan: List[Tuple[str, List[str]]] = []
    seen_dirs = set()

    for profile in profiles:
        if profile == "1":
            dir_name = "1_param"
            params = ["min_granularity_ns"]
        elif profile == "2":
            dir_name = "2_param"
            params = ["min_granularity_ns", "latency_ns"]
        elif profile == "4":
            dir_name = "4_param"
            params = ["min_granularity_ns", "latency_ns", "wakeup_granularity_ns", "cstate_max"]
        elif profile == "all":
            dir_name = f"{manager_count}_param"
            params = list(manager_order)
        else:
            try:
                n = int(profile)
            except ValueError as exc:
                raise ValueError(
                    f"Unsupported profile '{profile}'. Use single,two,4,8,16,20,32,all or integers."
                ) from exc
            dir_name = f"{n}_param"
            params = build_count_params(n, base_eight, manager_order)

        for p in params:
            if p not in manager_order:
                raise ValueError(f"Profile '{profile}' includes unknown parameter '{p}'.")

        if dir_name in seen_dirs:
            continue
        seen_dirs.add(dir_name)
        plan.append((dir_name, params))

    return plan


def main() -> int:
    args = parse_args()

    source_root = (REPO_ROOT / args.source_root).resolve()
    output_parent = (REPO_ROOT / args.output_parent).resolve()

    if not source_root.exists() or not source_root.is_dir():
        raise SystemExit(f"Error: source root missing: {source_root}")

    source_json_files = list_json_files(source_root)
    if not source_json_files:
        raise SystemExit(f"Error: no JSON files in source root: {source_root}")

    full_param_order, canonical_full_ranges = discover_full_param_order_and_canonical_ranges(source_json_files)
    manager_order = list(PARAMETER_METADATA.keys())

    for name in BASE_EIGHT_REQUIRED:
        if name not in manager_order:
            raise SystemExit(f"Error: required base parameter not in PARAMETER_METADATA: {name}")

    # Keep the full-param ordering where possible but enforce required base set membership.
    base_eight = [name for name in full_param_order if name in BASE_EIGHT_REQUIRED]
    for name in BASE_EIGHT_REQUIRED:
        if name not in base_eight:
            base_eight.append(name)
    base_eight = base_eight[:8]

    profiles = parse_profiles(args.profiles)
    variants = build_variant_plan(profiles, base_eight, manager_order)

    print(f"Source root: {source_root}")
    print(f"Source JSON configs: {len(source_json_files)}")
    print(f"Discovered full_param knobs: {len(full_param_order)} ({', '.join(full_param_order)})")
    print(f"ParameterManager knobs: {len(manager_order)}")
    print(f"Profiles requested: {', '.join(profiles)}")
    print("")

    for dir_name, params in variants:
        dest = output_parent / dir_name
        preserved_fixed: Dict[str, Any] = {}
        variant_source_root = source_root

        legacy_name = LEGACY_FIXED_SOURCE_BY_DEST.get(dir_name)
        if legacy_name:
            legacy_root = output_parent / legacy_name
            preserved_fixed = merge_fixed_maps_prefer_non_empty(
                preserved_fixed,
                collect_fixed_parameters_map(legacy_root),
            )

        # Existing destination gets final precedence.
            preserved_fixed = merge_fixed_maps_prefer_non_empty(
                preserved_fixed,
                collect_fixed_parameters_map(dest),
            )

        clone_legacy_name = LEGACY_CLONE_SOURCE_BY_DEST.get(dir_name)
        if clone_legacy_name:
            candidate_source = output_parent / clone_legacy_name
            if candidate_source.exists() and candidate_source.is_dir():
                variant_source_root = candidate_source

        print(f"[{dir_name}]")
        print(f"  Destination: {dest}")
        print(f"  Source: {variant_source_root}")
        print(f"  Knob count: {len(params)}")
        print(f"  Knobs: {', '.join(params)}")
        if preserved_fixed:
            print(f"  Preserving fixed_parameters from {len(preserved_fixed)} matching config files")

        clone_tree(variant_source_root, dest, overwrite=args.overwrite, dry_run=args.dry_run)
        updated, skipped = apply_variant(
            destination_root=(variant_source_root if args.dry_run else dest),
            desired_param_names=params,
            canonical_full_ranges=canonical_full_ranges,
            preserved_fixed_map=preserved_fixed,
            enforce_nonfixed_latency_fixed_1000=(dir_name in {"1_param", "single_param"}),
            enforce_two_param_lo_pwr_pair=(dir_name in {"2_param", "two_param"}),
            enforce_four_param_lo_pwr_swap_wakeup=(dir_name == "4_param"),
            dry_run=args.dry_run,
        )
        if args.dry_run:
            print(f"  Dry-run: would update {updated} files, skip {skipped} without parameter_ranges")
        else:
            print(f"  Updated {updated} files, skipped {skipped} without parameter_ranges")
        print("")

    if args.dry_run:
        print("Dry-run complete. No files written.")
    else:
        print("Done.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
