#!/usr/bin/env python3
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "src"))

from optimizer.memory.common import GEMINI_SUMMARY_MODEL
from optimizer.memory.redaction import redact_history_data
from optimizer.memory.summary import (
    GoogleGenAISummaryBackend,
    SUMMARY_RESPONSE_SCHEMA,
    build_summary_prompt,
    summarize_redacted_data,
)
from tests.memory_test_utils import FakeSummaryBackend, MemoryFixtureMixin


class TestMemorySummary(MemoryFixtureMixin, unittest.TestCase):
    def test_summary_prompt_mentions_first_history_entry(self):
        payload = self.build_payload(include_pre_tuning=True, unchanged_prefix_len=2, total_tuning_iterations=3)
        redacted = redact_history_data(payload)
        prompt = build_summary_prompt(redacted)

        self.assertIn("first history entry", prompt.lower())
        self.assertIn(redacted["objective"]["optimization_metric"], prompt)
        self.assertIn("how later good and bad regions diverged", prompt)

    def test_summarize_redacted_data_builds_summary_document(self):
        payload = self.build_payload(include_pre_tuning=True, unchanged_prefix_len=2, total_tuning_iterations=3)
        redacted = redact_history_data(payload)
        backend = FakeSummaryBackend()

        result = summarize_redacted_data(redacted, backend=backend)

        self.assertIn("summary", result)
        self.assertIn("summary_text", result)
        self.assertIn("initial unchanged", result["summary_text"].lower())
        self.assertEqual(
            result["rag_projection"]["first_entry_to_summary"]["metadata"]["source_kind"],
            "first_entry_to_summary",
        )
        self.assertEqual(
            result["rag_projection"]["full_summary"]["metadata"]["source_kind"],
            "full_summary",
        )
        self.assertEqual(
            result["rag_projection"]["first_entry_to_summary"]["metadata"]["target_document_id"],
            result["rag_projection"]["full_summary"]["id"],
        )
        self.assertIn("first history entry", backend.prompt.lower())

    def test_default_backend_uses_fixed_gemini_flash_lite_model(self):
        payload = self.build_payload(include_pre_tuning=True, unchanged_prefix_len=2, total_tuning_iterations=3)
        redacted = redact_history_data(payload)
        captured = {}

        def fake_init(self, api_key=None, model_name=GEMINI_SUMMARY_MODEL):
            self.model_name = model_name

        def fake_generate(self, prompt, response_schema):
            captured["model_name"] = self.model_name
            return FakeSummaryBackend().generate_summary(prompt, response_schema)

        with patch.object(GoogleGenAISummaryBackend, "__init__", fake_init), patch.object(
            GoogleGenAISummaryBackend,
            "generate_summary",
            fake_generate,
        ):
            result = summarize_redacted_data(redacted)

        self.assertEqual(captured["model_name"], GEMINI_SUMMARY_MODEL)
        self.assertEqual(result["model_name"], GEMINI_SUMMARY_MODEL)

    def test_backend_retries_invalid_json_response(self):
        valid_payload = FakeSummaryBackend().generate_summary("prompt", SUMMARY_RESPONSE_SCHEMA)[0]
        responses = [
            SimpleNamespace(parsed=None, text='{"summary_text":"broken', usage_metadata=None),
            SimpleNamespace(parsed=valid_payload, text=None, usage_metadata=None),
        ]
        calls = {"count": 0}

        def fake_generate_content(**kwargs):
            response = responses[calls["count"]]
            calls["count"] += 1
            return response

        backend = GoogleGenAISummaryBackend.__new__(GoogleGenAISummaryBackend)
        backend.model_name = GEMINI_SUMMARY_MODEL
        backend.max_attempts = 2
        backend.client = SimpleNamespace(
            models=SimpleNamespace(generate_content=fake_generate_content)
        )

        payload, metadata = backend.generate_summary("prompt", SUMMARY_RESPONSE_SCHEMA)

        self.assertEqual(calls["count"], 2)
        self.assertEqual(payload["summary_text"], valid_payload["summary_text"])
        self.assertEqual(metadata["attempt_count"], 2)


if __name__ == "__main__":
    unittest.main()
