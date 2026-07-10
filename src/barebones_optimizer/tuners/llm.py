#!/usr/bin/env python3
"""
LLM-based tuner implementation using Gemini API.

This tuner uses Google's Gemini API to suggest parameter values based on
conversational interaction with the optimization process.
"""

import os
import json
import time
import logging
import re
import socket
from typing import Dict, Optional, Any, List

from ..benchmark import BenchmarkMetrics
from ..token_bookkeeping import normalize_token_metrics
from ..parameter_manager import (
    get_parameter_type, is_per_core_parameter, get_parameter_description,
    get_parameter_dependencies
)
from .base import TunerInterface, TunerResponse

logger = logging.getLogger(__name__)


class LLMHTTPStatusError(RuntimeError):
    """Fatal error when the LLM backend returns a non-200 HTTP status."""

    def __init__(self, backend: str, status_code: int, detail: Optional[str] = None):
        message = f"{backend} LLM API returned HTTP {status_code}"
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)
        self.backend = backend
        self.status_code = status_code


class LLMTimeoutExhaustedError(RuntimeError):
    """Fatal error when LLM requests time out repeatedly."""

    def __init__(self, max_timeouts: int, backend: str, model_name: str):
        super().__init__(
            f"LLM request timed out {max_timeouts} consecutive times "
            f"(backend={backend}, model={model_name})"
        )
        self.max_timeouts = max_timeouts
        self.backend = backend
        self.model_name = model_name

# --- LLM-only API logging (independent of other logging) ---
# Set to True to log exact request/response for each LLM API call to a dedicated file.
LLM_API_LOG_ENABLED = True
# Logs are written under config.results_dir, one file per request with timestamp.
# If config.llm_api_log_dir is set, it overrides results_dir for log output.
# Format: llm_api_{agent_type}_{YYYYMMDD_HHMMSS_mmm}.txt (e.g., llm_api_single_20260205_143022_123.txt)
LLM_API_LOG_FILENAME_PATTERN = "llm_api_{agent}_{timestamp}.txt"

# --- Feature Flags ---
# Number of top configurations to include in the context (only for the latest window). Set to 0 to disable.
LLM_INCLUDE_TOP_N_CONFIGS = 3
# Whether to include workload description in the prompt (requires 'workload_description' in config).
LLM_INCLUDE_WORKLOAD_DESCRIPTION = False
# Whether to include parameter dependencies in the prompt.
LLM_INCLUDE_PARAM_DEPENDENCIES = False
# Whether to include the justification/reasoning in the history entries (default False)
LLM_INCLUDE_JUSTIFICATION_IN_HISTORY = False
# Try to import google.generativeai for Gemini API
try:
    from google import genai
    from google.genai import types
    GOOGLE_GENAI_AVAILABLE = True
except ImportError:
    GOOGLE_GENAI_AVAILABLE = False
    genai = None
    types = None

# Try to import openai SDK for OpenRouter
try:
    import openai as openai_sdk
    OPENAI_SDK_AVAILABLE = True
except ImportError:
    OPENAI_SDK_AVAILABLE = False
    openai_sdk = None


