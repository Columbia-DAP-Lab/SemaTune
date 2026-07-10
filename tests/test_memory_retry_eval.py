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

from scripts.evaluate_retry_full_redacted_retrieval import (
    FIRST_SIGNATURE_COLLECTION_NAME,
    FULL_REDACTED_COLLECTION_NAME,
    build_first_signature_documents,
    build_corpus_documents,
    build_experiment_store,
    build_holdout_queries,
    combine_result_rows,
    discover_retry_workloads,
    render_first_entry_system_query_text,
    score_retrieval,
    split_holdouts,
)
from tests.memory_test_utils import FakeEmbeddingProvider, MemoryFixtureMixin
from tests.memory_test_utils import FakeSummaryBackend


class TestMemoryRetryEval(MemoryFixtureMixin, unittest.TestCase):
    def _build_retry_layout(self, root: Path) -> None:
        specs = {
            "masstree_hi_p99": 3,
            "silo_hi_p99": 4,
        }
        for workload, count in specs.items():
            tuner_dir = root / f"results_{workload}_retry" / workload / "llm_dual_app_metrics_final_actor"
            tuner_dir.mkdir(parents=True, exist_ok=True)
            for idx in range(count):
                payload = self.build_payload(include_pre_tuning=True, unchanged_prefix_len=2, total_tuning_iterations=3)
                payload["config"]["tailbench_app"] = workload
                payload["history"][0]["metrics"]["app_name"] = workload
                filename = f"dual_loop_actor_speculator_tailbench_20260306_0{idx}0000.json"
                with (tuner_dir / filename).open("w", encoding="utf-8") as handle:
                    json.dump(payload, handle)

    def test_discovery_and_split_counts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._build_retry_layout(root)
            rows = discover_retry_workloads(root)
            corpus, holdouts = split_holdouts(rows)
            self.assertEqual(len(rows), 2)
            self.assertEqual(len(holdouts), 2)
            self.assertEqual(len(corpus), (3 - 1) + (4 - 1))
            self.assertTrue(all(record.holdout for record in holdouts))
            self.assertTrue(all(not record.holdout for record in corpus))
            corpus_all, holdouts_all = split_holdouts(rows, cv_mode="all_runs")
            self.assertEqual(len(corpus_all), 7)
            self.assertEqual(len(holdouts_all), 7)
            self.assertTrue(all(record.holdout for record in holdouts_all))
            self.assertTrue(all(not record.holdout for record in corpus_all))

    def test_query_text_uses_only_system_metrics(self):
        payload = self.build_payload(include_pre_tuning=True, unchanged_prefix_len=2, total_tuning_iterations=3)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.json"
            with path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            from scripts.evaluate_retry_full_redacted_retrieval import redact_run

            redacted = redact_run(path)
            query_text = render_first_entry_system_query_text(redacted)
            self.assertIn("system_metrics=", query_text)
            self.assertIn("system_perf_metrics=", query_text)
            self.assertNotIn("masstree", query_text.lower())
            self.assertNotIn("latency_p99", query_text)
            self.assertNotIn("best_reward", query_text)
            self.assertNotIn("parameters=", query_text)
            redacted_with_app = redact_run(path, keep_app_metrics=True)
            query_with_app = render_first_entry_system_query_text(
                redacted_with_app,
                include_app_metrics=True,
            )
            self.assertIn("app_metrics=", query_with_app)
            self.assertIn("throughput", query_with_app)

    def test_store_and_scoring_use_only_full_redacted_docs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._build_retry_layout(root)
            rows = discover_retry_workloads(root)
            corpus, holdouts = split_holdouts(rows)

            corpus_docs = build_corpus_documents(corpus)
            self.assertTrue(corpus_docs)
            self.assertTrue(all(doc["metadata"]["source_kind"] == "full_redacted" for doc in corpus_docs))
            self.assertTrue(all("run_summary_text" in doc["metadata"] for doc in corpus_docs))
            first_signature_docs = build_first_signature_documents(corpus)
            self.assertTrue(all(doc["metadata"]["source_kind"] == "first_signature" for doc in first_signature_docs))

            holdout_queries = build_holdout_queries(holdouts)
            store_path = root / "store"
            summary_cache_dir = root / "summary_cache"
            build_experiment_store(
                build_corpus_documents(
                    corpus,
                    summary_backend=FakeSummaryBackend(),
                    summary_cache_dir=summary_cache_dir,
                    summary_cache={},
                ),
                store_path=store_path,
                collection_name=FULL_REDACTED_COLLECTION_NAME,
                embedding_provider=FakeEmbeddingProvider(),
            )
            build_experiment_store(
                build_first_signature_documents(
                    corpus,
                    summary_backend=FakeSummaryBackend(),
                    summary_cache_dir=summary_cache_dir,
                    summary_cache={},
                ),
                store_path=store_path,
                collection_name=FIRST_SIGNATURE_COLLECTION_NAME,
                embedding_provider=FakeEmbeddingProvider(),
            )
            client = chromadb.PersistentClient(path=str(store_path))
            stored_full = client.get_collection(FULL_REDACTED_COLLECTION_NAME).get(include=["metadatas"])
            stored_sig = client.get_collection(FIRST_SIGNATURE_COLLECTION_NAME).get(include=["metadatas"])
            self.assertEqual(len(stored_full["ids"]), len(corpus_docs))
            self.assertEqual(len(stored_sig["ids"]), len(first_signature_docs))
            self.assertTrue(all(meta["source_kind"] == "full_redacted" for meta in stored_full["metadatas"]))
            self.assertTrue(all(meta["source_kind"] == "first_signature" for meta in stored_sig["metadatas"]))
            self.assertTrue(all(meta.get("run_summary_text") for meta in stored_full["metadatas"]))
            self.assertTrue(all(meta.get("run_summary_text") for meta in stored_sig["metadatas"]))

            rows_out, summary = score_retrieval(
                holdout_queries,
                store_path=store_path,
                collection_name=FULL_REDACTED_COLLECTION_NAME,
                embedding_provider=FakeEmbeddingProvider(),
            )
            sig_rows, sig_summary = score_retrieval(
                holdout_queries,
                store_path=store_path,
                collection_name=FIRST_SIGNATURE_COLLECTION_NAME,
                embedding_provider=FakeEmbeddingProvider(),
            )
            self.assertEqual(len(rows_out), len(holdouts))
            self.assertIn("top1_accuracy", summary)
            self.assertIn("top3_accuracy", summary)
            self.assertIn("confusions", summary)
            self.assertIn("top1_correct", rows_out[0])
            self.assertIn("top3_correct", rows_out[0])
            self.assertIn("top3_labels", rows_out[0])
            self.assertIn("retrieved_summary_text", rows_out[0])
            self.assertIn("top3_summary_texts", rows_out[0])
            combined = combine_result_rows(rows_out, sig_rows)
            self.assertEqual(len(combined), len(holdouts))
            self.assertIn("first_signature_top1_correct", combined[0])
            self.assertIn("full_redacted_top1_correct", combined[0])
            self.assertIn("first_signature_retrieved_summary_text", combined[0])
            self.assertIn("full_redacted_retrieved_summary_text", combined[0])

            all_corpus, all_holdouts = split_holdouts(rows, cv_mode="all_runs")
            all_docs = build_corpus_documents(all_corpus)
            all_sig_docs = build_first_signature_documents(all_corpus)
            store_path_all = root / "store_all"
            build_experiment_store(
                all_docs,
                store_path=store_path_all,
                collection_name=FULL_REDACTED_COLLECTION_NAME,
                embedding_provider=FakeEmbeddingProvider(),
            )
            build_experiment_store(
                all_sig_docs,
                store_path=store_path_all,
                collection_name=FIRST_SIGNATURE_COLLECTION_NAME,
                embedding_provider=FakeEmbeddingProvider(),
            )
            all_queries = build_holdout_queries(all_holdouts)
            all_rows, all_summary = score_retrieval(
                all_queries,
                store_path=store_path_all,
                collection_name=FULL_REDACTED_COLLECTION_NAME,
                self_exclude=True,
                embedding_provider=FakeEmbeddingProvider(),
            )
            self.assertEqual(len(all_rows), len(all_holdouts))
            self.assertTrue(all(row["retrieved_json_path"] != row["holdout_json_path"] for row in all_rows if row["retrieved_json_path"]))
            self.assertTrue(all_summary["self_exclude"])

    def test_real_repo_discovery_matches_expected_counts(self):
        rows = discover_retry_workloads(Path("/mydata/os-param-tuning"))
        corpus, holdouts = split_holdouts(rows)
        self.assertEqual(len(rows), 15)
        self.assertEqual(len(holdouts), 15)
        self.assertEqual(len(corpus), 56)
        corpus_all, holdouts_all = split_holdouts(rows, cv_mode="all_runs")
        self.assertEqual(len(corpus_all), 71)
        self.assertEqual(len(holdouts_all), 71)


if __name__ == "__main__":
    unittest.main()
