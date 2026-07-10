#!/usr/bin/env python3
"""
Generate a new config directory from a base config directory by replacing
parameter_ranges, parameters_to_tune, and clearing fixed_parameters.

Any parameter that appears in both the new parameters_to_tune AND the base
config's fixed_parameters is removed from fixed_parameters (since it's now
tunable, it shouldn't be fixed).

Usage:
    # Generate config/full_param from config/single_param with the full parameter set
    python scripts/generate_full_param_from_single.py \\
        --base config/single_param \\
        --output config/full_param \\
        --params params_full.json

    # Use inline parameter specification
    python scripts/generate_full_param_from_single.py \\
        --base config/single_param \\
        --output config/two_param \\
        --param "min_granularity_ns=[100000,100000000]" \\
        --param "latency_ns=[100000,100000000]"

    # Use a JSON file for parameter ranges
    # The JSON file should have the structure:
    #   {
    #     "parameter_ranges": { ... },
    #     "parameters_to_tune": [ ... ]
    #   }
    # If parameters_to_tune is omitted, all keys from parameter_ranges are used.
"""

import json
import os
import sys
import argparse
import fnmatch


def parse_inline_param(spec: str):
    """Parse an inline param spec like 'name=[min,max]' or 'name=["A","B","C"]'.

    Returns (name, range_value) where range_value is a Python list.
    """
    if "=" not in spec:
        raise ValueError(f"Invalid param spec (missing '='): {spec}")
    name, val_str = spec.split("=", 1)
    name = name.strip()
    try:
        value = json.loads(val_str)
    except json.JSONDecodeError:
        raise ValueError(f"Cannot parse value for '{name}': {val_str}")
    if not isinstance(value, list):
        raise ValueError(f"Value for '{name}' must be a JSON array, got: {type(value).__name__}")
    return name, value


def transform_config(data: dict, new_ranges: dict, new_params_to_tune: list) -> dict:
    """Apply parameter overrides to a config dict (mutates in place).

    - Sets parameter_ranges and parameters_to_tune to the provided values.
    - Removes any key from fixed_parameters that is now in parameters_to_tune.
    """
    # Remove tunable params from fixed_parameters
    old_fixed = data.get("fixed_parameters", {})
    cleaned_fixed = {
        k: v for k, v in old_fixed.items()
        if k not in new_params_to_tune
    }
    data["parameter_ranges"] = new_ranges
    data["parameters_to_tune"] = new_params_to_tune
    data["fixed_parameters"] = cleaned_fixed

    return data


