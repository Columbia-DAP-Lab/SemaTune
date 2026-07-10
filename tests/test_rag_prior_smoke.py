#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path

from scripts.smoke_test_rag_tuner_groups import (
    GROUP_SPECS,
    build_requested_commands,
    collect_target_configs,
    stage_subset_configs,
    validate_config_entry,
)


STAGE_DIR = Path("/mydata/os-param-tuning/generated/rag_prior_configs/batch_stage")


@unittest.skipUnless(STAGE_DIR.is_dir(), "full rag prior stage dir not present")
class TestRagPriorSmoke(unittest.TestCase):
    def test_collect_target_configs_counts(self):
        entries = collect_target_configs(STAGE_DIR)
        self.assertEqual(len(entries), 36)
        counts = {}
        for entry in entries:
            counts[entry["group"].key] = counts.get(entry["group"].key, 0) + 1
        for group in GROUP_SPECS:
            self.assertEqual(counts[group.key], 4)

    def test_representative_prompt_checks_pass(self):
        entries = collect_target_configs(STAGE_DIR)
        wanted = {"fixed", "app_nomem", "indirect_nomem", "app_top1_raw", "sys_top1_raw"}
        checked = 0
        for entry in entries:
            if entry["group"].key not in wanted:
                continue
            row = validate_config_entry(entry)
            self.assertTrue(row["passed"], row)
            checked += 1
            wanted.remove(entry["group"].key)
            if not wanted:
                break
        self.assertEqual(checked, 5)

    def test_stage_subset_and_commands(self):
        entries = collect_target_configs(STAGE_DIR)
        rows = [validate_config_entry(entry) for entry in entries]
        self.assertTrue(all(row["passed"] for row in rows))
        with tempfile.TemporaryDirectory() as tmpdir:
            stage_dir = Path(tmpdir) / "subset"
            copied = stage_subset_configs(rows, stage_dir)
            self.assertEqual(len(copied), 36)
            self.assertEqual(len(list(stage_dir.rglob("*.json"))), 36)
            commands = build_requested_commands(stage_dir, num_runs=5)
            self.assertEqual(len(commands), 9)
            self.assertTrue(all(str(stage_dir) in command for command in commands))


if __name__ == "__main__":
    unittest.main()
