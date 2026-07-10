#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "src"))

from scripts.evaluate_retry_full_redacted_retrieval import discover_retry_workloads, split_holdouts
from scripts.export_retry_cv_summary_reports import (
    build_ranked_hits,
    build_summary_text_documents,
    build_summary_text_queries,
)
from tests.memory_test_utils import FakeEmbeddingProvider, FakeSummaryBackend, MemoryFixtureMixin


class TestMemoryReportExport(MemoryFixtureMixin, unittest.TestCase):
    def _build_retry_layout(self, root: Path) -> None:
        specs = {
            "alpha_hi_p99": 2,
            "beta_hi_p99": 2,
        }
        for workload, count in specs.items():
            tuner_dir = root / f"results_{workload}_retry" / workload / "llm_dual_app_metrics_final_actor"
            tuner_dir.mkdir(parents=True, exist_ok=True)
            for idx in range(count):
                payload = self.build_payload(include_pre_tuning=True, unchanged_prefix_len=1, total_tuning_iterations=2)
                payload["config"]["tailbench_app"] = workload
                payload["history"][0]["metrics"]["app_name"] = workload
                filename = f"dual_loop_actor_speculator_tailbench_20260306_0{idx}0000.json"
                with (tuner_dir / filename).open("w", encoding="utf-8") as handle:
                    json.dump(payload, handle)

    def test_summary_text_representation_builds_and_scores(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._build_retry_layout(root)
            rows = discover_retry_workloads(root)
            corpus, holdouts = split_holdouts(rows, cv_mode="all_runs")
            summary_cache_dir = root / "summary_cache"

            corpus_docs = build_summary_text_documents(
                corpus,
                keep_app_metrics=True,
                summary_backend=FakeSummaryBackend(),
                summary_cache_dir=summary_cache_dir,
                summary_cache={},
            )
            holdout_queries = build_summary_text_queries(
                holdouts,
                keep_app_metrics=True,
                summary_backend=FakeSummaryBackend(),
                summary_cache_dir=summary_cache_dir,
                summary_cache={},
            )

            self.assertTrue(corpus_docs)
            self.assertTrue(holdout_queries)
            self.assertTrue(all(doc["metadata"]["source_kind"] == "summary_text" for doc in corpus_docs))
            self.assertTrue(all(doc["metadata"].get("run_summary_text") for doc in corpus_docs))
            self.assertTrue(all(item["query_text"] for item in holdout_queries))

            provider = FakeEmbeddingProvider()
            corpus_vectors = provider.embed_documents([doc["text"] for doc in corpus_docs])
            rows_out = build_ranked_hits(
                corpus_docs,
                corpus_vectors,
                holdout_queries,
                embedding_provider=provider,
            )
            self.assertEqual(len(rows_out), len(holdouts))
            self.assertTrue(all(len(row["top3"]) <= 3 for row in rows_out))
            self.assertTrue(all(row["farthest"] is not None for row in rows_out))


if __name__ == "__main__":
    unittest.main()
