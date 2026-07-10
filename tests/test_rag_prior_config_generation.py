#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "src"))

from scripts.generate_rag_prior_configs import generate_rag_prior_configs
from tests.memory_test_utils import FakeEmbeddingProvider, FakeSummaryBackend, MemoryFixtureMixin


class FakePriorBackend:
    model_name = "gemini-2.5-flash-lite"

    def generate_text(self, *, system_instruction: str, prompt: str):
        text = "Synthesized prior.\n\n" + prompt.splitlines()[0]
        return text, {
            "model_name": self.model_name,
            "attempt_count": 1,
            "token_usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }


class TestRagPriorConfigGeneration(MemoryFixtureMixin, unittest.TestCase):
    def _build_layout(self, root: Path) -> list[str]:
        workloads = ["alpha_hi_p99", "beta_hi_p99"]
        config_root = root / "config" / "full_param"
        for workload in workloads:
            workload_config_dir = config_root / workload
            workload_config_dir.mkdir(parents=True, exist_ok=True)
            app_prefix = f"{workload}_config"
            for filename in [
                f"{app_prefix}_llm_dual_app_metrics_final_actor.json",
                f"{app_prefix}_llm_dual_indirect_all_mode3_final_actor.json",
                f"{app_prefix}_fixed.json",
            ]:
                payload = {
                    "results_dir": f"results/{workload}/{Path(filename).stem}/",
                    "previous_run_gist": "",
                    "optimization_metric": "latency_p99",
                    "optimization_goal": "minimize",
                }
                (workload_config_dir / filename).write_text(
                    json.dumps(payload, indent=2) + "\n",
                    encoding="utf-8",
                )

            retry_root = root / f"results_{workload}_retry" / workload
            for leaf in ["llm_dual_app_metrics_final_actor", "llm_dual_indirect_all_mode3_final_actor"]:
                tuner_dir = retry_root / leaf
                tuner_dir.mkdir(parents=True, exist_ok=True)
                for idx in range(3):
                    payload = self.build_payload(
                        include_pre_tuning=True,
                        unchanged_prefix_len=1,
                        total_tuning_iterations=3,
                    )
                    payload["config"]["tailbench_app"] = workload
                    payload["history"][0]["metrics"]["app_name"] = workload
                    payload["history"][0]["metrics"]["throughput"] = 1000 + idx * 100 + (0 if workload == "alpha_hi_p99" else 50)
                    payload["history"][1]["metrics"]["throughput"] = 900 + idx * 100
                    name = f"dual_loop_actor_speculator_{leaf}_{idx:02d}.json"
                    (tuner_dir / name).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return workloads

    def test_generate_rag_prior_configs_writes_configs_and_stage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workloads = self._build_layout(root)
            artifacts_root = root / "generated" / "rag_prior_configs"
            stage_dir = artifacts_root / "batch_stage"

            manifest = generate_rag_prior_configs(
                repo_root=root,
                workloads=workloads,
                artifacts_root=artifacts_root,
                stage_dir=stage_dir,
                embedding_provider=FakeEmbeddingProvider(),
                summary_backend=FakeSummaryBackend(),
                prior_backend=FakePriorBackend(),
            )

            self.assertEqual(manifest["generated_config_count"], 24)
            self.assertEqual(manifest["batch_stage_file_count"], 30)
            self.assertTrue((artifacts_root / "generated_config_manifest.json").exists())
            self.assertTrue((artifacts_root / "generated_config_paths.txt").exists())
            self.assertTrue((artifacts_root / "batch_command.sh").exists())

            generated_paths = [Path(path) for path in manifest["generated_config_paths"]]
            app_raw = next(
                path for path in generated_paths if "app_metrics_memory_top_1_raw_to_summary" in path.name
            )
            indirect_summary = next(
                path for path in generated_paths if "indirect_all_mode3_memory_top_3_summary_to_summary" in path.name
            )
            app_config = json.loads(app_raw.read_text())
            indirect_config = json.loads(indirect_summary.read_text())
            self.assertIn("rag_llm_dual_app_metrics_memory_top_1_raw_to_summary_final_actor", app_config["results_dir"])
            self.assertIn("Synthesized prior.", app_config["previous_run_gist"])
            self.assertIn("rag_llm_dual_indirect_all_mode3_memory_top_3_summary_to_summary_final_actor", indirect_config["results_dir"])
            self.assertIn("Hidden primary-metric values from the prior run have been redacted.", indirect_config["previous_run_gist"])

            app_query = (artifacts_root / "app" / "alpha_hi_p99" / "holdout_raw_query.txt").read_text()
            indirect_query = (artifacts_root / "indirect" / "alpha_hi_p99" / "holdout_raw_query.txt").read_text()
            self.assertIn("app_metrics=", app_query)
            self.assertNotIn("app_metrics=", indirect_query)

            retrieval_manifest = json.loads(
                (artifacts_root / "app" / "alpha_hi_p99" / "raw_to_summary" / "retrieval_manifest.json").read_text()
            )
            self.assertIn("ranked_candidates", retrieval_manifest)
            self.assertIn("selected", retrieval_manifest)
            self.assertEqual(len(retrieval_manifest["selected"]["top_3"]), 3)

            staged_files = sorted(stage_dir.rglob("*.json"))
            self.assertEqual(len(staged_files), 30)


if __name__ == "__main__":
    unittest.main()
