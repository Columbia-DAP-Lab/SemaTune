#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "src"))

from optimizer.memory.redaction import redact_history_data, redact_history_file
from tests.memory_test_utils import MemoryFixtureMixin


class TestMemoryRedaction(MemoryFixtureMixin, unittest.TestCase):
    def test_redaction_whitelists_config_and_sanitizes_reasoning(self):
        payload = self.build_payload(include_pre_tuning=True, unchanged_prefix_len=2, total_tuning_iterations=3)
        redacted = redact_history_data(payload)

        self.assertNotIn("benchmark", redacted["tuner_config"])
        self.assertNotIn("tailbench_app", redacted["tuner_config"])
        self.assertNotIn("results_dir", redacted["tuner_config"])
        self.assertNotIn("llm_api_key", redacted["tuner_config"])
        self.assertEqual(
            redacted["tuner_config"]["llm_additional_metrics"],
            ["instructions_per_cycle", "cycles"],
        )

        entry = redacted["history"][1]
        self.assertNotIn("app_metrics", entry)
        self.assertIn("system_metrics", entry)
        self.assertIn("system_perf_metrics", entry)
        self.assertNotIn("time_elapsed_seconds", entry["system_perf_metrics"])
        self.assertNotIn("masstree", entry["sanitized_reasoning"].lower())
        self.assertNotIn("/tmp/", entry["sanitized_reasoning"])
        self.assertNotIn("throughput", entry["sanitized_reasoning"].lower())
        self.assertIn("application objective", entry["sanitized_reasoning"].lower())
        self.assertNotIn("total_time", redacted["best_result"])

    def test_keep_app_metrics_preserves_app_metrics(self):
        payload = self.build_payload(include_pre_tuning=True, unchanged_prefix_len=2, total_tuning_iterations=3)
        redacted = redact_history_data(payload, keep_app_metrics=True)

        entry = redacted["history"][1]
        self.assertIn("app_metrics", entry)
        self.assertIn("throughput", entry["app_metrics"])
        self.assertIn("latency_p99", entry["app_metrics"])
        self.assertNotIn("duration_seconds", entry["app_metrics"])
        self.assertNotIn("elapsed_time_ns", entry["app_metrics"])
        self.assertIn("throughput", redacted["tuner_config"]["llm_additional_metrics"])

    def test_first_history_entry_uses_iteration_zero_when_present(self):
        payload = self.build_payload(include_pre_tuning=True, unchanged_prefix_len=2, total_tuning_iterations=4)
        redacted = redact_history_data(payload)
        first_entry = redacted["first_history_entry"]

        self.assertEqual(first_entry["iteration"], 0)
        self.assertEqual(first_entry["phase"], "pre_tuning_default")
        self.assertTrue(first_entry["matches_initial_parameters"])

    def test_first_history_entry_uses_first_recorded_entry_without_iteration_zero(self):
        payload = self.build_payload(include_pre_tuning=False, unchanged_prefix_len=2, total_tuning_iterations=4)
        redacted = redact_history_data(payload)
        first_entry = redacted["first_history_entry"]

        self.assertEqual(first_entry["iteration"], 1)
        self.assertEqual(first_entry["phase"], "tuning")

    def test_rag_projection_contains_only_pointer_and_full_raw_docs(self):
        payload = self.build_payload(include_pre_tuning=False, unchanged_prefix_len=1, total_tuning_iterations=4)
        redacted = redact_history_data(payload)
        projection = redacted["rag_projection"]

        self.assertEqual(set(projection.keys()), {"first_entry_to_redacted", "full_redacted"})
        self.assertEqual(
            projection["first_entry_to_redacted"]["metadata"]["source_kind"],
            "first_entry_to_redacted",
        )
        self.assertEqual(
            projection["full_redacted"]["metadata"]["source_kind"],
            "full_redacted",
        )
        self.assertEqual(
            projection["first_entry_to_redacted"]["metadata"]["target_document_id"],
            projection["full_redacted"]["id"],
        )

    def test_redact_history_file_writes_expected_output(self):
        payload = self.build_payload(include_pre_tuning=True, unchanged_prefix_len=2, total_tuning_iterations=3)
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "sample_history.json"
            with input_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            redact_history_file(input_path)
            output_path = Path(tmpdir) / "sample_history_redacted.json"
            self.assertTrue(output_path.exists())


if __name__ == "__main__":
    unittest.main()
