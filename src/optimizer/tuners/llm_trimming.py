#!/usr/bin/env python3
"""
LLM-based trimming tuner for search space narrowing.

This tuner runs for an initial number of cycles to explore the parameter space
and progressively narrow (or widen) parameter ranges. After the trimming phase,
the effective ranges are handed off to the primary tuner.
"""

import json
import copy
import logging
from typing import Dict, Optional, Any, List, Tuple, Union

from optimizer.tuners.llm import LLMTuner, LLM_API_LOG_ENABLED
from optimizer.tuners.base import TunerResponse
from optimizer.parameter_manager import get_parameter_description

logger = logging.getLogger(__name__)

# --- Feature Flags ---
# Whether the trimming tuner should also suggest parameter values (not just ranges)
TRIMMING_SUGGEST_PARAMS = True
# Whether to log suggested ranges each cycle
TRIMMING_LOG_RANGES = True


class InvalidTrimmingCandidateError(RuntimeError):
    """Raised when trimming cannot produce a complete, in-range candidate."""


class LLMTrimmingTuner(LLMTuner):
    """LLM tuner that focuses on narrowing parameter ranges.

    Maintains 'effective ranges' that evolve each cycle. The LLM can narrow
    or widen any subset of parameters per cycle. After trimming completes,
    call get_effective_ranges() to retrieve the final ranges.
    """

    def __init__(self, config, agent_type: str = "single"):
        """Initialize trimming tuner.

        Args:
            config: Configuration object
            agent_type: Agent type identifier (default "single")
        """
        # Whether LLM should also suggest parameter values
        self._suggest_params = getattr(config, 'trimming_suggest_params', TRIMMING_SUGGEST_PARAMS)

        # Initialize parent LLMTuner
        super().__init__(config, agent_type=agent_type)

        # Override agent_type for distinct API log filenames (llm_api_trimming_*.txt)
        self.agent_type = "trimming"

        # Store original ranges (immutable reference — used to clamp widening)
        self._original_ranges: Dict[str, Any] = copy.deepcopy(self.parameter_ranges)

        # Effective ranges evolve each cycle (mutable)
        self.effective_ranges: Dict[str, Any] = copy.deepcopy(self.parameter_ranges)
        self._last_candidate_parse_error = ""

        # History of trimming actions: list of {cycle, param, old_range, new_range}
        self._trimming_actions: List[Dict[str, Any]] = []

        # Eliminated parameters: param_name -> fixed_value (the value to lock it at)
        self._eliminated_params: Dict[str, Any] = {}

        logger.info(f"LLMTrimmingTuner initialized (suggest_params={self._suggest_params})")

    def suggest_parameters(self, *args, **kwargs) -> TunerResponse:
        """Require a complete, in-range candidate, retrying one malformed reply."""
        if not self._suggest_params:
            return super().suggest_parameters(*args, **kwargs)

        phase_override = kwargs.get("phase_instruction_override")
        attempts = 1 if self.replay_history_file else 2
        last_error = "missing candidate configuration"

        for attempt in range(1, attempts + 1):
            candidate_ranges = copy.deepcopy(self.effective_ranges)
            self._last_candidate_parse_error = ""
            response = super().suggest_parameters(*args, **kwargs)
            last_error = self._last_candidate_parse_error or self._candidate_validation_error(
                response.parameters, ranges=candidate_ranges
            )
            if not last_error:
                response.required_parameters = sorted(candidate_ranges)
                return response

            logger.error(
                "Rejected trimming candidate on attempt %d/%d: %s",
                attempt, attempts, last_error,
            )
            if attempt < attempts:
                retry_instruction = (
                    "RETRY REQUIREMENT: Your previous response did not contain a complete, "
                    "valid candidate configuration. Return exactly one value for every currently "
                    "tunable parameter, using only values from the CURRENT EFFECTIVE PARAMETER "
                    "RANGES. Do not copy an out-of-range OS-default value.\n"
                )
                kwargs["phase_instruction_override"] = (
                    (phase_override or "") + retry_instruction
                )

        raise InvalidTrimmingCandidateError(
            f"trimming LLM failed to provide a complete in-range candidate after "
            f"{attempts} attempt(s): {last_error}"
        )

    # ------------------------------------------------------------------
    # Prompt overrides
    # ------------------------------------------------------------------

    def _create_base_prompt(self) -> str:
        """Override base prompt to include trimming-specific instructions."""
        # Build the standard prompt first
        base_prompt = super()._create_base_prompt()

        # Build effective ranges display
        ranges_display = self._format_effective_ranges()

        trimming_instructions = f"""
TRIMMING PHASE INSTRUCTIONS:
You are in the SEARCH SPACE TRIMMING phase. Your primary goal is to ADJUST the
parameter ranges to narrow (or widen) the search space based on observed performance.

CURRENT EFFECTIVE PARAMETER RANGES:
{ranges_display}

FOR EACH CYCLE you should:
1. Observe the performance metrics from the latest configuration
2. Decide which parameter ranges (if any) to adjust
3. Include a "suggested_ranges" object in your response with adjustments
   - For numeric parameters: {{"min": <new_min>, "max": <new_max>}}
   - For categorical parameters: {{"values": [<subset of valid values>]}}
   - Only include parameters you want to change (omit unchanged ones)
   - You may NARROW ranges to focus on promising regions
   - You may WIDEN ranges back if you realize you were too restrictive
     (but never beyond the original bounds)
4. If a parameter has NO significant impact on the optimization metric, you may
   ELIMINATE it entirely by listing it in "eliminated_params". Eliminated parameters
   will be fixed at their current value and excluded from future tuning.
   - Only eliminate a parameter if you have strong evidence it doesn't matter
   - This is irreversible for the current run
5. {"Also suggest parameter values to try in the current cycle" if self._suggest_params else "Parameter values will be chosen by the system; focus only on range adjustments"}

IMPORTANT:
- These trimming instructions OVERRIDE the standard instruction that allows
  unchanged parameter fields to be omitted
- When suggesting parameter values, return EXACTLY ONE VALUE FOR EVERY CURRENTLY
  TUNABLE PARAMETER, and choose each value from its CURRENT EFFECTIVE PARAMETER RANGE
- The measured configuration may be an OS-default baseline outside these ranges;
  DO NOT retain or copy any such out-of-range value into the candidate configuration
- The complete parameter fields specify the configuration measured next;
  suggested_ranges changes the search space used by future cycles
- You do NOT need to adjust all parameters every cycle
- Omitting a parameter from suggested_ranges means its range stays as-is
- An empty suggested_ranges object ({{}}) means no range changes this cycle
- An empty eliminated_params list ([]) means no parameters are eliminated this cycle
"""

        return base_prompt + trimming_instructions

    def _create_update_message(self, metrics, current_params, iteration,
                                best_reward, history=None, baseline_index=0,
                                aggregation_interval_s=None,
                                phase_instruction_override=None,
                                final_freeze_request=False) -> str:
        """Override to include trimming action history and current effective ranges."""
        # Get the standard update message
        base_msg = super()._create_update_message(
            metrics, current_params, iteration, best_reward,
            history, baseline_index, aggregation_interval_s,
            phase_instruction_override=phase_instruction_override,
            final_freeze_request=final_freeze_request,
        )

        # Append trimming history
        if self._trimming_actions:
            trimming_block = "\n[Trimming History]\n"
            for action in self._trimming_actions:
                trimming_block += (
                    f"  Cycle {action['cycle']}: {action['param']} "
                    f"changed from {self._format_range_value(action['old_range'])} "
                    f"to {self._format_range_value(action['new_range'])}\n"
                )
            base_msg += trimming_block

        # Append eliminated params
        if self._eliminated_params:
            elim_block = "\n[Eliminated Parameters (fixed, no longer tuned)]\n"
            for param_name, fixed_val in self._eliminated_params.items():
                elim_block += f"  {param_name} = {fixed_val}\n"
            base_msg += elim_block

        # Append current effective ranges
        base_msg += f"\n[Current Effective Ranges]\n{self._format_effective_ranges()}\n"

        return base_msg

    # ------------------------------------------------------------------
    # Response schema override
    # ------------------------------------------------------------------

    def _build_response_schema(self) -> Optional[Dict[str, Any]]:
        """Override to add suggested_ranges to the response schema."""
        try:
            properties = {}
            property_order = []

            # Add parameter value properties only if suggest_params is enabled
            if self._suggest_params:
                for param_name, param_range in self.effective_ranges.items():
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

            # Build suggested_ranges schema
            # Each parameter can have either {min, max} for numeric or {values} for categorical
            range_properties = {}
            for param_name, param_range in self.effective_ranges.items():
                if isinstance(param_range, tuple):
                    range_properties[param_name] = {
                        "type": "object",
                        "description": f"New range for {param_name} (current: [{param_range[0]}, {param_range[1]}])",
                        "properties": {
                            "min": {"type": "integer", "description": "New minimum value"},
                            "max": {"type": "integer", "description": "New maximum value"}
                        }
                    }
                elif isinstance(param_range, list):
                    range_properties[param_name] = {
                        "type": "object",
                        "description": f"New values for {param_name} (current: {param_range})",
                        "properties": {
                            "values": {
                                "type": "array",
                                "description": "Subset of valid values to keep",
                                "items": {"type": "string"}
                            }
                        }
                    }

            properties["suggested_ranges"] = {
                "type": "object",
                "description": "Parameter range adjustments. Include only parameters whose ranges you want to change.",
                "properties": range_properties
            }
            property_order.append("suggested_ranges")

            # Eliminated params
            all_param_names = list(self.effective_ranges.keys())
            properties["eliminated_params"] = {
                "type": "array",
                "description": "List of parameter names to permanently eliminate from tuning. "
                               "These will be fixed at their current value. Only eliminate params "
                               "that have no significant impact on the optimization metric. "
                               f"Valid parameter names: {all_param_names}",
                "items": {"type": "string"}
            }
            property_order.append("eliminated_params")

            # Justification
            properties["justification"] = {
                "type": "string",
                "description": "Brief 1-2 sentence justification explaining the reasoning behind the range adjustments and parameter choices"
            }
            property_order.append("justification")

            # Converged
            properties["converged"] = {
                "type": "boolean",
                "description": "True if the search space has been sufficiently narrowed and no further trimming is needed"
            }
            property_order.append("converged")

            schema = {
                "type": "object",
                "properties": properties,
                "propertyOrdering": property_order,
                "required": property_order,
                "description": (
                    "Trimming response with suggested range adjustments and a required complete candidate"
                    if self._suggest_params
                    else "Trimming response with suggested range adjustments"
                )
            }

            return schema
        except Exception as e:
            logger.warning(f"Failed to build trimming response schema: {e}")
            return None

    # ------------------------------------------------------------------
    # Response parsing override
    # ------------------------------------------------------------------

    def _replay_response(self, iteration: int, *, final_freeze_request: bool = False) -> TunerResponse:
        """Replay both parameter choices and trimming-specific range actions."""

        entry = self.replay_by_iteration.get(iteration) or {}
        responses = entry.get("responses") or {}
        response = responses.get("reasoning") or {}
        tuner_response = super()._replay_response(
            iteration, final_freeze_request=final_freeze_request
        )
        if self._suggest_params:
            error = self._candidate_validation_error(tuner_response.parameters)
            if error:
                return tuner_response
        suggested_ranges = response.get("suggested_ranges")
        eliminated_params = response.get("eliminated_params")
        if isinstance(suggested_ranges, dict):
            self._apply_range_adjustments(suggested_ranges)
        if isinstance(eliminated_params, list):
            self._apply_eliminations(eliminated_params, dict(tuner_response.parameters))
        return tuner_response

    def _parse_structured_response(self, parsed_data: Any) -> tuple[Dict[str, Any], Optional[str], List[str]]:
        """Override to extract suggested_ranges and update effective ranges."""
        if isinstance(parsed_data, dict):
            params_dict = dict(parsed_data)  # shallow copy
        elif hasattr(parsed_data, '__dict__'):
            params_dict = dict(parsed_data.__dict__)
        else:
            logger.warning(f"Unexpected structured response format: {type(parsed_data)}")
            return {}, None, []

        # Extract suggested_ranges before parent parsing
        suggested_ranges = params_dict.pop("suggested_ranges", None)

        # Extract eliminated_params before parent parsing
        eliminated_params = params_dict.pop("eliminated_params", None)

        # Parse and validate the candidate before mutating the effective search space.
        result, justification, warnings = super()._parse_structured_response(params_dict)

        candidate_error = self._candidate_validation_error(result)
        bounds_warnings = [warning for warning in warnings if "BOUNDS EXCEEDED" in warning]
        if bounds_warnings:
            candidate_error = "; ".join(bounds_warnings)
        self._last_candidate_parse_error = candidate_error if self._suggest_params else ""

        if not candidate_error:
            if suggested_ranges and isinstance(suggested_ranges, dict):
                self._apply_range_adjustments(suggested_ranges)
            if eliminated_params and isinstance(eliminated_params, list):
                self._apply_eliminations(eliminated_params, result)
        elif self._suggest_params:
            warnings.append(f"INVALID TRIMMING CANDIDATE: {candidate_error}")

        return result, justification, warnings

    def _candidate_validation_error(
        self, params: Dict[str, Any], *, ranges: Optional[Dict[str, Any]] = None
    ) -> str:
        """Return a diagnostic when a suggested candidate violates active ranges."""
        if not self._suggest_params:
            return ""

        active_ranges = self.effective_ranges if ranges is None else ranges
        expected = set(active_ranges)
        supplied = set(params)
        missing = sorted(expected - supplied)
        extra = sorted(supplied - expected)
        problems = []
        if missing:
            problems.append(f"missing parameters: {missing}")
        if extra:
            problems.append(f"parameters are no longer tunable: {extra}")

        for name in sorted(expected & supplied):
            value = params[name]
            allowed = active_ranges[name]
            if isinstance(allowed, tuple):
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    problems.append(f"{name}={value!r} is not numeric")
                elif not allowed[0] <= value <= allowed[1]:
                    problems.append(
                        f"{name}={value!r} is outside [{allowed[0]}, {allowed[1]}]"
                    )
            elif value not in allowed:
                problems.append(f"{name}={value!r} is not one of {allowed!r}")
        return "; ".join(problems)

    # ------------------------------------------------------------------
    # Range adjustment logic
    # ------------------------------------------------------------------

    def _apply_range_adjustments(self, suggested_ranges: Dict[str, Any]) -> None:
        """Apply suggested range adjustments to effective_ranges.

        Each param in suggested_ranges can narrow or widen the range,
        but never beyond original bounds.
        """
        current_iteration = len(self.trial_history) + 1  # approximate cycle number

        for param_name, adjustment in suggested_ranges.items():
            if param_name not in self._original_ranges:
                logger.warning(f"Trimming: unknown parameter '{param_name}' in suggested_ranges, skipping")
                continue

            if not isinstance(adjustment, dict):
                logger.warning(f"Trimming: invalid adjustment for '{param_name}': {adjustment}, skipping")
                continue

            original = self._original_ranges[param_name]
            old_effective = self.effective_ranges[param_name]

            if isinstance(original, tuple):
                # Numeric range: expect {min, max}
                new_range = self._adjust_numeric_range(param_name, adjustment, original)
                if new_range and new_range != old_effective:
                    self._trimming_actions.append({
                        "cycle": current_iteration,
                        "param": param_name,
                        "old_range": old_effective,
                        "new_range": new_range
                    })
                    self.effective_ranges[param_name] = new_range
                    if TRIMMING_LOG_RANGES:
                        logger.info(
                            f"Trimming [{param_name}]: "
                            f"{self._format_range_value(old_effective)} -> "
                            f"{self._format_range_value(new_range)}"
                        )

            elif isinstance(original, list):
                # Categorical range: expect {values: [...]}
                new_values = self._adjust_categorical_range(param_name, adjustment, original)
                if new_values is not None and new_values != old_effective:
                    self._trimming_actions.append({
                        "cycle": current_iteration,
                        "param": param_name,
                        "old_range": old_effective,
                        "new_range": new_values
                    })
                    self.effective_ranges[param_name] = new_values
                    if TRIMMING_LOG_RANGES:
                        logger.info(
                            f"Trimming [{param_name}]: "
                            f"{self._format_range_value(old_effective)} -> "
                            f"{self._format_range_value(new_values)}"
                        )

    def _adjust_numeric_range(self, param_name: str, adjustment: Dict,
                               original: Tuple[int, int]) -> Optional[Tuple[int, int]]:
        """Adjust a numeric parameter range. Returns new (min, max) or None if invalid."""
        orig_min, orig_max = original

        try:
            new_min = int(adjustment.get("min", self.effective_ranges[param_name][0]))
            new_max = int(adjustment.get("max", self.effective_ranges[param_name][1]))
        except (ValueError, TypeError) as e:
            logger.warning(f"Trimming: invalid numeric values for '{param_name}': {e}")
            return None

        # Clamp to original bounds
        new_min = max(orig_min, min(orig_max, new_min))
        new_max = max(orig_min, min(orig_max, new_max))

        # Ensure min <= max
        if new_min > new_max:
            logger.warning(f"Trimming: invalid range for '{param_name}': min={new_min} > max={new_max}, swapping")
            new_min, new_max = new_max, new_min

        # Don't allow a zero-width range (keep at least some spread)
        if new_min == new_max:
            logger.warning(f"Trimming: zero-width range for '{param_name}', keeping at least ±1")
            new_min = max(orig_min, new_min - 1)
            new_max = min(orig_max, new_max + 1)

        return (new_min, new_max)

    def _adjust_categorical_range(self, param_name: str, adjustment: Dict,
                                   original: List) -> Optional[List]:
        """Adjust a categorical parameter range. Returns new list or None if invalid."""
        values = adjustment.get("values")
        if values is None or not isinstance(values, list):
            logger.warning(f"Trimming: missing or invalid 'values' for '{param_name}'")
            return None

        if len(values) == 0:
            logger.warning(f"Trimming: empty values for '{param_name}', keeping current")
            return None

        # Validate: only keep values that are in the original list
        # Handle type coercion (e.g., string "True" for bool True)
        valid_values = []
        for v in values:
            if v in original:
                valid_values.append(v)
            else:
                # Try matching by string representation for booleans
                matched = False
                for orig_v in original:
                    if str(orig_v).lower() == str(v).lower():
                        valid_values.append(orig_v)
                        matched = True
                        break
                if not matched:
                    logger.warning(f"Trimming: value '{v}' not in original range for '{param_name}', skipping")

        if not valid_values:
            logger.warning(f"Trimming: no valid values remaining for '{param_name}', keeping current")
            return None

        return valid_values

    def _apply_eliminations(self, eliminated_params: List[str], params_dict: Dict[str, Any]) -> None:
        """Eliminate parameters from the effective ranges.

        Eliminated params are removed from effective_ranges and recorded in
        _eliminated_params with the value to fix them at (taken from the
        current suggested value or the midpoint of the current effective range).
        """
        current_iteration = len(self.trial_history) + 1

        for param_name in eliminated_params:
            if not isinstance(param_name, str):
                continue
            if param_name in self._eliminated_params:
                logger.debug(f"Trimming: parameter '{param_name}' already eliminated, skipping")
                continue
            if param_name not in self._original_ranges:
                logger.warning(f"Trimming: unknown parameter '{param_name}' in eliminated_params, skipping")
                continue
            if param_name not in self.effective_ranges:
                logger.debug(f"Trimming: parameter '{param_name}' not in effective_ranges (already removed?), skipping")
                continue

            # Determine the value to fix the parameter at:
            # prefer the value the LLM suggested this cycle, else use midpoint/first
            if param_name in params_dict:
                fixed_value = params_dict[param_name]
            else:
                rng = self.effective_ranges[param_name]
                if isinstance(rng, tuple):
                    fixed_value = (rng[0] + rng[1]) // 2
                elif isinstance(rng, list) and len(rng) > 0:
                    fixed_value = rng[0]
                else:
                    fixed_value = None

            old_range = self.effective_ranges.pop(param_name)
            self._eliminated_params[param_name] = fixed_value

            self._trimming_actions.append({
                "cycle": current_iteration,
                "param": param_name,
                "old_range": old_range,
                "new_range": f"ELIMINATED (fixed={fixed_value})"
            })

            logger.info(
                f"Trimming: ELIMINATED parameter '{param_name}' "
                f"(was {self._format_range_value(old_range)}, fixed at {fixed_value})"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_effective_ranges(self) -> Dict[str, Any]:
        """Return the current effective parameter ranges after trimming."""
        return copy.deepcopy(self.effective_ranges)

    def get_eliminated_params(self) -> Dict[str, Any]:
        """Return dict of eliminated param_name -> fixed_value."""
        return copy.deepcopy(self._eliminated_params)

    def get_trimming_summary(self) -> str:
        """Return a human-readable summary of all trimming actions."""
        if not self._trimming_actions and not self._eliminated_params:
            return "No trimming actions taken."

        lines = ["Trimming Summary:"]
        for action in self._trimming_actions:
            lines.append(
                f"  Cycle {action['cycle']}: {action['param']} "
                f"{self._format_range_value(action['old_range'])} -> "
                f"{self._format_range_value(action['new_range'])}"
            )

        if self._eliminated_params:
            lines.append("\nEliminated Parameters (fixed at current value):")
            for param_name, fixed_val in self._eliminated_params.items():
                lines.append(f"  {param_name} = {fixed_val}")

        lines.append("\nFinal Effective Ranges:")
        for param_name, effective in self.effective_ranges.items():
            original = self._original_ranges[param_name]
            changed = effective != original
            marker = " [CHANGED]" if changed else ""
            lines.append(f"  {param_name}: {self._format_range_value(effective)}{marker}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _format_effective_ranges(self) -> str:
        """Format effective ranges for display in prompts."""
        lines = []
        for param_name, effective in self.effective_ranges.items():
            original = self._original_ranges[param_name]
            if isinstance(effective, tuple):
                orig_str = f"[{original[0]:,}, {original[1]:,}]"
                eff_str = f"[{effective[0]:,}, {effective[1]:,}]"
                if effective != original:
                    lines.append(f"  {param_name}: {eff_str} (original: {orig_str})")
                else:
                    lines.append(f"  {param_name}: {eff_str}")
            elif isinstance(effective, list):
                eff_str = str(effective)
                if effective != original:
                    lines.append(f"  {param_name}: {eff_str} (original: {original})")
                else:
                    lines.append(f"  {param_name}: {eff_str}")
            else:
                lines.append(f"  {param_name}: {effective}")
        return "\n".join(lines) if lines else "  (none)"

    @staticmethod
    def _format_range_value(value: Any) -> str:
        """Format a range value for display."""
        if isinstance(value, tuple):
            return f"[{value[0]:,}, {value[1]:,}]"
        elif isinstance(value, list):
            return str(value)
        return str(value)