class LLMTuner(TunerInterface):
    """LLM-based tuner using Gemini API with conversational mode."""
    
    def __init__(self, config, agent_type: str = "single"):
        """Initialize LLM tuner.

        Args:
            config: Configuration object with LLM settings
            agent_type: "single", "quick", or "reasoning"
        """
        self.config = config
        self.agent_type = agent_type
        # Filter parameter_ranges based on parameters_to_tune
        if config.parameters_to_tune is not None:
            self.parameter_ranges = {
                k: v for k, v in config.parameter_ranges.items()
                if k in config.parameters_to_tune
            }
        else:
            self.parameter_ranges = config.parameter_ranges
        self.fixed_parameters = getattr(config, 'fixed_parameters', {})
        self.optimization_metric = config.optimization_metric
        self.optimization_goal = config.optimization_goal
        self._hide_primary_metric_value_only = bool(
            getattr(config, 'llm_hide_primary_metric_value', False)
        )
        self._globally_hide_primary_metric = bool(
            getattr(config, 'llm_hide_primary_metric', False)
        )
        
        # LLM configuration
        if agent_type == "quick":
            # Speculator: prefer explicit llm_speculator_model, fall back to llm_secondary_model
            self.model_name = getattr(config, 'llm_speculator_model', None) or config.llm_secondary_model
        elif agent_type == "reasoning":
            # Actor: prefer explicit llm_actor_model, fall back to llm_model_name
            self.model_name = getattr(config, 'llm_actor_model', None) or config.llm_model_name
        else:
            # Single-loop: always use llm_model_name
            self.model_name = config.llm_model_name
        
        # Hardcoded LLM settings (can be overridden via config)
        self.temperature = getattr(config, "llm_temperature", 1.0)
        self.llm_request_max_retries = max(1, int(getattr(config, "llm_request_max_retries", 10)))
        self.llm_request_retry_backoff_sec = max(0.0, float(getattr(config, "llm_request_retry_backoff_sec", 1.0)))
        self.llm_request_timeout_seconds = self._coerce_timeout_seconds(
            getattr(config, "llm_request_timeout_seconds", None)
        )
        self.llm_final_freeze_max_retries = max(
            1, int(getattr(config, "llm_final_freeze_max_retries", 3))
        )
        self.llm_final_freeze_timeout_seconds = self._coerce_timeout_seconds(
            getattr(config, "llm_final_freeze_timeout_seconds", 240.0)
        )
        self.llm_gist_max_retries = max(1, int(getattr(config, "llm_gist_max_retries", 3)))
        self.llm_gist_timeout_seconds = self._coerce_timeout_seconds(
            getattr(config, "llm_gist_timeout_seconds", 240.0)
        )
        
        # Detect backend: Gemini vs OpenRouter
        self._use_gemini = self.model_name.startswith("gemini-")
        self._openrouter_legacy_sdk = False
        
        # Thinking configuration (Gemini-only):
        #   Gemini 3: uses thinking_level ("low", "medium", "high"); high is default
        #   Gemini 2.5: uses thinking_budget (int token count)
        # Both are supported for backward compat; thinking_level takes priority if set
        # For OpenRouter models, thinking config is silently ignored.
        self.thinking_level = getattr(config, "llm_thinking_level", None)
        self.thinking_budget = getattr(config, "llm_thinking_budget", None)
        self.openrouter_max_tokens = max(1, int(getattr(config, "llm_openrouter_max_tokens", 2048)))
        
        # Default thinking config per model family (Gemini only)
        if self._use_gemini and self.thinking_level is None and self.thinking_budget is None:
            if "gemini-3" in self.model_name:
                self.thinking_level = "high"
            else:
                self.thinking_budget = 5000

        # Local trial history so we can build compact summaries
        self.trial_history = []  # list of {"iteration", "params", "reward", "metrics"}
        self._last_recorded_iteration: Optional[int] = None
        
        # Replay configuration
        self.replay_history_file = config.llm_replay_file
        self.replay_history = None
        self.replay_by_iteration = {}
        if self.replay_history_file:
            self._load_replay_history()

        # LLM-only API log (independent of other logging): one file per iteration under results_dir
        self._llm_api_log_enabled = getattr(config, "llm_api_log_enabled", LLM_API_LOG_ENABLED)
        self._llm_api_log_dir = getattr(config, "llm_api_log_dir", None) or getattr(config, "results_dir", ".")
        
        # Conversation history (only for reasoning agent)
        # No max history limit (unlimited)
        self.conversation_history = []
        self.raw_conversation_history = []
        
        # Additional metrics to expose to LLM beyond the optimization metric.
        # Defaults preserve legacy behavior unless per-agent fields are provided.
        actor_metrics = getattr(config, 'llm_actor_additional_metrics', None)
        spec_metrics = getattr(config, 'llm_speculator_additional_metrics', None)
        default_metrics = getattr(config, 'llm_additional_metrics', None)
        if self.agent_type == "quick" and spec_metrics is not None:
            self._additional_metrics = spec_metrics
        elif self.agent_type == "reasoning" and actor_metrics is not None:
            self._additional_metrics = actor_metrics
        else:
            self._additional_metrics = default_metrics or []

        # Optional dual-loop mode where the speculator does not see the primary app metric.
        self._speculator_hide_primary_metric = bool(
            self.agent_type == "quick"
            and getattr(config, 'llm_speculator_hide_primary_metric', False)
        )
        
        # Store justifications by iteration for history summary
        self._justifications = {}
        
        # Initialize API client if not replaying
        if self.replay_history_file:
            logger.info("Replay mode enabled: LLM requests will be simulated from history")
            self.client = None
        elif self._use_gemini:
            # Gemini backend
            if not GOOGLE_GENAI_AVAILABLE:
                raise RuntimeError(
                    f"Gemini model '{self.model_name}' requires google-genai. "
                    f"Install with: pip install google-genai"
                )
            self.api_key = self._normalize_api_key(os.getenv("GEMINI_API_KEY"))
            if not self.api_key:
                raise RuntimeError("Gemini API key not provided (set GEMINI_API_KEY)")
            self.client = genai.Client(api_key=self.api_key)
            logger.info(f"Initialized Gemini LLM tuner ({agent_type}): {self.model_name}")
        else:
            # OpenRouter backend via OpenAI SDK
            if not OPENAI_SDK_AVAILABLE:
                raise RuntimeError(
                    f"OpenRouter model '{self.model_name}' requires the OpenAI Python SDK. "
                    f"Install with: pip install openai"
                )
            self.api_key = self._normalize_api_key(os.getenv("OPENROUTER_API_KEY"))
            if not self.api_key:
                raise RuntimeError("OpenRouter API key not provided (set OPENROUTER_API_KEY)")
            if hasattr(openai_sdk, "OpenAI"):
                self.client = openai_sdk.OpenAI(
                    api_key=self.api_key,
                    base_url="https://openrouter.ai/api/v1"
                )
            else:
                # Compatibility path for older openai<1.x installs.
                openai_sdk.api_key = self.api_key
                openai_sdk.api_base = "https://openrouter.ai/api/v1"
                self.client = openai_sdk
                self._openrouter_legacy_sdk = True
            logger.info(f"Initialized OpenRouter LLM tuner ({agent_type}): {self.model_name}")

    def _hide_primary_metric_for_this_agent(self) -> bool:
        """Whether this tuner instance should hide the primary optimization metric."""
        return self._globally_hide_primary_metric or self._speculator_hide_primary_metric

    def _hide_primary_metric_value_for_this_agent(self) -> bool:
        """Whether this tuner instance should hide only the primary metric value."""
        return (
            self._hide_primary_metric_for_this_agent()
            or self._hide_primary_metric_value_only
            or getattr(self.config, 'use_indirect_optimization', False)
        )

    def _display_metric_name(self) -> str:
        """User-visible label for the optimization metric in prompts/history."""
        if self._hide_primary_metric_for_this_agent():
            return "primary_metric"
        return str(self.optimization_metric)

    def _task_metric_reference(self) -> str:
        """Task-line reference for the optimization metric."""
        if self._hide_primary_metric_for_this_agent():
            return "the HIDDEN primary optimization metric"
        return str(self.optimization_metric)

    def _get_indirect_prompt_style(self) -> str:
        """Return normalized prompt style for indirect optimization instructions."""
        raw_style = getattr(self.config, "llm_indirect_prompt_style", "signature_compare")
        style = str(raw_style or "signature_compare").strip().lower()
        aliases = {
            "signature": "signature_compare",
            "signature_explicit": "signature_compare",
            "plain": "all_metrics_plain",
            "all_metrics": "all_metrics_plain",
        }
        style = aliases.get(style, style)
        if style not in {"signature_compare", "all_metrics_plain"}:
            logger.warning(
                "Unknown llm_indirect_prompt_style=%s; falling back to signature_compare",
                raw_style,
            )
            style = "signature_compare"
        return style

    def _omit_explicit_pairwise_comparison_instruction(self) -> bool:
        """Whether to omit the explicit pairwise/full-signature comparison sentence."""
        raw_mode = getattr(self.config, "omit_explicit_pairwise_comparison_instruction", False)
        try:
            mode_int = int(raw_mode)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid omit_explicit_pairwise_comparison_instruction=%s; expected false/0 or 2. Keeping default behavior.",
                raw_mode,
            )
            return False
        return mode_int == 2

    @staticmethod
    def _extract_http_status_code(obj: Any, _depth: int = 0) -> Optional[int]:
        """Best-effort extraction of HTTP status code from SDK responses/exceptions."""
        if _depth > 4:
            return None

        if obj is None:
            return None

        for attr in ("status_code", "http_status", "code"):
            try:
                value = getattr(obj, attr)
            except Exception:
                value = None
            if isinstance(value, int) and 100 <= value <= 599:
                return value
            if isinstance(value, str) and value.isdigit():
                parsed = int(value)
                if 100 <= parsed <= 599:
                    return parsed

        if isinstance(obj, dict):
            for key in ("status_code", "http_status", "code"):
                value = obj.get(key)
                if isinstance(value, int) and 100 <= value <= 599:
                    return value
                if isinstance(value, str) and value.isdigit():
                    parsed = int(value)
                    if 100 <= parsed <= 599:
                        return parsed

        for nested_attr in ("response", "http_response"):
            try:
                nested = getattr(obj, nested_attr)
            except Exception:
                nested = None
            if nested is not None:
                nested_status = LLMTuner._extract_http_status_code(nested, _depth=_depth + 1)
                if nested_status is not None:
                    return nested_status

        return None

    @staticmethod
    def _is_timeout_error(exc: Exception) -> bool:
        """Best-effort detection for timeout-like request errors."""
        timeout_types = (TimeoutError, socket.timeout)
        if isinstance(exc, timeout_types):
            return True

        timeout_markers = (
            "timeout",
            "timed out",
            "read timeout",
            "connect timeout",
            "request timeout",
            "deadline exceeded",
        )

        # Walk cause/context chain to catch wrapped SDK exceptions.
        cur: Optional[BaseException] = exc
        seen = set()
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            name = cur.__class__.__name__.lower()
            msg = str(cur).lower()
            if "timeout" in name:
                return True
            if any(marker in msg for marker in timeout_markers):
                return True
            cur = cur.__cause__ or cur.__context__

        return False

    @staticmethod
    def _describe_exception_chain(exc: Exception, max_depth: int = 4) -> str:
        """Return a compact exception/cause chain for logging."""
        parts: List[str] = []
        cur: Optional[BaseException] = exc
        depth = 0
        while cur is not None and depth < max_depth:
            name = type(cur).__name__
            message = str(cur).strip()
            parts.append(f"{name}: {message}" if message else name)
            cur = cur.__cause__ or cur.__context__
            depth += 1
        return " <- ".join(parts)

    @staticmethod
    def _normalize_api_key(raw_key: Optional[str]) -> Optional[str]:
        """Strip whitespace from API keys copied across wrapped lines."""
        if raw_key is None:
            return None
        normalized = "".join(str(raw_key).split())
        return normalized or None

    @staticmethod
    def _coerce_timeout_seconds(raw_timeout: Optional[Any]) -> Optional[float]:
        """Normalize optional timeout values."""
        if raw_timeout is None:
            return None
        timeout_seconds = float(raw_timeout)
        if timeout_seconds <= 0:
            raise ValueError(f"Timeout must be positive when provided, got {raw_timeout}")
        return timeout_seconds

    @staticmethod
    def _seconds_to_milliseconds(timeout_seconds: Optional[float]) -> Optional[int]:
        """Convert timeout seconds to SDK milliseconds."""
        if timeout_seconds is None:
            return None
        return max(1, int(round(timeout_seconds * 1000.0)))

    def _resolve_request_policy(self, *,
                                final_freeze_request: bool = False,
                                gist_request: bool = False) -> tuple[str, int, Optional[float]]:
        """Return request label, retry budget, and per-attempt timeout."""
        label = "LLM request"
        max_retries = self.llm_request_max_retries
        timeout_seconds = self.llm_request_timeout_seconds

        if gist_request:
            label = "gist generation"
            max_retries = self.llm_gist_max_retries
            if self.llm_gist_timeout_seconds is not None:
                timeout_seconds = self.llm_gist_timeout_seconds
        elif final_freeze_request:
            label = "final convergence request"
            max_retries = self.llm_final_freeze_max_retries
            if self.llm_final_freeze_timeout_seconds is not None:
                timeout_seconds = self.llm_final_freeze_timeout_seconds

        return label, max_retries, timeout_seconds

    def get_final_freeze_wait_timeout_seconds(self) -> float:
        """Maximum wall-clock wait for the final convergence/freeze request future."""
        _, max_retries, timeout_seconds = self._resolve_request_policy(final_freeze_request=True)
        per_attempt_timeout = timeout_seconds if timeout_seconds is not None else 300.0
        total_backoff = self.llm_request_retry_backoff_sec * max(0, max_retries - 1)
        # Keep a small buffer for SDK/client cleanup around the last attempt.
        return (per_attempt_timeout * max_retries) + total_backoff + 30.0

    def _call_llm_with_retries(self, contents, response_schema_dict, iteration,
                               request_timestamp=None, aggregation_interval_s=None,
                               request_label: str = "LLM request",
                               max_retries: Optional[int] = None,
                               request_timeout_seconds: Optional[float] = None,
                               temperature: Optional[float] = None,
                               max_output_tokens: Optional[int] = None,
                               allow_thinking: bool = True):
        """Call LLM backend with retry-on-failure behavior.

        Retries request failures up to llm_request_max_retries.
        If timeout failures occur llm_request_max_retries consecutive times,
        raises LLMTimeoutExhaustedError (fatal).
        """
        backend_name = "Gemini" if self._use_gemini else "OpenRouter"
        last_error: Optional[Exception] = None
        effective_max_retries = max_retries if max_retries is not None else self.llm_request_max_retries
        consecutive_timeouts = 0

        for attempt in range(1, effective_max_retries + 1):
            try:
                if self._use_gemini:
                    result = self._call_gemini(
                        contents, response_schema_dict, iteration,
                        request_timestamp, aggregation_interval_s,
                        request_timeout_seconds=request_timeout_seconds,
                        temperature=temperature,
                        max_output_tokens=max_output_tokens,
                        allow_thinking=allow_thinking,
                    )
                else:
                    result = self._call_openrouter(
                        contents, response_schema_dict, iteration,
                        request_timestamp, aggregation_interval_s,
                        request_timeout_seconds=request_timeout_seconds,
                        temperature=temperature,
                        max_output_tokens=max_output_tokens,
                    )
                # Any successful call breaks the consecutive-timeout streak.
                consecutive_timeouts = 0
                if attempt > 1:
                    logger.info(
                        "%s recovered on attempt %d/%d (%s, model=%s)",
                        request_label,
                        attempt,
                        effective_max_retries,
                        backend_name,
                        self.model_name,
                    )
                return result
            except Exception as e:
                last_error = e
                error_details = self._describe_exception_chain(e)

                if isinstance(e, LLMHTTPStatusError):
                    # Retry non-200 HTTP responses up to max retries.
                    consecutive_timeouts = 0
                    logger.warning(
                        "%s got a non-200 HTTP response on attempt %d/%d (%s, model=%s): %s",
                        request_label,
                        attempt,
                        effective_max_retries,
                        backend_name,
                        self.model_name,
                        error_details,
                    )
                elif self._is_timeout_error(e):
                    consecutive_timeouts += 1
                    logger.warning(
                        "%s timed out on attempt %d/%d (%s, model=%s); consecutive_timeouts=%d; details=%s",
                        request_label,
                        attempt,
                        effective_max_retries,
                        backend_name,
                        self.model_name,
                        consecutive_timeouts,
                        error_details,
                    )
                    if consecutive_timeouts >= effective_max_retries:
                        raise LLMTimeoutExhaustedError(
                            effective_max_retries, backend_name, self.model_name
                        ) from e
                else:
                    # Only consecutive timeout failures count toward fatal exhaustion.
                    consecutive_timeouts = 0
                    logger.warning(
                        "%s failed on attempt %d/%d (%s, model=%s): %s",
                        request_label,
                        attempt,
                        effective_max_retries,
                        backend_name,
                        self.model_name,
                        error_details,
                    )

                if attempt >= effective_max_retries:
                    break

                if self.llm_request_retry_backoff_sec > 0:
                    time.sleep(self.llm_request_retry_backoff_sec)

        assert last_error is not None
        raise last_error
    
    def _load_replay_history(self) -> None:
        """Load replay history from JSON file."""
        try:
            with open(self.replay_history_file, 'r') as f:
                self.replay_history = json.load(f)
            for entry in self.replay_history.get('history', []):
                it = entry.get('iteration')
                if it is not None:
                    self.replay_by_iteration[it] = entry
            logger.info(f"Loaded replay history with {len(self.replay_by_iteration)} iterations")
        except Exception as e:
            logger.error(f"Failed to load replay history: {e}")
            self.replay_history = None
            self.replay_by_iteration = {}

    def _write_llm_api_log(self, iteration: int,
                           request_contents: List[str],
                           request_config: Dict[str, Any],
                           response_text: Optional[str],
                           response_parsed: Any,
                           response_raw: Any = None,
                           request_timestamp: Optional[float] = None,
                           aggregation_interval_s: Optional[float] = None) -> None:
        """Write exact request/response to a timestamped file under results_dir."""
        if not self._llm_api_log_enabled:
            return
        try:
            from datetime import datetime
            os.makedirs(self._llm_api_log_dir, exist_ok=True)
            
            # Use provided timestamp or current time
            ts = request_timestamp if request_timestamp else time.time()
            dt = datetime.fromtimestamp(ts)
            timestamp_str = dt.strftime("%Y%m%d_%H%M%S") + f"_{int((ts % 1) * 1000):03d}"
            
            log_file = os.path.join(
                self._llm_api_log_dir,
                LLM_API_LOG_FILENAME_PATTERN.format(agent=self.agent_type, timestamp=timestamp_str)
            )
            with open(log_file, "w", encoding="utf-8") as f:
                f.write("=" * 60 + "\n")
                f.write(f" LLM API REQUEST - {self.agent_type.upper()} AGENT\n")
                f.write(f" Iteration: {iteration}\n")
                f.write(f" Timestamp: {dt.strftime('%Y-%m-%d %H:%M:%S.%f')}\n")
                if aggregation_interval_s is not None:
                    f.write(f" Metrics Aggregation Interval: {aggregation_interval_s:.2f}s\n")
                f.write("=" * 60 + "\n\n")
                f.write("--- REQUEST (exact context sent to LLM API) ---\n\n")
                f.write("contents (list of strings, in order):\n")
                for i, part in enumerate(request_contents):
                    f.write(f"\n--- content[{i}] ---\n")
                    f.write(part)
                    f.write("\n")
                f.write("\n--- generation_config ---\n")
                f.write(json.dumps(request_config, indent=2, default=str))
                f.write("\n\n--- RESPONSE (exact data returned from LLM API) ---\n\n")
                if response_text is not None:
                    f.write("response.text:\n")
                    f.write(response_text)
                    f.write("\n\n")
                if response_parsed is not None:
                    f.write("response.parsed:\n")
                    f.write(json.dumps(response_parsed, indent=2, default=str))
                    f.write("\n\n")
                if response_raw is not None:
                    f.write("response (raw repr):\n")
                    f.write(repr(response_raw))
                    f.write("\n")
        except Exception as e:
            logger.warning(f"Failed to write LLM API log: {e}")

    def _create_base_prompt(self) -> str:
        """Create the base system prompt."""
        
        # Check if we should include parameter descriptions
        include_descriptions = getattr(self.config, 'include_param_descriptions', True)
        
        # Format parameter info
        continuous_params = []
        categorical_params = []
        
        for param_name, param_range in self.parameter_ranges.items():
            param_type = get_parameter_type(param_name)
            is_per_core = is_per_core_parameter(param_name)
            desc = get_parameter_description(param_name)
            
            param_info = f"- {param_name}"
            
            if isinstance(param_range, tuple):
                # Continuous/discrete range
                min_val, max_val = param_range
                param_info += f" ({param_type}): {min_val:,} to {max_val:,}"
            elif isinstance(param_range, list):
                # Categorical
                valid_vals = ", ".join(str(v) for v in param_range)
                param_info += f" (categorical): [{valid_vals}]"
                
            if is_per_core:
                param_info += " [PER-CORE]"
            
            if include_descriptions and desc:
                param_info += f" - {desc}"
                
            if isinstance(param_range, tuple):
                continuous_params.append(param_info)
            else:
                categorical_params.append(param_info)
        
        params_section = ""
        if continuous_params:
            params_section += "\nCONTINUOUS/DISCRETE PARAMETERS:\n" + "\n".join(continuous_params) + "\n"
        if categorical_params:
            params_section += "\nCATEGORICAL PARAMETERS:\n" + "\n".join(categorical_params) + "\n"

        if self._use_minimal_prompt_mode():
            return (
                f"Goal: {self.config.optimization_goal.lower()} {self._task_metric_reference()} "
                "by adjusting these parameters within their ranges.\n"
                f"Parameters:\n{self._format_prompt_parameter_ranges(include_descriptions=False)}\n"
                "Reply with JSON only."
            )
            
        # Fixed parameters
        fixed_params_str = ""
        if self.fixed_parameters:
            fixed_params_str = "\nFIXED PARAMETERS:"
            for param_name, value in self.fixed_parameters.items():
                val_str = f"{value:,}" if isinstance(value, (int, float)) else str(value)
                fixed_params_str += f"\n- {param_name}: {val_str} (kept constant)"

        # Role description (Multi-agent vs Single)
        role_description = ""
        if self.agent_type == "quick":
            role_description = """
MULTI-AGENT ROLE: You are the Speculator in a MULTI-AGENT System.
Your role is to provide immediate, intuitive parameter recommendations for each window. You work alongside an Actor that performs deeper analysis.
"""
        elif self.agent_type == "reasoning":
            role_description = """
MULTI-AGENT ROLE: You are the Actor in a MULTI-AGENT System.
Your role is to provide thoughtful, well-analyzed parameter recommendations. You work alongside a Speculator that explores the parameter space rapidly. You will receive accumulated results from multiple agent calls to perform deeper analysis and identify trends.
"""

        # Strategy section from old prompt (adapted)
        if getattr(self.config, "llm_explore_until_last_iteration", False):
            strategy = """
OPTIMIZATION STRATEGY:
1. THROUGHOUT THE TUNING BUDGET: keep EXPLORING. Continue testing safe, informative alternatives across the budget instead of converging early.
2. FINAL TUNING CALL ONLY: converge to the strongest robust region you have observed and treat that final recommendation as the frozen candidate for the stable phase.
3. If a new configuration yields significantly better results, react by probing nearby values without prematurely stopping exploration.
4. Monitor for workload changes (e.g., sudden goodput drop or latency spike).
5. Avoid catastrophic settings, but do not mistake one good measurement for final convergence.
6. Always consider noise - don't overreact to single measurements."""
        else:
            strategy = """
OPTIMIZATION STRATEGY:
1. EARLY CYCLES: Prioritize EXPLORATION - try diverse values across the full range to map the space but avoid values that will likely be catastrophic.
2. LATER CYCLES: Shift to EXPLOITATION - refine around promising regions.
3. If a new configuration yields significantly better results, react by exploring nearby values.
4. Monitor for workload changes (e.g., sudden goodput drop or latency spike).
5. Aim for stability: After finding a good region, test variations to confirm robustness.
6. Always consider noise - don't overreact to single measurements."""

        # Task and Measurements
        optimization_goal = self.config.optimization_goal.upper()
        optimization_metric = self._task_metric_reference()
        
        # Constraints
        constraints_section = ""
        has_constraints = bool(
            getattr(self.config, 'constraint_metric', None)
            and self.config.constraint_threshold is not None
        )
        if has_constraints:
            c_metric = self.config.constraint_metric
            c_threshold = self.config.constraint_threshold
            c_direction = getattr(self.config, 'constraint_direction', 'less_than')
            
            op_symbol = "<"
            if c_direction in ["gt", "greater_than", ">"]:
                op_symbol = ">"
            
            task = f"TASK: Optimize OS parameters to {optimization_goal} {optimization_metric} with respect to the Constraints."
            constraints_section = (
                f"\nCONSTRAINTS:\n"
                f"- {c_metric} {op_symbol} {c_threshold}\n"
                f"- Respecting constraints is MORE IMPORTANT than the optimization goal. "
                f"If you improve {optimization_metric} but violate the constraint, it does not count as an improvement.\n"
                f"- If the constraint is violated and you cannot avoid it, a SMALLER violation is still better than a larger one. "
                # f"Always try to move {c_metric} closer to the threshold even when you cannot fully satisfy the constraint."
            )
        else:
            task = f"TASK: Optimize OS parameters to {optimization_goal} {optimization_metric}."
        
        # Note about additional metrics
        if self._additional_metrics:
            use_indirect = getattr(self.config, 'use_indirect_optimization', False)
            task += f" Additional observability metrics included: {', '.join(self._additional_metrics)}."

            if use_indirect:
                indirect_style = self._get_indirect_prompt_style()
                if self._omit_explicit_pairwise_comparison_instruction():
                    # Mode 2: keep metric list, omit explicit instruction sentence(s).
                    task += (
                        " IMPORTANT: The primary optimization metric is NOISY or HIDDEN."
                    )
                elif indirect_style == "all_metrics_plain":
                    task += (
                        " IMPORTANT: The primary optimization metric is HIDDEN for this run. "
                        "Use the additional observability metrics as indirect evidence to decide whether each "
                        "configuration change is likely helping or hurting the optimization objective. "
                        "Prioritize robust trends and avoid overreacting to single noisy measurements."
                    )
                else:
                    task += (
                        " IMPORTANT: The primary optimization metric is NOISY or HIDDEN. "
                        "You MUST rely on the 'Metric Signature' (the correlation of all additional metrics "
                        "like IPC, power, cache misses, etc.) to infer whether a configuration change was beneficial. "
                        "Explicitly compare the full signature against past iterations to identify which signatures "
                        "were associated with stronger performance, and steer the next configuration toward those "
                        "higher-quality signatures even when the primary metric fluctuates."
                    )
            else:
                if getattr(self.config, 'llm_additional_metrics_dump_only', False):
                    pass
                elif getattr(self.config, 'llm_full_metrics_prompt_mode', False):
                    if has_constraints:
                        task += (
                            " The tuned metric's performance remains the primary optimization goal "
                            "(constraints take priority). "
                            "You may use any provided metrics as supporting evidence for trend confirmation, "
                            "anomaly detection, and tradeoff awareness."
                        )
                    else:
                        task += (
                            " The tuned metric's performance remains the primary optimization goal. "
                            "You may use any provided metrics as supporting evidence for trend confirmation, "
                            "anomaly detection, and tradeoff awareness."
                        )
                    if getattr(self.config, 'llm_full_metrics_explicit_signature_compare', False):
                        task += (
                            " Treat the 'Metric Signature' as the correlation pattern across the additional "
                            "observability metrics (for example IPC, power, cache misses, and related signals). "
                            "Explicitly compare the full metric signature against past iterations to identify "
                            "which signatures were associated with stronger primary-metric outcomes, and steer "
                            "toward those higher-quality signatures while still optimizing the primary objective."
                        )
                else:
                    task += " If the primary metric is not directly available, use these as indirect signals to guide optimization."

        # Optional Workload Description
        if LLM_INCLUDE_WORKLOAD_DESCRIPTION:
            workload_desc = getattr(self.config, 'workload_description', None)
            if workload_desc:
                task += f" Workload: {workload_desc}."
        
        measurements = "MEASUREMENTS: The workload performance metrics are NOISY due to system variability."
        if getattr(self.config, 'stability_threshold', 1) > 1:
            measurements += f" The same parameters are tested for {self.config.stability_threshold} iterations to mitigate noise."

        # Parameter Dependencies
        dependencies_section = ""
        if LLM_INCLUDE_PARAM_DEPENDENCIES:
            deps_list = []
            for param in self.parameter_ranges:
                deps = get_parameter_dependencies(param)
                if deps:
                    # Only show dependencies that are relevant (tuned or fixed)
                    # For now, show all listed dependencies to be safe
                    deps_str = ", ".join(deps)
                    deps_list.append(f"- {param} is related to: {deps_str}")
            
            if deps_list:
                dependencies_section = "\nPARAMETER DEPENDENCIES:\n" + "\n".join(deps_list) + "\n"

        # Previous Run Gist
        gist_section = ""
        previous_run_gist = getattr(self.config, 'previous_run_gist', None)
        if previous_run_gist:
            gist_text = str(previous_run_gist).strip()
            if gist_text:
                gist_section = f"""
SUMMARY OF PREVIOUS TUNING SESSION OF A SIMILAR WORKLOAD:
Treat this as a strong prior for initialization and early exploration. Reuse stable parameter relationships it identifies, but let the current measurements override it if this run behaves differently.
"{gist_text}"
"""

        prompt = f"""You are a Linux kernel scheduler tuning expert with deep knowledge of OS performance optimization.
{role_description}
{task}
{constraints_section}
{measurements}
{gist_section}
TUNABLE PARAMETERS:
{params_section}{fixed_params_str}
{dependencies_section}
{strategy}

Performance data will be provided in future calls. Respond ONLY in the format shown below:
Analysis: <Your one or two-sentence decision reasoning>
Config: {{ "parameter_name": <value> }}"""
        return prompt

    def generate_gist(self, history: List[Dict[str, Any]]) -> tuple[str, Any]:
        """Generate a summary 'gist' of the optimization run.
        
        Returns:
            Tuple of (summary_text, full_response_object)
        """
        if not history:
            return "No history available.", None

        hide_reward = bool(
            getattr(self.config, "llm_additional_metrics_dump_only", False)
            or getattr(self.config, "llm_hide_primary_metric_value", False)
            or getattr(self.config, "use_indirect_optimization", False)
        )

        # Re-use _build_history_summary but with all available data
        # We can just format the history nicely
        history_text = ""
        for i, entry in enumerate(history):
            # Use concise formatting
            metrics = entry.get('metrics', {})
            reward = entry.get('reward', 'N/A')
            params = self._short_params_for_history(entry.get('parameters', {}))

            visible_metric_parts = []
            if self._additional_metrics:
                for metric_name in self._additional_metrics:
                    if metric_name == self.optimization_metric and hide_reward:
                        continue
                    value = metrics.get(metric_name)
                    if value is None:
                        continue
                    if isinstance(value, (int, float)):
                        visible_metric_parts.append(f"{metric_name}={value:,.2f}")
                    else:
                        visible_metric_parts.append(f"{metric_name}={value}")
            metrics_str = f" | Metrics: {', '.join(visible_metric_parts)}" if visible_metric_parts else ""
            
            cmds = ""
            if 'command_results' in entry:
                 cmds = " Commands: " + "; ".join([c.get('command') for c in entry['command_results']])
            
            tuner_timing = entry.get('tuner_timing', {})
            justification = entry.get('llm_justification') or tuner_timing.get('justification') or entry.get('justification', '')
            
            if hide_reward:
                history_text += f"Iter {entry['iteration']}: Params: {params}{metrics_str}{cmds}\n"
            else:
                history_text += f"Iter {entry['iteration']}: Reward={reward} | Params: {params}{metrics_str}{cmds}\n"
            if justification:
                history_text += f"  Analysis: {justification}\n"

        reward_visibility_note = ""
        if hide_reward:
            reward_visibility_note = (
                "The direct optimization reward / primary objective values are intentionally withheld in this history. "
                "Use only the provided observability metrics, parameter choices, and action patterns.\n\n"
            )

        prompt = f"""Analyze the following optimization history for a Linux kernel tuning session.
Identify the key patterns, which parameters had the biggest impact (positive or negative), and what the optimal configuration appears to be.

Based on this analysis, provide concise and informative takeaways for making **future runs more efficient**.
For example: suggest narrower parameter ranges to focus on, specific parameters that seem irrelevant and can be fixed, or a starting configuration that is close to the optimum.

Do NOT output any JSON, just the text summary.

{reward_visibility_note}HISTORY:
{history_text}

SUMMARY:"""

        try:
            request_label, max_retries, timeout_seconds = self._resolve_request_policy(gist_request=True)
            gist_system_prompt = (
                "You summarize completed Linux tuning runs. "
                "Return only the concise text summary requested by the user prompt."
            )
            response_text, parsed_data, _response_time, log_config, raw_response, token_usage = (
                self._call_llm_with_retries(
                    [gist_system_prompt, prompt],
                    None,
                    history[-1].get('iteration', len(history)),
                    request_label=request_label,
                    max_retries=max_retries,
                    request_timeout_seconds=timeout_seconds,
                    temperature=0.7,
                    max_output_tokens=8192 if self._use_gemini else self.openrouter_max_tokens,
                    allow_thinking=False,
                )
            )
            final_text = (response_text or (json.dumps(parsed_data) if parsed_data is not None else "")).strip()
            if not final_text:
                raise RuntimeError("Gist generation returned an empty response")
            response_data = {
                "backend": "gemini" if self._use_gemini else "openrouter",
                "model": self.model_name,
                "response_model": log_config.get("response_model"),
                "text": final_text,
                "token_usage": token_usage,
            }
            if hasattr(raw_response, "usage_metadata"):
                usage_metadata = getattr(raw_response, "usage_metadata", None)
                if usage_metadata is not None:
                    response_data["usage_metadata"] = {
                        "prompt_token_count": getattr(usage_metadata, "prompt_token_count", None),
                        "candidates_token_count": getattr(usage_metadata, "candidates_token_count", None),
                        "thoughts_token_count": getattr(usage_metadata, "thoughts_token_count", None),
                        "cached_content_token_count": getattr(usage_metadata, "cached_content_token_count", None),
                        "total_token_count": getattr(usage_metadata, "total_token_count", None),
                    }
            return final_text, response_data
        except Exception as e:
            logger.error(f"Failed to generate gist: {e}")
            return "Optimization run completed.", {"error": str(e)}

    def _format_additional_metrics(self, metrics: BenchmarkMetrics) -> str:
        """Format additional metrics as comma-separated string for inline display.
        
        Returns empty string if no additional metrics configured or none available.
        """
        if not self._additional_metrics:
            return ""
        parts = []
        for m in self._additional_metrics:
            val = metrics.get_metric(m)
            if val is not None and val != 0.0:
                parts.append(f"{m}={val:,.2f}" if isinstance(val, (int, float)) else f"{m}={val}")
        if not parts:
            return ""
        return ", " + ", ".join(parts)

    def _use_minimal_prompt_mode(self) -> bool:
        """Whether to use the stripped prompt/update format."""
        return bool(getattr(self.config, "llm_minimal_prompt_mode", False))

    def _format_prompt_parameter_ranges(self, include_descriptions: bool = True) -> str:
        """Render tunable parameter ranges for prompt text."""
        lines: List[str] = []
        for param_name, param_range in self.parameter_ranges.items():
            param_type = get_parameter_type(param_name)
            desc = get_parameter_description(param_name)

            if isinstance(param_range, tuple):
                lo, hi = param_range
                line = f"- {param_name}: [{lo:,}, {hi:,}]"
                if include_descriptions and desc:
                    line += f" ({param_type}; {desc})"
            else:
                values = ", ".join(str(v) for v in param_range)
                line = f"- {param_name}: [{values}]"
                if include_descriptions and desc:
                    line += f" ({desc})"
            lines.append(line)
        return "\n".join(lines)

    def _format_round_progress(self, iteration: int) -> str:
        """Format the current round against the configured tuning budget."""
        total_rounds = int(getattr(self.config, "max_iterations", 0) or 0)
        current_round = iteration + 1
        if total_rounds > 0:
            current_round = min(current_round, total_rounds)
            return f"Round {current_round} of {total_rounds}."
        return f"Round {current_round}."

    def _create_minimal_update_message(self,
                                       metrics: BenchmarkMetrics,
                                       current_params: Dict[str, Any],
                                       iteration: int,
                                       best_reward: float,
                                       history: Optional[List[Dict[str, Any]]] = None,
                                       baseline_index: int = 0,
                                       final_freeze_request: bool = False) -> str:
        """Create a stripped update message for minimal prompt mode."""
        lines: List[str] = []

        lines.append(self._format_round_progress(iteration))
        lines.append(
            f"Current {self._display_metric_name()}: "
            f"{self._format_reward(metrics.get_metric(self.optimization_metric))}"
            f"{self._format_additional_metrics(metrics)}"
        )
        if final_freeze_request:
            lines.append("Converge to one config.")
        lines.append("Reply with JSON only.")
        return "\n".join(line for line in lines if line) + "\n"

    def _format_reward(self, reward) -> str:
        """Format reward value, showing N/A when metric is unavailable (0.0 with additional metrics)."""
        if reward is None:
            return "N/A"

        if self._hide_primary_metric_value_for_this_agent():
            return "HIDDEN"
            
        if reward == 0.0 and self._additional_metrics:
            return "N/A"
        if isinstance(reward, float) and abs(reward) == float('inf'):
            return "N/A"
        return f"{reward:,.2f}"

    def _get_phase_instruction(self, iteration: int) -> str:
        """Return explicit phase guidance for profiles that defer convergence."""
        if not getattr(self.config, "llm_explore_until_last_iteration", False):
            return ""

        final_tuning_iteration = int(getattr(self.config, "max_iterations", 0) or 0)
        if final_tuning_iteration <= 0:
            return ""

        next_call = iteration + 1
        if (
            (
                getattr(self.config, "llm_force_final_freeze_before_stable", False)
                or getattr(self.config, "dual_loop_force_final_actor_before_stable", False)
            )
            and int(getattr(self.config, "post_tuning_windows", 0) or 0) > 0
        ):
            if next_call < final_tuning_iteration:
                return (
                    f"PHASE POLICY FOR call #{next_call}: stay in EXPLORATION mode. "
                    "Do not declare convergence yet and do not permanently lock onto a single incumbent. "
                    "Probe a distinct but safe hypothesis or a targeted nearby variation so the run keeps learning "
                    "throughout the remaining tuning budget.\n"
                )

            if next_call == final_tuning_iteration:
                return (
                    f"PHASE POLICY FOR call #{next_call}: this is the LAST measured tuning-stage candidate. "
                    "Keep exploring safely on this call; do not freeze yet. A separate post-window convergence "
                    "request will choose the one configuration to carry into the stable phase using the completed "
                    "last tuning-window measurement.\n"
                )

            return ""

        if next_call < final_tuning_iteration:
            return (
                f"PHASE POLICY FOR call #{next_call}: stay in EXPLORATION mode. "
                "Do not declare convergence yet and do not permanently lock onto a single incumbent. "
                "Probe a distinct but safe hypothesis or a targeted nearby variation so the run keeps learning "
                "throughout the remaining tuning budget.\n"
            )

        if next_call == final_tuning_iteration:
            return (
                f"PHASE POLICY FOR call #{next_call}: this is the FINAL tuning call. "
                "Converge now to the single most promising robust region seen so far. "
                "Prefer the best safe incumbent or a very nearby refinement; avoid broad exploration because "
                "this configuration is the last tuning-stage candidate.\n"
            )

        return ""

    def _format_history_entry(self, entry: Dict[str, Any], show_additional_metrics: bool = True) -> str:
        """Format a single history entry with details and auto-detected agent tag."""
        metric_label = self._display_metric_name()
        if entry.get("pre_tuning_default_config"):
            reward_str = self._format_reward(entry.get('reward'))
            additional_metrics_str = ""
            if self._additional_metrics and show_additional_metrics:
                entry_metrics = entry.get('metrics', {})
                parts = []
                for m in self._additional_metrics:
                    val = entry_metrics.get(m)
                    if val is not None:
                        parts.append(f"{m}={val:,.2f}" if isinstance(val, (int, float)) else f"{m}={val}")
                if parts:
                    additional_metrics_str = ", " + ", ".join(parts)
            param_str = self._short_params_for_history(entry.get('parameters', entry.get('params', {})))
            return (
                f"  * Initial/OS Default config: {metric_label}={reward_str}"
                f"{additional_metrics_str} with {param_str}"
            )

        # Use helper for params
        param_str = self._short_params_for_history(entry.get('parameters', {}))
        
        # Metric/Reward
        reward_str = self._format_reward(entry.get('reward'))
        
        # Auto-detect agent tag from history entry data (supports old/new dual-loop formats).
        timing_block = entry.get('tuner_timing') or {}
        quick_timing = None
        reasoning_timing = None
        if isinstance(timing_block, dict):
            if 'quick' in timing_block or 'reasoning' in timing_block:
                quick_timing = timing_block.get('quick')
                reasoning_timing = timing_block.get('reasoning')
            elif timing_block.get('tuner_type') == 'speculator_quick':
                quick_timing = timing_block
            elif timing_block.get('tuner_type') == 'actor_reasoning':
                reasoning_timing = timing_block

        legacy_reasoning_timing = entry.get('reasoning_tuner_timing')
        if reasoning_timing is None and isinstance(legacy_reasoning_timing, dict):
            reasoning_timing = legacy_reasoning_timing

        has_actor = bool(reasoning_timing)
        has_speculator = bool(quick_timing)
        
        if has_actor and has_speculator:
            agent_tag = " [Actor+Speculator]"
        elif has_actor:
            agent_tag = " [Actor]"
        elif has_speculator:
            agent_tag = " [Speculator]"
        else:
            agent_tag = ""  # Single-loop or unknown
        
        # Constraints
        constraint_note = ""
        violated = False
        
        if not self._hide_primary_metric_for_this_agent():
            # 1. Check pre-calculated violation (single loop)
            if 'constraint_violated' in entry:
                if entry['constraint_violated']:
                    violated = True
                    c_metric = getattr(self.config, 'constraint_metric', 'CONSTRAINT')
                    details = entry.get('constraint_detail', '')
                    constraint_note = f" [{c_metric}={details} CONSTRAINT VIOLATED]"
            # 2. Calculate on-the-fly (dual loop history)
            elif getattr(self.config, 'constraint_metric', None) and self.config.constraint_threshold is not None:
                metrics = entry.get('metrics', {})
                val = metrics.get(self.config.constraint_metric)
                if val is not None:
                    direction = getattr(self.config, 'constraint_direction', 'less_than')
                    op_symbol = ">="
                    if direction in ["lt", "less_than", "<"]:
                        violated = val >= self.config.constraint_threshold
                        op_symbol = ">="
                    elif direction in ["gt", "greater_than", ">"]:
                        violated = val <= self.config.constraint_threshold
                        op_symbol = "<="
                    
                    if violated:
                        constraint_note = f" [{self.config.constraint_metric}={val:.2f} {op_symbol} {self.config.constraint_threshold:.2f} CONSTRAINT VIOLATED]"

        # Additional metrics inline
        additional_metrics_str = ""
        if self._additional_metrics and show_additional_metrics:
            entry_metrics = entry.get('metrics', {})
            parts = []
            for m in self._additional_metrics:
                val = entry_metrics.get(m)
                if val is not None:
                    parts.append(f"{m}={val:,.2f}" if isinstance(val, (int, float)) else f"{m}={val}")
            if parts:
                additional_metrics_str = ", " + ", ".join(parts)

        # Build line
        call_id = entry.get('iteration', '?')
        line = f"  * iter {call_id}{agent_tag}: {metric_label}={reward_str}{additional_metrics_str} with {param_str}{constraint_note}"
        
        # Justification
        if LLM_INCLUDE_JUSTIFICATION_IN_HISTORY:
            # Collect all justifications with metadata
            justifications = []
            
            # 1. Speculator (Quick)
            if isinstance(quick_timing, dict) and quick_timing.get('justification'):
                justifications.append({
                    'role': 'Speculator',
                    'text': quick_timing['justification'],
                    'time': quick_timing.get('tuner_response_time', 0),
                    'start_iter': None
                })
            
            # 2. Actor (Reasoning)
            if isinstance(reasoning_timing, dict) and reasoning_timing.get('justification'):
                justifications.append({
                    'role': 'Actor',
                    'text': reasoning_timing['justification'],
                    'time': reasoning_timing.get('tuner_response_time', 0),
                    'start_iter': reasoning_timing.get('tuner_start_iteration')
                })
            
            # 3. Fallback (root justification if no details)
            if not justifications and entry.get('justification'):
                justifications.append({
                    'role': 'Agent',
                    'text': entry['justification'],
                    'time': 0,
                    'start_iter': None
                })
            
            # Sort by response time (arrival order)
            justifications.sort(key=lambda x: x['time'])
            
            if justifications:
                line += "\n    Justification:"
                for j in justifications:
                    role_prefix = f" [{j['role']}]"
                    context_suffix = ""
                    if j['role'] == 'Actor' and j['start_iter'] is not None:
                        context_suffix = f" (started at iter {j['start_iter']})"
                    
                    line += f"{role_prefix} {j['text']}{context_suffix}"
                
        return line

    def _create_update_message(self, metrics: BenchmarkMetrics,
                              current_params: Dict[str, Any],
                              iteration: int,
                              best_reward: float,
                              history: Optional[List[Dict[str, Any]]] = None,
                              baseline_index: int = 0,
                              aggregation_interval_s: Optional[float] = None,
                              phase_instruction_override: Optional[str] = None,
                              final_freeze_request: bool = False) -> str:
        """Create conversational update message based on role.
        
        Args:
            metrics: Current benchmark metrics
            current_params: Current parameter values
            iteration: Current iteration number
            best_reward: Best reward seen so far
            history: Optional history for dual-loop mode
            baseline_index: Baseline index for dual-loop mode
            aggregation_interval_s: Optional aggregation interval in seconds
        """
        if self._use_minimal_prompt_mode():
            return self._create_minimal_update_message(
                metrics,
                current_params,
                iteration,
                best_reward,
                history=history,
                baseline_index=baseline_index,
                final_freeze_request=final_freeze_request,
            )
        
        # Default behavior for single loop (no history provided)
        if history is None:
            # Use legacy method if available or build basic update
            return self._create_legacy_update_message(metrics, current_params, iteration, best_reward, aggregation_interval_s)
            
        # Separate compressed (baseline) and recent history
        compressed_history = history[:baseline_index]
        recent_history = history[baseline_index:]
        
        # Current status
        reward = metrics.get_metric(self.optimization_metric)
        reward_str = self._format_reward(reward)
        best_str = self._format_reward(best_reward)
        metric_label = self._display_metric_name()
        current_param_str = ", ".join(f"{k}={v}" for k, v in current_params.items() if k in self.parameter_ranges)
        hide_primary_metric = self._hide_primary_metric_for_this_agent()
        
        # Compact constraint status for CURRENT iteration
        constraint_block = ""
        if (
            not hide_primary_metric
            and getattr(self.config, "constraint_metric", None)
            and self.config.constraint_threshold is not None
        ):
            constraint_value = metrics.get_metric(self.config.constraint_metric)
            direction = getattr(self.config, "constraint_direction", "less_than")
            if direction == "less_than":
                # Handle None constraint value gracefully
                if constraint_value is None:
                     violated = True # Assume violated if missing? Or ignore.
                     threshold_text = f"< {self.config.constraint_threshold:,.2f}"
                     constraint_val_str = "None"
                else:
                    violated = constraint_value >= self.config.constraint_threshold
                    threshold_text = f"< {self.config.constraint_threshold:,.2f}"
                    constraint_val_str = f"{constraint_value:,.2f}"
            else:
                if constraint_value is None:
                     violated = True
                     threshold_text = f"> {self.config.constraint_threshold:,.2f}"
                     constraint_val_str = "None"
                else:
                    violated = constraint_value <= self.config.constraint_threshold
                    threshold_text = f"> {self.config.constraint_threshold:,.2f}"
                    constraint_val_str = f"{constraint_value:,.2f}"
                    
            status = "VIOLATED" if violated else "OK"
            constraint_block = (
                f"Constraint status: {self.config.constraint_metric}="
                f"{constraint_val_str} (threshold {threshold_text}) [{status}]\n"
            )

        # Aggregation interval info
        aggregation_info = ""
        if aggregation_interval_s is not None:
            aggregation_info = f"(mean over past {aggregation_interval_s:.1f}s)"
        
        # Build combined history (all entries tagged with [Actor]/[Speculator])
        all_history = list(compressed_history[-10:]) + list(recent_history)
        
        history_block = ""
        if all_history:
            history_block = "[History]\n"
            
            show_all = getattr(self.config, 'llm_indirect_history_show_all_metrics', True)
            use_indirect = getattr(self.config, 'use_indirect_optimization', False)
            
            for i, entry in enumerate(all_history):
                is_last = (i == len(all_history) - 1)
                is_second_to_last = (i == len(all_history) - 2)
                
                # Logic for showing additional metrics:
                # - If NOT indirect optimization: Always show (default behavior)
                # - If indirect optimization:
                #   - If show_all is True: Show for all
                #   - If show_all is False: Show only for last TWO
                
                should_show = True
                if use_indirect and not show_all and not is_last and not is_second_to_last:
                    should_show = False
                
                history_block += self._format_history_entry(entry, show_additional_metrics=should_show) + "\n"
        
        msg = ""
        if self.agent_type == "quick": # Speculator
            msg += f"Update for Speculator\n"
            msg += history_block
            
            msg += f"\nCURRENT BEST: {metric_label}={best_str}\n"
            additional_str = self._format_additional_metrics(metrics)
            msg += f"Latest Result for call #{iteration}: {current_param_str} -> {metric_label}={reward_str}{additional_str} {aggregation_info}\n"
            if hide_primary_metric:
                msg += "Primary optimization metric name/value are hidden for this run type; rely on provided observability metrics.\n"
            msg += constraint_block
            msg += phase_instruction_override if phase_instruction_override is not None else self._get_phase_instruction(iteration)
            if final_freeze_request:
                msg += "\nPlease provide your analysis and the one configuration to freeze for the stable phase."
            else:
                msg += f"\nPlease provide your analysis and the next configuration for iteration #{iteration + 1}."
            
        elif self.agent_type == "reasoning": # Actor (Reasoning)
            msg += f"Update for Actor\n"
            msg += history_block
            
            msg += f"\nCURRENT BEST: {metric_label}={best_str}\n"
            additional_str = self._format_additional_metrics(metrics)
            msg += f"Latest Result for call #{iteration}: {current_param_str} -> {metric_label}={reward_str}{additional_str} {aggregation_info}\n"
            if hide_primary_metric:
                msg += "Primary optimization metric name/value are hidden for this run type; rely on provided observability metrics.\n"
            msg += constraint_block
            msg += phase_instruction_override if phase_instruction_override is not None else self._get_phase_instruction(iteration)
            if final_freeze_request:
                msg += "\nPlease provide your analysis and the one configuration to freeze for the stable phase."
            else:
                msg += f"\nPlease provide your analysis of the trend and the next configuration for call #{iteration + 1}."

        else: # Single agent (fallback if history is provided)
            msg += f"Update for Iteration #{iteration}\n"
            msg += history_block
            
            msg += f"\nCURRENT BEST: {metric_label}={best_str}\n"
            additional_str = self._format_additional_metrics(metrics)
            msg += f"Latest Result: {current_param_str} -> {metric_label}={reward_str}{additional_str} {aggregation_info}\n"
            if hide_primary_metric:
                msg += "Primary optimization metric name/value are hidden for this run type; rely on provided observability metrics.\n"
            msg += constraint_block
            msg += phase_instruction_override if phase_instruction_override is not None else self._get_phase_instruction(iteration)
            if final_freeze_request:
                msg += "\nPlease provide your analysis and the one configuration to freeze for the stable phase."
            else:
                msg += f"\nPlease provide your analysis and the next configuration for iteration #{iteration + 1}."

        return msg

    def _create_legacy_update_message(self,
                               metrics: BenchmarkMetrics,
                               current_params: Dict[str, Any],
                               iteration: int,
                               best_reward: float,
                               aggregation_interval_s: Optional[float] = None) -> str:
        """Create conversational update message with compact history (Single Loop Legacy)."""
        if self._use_minimal_prompt_mode():
            return self._create_minimal_update_message(
                metrics,
                current_params,
                iteration,
                best_reward,
                history=None,
                baseline_index=0,
                final_freeze_request=False,
            )

        # Short history summary, including best_reward
        history_summary = self._build_history_summary(best_reward)
        metric_label = self._display_metric_name()

        # Only show tunable params
        tunable_params = {
            k: v for k, v in current_params.items()
            if k in self.parameter_ranges
        }

        # Format parameters for display
        params_parts = []
        for k, v in tunable_params.items():
            if isinstance(v, dict):
                # Per-core parameter with value/cores structure
                if "value" in v:
                    val = v["value"]
                    cores = v.get("cores", "all")
                    if isinstance(val, bool):
                        params_parts.append(f"{k}={val} (cores={cores})")
                    elif isinstance(val, str):
                        params_parts.append(f'{k}="{val}" (cores={cores})')
                    else:
                        params_parts.append(f"{k}={val:,} (cores={cores})")
                else:
                    params_parts.append(f"{k}={v}")
            elif isinstance(v, bool):
                params_parts.append(f"{k}={v}")
            elif isinstance(v, str):
                params_parts.append(f'{k}="{v}"')
            else:
                params_parts.append(f"{k}={v:,}")
        params_str = ", ".join(params_parts) if params_parts else "(no tunable params)"

        # Metric value for this iteration
        reward = metrics.get_metric(self.optimization_metric)

        # Compact constraint status
        constraint_block = ""
        if getattr(self.config, "constraint_metric", None) and self.config.constraint_threshold is not None:
            constraint_value = metrics.get_metric(self.config.constraint_metric)
            direction = getattr(self.config, "constraint_direction", "less_than")
            if direction == "less_than":
                violated = constraint_value >= self.config.constraint_threshold
                threshold_text = f"< {self.config.constraint_threshold:,.2f}"
            else:
                violated = constraint_value <= self.config.constraint_threshold
                threshold_text = f"> {self.config.constraint_threshold:,.2f}"
            status = "VIOLATED" if violated else "OK"
            constraint_block = (
                f"Constraint status: {self.config.constraint_metric}="
                f"{constraint_value:,.2f} (threshold {threshold_text}) [{status}]\n"
            )

        # Parameter ranges as short bullets
        param_ranges_lines = []
        for name, pr in self.parameter_ranges.items():
            if isinstance(pr, tuple):
                lo, hi = pr
                ptype = get_parameter_type(name)
                param_ranges_lines.append(
                    f"- {name} ({ptype}, integer) in [{lo:,}, {hi:,}]"
                )
            elif isinstance(pr, list):
                vals = ", ".join(str(v) for v in pr)
                param_ranges_lines.append(
                    f"- {name} (categorical) ∈ [{vals}]"
                )

        # Aggregation interval info
        aggregation_info = ""
        if aggregation_interval_s is not None:
            aggregation_info = f" (mean over past {aggregation_interval_s:.1f}s)"

        update = f"""{history_summary}
Current iteration:
- Iteration #{iteration}: {metric_label}={self._format_reward(reward)}{self._format_additional_metrics(metrics)}{aggregation_info}
- Parameters used: {params_str}
{constraint_block}Next step:
- Propose parameters for iteration #{iteration + 1}.
- Return a JSON object with:
  - Only the parameters you want to change
  - If a parameter is not included it will retain its last value (unchanged).
  - A "justification" field with 1–2 sentences explaining your reasoning.

Valid parameter ranges:
{chr(10).join(param_ranges_lines)}
"""
        return update

    def _record_trial(self,
                      iteration: int,
                      metrics: BenchmarkMetrics,
                      current_params: Dict[str, Any]) -> None:
        """Record a completed trial for history summarization."""
        # Fix for crash when metrics is None (first call)
        if metrics is None:
            return

        # Avoid double-recording the same iteration.
        if self._last_recorded_iteration == iteration:
            return

        reward = metrics.get_metric(self.optimization_metric)
        
        # Check if constraint was violated
        constraint_violated = False
        constraint_detail = ""
        if getattr(self.config, 'constraint_metric', None) and self.config.constraint_threshold is not None:
            constraint_value = metrics.get_metric(self.config.constraint_metric)
            direction = getattr(self.config, 'constraint_direction', 'less_than')
            
            # Normalize direction
            op_symbol = ">="
            if direction in ["lt", "less_than", "<"]:
                direction = "less_than"
                op_symbol = ">="
            elif direction in ["gt", "greater_than", ">"]:
                direction = "greater_than"
                op_symbol = "<="
            
            if constraint_value is not None:
                if direction == "less_than":
                    # Constraint: metric < threshold. Violated if metric >= threshold.
                    constraint_violated = constraint_value >= self.config.constraint_threshold
                else:
                    # Constraint: metric > threshold. Violated if metric <= threshold.
                    constraint_violated = constraint_value <= self.config.constraint_threshold
                
                if constraint_violated:
                    constraint_detail = f"{constraint_value:.2f} {op_symbol} {self.config.constraint_threshold:.2f}"
            else:
                # If constraint metric is missing, assume violated or handle gracefully
                # Here we assume violated if we can't measure it
                constraint_violated = True
                constraint_detail = "metric missing"
        
        entry = {
            "iteration": iteration,
            "params": dict(current_params),  # shallow copy
            "reward": reward,
            "metrics": getattr(metrics, "extra_metrics", {}) or {},
            "constraint_violated": constraint_violated,
            "constraint_detail": constraint_detail,
            "justification": self._justifications.get(iteration, ""),
        }
        self.trial_history.append(entry)
        self._last_recorded_iteration = iteration

    def seed_default_baseline_trial(self,
                                    metrics: BenchmarkMetrics,
                                    current_params: Dict[str, Any]) -> None:
        """Insert a pre-tuning baseline measurement for OS defaults."""
        if metrics is None:
            return

        reward = metrics.get_metric(self.optimization_metric)
        metrics_snapshot = {
            "throughput": getattr(metrics, "throughput", None),
            "goodput": getattr(metrics, "goodput", None),
            "latency_avg": getattr(metrics, "latency_avg", None),
            "latency_p95": getattr(metrics, "latency_p95", None),
            **(getattr(metrics, "extra_metrics", {}) or {}),
        }
        entry = {
            "iteration": 0,
            "params": dict(current_params),
            "parameters": dict(current_params),
            "reward": reward,
            "metrics": metrics_snapshot,
            "constraint_violated": False,
            "constraint_detail": "",
            "justification": "",
            "pre_tuning_default_config": True,
        }
        self.trial_history.append(entry)

    def _short_params_for_history(self,
                                  params: Dict[str, Any],
                                  max_keys: int = None) -> str:
        """Compact param string showing only tunable parameters."""
        # Only show parameters that are being tuned (in parameter_ranges)
        tunable = {k: v for k, v in params.items() if k in self.parameter_ranges}
        if not tunable:
            return "{}"

        # Show all parameters by default (max_keys=None means no limit)
        if max_keys is None:
            items = list(tunable.items())
        else:
            items = list(tunable.items())[:max_keys]
        
        parts = []
        for k, v in items:
            if isinstance(v, dict) and "value" in v:
                v = v["value"]
            parts.append(f"{k}={v}")
        s = ", ".join(parts)
        if max_keys is not None and len(tunable) > max_keys:
            s += ", ..."
        return "{" + s + "}"

    def _build_history_summary(self,
                               best_reward: float,
                               max_top: int = 0,
                               max_recent: int = 100) -> str:
        """Compact structured history summary for the LLM."""
        history = self.trial_history
        metric = self._display_metric_name()
        goal = self.optimization_goal or "maximize"

        if not history:
            return (
                f"History summary: no previous trials yet for {metric}. "
                f"Current best={self._format_reward(best_reward)}.\n"
            )

        higher_is_better = (goal == "maximize")
        
        # Filter valid history for sorting (ignore None rewards)
        valid_history = [e for e in history if e["reward"] is not None]
        
        sorted_hist = sorted(
            valid_history,
            key=lambda e: e["reward"],
            reverse=higher_is_better,
        )
        
        # For Top N, we exclude constraint violations
        top_candidates = [e for e in sorted_hist if not e.get('constraint_violated', False)]
        top = top_candidates[:max_top] if max_top > 0 else []
        
        recent = history[-max_recent:] if max_recent > 0 else []

        lines: list[str] = []
        lines.append(f"History summary (metric={metric}, goal={goal}):")
        lines.append(f"- Current best value: {self._format_reward(best_reward)}")
        
        if max_top > 0 and top:
            lines.append(f"- Top {len(top)} configs:")
            for e in top:
                if e.get("pre_tuning_default_config"):
                    reward_str = self._format_reward(e.get("reward"))
                    line = (
                        f"  * Initial/OS Default config: {metric}={reward_str} "
                        f"with {self._short_params_for_history(e['params'])}"
                    )
                    lines.append(line)
                    continue
                # Top configs should not have violations, but just in case
                constraint_note = ""
                if e.get('constraint_violated', False):
                    c_metric = getattr(self.config, 'constraint_metric', 'CONSTRAINT')
                    details = e.get('constraint_detail', '')
                    constraint_note = f" [{c_metric}={details} CONSTRAINT VIOLATED]"
                
                line = (f"  * iter {e['iteration']}: {metric}={e['reward']:,.2f} "
                        f"with {self._short_params_for_history(e['params'])}{constraint_note}")
                
                if LLM_INCLUDE_JUSTIFICATION_IN_HISTORY and e.get('justification'):
                    line += f"\n    Justification: {e['justification']}"
                
                lines.append(line)

        lines.append(f"- Recent {len(recent)} trials (oldest → newest):")
        for idx, e in enumerate(recent):
            if e.get("pre_tuning_default_config"):
                reward_str = self._format_reward(e.get("reward"))
                additional_metrics_str = ""
                metrics_data = e.get('metrics', {})
                if self._additional_metrics:
                    parts = []
                    for m in self._additional_metrics:
                        val = metrics_data.get(m)
                        if val is not None:
                            parts.append(f"{m}={val:,.2f}" if isinstance(val, (int, float)) else f"{m}={val}")
                    if parts:
                        additional_metrics_str = ", " + ", ".join(parts)
                lines.append(
                    f"  * Initial/OS Default config: {metric}={reward_str}{additional_metrics_str} "
                    f"with {self._short_params_for_history(e['params'])}"
                )
                continue
            constraint_note = ""
            if e.get('constraint_violated', False):
                c_metric = getattr(self.config, 'constraint_metric', 'CONSTRAINT')
                details = e.get('constraint_detail', '')
                constraint_note = f" [{c_metric}={details} CONSTRAINT VIOLATED]"
            
            # Determine if we should show additional metrics for this entry
            is_last = (idx == len(recent) - 1)
            is_second_to_last = (idx == len(recent) - 2)
            
            use_indirect = getattr(self.config, 'use_indirect_optimization', False)
            show_all = getattr(self.config, 'llm_indirect_history_show_all_metrics', False)
            
            should_show_additional = True
            if use_indirect and not show_all and not is_last and not is_second_to_last:
                should_show_additional = False
            
            reward_str = self._format_reward(e['reward'])
            
            # Additional metrics
            additional_metrics_str = ""
            if self._additional_metrics and should_show_additional:
                metrics_data = e.get('metrics', {})
                parts = []
                for m in self._additional_metrics:
                    val = metrics_data.get(m)
                    if val is not None:
                        parts.append(f"{m}={val:,.2f}" if isinstance(val, (int, float)) else f"{m}={val}")
                if parts:
                    additional_metrics_str = ", " + ", ".join(parts)

            line = (f"  * iter {e['iteration']}: {metric}={reward_str}{additional_metrics_str} "
                    f"with {self._short_params_for_history(e['params'])}{constraint_note}")
            
            if LLM_INCLUDE_JUSTIFICATION_IN_HISTORY and e.get('justification'):
                line += f"\n    Justification: {e['justification']}"
            
            lines.append(line)

        return "\n".join(lines) + "\n"

    def _get_recent_conversation(self,
                                 raw: bool = False,
                                 unbounded: bool = False) -> list[str]:
        """Flatten recent conversation turns into a content list."""
        history_source = self.raw_conversation_history if raw else self.conversation_history
        if not history_source:
            return []

        if unbounded:
            pairs = history_source
        else:
            max_pairs = getattr(self.config, "llm_max_history_pairs", 8)
            if max_pairs <= 0:
                return []
            pairs = history_source[-max_pairs:]

        contents: list[str] = []
        for user_msg, assistant_msg in pairs:
            contents.append(user_msg)
            contents.append(assistant_msg)
        return contents
    
    def _get_top_n_summary(self, n: int, history: Optional[List[Dict[str, Any]]] = None) -> str:
        """Get summary of top N configurations."""
        # Use provided history (dual loop) or local history (single loop)
        source_history = history if history is not None else self.trial_history
        
        if n <= 0 or not source_history:
            return ""
        
        goal = self.optimization_goal or "maximize"
        higher_is_better = (goal == "maximize")
        
        # Filter valid history (ignore None rewards)
        valid_history = [e for e in source_history if e.get("reward") is not None]
        
        sorted_hist = sorted(
            valid_history,
            key=lambda e: e["reward"],
            reverse=higher_is_better,
        )
        
        # Exclude constraint violations from Top N
        # In dual loop, 'constraint_violated' might not be pre-calculated in history entries.
        # We should calculate it on the fly if missing, or trust the entry.
        # For simplicity, we trust the entry if present, or assume False if missing (unless we want to recalculate).
        # However, for dual loop, we really should have constraint info.
        # Since _format_history_entry calculates it on the fly, maybe we should reuse that logic?
        # But for sorting/filtering here, let's stick to what's in the entry or skip filtering if missing.
        # Actually, let's implement a robust check helper or inline check.
        
        top_candidates = []
        for e in sorted_hist:
            violated = e.get('constraint_violated', False)
            # If not explicitly marked, check if we should verify it (similar to _format_history_entry)
            if not violated and 'constraint_violated' not in e:
                 if getattr(self.config, 'constraint_metric', None) and self.config.constraint_threshold is not None:
                    metrics = e.get('metrics', {})
                    val = metrics.get(self.config.constraint_metric)
                    if val is not None:
                        direction = getattr(self.config, 'constraint_direction', 'less_than')
                        if direction in ["lt", "less_than", "<"]:
                            violated = val >= self.config.constraint_threshold
                        elif direction in ["gt", "greater_than", ">"]:
                            violated = val <= self.config.constraint_threshold
            
            if not violated:
                top_candidates.append(e)

        top = top_candidates[:n]
        
        lines = [f"\nTOP {len(top)} CONFIGURATIONS SO FAR:"]
        lines.append("(NOTE: These configs may not be global optima. Your goal is to make nuanced decisions to reach a global optimum region given the noise.)")
        for i, e in enumerate(top, 1):
            metric = self._display_metric_name()
            reward_str = self._format_reward(e.get('reward'))
            # Show only tunable params
            params_str = self._short_params_for_history(e['parameters'] if 'parameters' in e else e.get('params', {}))
            if e.get("pre_tuning_default_config"):
                lines.append(f"{i}. Initial/OS Default config: {params_str} -> {metric}={reward_str}")
            else:
                lines.append(f"{i}. Iter {e['iteration']}: {params_str} -> {metric}={reward_str}")
            
        return "\n".join(lines) + "\n"

    def suggest_parameters(self, metrics: BenchmarkMetrics,
                           current_params: Dict[str, Any],
                           iteration: int,
                           best_reward: float = 0.0,
                           history: Optional[List[Dict[str, Any]]] = None,
                           baseline_index: int = 0,
                           aggregation_interval_s: Optional[float] = None,
                           phase_instruction_override: Optional[str] = None,
                           final_freeze_request: bool = False) -> TunerResponse:
        """Suggest new parameters using LLM.
        
        Args:
            metrics: Current benchmark metrics
            current_params: Current parameter values
            iteration: Current iteration number
            best_reward: Best reward seen so far
            history: Optional history for dual-loop mode
            baseline_index: Baseline index for dual-loop mode
            aggregation_interval_s: Optional aggregation interval in seconds (for prompt context)
        """
        # Track request timestamp
        request_timestamp = time.time()
        
        if self.replay_history_file:
            return self._replay_response(iteration)

        # Record the just-finished trial for history summarization (only for legacy single loop)
        # In dual loop, history is passed explicitly
        if history is None:
            self._record_trial(iteration, metrics, current_params)

        # Create update message with aggregation interval info
        update_message = self._create_update_message(
            metrics, current_params, iteration, best_reward,
            history, baseline_index, aggregation_interval_s,
            phase_instruction_override=phase_instruction_override,
            final_freeze_request=final_freeze_request,
        )

        # Build conversation contents
        contents: list[str] = [self._create_base_prompt()]
        if self._use_minimal_prompt_mode():
            contents.extend(self._get_recent_conversation(raw=True, unbounded=True))
        
        # Inject Top-N configs (only for this call, not saved to history)
        top_n_text = ""
        if (
            LLM_INCLUDE_TOP_N_CONFIGS > 0
            and not self._hide_primary_metric_for_this_agent()
            and not self._use_minimal_prompt_mode()
        ):
            # Pass history if available (dual loop), otherwise use internal trial_history (single loop)
            top_n_text = self._get_top_n_summary(LLM_INCLUDE_TOP_N_CONFIGS, history=history)
        
        # We append update_message + top_n_text for the LLM
        full_message_for_llm = update_message + top_n_text
        contents.append(full_message_for_llm)
    
        # Call LLM API (Gemini or OpenRouter)
        try:
            # Build response schema for structured output
            response_schema_dict = self._build_response_schema()
            request_label, max_retries, timeout_seconds = self._resolve_request_policy(
                final_freeze_request=final_freeze_request
            )

            # Dispatch to backend with retry behavior.
            response_text, parsed_data, response_time, log_config, raw_response, token_usage = \
                self._call_llm_with_retries(
                    contents, response_schema_dict, iteration,
                    request_timestamp, aggregation_interval_s,
                    request_label=request_label,
                    max_retries=max_retries,
                    request_timeout_seconds=timeout_seconds,
                )
            
            # Handle empty response
            if response_text is None and parsed_data is None:
                logger.error("LLM returned empty response")
                self._write_llm_api_log(
                    iteration + 1, contents, log_config,
                    None, None, None,
                    request_timestamp=request_timestamp,
                    aggregation_interval_s=aggregation_interval_s
                )
                return TunerResponse(parameters={}, confidence=0.0, justification="Empty response from LLM", response_time=response_time)
            
            # Parse response
            if parsed_data is not None and isinstance(parsed_data, dict):
                params, justification, warnings = self._parse_structured_response(parsed_data)
                response_text = response_text or json.dumps(parsed_data)
            elif response_text:
                try:
                    parsed_text = json.loads(response_text.strip())
                    if isinstance(parsed_text, dict):
                        params, justification, warnings = self._parse_structured_response(parsed_text)
                    else:
                        params, justification, warnings = self._parse_response(response_text)
                except json.JSONDecodeError:
                    params, justification, warnings = self._parse_response(response_text)
            else:
                logger.error("LLM response has no parsed data or text")
                self._write_llm_api_log(
                    iteration + 1, contents, log_config,
                    None, None, raw_response,
                    request_timestamp=request_timestamp,
                    aggregation_interval_s=aggregation_interval_s
                )
                return TunerResponse(parameters={}, confidence=0.0, justification="No parseable response from LLM", response_time=response_time)
            
            # LLM-only log: exact request and response
            self._write_llm_api_log(
                iteration + 1, contents, log_config,
                response_text, parsed_data, raw_response,
                request_timestamp=request_timestamp,
                aggregation_interval_s=aggregation_interval_s
            )
            
            # Update conversation history
            if warnings:
                warning_msg = "\n".join(warnings)
                response_text_with_warnings = f"{response_text}\n\n[SYSTEM NOTE: {warning_msg}]"
                self.conversation_history.append((update_message, response_text_with_warnings))
            else:
                self.conversation_history.append((update_message, response_text))
            self.raw_conversation_history.append((update_message, response_text or ""))
            
            # Store justification for history
            if justification:
                self._justifications[iteration + 1] = justification
            
            # Return TunerResponse
            return TunerResponse(
                parameters=params,
                confidence=1.0,
                justification=justification,
                converged=None,
                response_time=response_time,
                token_metrics=token_usage
            )
        except Exception as e:
            if isinstance(e, (LLMHTTPStatusError, LLMTimeoutExhaustedError)):
                logger.error(f"Fatal LLM error: {e}", exc_info=True)
                raise

            status_code = self._extract_http_status_code(e)
            if status_code is not None and status_code != 200:
                backend = "Gemini" if self._use_gemini else "OpenRouter"
                fatal_error = LLMHTTPStatusError(backend, status_code, detail=str(e))
                logger.error(f"Fatal LLM HTTP status error: {fatal_error}", exc_info=True)
                raise fatal_error from e

            logger.error(f"Error calling LLM API: {e}", exc_info=True)
            return TunerResponse(
                parameters={},
                confidence=0.0,
                justification=f"LLM API error: {e}"
            )
    
    def _call_gemini(self, contents, response_schema_dict, iteration,
                     request_timestamp=None, aggregation_interval_s=None,
                     request_timeout_seconds: Optional[float] = None,
                     temperature: Optional[float] = None,
                     max_output_tokens: Optional[int] = None,
                     allow_thinking: bool = True):
        """Call Gemini API. Returns (response_text, parsed_data, response_time, log_config, raw_response)."""
        config_dict = {"temperature": self.temperature if temperature is None else temperature}
        if max_output_tokens is not None:
            config_dict["max_output_tokens"] = max_output_tokens
        if request_timeout_seconds is not None:
            config_dict["http_options"] = types.HttpOptions(
                timeout=self._seconds_to_milliseconds(request_timeout_seconds)
            )
        
        # Use structured output with JSON mime type to enforce JSON schema
        if response_schema_dict:
            config_dict["response_mime_type"] = "application/json"
            try:
                if hasattr(types, 'Schema'):
                    try:
                        config_dict["response_schema"] = types.Schema(response_schema_dict)
                    except (TypeError, ValueError):
                        config_dict["response_schema"] = types.Schema(**response_schema_dict)
                else:
                    config_dict["response_schema"] = response_schema_dict
            except Exception as e:
                logger.warning(f"Failed to create Schema object, using dict directly: {e}")
                config_dict["response_schema"] = response_schema_dict
        
        # Configure thinking (only when structured output / JSON mode is NOT active,
        # as thinking and response_mime_type are incompatible in some models)
        if allow_thinking and "response_mime_type" not in config_dict:
            supports_thinking = any(tag in self.model_name for tag in [
                "gemini-2.5-pro", "gemini-2.5-flash",
                "gemini-3-pro", "gemini-3.1-pro", "gemini-3-flash",
            ])
            if supports_thinking:
                # thinking_level takes priority (Gemini 3 style)
                if self.thinking_level:
                    config_dict["thinking_config"] = types.ThinkingConfig(
                        thinking_level=self.thinking_level
                    )
                elif self.thinking_budget:
                    config_dict["thinking_config"] = types.ThinkingConfig(
                        thinking_budget=self.thinking_budget
                    )

        # Serializable config for LLM-only log (schema/ThinkingConfig as dicts)
        log_config = {}
        for _k, _v in config_dict.items():
            if _k == "response_schema" and response_schema_dict:
                log_config[_k] = response_schema_dict
            elif _k == "thinking_config":
                tc = {}
                if self.thinking_level:
                    tc["thinking_level"] = self.thinking_level
                if self.thinking_budget:
                    tc["thinking_budget"] = self.thinking_budget
                log_config[_k] = tc
            else:
                log_config[_k] = _v
        if "response_schema" in log_config and not isinstance(log_config["response_schema"], dict):
            log_config["response_schema"] = response_schema_dict or repr(config_dict.get("response_schema"))
        if request_timeout_seconds is not None:
            log_config["request_timeout_seconds"] = request_timeout_seconds
        
        generation_config = types.GenerateContentConfig(**config_dict)
        
        # Make API call
        api_call_start_time = time.time()
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=generation_config
            )
        except Exception as e:
            status_code = self._extract_http_status_code(e)
            if status_code is not None and status_code != 200:
                raise LLMHTTPStatusError("Gemini", status_code, detail=str(e)) from e
            raise
        response_time = time.time() - api_call_start_time

        response_status_code = self._extract_http_status_code(response)
        if response_status_code is not None:
            log_config["http_status_code"] = response_status_code
            if response_status_code != 200:
                raise LLMHTTPStatusError("Gemini", response_status_code)
        response_model = getattr(response, "model_version", None) or getattr(response, "model", None)
        if response_model:
            log_config["response_model"] = str(response_model)
        
        if not response:
            return None, None, response_time, log_config, None
        
        # Extract text and parsed data from Gemini response
        response_text = getattr(response, "text", None)
        parsed_data = getattr(response, "parsed", None)
        if parsed_data is not None and isinstance(parsed_data, dict):
            response_text = response_text or json.dumps(parsed_data)
        
        # Extract usage metadata
        usage_metadata = getattr(response, "usage_metadata", None)
        token_usage = None
        if usage_metadata:
            token_usage = normalize_token_metrics({
                "input_tokens": getattr(usage_metadata, "prompt_token_count", None),
                "output_tokens": getattr(usage_metadata, "candidates_token_count", None),
                "total_token_count": getattr(usage_metadata, "total_token_count", None),
                "thoughts_token_count": getattr(usage_metadata, "thoughts_token_count", None),
                "cached_content_token_count": getattr(usage_metadata, "cached_content_token_count", None),
            })
        
        return response_text, parsed_data, response_time, log_config, response, token_usage
    
    def _call_openrouter(self, contents, response_schema_dict, iteration,
                         request_timestamp=None, aggregation_interval_s=None,
                         request_timeout_seconds: Optional[float] = None,
                         temperature: Optional[float] = None,
                         max_output_tokens: Optional[int] = None):
        """Call OpenRouter API via OpenAI-compatible SDK.
        Returns (response_text, parsed_data, response_time, log_config, raw_response).
        """
        # Convert contents (list of strings) to OpenAI messages format
        messages = self._contents_to_openai_messages(contents)

        # Build kwargs for chat.completions.create
        kwargs = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.openrouter_max_tokens if max_output_tokens is None else max_output_tokens,
        }
        if request_timeout_seconds is not None:
            if self._openrouter_legacy_sdk:
                kwargs["request_timeout"] = request_timeout_seconds
            else:
                kwargs["timeout"] = request_timeout_seconds

        # Structured output via json_schema response_format
        if response_schema_dict:
            # Convert Gemini-style schema to OpenAI json_schema format
            openai_schema = self._schema_to_openai_format(response_schema_dict)
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "tuner_response",
                    "strict": False,
                    "schema": openai_schema
                }
            }
        
        log_config = {
            "backend": "openrouter",
            "model": self.model_name,
            "temperature": kwargs["temperature"],
            "max_tokens": kwargs["max_tokens"],
        }
        if response_schema_dict:
            log_config["response_schema"] = response_schema_dict
        if request_timeout_seconds is not None:
            log_config["request_timeout_seconds"] = request_timeout_seconds
        
        # Make API call
        api_call_start_time = time.time()
        response_status_code = None
        raw_response = None
        try:
            if self._openrouter_legacy_sdk:
                response = self.client.ChatCompletion.create(**kwargs)
            else:
                completions_client = self.client.chat.completions
                if hasattr(completions_client, "with_raw_response"):
                    raw_response = completions_client.with_raw_response.create(**kwargs)
                    response_status_code = self._extract_http_status_code(raw_response)
                    response = raw_response.parse() if hasattr(raw_response, "parse") else raw_response
                else:
                    response = completions_client.create(**kwargs)
                    response_status_code = self._extract_http_status_code(response)
        except Exception as e:
            status_code = self._extract_http_status_code(e)
            if status_code is not None and status_code != 200:
                raise LLMHTTPStatusError("OpenRouter", status_code, detail=str(e)) from e
            raise
        response_time = time.time() - api_call_start_time

        if response_status_code is not None:
            log_config["http_status_code"] = response_status_code
            if response_status_code != 200:
                raise LLMHTTPStatusError("OpenRouter", response_status_code)
        response_model = getattr(response, "model", None)
        if response_model is None and isinstance(response, dict):
            response_model = response.get("model")
        if response_model:
            log_config["response_model"] = str(response_model)

        choices = getattr(response, "choices", None)
        if choices is None and isinstance(response, dict):
            choices = response.get("choices")
        if not response or not choices:
            return None, None, response_time, log_config, None
        
        # Extract text from the response
        choice = choices[0]
        response_text = None
        message = getattr(choice, "message", None)
        if message is None and isinstance(choice, dict):
            message = choice.get("message")
        if message is not None:
            response_text = getattr(message, "content", None)
            if response_text is None and isinstance(message, dict):
                response_text = message.get("content")
        if response_text is None:
            response_text = getattr(choice, "text", None)
        if response_text is None and isinstance(choice, dict):
            response_text = choice.get("text")
        
        # Try to parse as JSON
        parsed_data = None
        if response_text:
            try:
                parsed_data = json.loads(response_text.strip())
            except json.JSONDecodeError:
                pass  # Will fall through to _parse_response
        
        # Extract usage metadata
        token_usage = None
        usage = getattr(response, "usage", None)
        if usage is None and isinstance(response, dict):
            usage = response.get("usage")
        if usage:
            prompt_tokens = getattr(usage, "prompt_tokens", None)
            completion_tokens = getattr(usage, "completion_tokens", None)
            total_tokens = getattr(usage, "total_tokens", None)
            reasoning_tokens = None
            if isinstance(usage, dict):
                prompt_tokens = usage.get("prompt_tokens")
                completion_tokens = usage.get("completion_tokens")
                total_tokens = usage.get("total_tokens")
            # OpenRouter sometimes provides reasoning_tokens in usage
            # Check if it's available in the usage object
            details = getattr(usage, "completion_tokens_details", None)
            if details is None and isinstance(usage, dict):
                details = usage.get("completion_tokens_details")
            if details:
                if hasattr(details, "reasoning_tokens"):
                    reasoning_tokens = details.reasoning_tokens
                elif isinstance(details, dict) and "reasoning_tokens" in details:
                    reasoning_tokens = details["reasoning_tokens"]
            token_usage = normalize_token_metrics({
                "input_tokens": prompt_tokens,
                "output_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "thinking_tokens": reasoning_tokens,
            })
        
        return response_text, parsed_data, response_time, log_config, raw_response or response, token_usage
    
    def _contents_to_openai_messages(self, contents: List[str]) -> List[Dict[str, str]]:
        """Convert Gemini-style contents (list of strings) to OpenAI messages format.
        
        The first element is the system prompt, followed by alternating 
        user/assistant messages from conversation history, and the final 
        element is the current user message.
        """
        messages = []
        
        if not contents:
            return messages
        
        # First content is the system prompt
        messages.append({"role": "system", "content": contents[0]})
        
        # Remaining contents alternate user/assistant
        # In conversational mode (reasoning agent), conversation_history 
        # pairs are injected. In stateless mode, there's just the base prompt 
        # + current update message.
        for i, content in enumerate(contents[1:], start=1):
            # Even indices (1, 3, 5, ...) are user messages
            # Odd indices (2, 4, 6, ...) are assistant messages
            if i % 2 == 1:
                messages.append({"role": "user", "content": content})
            else:
                messages.append({"role": "assistant", "content": content})
        
        return messages
    
    def _schema_to_openai_format(self, gemini_schema: Dict) -> Dict:
        """Convert Gemini response schema dict to OpenAI JSON Schema format.
        
        Gemini uses 'propertyOrdering' which is not part of standard JSON Schema.
        OpenAI expects standard JSON Schema with 'required' array.
        """
        schema = {
            "type": gemini_schema.get("type", "object"),
        }
        
        if "description" in gemini_schema:
            schema["description"] = gemini_schema["description"]
        
        if "properties" in gemini_schema:
            schema["properties"] = {}
            for prop_name, prop_def in gemini_schema["properties"].items():
                schema["properties"][prop_name] = self._convert_property(prop_def)
        
        # Use propertyOrdering as required fields if no explicit required
        if "required" in gemini_schema:
            schema["required"] = gemini_schema["required"]
        elif "propertyOrdering" in gemini_schema:
            schema["required"] = gemini_schema["propertyOrdering"]
        
        # additionalProperties: false is preferred for strict schemas
        schema["additionalProperties"] = False
        
        return schema
    
    def _convert_property(self, prop_def: Dict) -> Dict:
        """Convert a single property definition from Gemini to OpenAI JSON Schema format."""
        converted = {}
        
        prop_type = prop_def.get("type", "string")
        converted["type"] = prop_type
        
        if "description" in prop_def:
            converted["description"] = prop_def["description"]
        
        if "enum" in prop_def:
            converted["enum"] = prop_def["enum"]
        
        if "items" in prop_def:
            converted["items"] = self._convert_property(prop_def["items"])
        
        if "properties" in prop_def:
            converted["properties"] = {}
            for sub_name, sub_def in prop_def["properties"].items():
                converted["properties"][sub_name] = self._convert_property(sub_def)
            if "required" in prop_def:
                converted["required"] = prop_def["required"]
            elif "propertyOrdering" in prop_def:
                converted["required"] = prop_def["propertyOrdering"]
        
        return converted
    
    def _parse_response(self, response_text: str) -> tuple[Dict[str, Any], Optional[str], List[str]]:
        """Parse non-structured LLM response to extract parameters and justification."""
        # Try to find JSON in response
        # Look for "Config: { ... }" pattern first as per prompt instructions
        config_match = re.search(r'Config:\s*(\{.*?\})', response_text, re.DOTALL | re.IGNORECASE)
        if config_match:
            json_text = config_match.group(1)
        else:
            # Fallback to finding any JSON
            json_match = re.search(r'(\{.*?\})', response_text, re.DOTALL)
            if json_match:
                json_text = json_match.group(1)
            else:
                # Try balanced braces
                json_text = None
                brace_count = 0
                start_idx = -1
                for i, char in enumerate(response_text):
                    if char == '{':
                        if brace_count == 0:
                            start_idx = i
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0 and start_idx >= 0:
                            json_text = response_text[start_idx:i+1]
                            break
        
        if json_text:
            try:
                parsed = json.loads(json_text)
                if isinstance(parsed, dict):
                    # Extract justification from Analysis line if not in JSON
                    if "justification" not in parsed:
                        analysis_match = re.search(r'Analysis:\s*(.*?)(?:\n|Config:)', response_text, re.DOTALL | re.IGNORECASE)
                        if analysis_match:
                            parsed["justification"] = analysis_match.group(1).strip()
                    
                    return self._parse_structured_response(parsed)
            except json.JSONDecodeError:
                pass

        logger.error(f"Could not find valid JSON parameters in response. Response text: {response_text[:200]}...")
        return {}, None, []
    
    def _build_response_schema(self) -> Optional[Dict[str, Any]]:
        """Build a JSON schema for structured output."""
        try:
            properties = {}
            property_order = []
            
            for param_name, param_range in self.parameter_ranges.items():
                property_order.append(param_name)
                
                if isinstance(param_range, tuple):
                    properties[param_name] = {
                        "type": "integer",
                        "description": get_parameter_description(param_name)
                    }
                elif isinstance(param_range, list):
                    if all(isinstance(v, bool) for v in param_range):
                        properties[param_name] = {
                            "type": "boolean",
                            "description": get_parameter_description(param_name)
                        }
                    elif all(isinstance(v, str) for v in param_range):
                        properties[param_name] = {
                            "type": "string",
                            "enum": param_range,
                            "description": get_parameter_description(param_name)
                        }
                    else:
                        properties[param_name] = {
                            "type": "string",
                            "description": get_parameter_description(param_name)
                        }
                else:
                    properties[param_name] = {
                        "type": "string",
                        "description": get_parameter_description(param_name)
                    }
            if not self._use_minimal_prompt_mode():
                properties["justification"] = {
                    "type": "string",
                    "description": "Brief 1-2 sentence justification explaining the reasoning behind the parameter changes"
                }
                property_order.append("justification")
            
            schema = {
                "type": "object",
                "properties": properties,
                "propertyOrdering": property_order,
                "description": (
                    "Configuration parameters for OS tuning"
                    if self._use_minimal_prompt_mode()
                    else "Configuration parameters for OS tuning with justification"
                )
            }
            
            return schema
        except Exception as e:
            logger.warning(f"Failed to build response schema: {e}")
            return None
    
    def _parse_structured_response(self, parsed_data: Any) -> tuple[Dict[str, Any], Optional[str], List[str]]:
        """Parse structured response from Gemini API."""
        result = {}
        justification = None
        warnings = []
        
        if isinstance(parsed_data, dict):
            params_dict = parsed_data
        elif hasattr(parsed_data, '__dict__'):
            params_dict = parsed_data.__dict__
        else:
            logger.warning(f"Unexpected structured response format: {type(parsed_data)}")
            return {}, None, []
        
        if "justification" in params_dict:
            justification = str(params_dict.pop("justification"))
        
        for param_name, value in params_dict.items():
            if param_name not in self.parameter_ranges:
                # logger.warning(f"Unknown parameter in LLM response: {param_name}")
                continue
            
            param_range = self.parameter_ranges[param_name]
            
            if isinstance(param_range, list):
                normalized_value = self._normalize_categorical_value(param_name, value)
                if normalized_value in param_range:
                    result[param_name] = normalized_value
                else:
                    # logger.warning(f"Invalid categorical value for {param_name}: {value}")
                    warnings.append(f"Invalid categorical value for {param_name}: {value}. Valid: {param_range}")
            else:
                try:
                    int_value = int(float(value))
                    min_val, max_val = param_range
                    clamped = max(min_val, min(max_val, int_value))
                    
                    if clamped != int_value:
                        warnings.append(f"BOUNDS EXCEEDED for {param_name}: you proposed {int_value}, clamped to {clamped}. Valid bounds: [{min_val}, {max_val}]")
                    
                    result[param_name] = clamped
                except (ValueError, TypeError):
                    # logger.warning(f"Could not convert {param_name}={value} to int")
                    pass
        
        return result, justification, warnings
    
    def _normalize_categorical_value(self, param_name: str, value: Any) -> Any:
        """Normalize categorical value to match valid values."""
        if param_name == "turbo":
            if value in [True, "true", "True", "on", "ON", "enabled", "ENABLED", 1]:
                return True
            elif value in [False, "false", "False", "off", "OFF", "disabled", "DISABLED", 0]:
                return False
            return value
        
        if isinstance(value, str):
            param_range = self.parameter_ranges.get(param_name, [])
            if isinstance(param_range, list):
                for valid_val in param_range:
                    if isinstance(valid_val, str) and valid_val.lower() == value.lower():
                        return valid_val
        
        return value
    
    def _replay_response(self, iteration: int) -> TunerResponse:
        """Simulate response from replay history."""
        entry = self.replay_by_iteration.get(iteration)
        if not entry:
            logger.warning(f"Replay: No history entry for iteration {iteration}")
            return TunerResponse(parameters={}, confidence=0.0, justification=None)
        
        if self.agent_type == "quick":
            params = entry.get('quick_parameters', {})
        else:
            params = entry.get('parameters', {})
        
        tunable_params = {k: v for k, v in params.items() if k in self.parameter_ranges}
        
        timing = entry.get('timing_info', {})
        if self.agent_type == "quick":
            duration = timing.get('quick_response_duration', 0)
        else:
            duration = timing.get('reasoning_response_duration', 0)
        
        if duration and duration > 0:
            time.sleep(float(duration))
        
        return TunerResponse(parameters=tunable_params, confidence=1.0, justification=None)
    
    def clear_history(self) -> None:
        """Clear conversation history."""
        self.conversation_history = []
        self.raw_conversation_history = []
        logger.info("Cleared conversation history")
