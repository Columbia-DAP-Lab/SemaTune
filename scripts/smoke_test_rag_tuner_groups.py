#!/usr/bin/env python3
"""Prompt-level smoke test for selected RAG tuner groups."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from barebones_optimizer.config import SimpleConfig  # noqa: E402
from barebones_optimizer.tuners.llm import LLMTuner  # noqa: E402


SELECTED_WORKLOADS = [
    "silo_hi_p99",
    "tpcc_hi_p99",
    "sysbench_oltp_rw_hi_p99",
    "wikipedia_p99",
]
PRIOR_BLOCK_MARKER = "SUMMARY OF PREVIOUS TUNING SESSION OF A SIMILAR WORKLOAD:"
INDIRECT_SANITIZER_PREFIX = "Hidden primary-metric values from the prior run have been redacted."


@dataclass(frozen=True)
class GroupSpec:
    key: str
    tuner_suffix: str
    expected_results_leaf: str
    prior_expected: bool
    is_fixed: bool = False
    requires_indirect_sanitizer: bool = False


GROUP_SPECS: Sequence[GroupSpec] = (
    GroupSpec("fixed", "fixed", "fixed", prior_expected=False, is_fixed=True),
    GroupSpec(
        "app_nomem",
        "llm_dual_app_metrics_final_actor",
        "llm_dual_app_metrics_final_actor",
        prior_expected=False,
    ),
    GroupSpec(
        "indirect_nomem",
        "llm_dual_indirect_all_mode3_final_actor",
        "llm_dual_indirect_all_mode3_final_actor",
        prior_expected=False,
    ),
    GroupSpec(
        "sys_top1_raw",
        "llm_dual_indirect_all_mode3_memory_top_1_raw_to_summary_final_actor",
        "rag_llm_dual_indirect_all_mode3_memory_top_1_raw_to_summary_final_actor",
        prior_expected=True,
        requires_indirect_sanitizer=True,
    ),
    GroupSpec(
        "app_top1_raw",
        "llm_dual_app_metrics_memory_top_1_raw_to_summary_final_actor",
        "rag_llm_dual_app_metrics_memory_top_1_raw_to_summary_final_actor",
        prior_expected=True,
    ),
    GroupSpec(
        "sys_top3_raw",
        "llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_final_actor",
        "rag_llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_final_actor",
        prior_expected=True,
        requires_indirect_sanitizer=True,
    ),
    GroupSpec(
        "app_top3_raw",
        "llm_dual_app_metrics_memory_top_3_raw_to_summary_final_actor",
        "rag_llm_dual_app_metrics_memory_top_3_raw_to_summary_final_actor",
        prior_expected=True,
    ),
    GroupSpec(
        "sys_last1_raw",
        "llm_dual_indirect_all_mode3_memory_last_1_raw_to_summary_final_actor",
        "rag_llm_dual_indirect_all_mode3_memory_last_1_raw_to_summary_final_actor",
        prior_expected=True,
        requires_indirect_sanitizer=True,
    ),
    GroupSpec(
        "app_last1_raw",
        "llm_dual_app_metrics_memory_last_1_raw_to_summary_final_actor",
        "rag_llm_dual_app_metrics_memory_last_1_raw_to_summary_final_actor",
        prior_expected=True,
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage-dir",
        default=str(REPO_ROOT / "generated" / "rag_prior_configs" / "batch_stage"),
        help="Full stage dir created by generate_rag_prior_configs.py",
    )
    parser.add_argument(
        "--output-root",
        default=str(REPO_ROOT / "generated" / "rag_prior_smoke"),
        help="Root for smoke-test artifacts and reduced stage dir.",
    )
    parser.add_argument(
        "--num-runs",
        type=int,
        default=5,
        help="Run count to bake into generated command artifacts.",
    )
    return parser.parse_args()


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def collect_target_configs(stage_dir: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for workload in SELECTED_WORKLOADS:
        workload_dir = stage_dir / workload
        if not workload_dir.is_dir():
            raise FileNotFoundError(f"Missing workload dir in stage: {workload_dir}")
        for group in GROUP_SPECS:
            pattern = f"*{group.tuner_suffix}.json"
            matches = sorted(workload_dir.glob(pattern))
            if len(matches) != 1:
                raise ValueError(
                    f"Expected exactly one config for {workload} / {group.key} using pattern {pattern}, found {len(matches)}"
                )
            rows.append(
                {
                    "workload": workload,
                    "group": group,
                    "config_path": matches[0],
                }
            )
    if len(rows) != len(SELECTED_WORKLOADS) * len(GROUP_SPECS):
        raise ValueError(f"Expected 36 configs, found {len(rows)}")
    return rows


def _results_dir_leaf(config_payload: Mapping[str, Any]) -> str:
    results_dir = str(config_payload.get("results_dir") or "").strip().rstrip("/")
    return results_dir.split("/")[-1] if results_dir else ""


def _normalize_fixed_schedule(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(payload)
    normalized["max_iterations"] = 50
    normalized["post_tuning_windows"] = 0
    return normalized


def _copy_subset_config(source: Path, destination: Path) -> None:
    payload = _load_json(source)
    if str(payload.get("tuner_type") or "").strip() == "fixed":
        _write_json(destination, _normalize_fixed_schedule(payload))
        return
    shutil.copy2(source, destination)


def _make_failure_row(base: Mapping[str, Any], message: str, *, extra: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    row = dict(base)
    row.update(
        {
            "passed": False,
            "failure_reason": message,
        }
    )
    if extra:
        row.update(extra)
    return row


def validate_config_entry(entry: Mapping[str, Any]) -> Dict[str, Any]:
    workload = str(entry["workload"])
    group: GroupSpec = entry["group"]
    config_path: Path = entry["config_path"]
    base_row: Dict[str, Any] = {
        "workload": workload,
        "tuner_group": group.key,
        "tuner_suffix": group.tuner_suffix,
        "config_path": str(config_path),
        "prior_expected": group.prior_expected,
        "results_dir_leaf_expected": group.expected_results_leaf,
        "prior_found_in_reasoning_prompt": False,
        "prior_found_in_quick_prompt": False,
        "prior_text_found_in_reasoning_prompt": False,
        "prior_text_found_in_quick_prompt": False,
        "indirect_sanitizer_found": False,
        "fixed_tuner": group.is_fixed,
    }

    try:
        raw_payload = _load_json(config_path)
        config = SimpleConfig.load(str(config_path))
        config.validate()
    except Exception as exc:
        return _make_failure_row(base_row, f"config load/validate failed: {exc}")

    actual_leaf = _results_dir_leaf(raw_payload)
    base_row["results_dir_leaf_actual"] = actual_leaf
    base_row["prior_field_nonempty"] = bool(str(raw_payload.get("previous_run_gist") or "").strip())
    base_row["explicit_dual_loop"] = bool(getattr(config, "_explicit_dual_loop", False))
    if workload not in SELECTED_WORKLOADS:
        return _make_failure_row(base_row, f"unexpected workload {workload}")
    if not config_path.name.endswith(f"{group.tuner_suffix}.json"):
        return _make_failure_row(
            base_row,
            f"filename does not end with expected suffix {group.tuner_suffix}.json",
        )
    if actual_leaf != group.expected_results_leaf:
        return _make_failure_row(
            base_row,
            f"results_dir leaf mismatch: expected {group.expected_results_leaf}, found {actual_leaf}",
        )

    gist_text = str(raw_payload.get("previous_run_gist") or "").strip()
    if group.is_fixed:
        if config.tuner_type != "fixed":
            return _make_failure_row(base_row, f"fixed config has tuner_type={config.tuner_type}")
        if gist_text:
            return _make_failure_row(base_row, "fixed config unexpectedly has previous_run_gist")
        if int(raw_payload.get("max_iterations", -1)) != 50:
            return _make_failure_row(base_row, f"fixed config max_iterations={raw_payload.get('max_iterations')} (expected 50)")
        if int(raw_payload.get("post_tuning_windows", -1)) != 0:
            return _make_failure_row(base_row, f"fixed config post_tuning_windows={raw_payload.get('post_tuning_windows')} (expected 0)")
        base_row.update({"passed": True, "failure_reason": ""})
        return base_row

    if not getattr(config, "_explicit_dual_loop", False):
        return _make_failure_row(base_row, "config is not explicit dual-loop")
    if group.prior_expected and not gist_text:
        return _make_failure_row(base_row, "memory config missing previous_run_gist")
    if not group.prior_expected and gist_text:
        return _make_failure_row(base_row, "no-memory config unexpectedly has previous_run_gist")

    try:
        reasoning_tuner = LLMTuner(config, agent_type="reasoning")
        quick_tuner = LLMTuner(config, agent_type="quick")
        reasoning_prompt = reasoning_tuner._create_base_prompt()
        quick_prompt = quick_tuner._create_base_prompt()
    except Exception as exc:
        return _make_failure_row(base_row, f"tuner/prompt construction failed: {exc}")

    prior_in_reasoning = PRIOR_BLOCK_MARKER in reasoning_prompt
    prior_in_quick = PRIOR_BLOCK_MARKER in quick_prompt
    prior_text_reasoning = bool(gist_text) and gist_text in reasoning_prompt
    prior_text_quick = bool(gist_text) and gist_text in quick_prompt
    sanitizer_found = INDIRECT_SANITIZER_PREFIX in gist_text
    base_row.update(
        {
            "prior_found_in_reasoning_prompt": prior_in_reasoning,
            "prior_found_in_quick_prompt": prior_in_quick,
            "prior_text_found_in_reasoning_prompt": prior_text_reasoning,
            "prior_text_found_in_quick_prompt": prior_text_quick,
            "indirect_sanitizer_found": sanitizer_found,
        }
    )

    if group.prior_expected:
        if not prior_in_reasoning or not prior_in_quick:
            return _make_failure_row(base_row, "prior block missing from one or both prompts")
        if not prior_text_reasoning or not prior_text_quick:
            return _make_failure_row(base_row, "exact previous_run_gist text missing from one or both prompts")
        if group.requires_indirect_sanitizer and not sanitizer_found:
            return _make_failure_row(base_row, "indirect memory config missing hidden-metric sanitizer prefix")
    else:
        if prior_in_reasoning or prior_in_quick:
            return _make_failure_row(base_row, "no-memory prompt unexpectedly contains prior block")

    base_row.update({"passed": True, "failure_reason": ""})
    return base_row


def write_results_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "workload",
        "tuner_group",
        "tuner_suffix",
        "config_path",
        "passed",
        "failure_reason",
        "fixed_tuner",
        "prior_expected",
        "prior_field_nonempty",
        "explicit_dual_loop",
        "results_dir_leaf_expected",
        "results_dir_leaf_actual",
        "prior_found_in_reasoning_prompt",
        "prior_found_in_quick_prompt",
        "prior_text_found_in_reasoning_prompt",
        "prior_text_found_in_quick_prompt",
        "indirect_sanitizer_found",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def write_results_md(path: Path, rows: Sequence[Mapping[str, Any]], summary: Mapping[str, Any]) -> None:
    lines = [
        "# RAG Prior Smoke Test",
        "",
        f"- configs_checked: {summary['configs_checked']}",
        f"- passed: {summary['passed']}",
        f"- failed: {summary['failed']}",
        f"- stage_created: {summary['stage_created']}",
        f"- stage_dir: `{summary['stage_dir']}`",
        "",
        "## Per-config results",
    ]
    for row in rows:
        status = "PASS" if row["passed"] else "FAIL"
        lines.append(
            f"- [{status}] `{row['workload']}` `{row['tuner_group']}` "
            f"`{row['config_path']}`"
        )
        if row["failure_reason"]:
            lines.append(f"  failure: {row['failure_reason']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def stage_subset_configs(rows: Sequence[Mapping[str, Any]], stage_dir: Path) -> List[Path]:
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)
    copied: List[Path] = []
    for row in rows:
        source = Path(row["config_path"])
        target_dir = stage_dir / row["workload"]
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = target_dir / source.name
        _copy_subset_config(source, destination)
        copied.append(destination)
    return copied


def build_requested_commands(stage_dir: Path, *, num_runs: int) -> List[str]:
    return [
        f'./run_configs_batch.sh {stage_dir} {num_runs} --tuners "{group.tuner_suffix}"'
        for group in GROUP_SPECS
    ]


def write_requested_commands(path: Path, commands: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["#!/usr/bin/env bash", "set -euo pipefail", ""]
    lines.extend(commands)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    path.chmod(0o755)


def write_subset_launcher(path: Path, stage_dir: Path, *, num_runs: int) -> None:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        f'STAGE_DIR="${{STAGE_DIR:-{stage_dir}}}"',
        f'NUM_RUNS="${{NUM_RUNS:-{num_runs}}}"',
        'RUNNER="${RUNNER:-/mydata/os-param-tuning/run_configs_batch.sh}"',
        "",
        "usage() {",
        "  cat <<'EOF'",
        "Usage:",
        "  run_requested_tuner_groups_subset.sh <group|all> [num_runs]",
        "",
        "Groups:",
        "  fixed",
        "  app_nomem",
        "  indirect_nomem",
        "  sys_top1_raw",
        "  app_top1_raw",
        "  sys_top3_raw",
        "  app_top3_raw",
        "  sys_last1_raw",
        "  app_last1_raw",
        "  all",
        "EOF",
        "}",
        "",
        'if [[ "${1:-}" == "" ]] || [[ "${1:-}" == "--help" ]] || [[ "${1:-}" == "-h" ]]; then',
        "  usage",
        "  exit 0",
        "fi",
        "",
        'GROUP="$1"',
        'if [[ "${2:-}" != "" ]]; then',
        '  NUM_RUNS="$2"',
        "fi",
        "",
        "run_group() {",
        '  local tuner_suffix="$1"',
        '  "${RUNNER}" "${STAGE_DIR}" "${NUM_RUNS}" --tuners "${tuner_suffix}"',
        "}",
        "",
        'case "${GROUP}" in',
        '  fixed) run_group "fixed" ;;',
        '  app_nomem) run_group "llm_dual_app_metrics_final_actor" ;;',
        '  indirect_nomem) run_group "llm_dual_indirect_all_mode3_final_actor" ;;',
        '  sys_top1_raw) run_group "llm_dual_indirect_all_mode3_memory_top_1_raw_to_summary_final_actor" ;;',
        '  app_top1_raw) run_group "llm_dual_app_metrics_memory_top_1_raw_to_summary_final_actor" ;;',
        '  sys_top3_raw) run_group "llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_final_actor" ;;',
        '  app_top3_raw) run_group "llm_dual_app_metrics_memory_top_3_raw_to_summary_final_actor" ;;',
        '  sys_last1_raw) run_group "llm_dual_indirect_all_mode3_memory_last_1_raw_to_summary_final_actor" ;;',
        '  app_last1_raw) run_group "llm_dual_app_metrics_memory_last_1_raw_to_summary_final_actor" ;;',
        "  all)",
        '    run_group "fixed"',
        '    run_group "llm_dual_app_metrics_final_actor"',
        '    run_group "llm_dual_indirect_all_mode3_final_actor"',
        '    run_group "llm_dual_indirect_all_mode3_memory_top_1_raw_to_summary_final_actor"',
        '    run_group "llm_dual_app_metrics_memory_top_1_raw_to_summary_final_actor"',
        '    run_group "llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_final_actor"',
        '    run_group "llm_dual_app_metrics_memory_top_3_raw_to_summary_final_actor"',
        '    run_group "llm_dual_indirect_all_mode3_memory_last_1_raw_to_summary_final_actor"',
        '    run_group "llm_dual_app_metrics_memory_last_1_raw_to_summary_final_actor"',
        "    ;;",
        "  *)",
        '    echo "Unknown group: ${GROUP}" >&2',
        "    usage >&2",
        "    exit 1",
        "    ;;",
        "esac",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    path.chmod(0o755)


def main() -> int:
    args = parse_args()
    stage_dir = Path(args.stage_dir)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    entries = collect_target_configs(stage_dir)
    rows = [validate_config_entry(entry) for entry in entries]
    passed = sum(1 for row in rows if row["passed"])
    failed_rows = [row for row in rows if not row["passed"]]

    subset_stage_dir = output_root / "batch_stage_subset"
    commands_file = output_root / "requested_tuner_commands.sh"
    launcher_file = output_root / "run_requested_tuner_groups_subset.sh"
    stage_created = False
    subset_files: List[Path] = []
    commands: List[str] = []

    if not failed_rows:
        subset_files = stage_subset_configs(rows, subset_stage_dir)
        commands = build_requested_commands(subset_stage_dir, num_runs=args.num_runs)
        write_requested_commands(commands_file, commands)
        write_subset_launcher(launcher_file, subset_stage_dir, num_runs=args.num_runs)
        stage_created = True

    summary = {
        "selected_workloads": SELECTED_WORKLOADS,
        "configs_checked": len(rows),
        "passed": passed,
        "failed": len(failed_rows),
        "stage_created": stage_created,
        "stage_dir": str(subset_stage_dir),
        "subset_stage_file_count": len(subset_files),
        "commands_file": str(commands_file) if stage_created else "",
        "launcher_file": str(launcher_file) if stage_created else "",
        "num_runs": args.num_runs,
        "tuner_suffixes": [group.tuner_suffix for group in GROUP_SPECS],
        "failures": [
            {
                "workload": row["workload"],
                "tuner_group": row["tuner_group"],
                "config_path": row["config_path"],
                "failure_reason": row["failure_reason"],
            }
            for row in failed_rows
        ],
    }
    _write_json(output_root / "summary.json", summary)
    write_results_csv(output_root / "results.csv", rows)
    write_results_md(output_root / "results.md", rows, summary)

    print(f"Checked configs: {len(rows)}")
    print(f"Passed: {passed}")
    print(f"Failed: {len(failed_rows)}")
    if stage_created:
        print(f"Subset stage created: {subset_stage_dir}")
        print(f"Subset stage files: {len(subset_files)}")
        print(f"Commands file: {commands_file}")
        print(f"Launcher file: {launcher_file}")
    else:
        print("Subset stage not created because at least one smoke check failed.")
    return 0 if not failed_rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
