#!/usr/bin/env python3
import os
import sys
import unittest

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "src"))

from scripts.generate_unseen_workload_subset_top3_configs import _nonmatching_top3, _unseen_filename


class TestUnseenWorkloadSubsetTop3Configs(unittest.TestCase):
    def test_nonmatching_top3_filters_same_workload(self):
        ranked = [
            {"benchmark_family_label": "target", "distance": 0.1},
            {"benchmark_family_label": "other_a", "distance": 0.2},
            {"benchmark_family_label": "target", "distance": 0.3},
            {"benchmark_family_label": "other_b", "distance": 0.4},
            {"benchmark_family_label": "other_c", "distance": 0.5},
        ]
        selected = _nonmatching_top3(ranked, "target")
        self.assertEqual(
            [item["benchmark_family_label"] for item in selected],
            ["other_a", "other_b", "other_c"],
        )

    def test_unseen_filename_suffix(self):
        from pathlib import Path

        path = Path(
            "rag_tpcc_config_llm_dual_app_metrics_memory_top_3_raw_to_summary_final_actor.json"
        )
        self.assertEqual(
            _unseen_filename(path),
            "rag_tpcc_config_llm_dual_app_metrics_memory_top_3_raw_to_summary_unseen_workload_final_actor.json",
        )


if __name__ == "__main__":
    unittest.main()
