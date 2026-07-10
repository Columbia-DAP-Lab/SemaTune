#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "src"))

import chromadb

from barebones_optimizer.memory.redaction import redact_history_data, redact_history_file
from barebones_optimizer.memory.store import load_into_store, query_store
from barebones_optimizer.memory.summary import summarize_redacted_history
from tests.memory_test_utils import (
    FakeEmbeddingProvider,
    FakeSummaryBackend,
    MemoryFixtureMixin,
    sample_repo_history_path,
)


class TestMemoryStore(MemoryFixtureMixin, unittest.TestCase):
    def test_load_into_store_backfills_summary_metadata_and_query_returns_it(self):
        payload = self.build_payload(
            include_pre_tuning=True,
            unchanged_prefix_len=2,
            total_tuning_iterations=13,
            stable_windows=2,
        )
        redacted = redact_history_data(payload)
        summary = summarize_redacted_history
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            redacted_path = self.write_json(tmp_path, "fixture_redacted.json", redacted)
            summary_payload = summarize_redacted_history(
                redacted_path,
                backend=FakeSummaryBackend(),
            )
            summary_path = tmp_path / "fixture_memory_summary.json"

            store_path = tmp_path / "chroma"
            load_into_store(
                [redacted_path],
                store_path=store_path,
                embedding_provider=FakeEmbeddingProvider(),
            )
            load_result = load_into_store(
                [summary_path],
                store_path=store_path,
                embedding_provider=FakeEmbeddingProvider(),
            )
            self.assertGreater(load_result["summary_backfilled"]["memory_raw_histories"], 0)

            client = chromadb.PersistentClient(path=str(store_path))
            raw_count = client.get_collection("memory_raw_histories").count()
            summary_count = client.get_collection("memory_run_summaries").count()
            self.assertEqual(raw_count, 2)
            self.assertEqual(summary_count, 2)
            raw_docs = client.get_collection("memory_raw_histories").get(include=["metadatas"])
            self.assertTrue(all(meta.get("run_summary_text") for meta in raw_docs["metadatas"]))

            snapshot = {
                "optimization_metric": redacted["objective"]["optimization_metric"],
                "optimization_goal": redacted["objective"]["optimization_goal"],
                "system_metrics": redacted["first_history_entry"]["system_metrics"],
                "system_perf_metrics": redacted["first_history_entry"]["system_perf_metrics"],
            }
            query_result = query_store(
                store_path=store_path,
                query_json=snapshot,
                top_k=5,
                embedding_provider=FakeEmbeddingProvider(),
            )
            self.assertTrue(query_result["hits"])
            self.assertIn(
                query_result["hits"][0]["metadata"]["source_kind"],
                {"first_entry_to_redacted", "first_entry_to_summary"},
            )
            self.assertIsNotNone(query_result["hits"][0]["resolved_target"])
            self.assertTrue(query_result["hits"][0]["run_summary_text"])
            self.assertTrue(query_result["hits"][0]["resolved_target"]["metadata"].get("run_summary_text"))

    def test_end_to_end_smoke_on_sample_result_file(self):
        sample_path = sample_repo_history_path()
        self.assertTrue(sample_path.exists(), sample_path)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            redacted_path = tmp_path / "sample_redacted.json"
            summary_path = tmp_path / "sample_memory_summary.json"
            summary_text_path = tmp_path / "sample_memory_summary.txt"
            store_path = tmp_path / "sample_store"

            redact_history_file(sample_path, output_path=redacted_path)
            summarize_redacted_history(
                redacted_path,
                output_json_path=summary_path,
                output_text_path=summary_text_path,
                backend=FakeSummaryBackend(),
            )
            load_into_store(
                [redacted_path, summary_path],
                store_path=store_path,
                embedding_provider=FakeEmbeddingProvider(),
            )

            redacted_payload = json.loads(redacted_path.read_text())
            snapshot = {
                "optimization_metric": redacted_payload["objective"]["optimization_metric"],
                "optimization_goal": redacted_payload["objective"]["optimization_goal"],
                "system_metrics": redacted_payload["first_history_entry"]["system_metrics"],
                "system_perf_metrics": redacted_payload["first_history_entry"]["system_perf_metrics"],
            }
            query_result = query_store(
                store_path=store_path,
                query_json=snapshot,
                top_k=5,
                embedding_provider=FakeEmbeddingProvider(),
            )

            self.assertTrue(query_result["hits"])
            top_doc = query_result["hits"][0]["document"].lower()
            self.assertIn(
                query_result["hits"][0]["metadata"]["source_kind"],
                {"first_entry_to_redacted", "first_entry_to_summary"},
            )
            self.assertIsNotNone(query_result["hits"][0]["resolved_target"])
            self.assertNotIn("masstree", top_doc)
            self.assertNotIn("tailbench", top_doc)


if __name__ == "__main__":
    unittest.main()
