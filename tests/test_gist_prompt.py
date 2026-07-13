#!/usr/bin/env python3
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Pre-mock openai and google.genai BEFORE importing the module
sys.modules['openai'] = MagicMock()
sys.modules['google.genai'] = MagicMock()
sys.modules['google.genai.types'] = MagicMock()

# Add src to path
sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), 'src'))

# Now import
try:
    from optimizer.tuners.llm import LLMTuner
except ImportError:
    # If import fails (e.g. deps missing), we relied on mocks above
    # But we need to ensure optimizer structure is importable
    # Ideally should work if PYTHONPATH is correct
    pass

# ... (imports remain)

try:
    from optimizer.tuners.llm import LLMTuner
    from optimizer.config import SimpleConfig
    from optimizer.benchmark import BenchmarkMetrics
except ImportError:
    pass

class TestGistPrompt(unittest.TestCase):
    def setUp(self):
        self.env_patcher = patch.dict(
            os.environ, {"OPENROUTER_API_KEY": "test-openrouter-key"}
        )
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)
        # The module-level SDK mock returns one shared client; clear calls and
        # side effects so tests remain independent.
        sys.modules["openai"].OpenAI.return_value.reset_mock(
            return_value=True, side_effect=True
        )

    def test_gist_prompt_structure(self):
        config = SimpleConfig()
        config.llm_model_name = "dummy-model"
        config.parameter_ranges = {"param1": (1, 10)}
        config.tuner_type = "llm"
        config.optimization_goal = "maximize"
        config.optimization_metric = "throughput"
        config.use_gemini = False 
        
        # Patch the openai_sdk usage inside llm.py
        # Since we mocked sys.modules['openai'], import openai as openai_sdk should result in a mock
        
        # But we need to make sure LLMTuner uses it
        
        tuner = LLMTuner(config)
        # Verify client is our mock (from sys.modules['openai'].OpenAI(...))
        
        # Sample history
        history = [
             {
                 "iteration": 1,
                 "metrics": {"throughput": 100},
                 "reward": 100,
                 "parameters": {"param1": 5},
                 "justification": "Initial try"
             },
        ]
        
        # Prepare the current OpenAI SDK raw-response call shape.
        parsed_response = MagicMock()
        parsed_response.choices = [
            MagicMock(message=MagicMock(content="Mock Summary"))
        ]
        raw_response = MagicMock()
        raw_response.parse.return_value = parsed_response
        tuner.client.chat.completions.with_raw_response.create.return_value = raw_response
        
        tuner.generate_gist(history)
        
        # Inspect calls
        calls = tuner.client.chat.completions.with_raw_response.create.call_args_list
        if not calls:
            self.fail("No API calls made")

        call_args = calls[0].kwargs
        messages = call_args.get('messages', [])
        prompt = "\n".join(message['content'] for message in messages)
        
        print("\n--- GENERATED PROMPT ---")
        print(prompt)
        print("------------------------\n")
        
        self.assertIn("concise and informative takeaways", prompt)
        self.assertIn("making **future runs more efficient**", prompt)
        self.assertIn("HISTORY:", prompt)

    def test_non_200_openrouter_response_raises_fatal_error(self):
        config = SimpleConfig()
        config.llm_model_name = "dummy-model"
        config.parameter_ranges = {"param1": (1, 10)}
        config.tuner_type = "llm"
        config.optimization_goal = "maximize"
        config.optimization_metric = "throughput"
        config.use_gemini = False
        config.llm_request_max_retries = 3
        config.llm_request_retry_backoff_sec = 0

        tuner = LLMTuner(config)

        class FakeApiError(Exception):
            def __init__(self, status_code: int, message: str):
                super().__init__(message)
                self.status_code = status_code

        calls = {"count": 0}

        def fail_with_500(**kwargs):
            calls["count"] += 1
            raise FakeApiError(500, "server error")

        tuner.client.chat.completions.with_raw_response.create.side_effect = fail_with_500

        with self.assertRaises(Exception) as ctx:
            tuner.suggest_parameters(
                metrics=BenchmarkMetrics(),
                current_params={"param1": 5},
                iteration=0,
                best_reward=0.0
            )

        self.assertEqual(calls["count"], 3)
        self.assertEqual(ctx.exception.__class__.__name__, "LLMHTTPStatusError")
        self.assertIn("HTTP 500", str(ctx.exception))

    def test_timeout_retry_then_success(self):
        config = SimpleConfig()
        config.llm_model_name = "dummy-model"
        config.parameter_ranges = {"param1": (1, 10)}
        config.tuner_type = "llm"
        config.optimization_goal = "maximize"
        config.optimization_metric = "throughput"
        config.use_gemini = False
        config.llm_request_max_retries = 3
        config.llm_request_retry_backoff_sec = 0

        tuner = LLMTuner(config)

        parsed_response = MagicMock()
        parsed_response.choices = [
            MagicMock(message=MagicMock(content='{"param1": 6, "justification": "retry worked"}'))
        ]

        raw_response = MagicMock()
        raw_response.parse.return_value = parsed_response

        calls = {"count": 0}

        def flaky_create(**kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise TimeoutError("request timed out")
            return raw_response

        tuner.client.chat.completions.with_raw_response.create.side_effect = flaky_create

        response = tuner.suggest_parameters(
            metrics=BenchmarkMetrics(),
            current_params={"param1": 5},
            iteration=0,
            best_reward=0.0
        )

        self.assertEqual(calls["count"], 2)
        self.assertEqual(response.parameters.get("param1"), 6)

    def test_consecutive_timeouts_exhaust_retries_and_raise_fatal_error(self):
        config = SimpleConfig()
        config.llm_model_name = "dummy-model"
        config.parameter_ranges = {"param1": (1, 10)}
        config.tuner_type = "llm"
        config.optimization_goal = "maximize"
        config.optimization_metric = "throughput"
        config.use_gemini = False
        config.llm_request_max_retries = 3
        config.llm_request_retry_backoff_sec = 0

        tuner = LLMTuner(config)
        tuner.client.chat.completions.with_raw_response.create.side_effect = TimeoutError("request timed out")

        with self.assertRaises(Exception) as ctx:
            tuner.suggest_parameters(
                metrics=BenchmarkMetrics(),
                current_params={"param1": 5},
                iteration=0,
                best_reward=0.0
            )

        self.assertEqual(ctx.exception.__class__.__name__, "LLMTimeoutExhaustedError")
        self.assertIn("timed out 3 consecutive times", str(ctx.exception))

    def test_non_timeout_failure_resets_timeout_streak(self):
        config = SimpleConfig()
        config.llm_model_name = "dummy-model"
        config.parameter_ranges = {"param1": (1, 10)}
        config.tuner_type = "llm"
        config.optimization_goal = "maximize"
        config.optimization_metric = "throughput"
        config.use_gemini = False
        config.llm_request_max_retries = 2
        config.llm_request_retry_backoff_sec = 0

        tuner = LLMTuner(config)
        tuner.client.chat.completions.with_raw_response.create.side_effect = [
            TimeoutError("request timed out"),
            RuntimeError("transient upstream failure"),
        ]

        response = tuner.suggest_parameters(
            metrics=BenchmarkMetrics(),
            current_params={"param1": 5},
            iteration=0,
            best_reward=0.0
        )

        self.assertEqual(response.parameters, {})
        self.assertEqual(
            tuner.client.chat.completions.with_raw_response.create.call_count, 2
        )

if __name__ == '__main__':
    unittest.main()