def main():
    parser = argparse.ArgumentParser(
        description="Generate a config directory from a base config directory "
                    "with new parameter ranges.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # From a JSON parameter file
  python scripts/generate_full_param_from_single.py \\
      --base config/single_param \\
      --output config/full_param \\
      --params params.json

  # Inline parameters
  python scripts/generate_full_param_from_single.py \\
      --base config/single_param \\
      --output config/two_param \\
      --param "min_granularity_ns=[100000,100000000]" \\
      --param "latency_ns=[100000,100000000]"
        """,
    )
    parser.add_argument(
        "--base", required=True,
        help="Base config directory to read from (e.g. config/single_param)",
    )
    parser.add_argument(
        "--output", required=True,
        help="Output config directory to write to (e.g. config/full_param)",
    )

    param_group = parser.add_mutually_exclusive_group(required=True)
    param_group.add_argument(
        "--params",
        help='JSON file with {"parameter_ranges": {...}, "parameters_to_tune": [...]}. '
             "If parameters_to_tune is omitted, all keys from parameter_ranges are used.",
    )
    param_group.add_argument(
        "--param", action="append", dest="inline_params",
        help='Inline param spec: NAME=[min,max] or NAME=["A","B"]. Repeat for each param.',
    )

    parser.add_argument(
        "--include", action="append", dest="include_patterns",
        help="Only process scenario subdirectories matching this glob pattern "
             "(e.g. '*lo_pwr_wrt*'). Can be repeated. If omitted, all are included.",
    )
    parser.add_argument(
        "--exclude", action="append", dest="exclude_patterns",
        help="Skip scenario subdirectories matching this glob pattern "
             "(e.g. '*lo_pwr_wrt*'). Can be repeated.",
    )

    args = parser.parse_args()

    # ── Resolve parameter ranges ─────────────────────────────────────────
    if args.params:
        with open(args.params, "r") as f:
            pdata = json.load(f)
        new_ranges = pdata.get("parameter_ranges", pdata)
        # If the file is just a flat dict of ranges (no wrapper key), use it directly
        if "parameter_ranges" in pdata:
            new_ranges = pdata["parameter_ranges"]
        new_params_to_tune = pdata.get(
            "parameters_to_tune", list(new_ranges.keys())
        )
    else:
        new_ranges = {}
        for spec in args.inline_params:
            name, value = parse_inline_param(spec)
            new_ranges[name] = value
        new_params_to_tune = list(new_ranges.keys())

    print(f"Parameters to tune ({len(new_params_to_tune)}):")
    for p in new_params_to_tune:
        r = new_ranges.get(p, "???")
        print(f"  {p}: {r}")
    print()

    # ── Resolve directories ──────────────────────────────────────────────
    # Support both absolute and relative paths (relative to repo root)
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def resolve_dir(d):
        if os.path.isabs(d):
            return d
        return os.path.join(repo_root, d)

    src = resolve_dir(args.base)
    dst = resolve_dir(args.output)

    # Derive base and output dir basenames for results_dir substitution
    base_name = os.path.basename(os.path.normpath(args.base))   # e.g. "single_param"
    output_name = os.path.basename(os.path.normpath(args.output))  # e.g. "full_param"

    if not os.path.isdir(src):
        print(f"Error: base directory not found: {src}")
        sys.exit(1)

    # ── Process files ────────────────────────────────────────────────────
    created = 0
    skipped_fixed = 0
    skipped_filter = 0
    errors = 0

    include_pats = args.include_patterns or []
    exclude_pats = args.exclude_patterns or []

    for dirpath, _dirnames, filenames in sorted(os.walk(src)):
        for fname in sorted(filenames):
            if not fname.endswith(".json"):
                continue

            src_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(src_path, src)

            # The scenario subdir is the first component of rel_path
            # e.g. "sphinx_lo_pwr_wrt_p99/tailbench_sphinx_config_fixed.json"
            scenario_dir = rel_path.split(os.sep)[0] if os.sep in rel_path else ""

            # Apply include filter: if patterns given, at least one must match
            if include_pats and not any(
                fnmatch.fnmatch(scenario_dir, pat) for pat in include_pats
            ):
                skipped_filter += 1
                continue

            # Apply exclude filter: skip if any pattern matches
            if exclude_pats and any(
                fnmatch.fnmatch(scenario_dir, pat) for pat in exclude_pats
            ):
                skipped_filter += 1
                continue
            dst_path = os.path.join(dst, rel_path)

            try:
                with open(src_path, "r") as f:
                    data = json.load(f)

                # Track which fixed params get dropped
                old_fixed = set(data.get("fixed_parameters", {}).keys())
                dropped = old_fixed & set(new_params_to_tune)
                if dropped:
                    skipped_fixed += len(dropped)

                data = transform_config(data, new_ranges, new_params_to_tune)

                # Update results_dir if it references the base dir name
                results_dir = data.get("results_dir", "")
                if results_dir and base_name != output_name:
                    data["results_dir"] = results_dir.replace(base_name, output_name)

                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                with open(dst_path, "w") as f:
                    json.dump(data, f, indent=4)
                    f.write("\n")

                created += 1

            except Exception as e:
                print(f"  ERROR: {rel_path}: {e}")
                errors += 1

    print(f"Done. {created} files written to {args.output}/ ({errors} errors)")
    if skipped_filter:
        print(f"  ({skipped_filter} files skipped by --include/--exclude filters)")
    if skipped_fixed:
        print(f"  ({skipped_fixed} fixed_parameters entries removed because "
              f"they now appear in parameters_to_tune)")


if __name__ == "__main__":
    main()
