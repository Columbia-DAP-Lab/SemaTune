#!/usr/bin/env python3
"""
LLM-based command tuner implementation using Gemini API.

This tuner uses Google's Gemini API to suggest raw shell commands to optimize
the system, rather than just adjusting specific parameters.
"""

import logging
import json
import re
import time as import_time
from typing import Dict, Any, List, Optional, Tuple
import subprocess

from ..benchmark import BenchmarkMetrics
from .base import TunerResponse
from .llm import LLMTuner

logger = logging.getLogger(__name__)

_SHELL_ASSIGNMENT_RE = re.compile(
    r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(?![=]).*$"
)
_SHELL_CONTROL_CONTINUATION_RE = re.compile(
    r"^\s*(?:do|then|elif|else|fi|done|esac|\})\b"
)

class LLMCommandTuner(LLMTuner):
    """LLM-based tuner that generates shell commands."""

    def __init__(self, config, agent_type: str = "single"):
        super().__init__(config, agent_type)
        # Store last command results so _record_trial can capture them
        self._last_command_results: List[Dict[str, Any]] = []

    def _get_available_governors(self) -> List[str]:
        """Return the distinct scaling governors exposed by the current host."""
        try:
            output = subprocess.check_output(
                "cat /sys/devices/system/cpu/cpufreq/policy*/scaling_available_governors 2>/dev/null",
                shell=True,
                text=True,
            ).strip()
        except Exception:
            return []

        governors: List[str] = []
        for token in output.split():
            if token not in governors:
                governors.append(token)
        return governors

    def _get_network_interfaces(self) -> List[str]:
        """Return non-loopback interfaces so the model doesn't guess eth0."""
        try:
            output = subprocess.check_output(
                "ip -o link show | awk -F': ' '{print $2}'",
                shell=True,
                text=True,
            ).strip()
        except Exception:
            return []

        interfaces = [iface for iface in output.splitlines() if iface and iface != "lo"]
        return interfaces

    def _get_available_tcp_congestion_controls(self) -> List[str]:
        """Return TCP congestion control algorithms supported on this host."""
        try:
            output = subprocess.check_output(
                "cat /proc/sys/net/ipv4/tcp_available_congestion_control 2>/dev/null",
                shell=True,
                text=True,
            ).strip()
        except Exception:
            return []

        return [token for token in output.split() if token]

    def _get_block_scheduler_hints(self) -> List[str]:
        """Summarize block scheduler choices so the model doesn't guess noop."""
        try:
            output = subprocess.check_output(
                r"""for f in /sys/block/*/queue/scheduler; do
  [ -f "$f" ] || continue
  dev="$(basename "$(dirname "$(dirname "$f")")")"
  case "$dev" in
    loop*|ram*|zram*) continue ;;
  esac
  scheds="$(cat "$f" 2>/dev/null)"
  [ -n "$scheds" ] && printf '%s: %s\n' "$dev" "$scheds"
done""",
                shell=True,
                text=True,
            ).strip()
        except Exception:
            return []

        lines = [line.strip() for line in output.splitlines() if line.strip()]
        return lines[:8]

    def _get_scheduler_sysctl_hints(self) -> List[str]:
        """Describe scheduler sysctls actually exposed by this kernel."""
        try:
            output = subprocess.check_output(
                r"ls /proc/sys/kernel 2>/dev/null | rg '^sched_.*(ns|us|cost|granularity|latency)'",
                shell=True,
                text=True,
            ).strip()
        except Exception:
            return []

        available = [line.strip() for line in output.splitlines() if line.strip()]
        if not available:
            return []

        hints = ["Scheduler-related sysctls visible on this host: " + ", ".join(available)]
        common_missing = [
            name
            for name in (
                "sched_latency_ns",
                "sched_min_granularity_ns",
                "sched_migration_cost_ns",
            )
            if name not in available
        ]
        if common_missing:
            hints.append(
                "Do NOT assume these legacy scheduler sysctls exist here: "
                + ", ".join(common_missing)
            )
        return hints

    def _get_network_queue_paths(self) -> List[str]:
        """Expose queue steering paths that actually exist on this host."""
        try:
            output = subprocess.check_output(
                r"""for iface in /sys/class/net/*; do
  [ -d "$iface" ] || continue
  name="$(basename "$iface")"
  [ "$name" = "lo" ] && continue
  for leaf in queues/rx-0/rps_cpus queues/tx-0/xps_cpus; do
    [ -e "$iface/$leaf" ] && printf '%s/%s\n' "$name" "$leaf"
  done
done""",
                shell=True,
                text=True,
            ).strip()
        except Exception:
            return []

        return [line.strip() for line in output.splitlines() if line.strip()][:12]

    @staticmethod
    def _get_shell_syntax_error(command: str) -> Optional[str]:
        """Return bash parser stderr when a shell command is syntactically invalid."""
        command = str(command or "").strip()
        if not command:
            return "empty shell command"

        try:
            result = subprocess.run(
                ["bash", "-n", "-c", command],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception as exc:
            return str(exc)

        if result.returncode == 0:
            return None

        message = (result.stderr or result.stdout or "").strip()
        return message or f"bash -n exited with {result.returncode}"

    @staticmethod
    def _extract_assigned_variables(command: str) -> List[str]:
        """Extract shell variable names from simple assignment statements."""
        names: List[str] = []
        for raw_line in str(command or "").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = _SHELL_ASSIGNMENT_RE.match(line)
            if match:
                names.append(match.group(1))
        return names

    @staticmethod
    def _command_references_variables(command: str, variable_names: List[str]) -> bool:
        """Whether a command references any variables assigned earlier."""
        if not variable_names:
            return False

        text = str(command or "")
        for name in variable_names:
            if f"${name}" in text or f"${{{name}}}" in text:
                return True
        return False

    @classmethod
    def _command_needs_grouping(cls, command: str) -> bool:
        """Heuristic for command fragments that should run as one shell program."""
        stripped = str(command or "").strip()
        if not stripped:
            return False
        if stripped.startswith("#"):
            return True
        if stripped.endswith("\\"):
            return True
        if _SHELL_CONTROL_CONTINUATION_RE.match(stripped):
            return True
        return cls._get_shell_syntax_error(stripped) is not None

    @classmethod
    def _normalize_command_list(cls, commands: List[str]) -> Tuple[List[str], List[str]]:
        """Merge fragmented shell snippets into standalone shell programs."""
        normalized: List[str] = []
        warnings: List[str] = []
        merge_count = 0
        index = 0

        while index < len(commands):
            current = str(commands[index] or "").strip()
            if not current:
                index += 1
                continue

            buffer = [current]
            assigned_vars = cls._extract_assigned_variables(current)
            next_index = index + 1

            needs_grouping = cls._command_needs_grouping(current)
            if not needs_grouping and next_index < len(commands):
                needs_grouping = cls._command_references_variables(
                    commands[next_index], assigned_vars
                )

            while needs_grouping and next_index < len(commands):
                nxt = str(commands[next_index] or "").strip()
                next_index += 1
                if not nxt:
                    continue

                buffer.append(nxt)
                assigned_vars.extend(cls._extract_assigned_variables(nxt))

                combined = "\n".join(buffer)
                if cls._get_shell_syntax_error(combined) is not None:
                    continue

                lookahead = ""
                for candidate in commands[next_index:]:
                    candidate = str(candidate or "").strip()
                    if candidate:
                        lookahead = candidate
                        break

                needs_grouping = bool(
                    lookahead
                    and (
                        cls._command_needs_grouping(lookahead)
                        or cls._command_references_variables(lookahead, assigned_vars)
                    )
                )

            if len(buffer) > 1:
                merge_count += 1
            normalized.append("\n".join(buffer))
            index = next_index

        if merge_count:
            warnings.append(
                f"Merged {merge_count} fragmented shell program(s) into standalone command blocks."
            )

        return normalized, warnings

    def _create_base_prompt(self) -> str:
        """Create the base system prompt for command generation."""
        
        # Gather system info
        try:
            uname = subprocess.check_output("uname -a", shell=True, text=True).strip()
        except:
            uname = "Unknown"
            
        try:
            # Try to get CPU info (concise)
            cpu_info = subprocess.check_output("lscpu | head -n 10", shell=True, text=True).strip()
        except:
            cpu_info = "Unknown"

        extra_context_lines: List[str] = []
        governors = self._get_available_governors()
        if governors:
            extra_context_lines.append(
                "Available CPU governors: " + ", ".join(governors)
            )
            extra_context_lines.append(
                "Preferred governor sysfs path: /sys/devices/system/cpu/cpufreq/policy*/scaling_governor"
            )

        interfaces = self._get_network_interfaces()
        if interfaces:
            extra_context_lines.append(
                "Detected network interfaces: " + ", ".join(interfaces)
            )

        tcp_ccs = self._get_available_tcp_congestion_controls()
        if tcp_ccs:
            extra_context_lines.append(
                "Available TCP congestion control algorithms: " + ", ".join(tcp_ccs)
            )

        block_scheduler_hints = self._get_block_scheduler_hints()
        if block_scheduler_hints:
            extra_context_lines.append(
                "Block scheduler choices currently exposed per device:"
            )
            extra_context_lines.extend(f"  {line}" for line in block_scheduler_hints)

        extra_context_lines.extend(self._get_scheduler_sysctl_hints())

        queue_paths = self._get_network_queue_paths()
        if queue_paths:
            extra_context_lines.append(
                "Writable network queue steering paths seen on this host:"
            )
            extra_context_lines.extend(f"  {line}" for line in queue_paths)

        system_context = f"SYSTEM INFO:\n{uname}\n\nCPU INFO:\n{cpu_info}"
        if extra_context_lines:
            system_context += "\n\nSYSTEM-SPECIFIC HINTS:\n" + "\n".join(
                f"- {line}" for line in extra_context_lines
            )
        
        # Optimization goal
        optimization_goal = self.config.optimization_goal.upper()
        optimization_metric = self.config.optimization_metric
        
        task = f"TASK: Optimize the system to {optimization_goal} {optimization_metric} by executing shell commands."
        
        # Workload description
        if getattr(self.config, 'workload_description', None):
            task += f" Workload: {self.config.workload_description}."

        measurements = "MEASUREMENTS: The workload performance metrics are NOISY. A single run might not be representative."

        constraints_section = ""
        if getattr(self.config, 'constraint_metric', None) and self.config.constraint_threshold is not None:
             c_metric = self.config.constraint_metric
             c_threshold = self.config.constraint_threshold
             c_direction = getattr(self.config, 'constraint_direction', 'less_than')
             op = "<" if c_direction == "less_than" else ">"
             constraints_section = f"\nCONSTRAINTS: You must keep {c_metric} {op} {c_threshold}. This is a hard constraint."

        prompt = f"""You are a Linux performance engineering expert with root access.
{task}
{measurements}
{constraints_section}

{system_context}

You have FULL CONTROL over the system via shell commands. You can:
- Change kernel parameters (sysctl, /sys/..., /proc/...)
- Change CPU frequency/governor
- Bind processes to cores (taskset)
- Change scheduler settings
- Any other Linux performance tuning command

COMMAND EXECUTION MODEL:
- Your commands are executed under `sudo bash -lc '<command>'`.
- Shell programs are supported: `for ...; do ...; done`, `if ...; then ...; fi`, pipes, redirects, and command substitution.
- Do NOT write commands like `sudo for ...; do ...; done`. Write the shell program directly instead.
- Avoid wrapping commands in another `sh -c` or `bash -c` unless absolutely necessary.
- Every item in `commands` must be a COMPLETE standalone shell program.
  Never split `for ... do ... done`, `if ... then ... fi`, or variable assignment/use across separate list items.
- For CPU governor changes, prefer `/sys/devices/system/cpu/cpufreq/policy*/scaling_governor`.
- Do NOT offline CPUs or write to `/sys/devices/system/cpu/cpu*/online`.
- Do NOT assume interface names like `eth0` or `eth1`; use only the detected interfaces listed above.
- Before writing to `/proc/sys` or `/sys`, prefer checking `[ -w <path> ]` or `[ -e <path> ]` so absent host-specific paths are skipped cleanly.
- For block scheduler changes, use only scheduler names that actually appear in the host hints above.
- For TCP congestion control, use only algorithms listed in the host hints above; do NOT invent values like `none`.
- Do NOT target guessed NIC paths such as `/sys/class/net/<iface>/core_rx_threshold` unless they are explicitly listed above.
- If you modify IRQ affinity, extract a single IRQ number carefully, strip the trailing `:`, and guard empty matches before writing to `/proc/irq/<irq>/smp_affinity` or `smp_affinity_list`.

OPTIMIZATION STRATEGY:
1. EXPLORE: Try different optimization strategies in early iterations.
2. ANALYZE: Look at the performance history and the commands you ran on each iteration. Check exit codes and stderr for failures.
3. ITERATE: If a command failed, try to fix it or try a different approach. If performance improved, try to refine further.
4. EXPLOIT: Once you find effective commands, fine-tune or combine them.
5. SAFETY: You are running as root. Be careful not to break the system or disconnect the session.
   - DO NOT reboot.
   - DO NOT stop essential network services that might disconnect us.
   - DO NOT delete critical files.

Respond with a JSON object containing:
- "analysis": Your reasoning for the next step (1-2 sentences).
- "commands": A list of shell commands to execute.

Example:
{{
  "analysis": "Increasing the read-ahead buffer might improve throughput for this IO-heavy workload.",
  "commands": [
    "blockdev --setra 4096 /dev/sda",
    "echo 10 > /proc/sys/vm/swappiness"
  ]
}}
"""
        return prompt

    # ------------------------------------------------------------------ #
    # History tracking: override _record_trial to capture command results
    # ------------------------------------------------------------------ #

    def _record_trial(self,
                      iteration: int,
                      metrics: BenchmarkMetrics,
                      current_params: Dict[str, Any]) -> None:
        """Record a completed trial including command results."""
        # Avoid double-recording the same iteration.
        if self._last_recorded_iteration == iteration:
            return

        reward = metrics.get_metric(self.optimization_metric)
        
        # Check if constraint was violated (same logic as parent)
        constraint_violated = False
        constraint_detail = ""
        if getattr(self.config, 'constraint_metric', None) and self.config.constraint_threshold is not None:
            constraint_value = metrics.get_metric(self.config.constraint_metric)
            direction = getattr(self.config, 'constraint_direction', 'less_than')
            
            op_symbol = ">="
            if direction in ["lt", "less_than", "<"]:
                direction = "less_than"
                op_symbol = ">="
            elif direction in ["gt", "greater_than", ">"]:
                direction = "greater_than"
                op_symbol = "<="
            
            if constraint_value is not None:
                if direction == "less_than":
                    constraint_violated = constraint_value >= self.config.constraint_threshold
                else:
                    constraint_violated = constraint_value <= self.config.constraint_threshold
                
                if constraint_violated:
                    constraint_detail = f"{constraint_value:.2f} {op_symbol} {self.config.constraint_threshold:.2f}"
            else:
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
            "command_results": list(self._last_command_results),  # capture commands
        }
        self.trial_history.append(entry)
        self._last_recorded_iteration = iteration

    # ------------------------------------------------------------------ #
    # History display: override to show commands instead of parameters
    # ------------------------------------------------------------------ #

    def _build_history_summary(self,
                               best_reward: float,
                               max_top: int = 0,
                               max_recent: int = 100) -> str:
        """Compact command-oriented history summary for the LLM."""
        history = self.trial_history
        metric = self.optimization_metric
        goal = self.optimization_goal or "maximize"

        if not history:
            return (
                f"History summary: no previous iterations yet for {metric}. "
                f"Current best={best_reward:,.2f}.\n"
            )

        lines: list[str] = []
        lines.append(f"PERFORMANCE HISTORY & COMMANDS (metric={metric}, goal={goal}):")
        lines.append(f"- Current best value: {best_reward:,.2f}")

        recent = history[-max_recent:] if max_recent > 0 else []

        lines.append(f"- Recent {len(recent)} iterations (oldest → newest):")
        for e in recent:
            constraint_note = ""
            if e.get('constraint_violated', False):
                c_metric = getattr(self.config, 'constraint_metric', 'CONSTRAINT')
                details = e.get('constraint_detail', '')
                constraint_note = f" [{c_metric}={details} CONSTRAINT VIOLATED]"
            
            reward_str = f"{e['reward']:,.2f}" if e['reward'] is not None else "None"
            line = f"  * Iteration {e['iteration']}: {metric}={reward_str}{constraint_note}"
            
            # Show commands and their results
            cmd_results = e.get('command_results', [])
            if cmd_results:
                for res in cmd_results:
                    cmd = res.get('command', '?')
                    rc = res.get('returncode', -1)
                    stdout = res.get('stdout', '').strip()
                    stderr = res.get('stderr', '').strip()
                    status = "OK" if rc == 0 else f"FAILED(rc={rc})"
                    line += f"\n      $ {cmd} -> {status}"
                    if rc == 0 and stdout:
                        stdout_short = stdout[:200]
                        line += f"\n        stdout: {stdout_short}"
                    if rc != 0 and stderr:
                        stderr_short = stderr[:200]
                        line += f"\n        stderr: {stderr_short}"
            else:
                line += "\n      (no commands in this iteration)"
            
            # Show the LLM's analysis/justification
            if e.get('justification'):
                line += f"\n      Analysis: {e['justification']}"
            
            lines.append(line)

        return "\n".join(lines) + "\n"

    def _get_top_n_summary(self, n: int, history: Optional[List[Dict[str, Any]]] = None) -> str:
        """Get summary of top N performing iterations (without empty params)."""
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
        
        # Exclude constraint violations
        top_candidates = [e for e in sorted_hist if not e.get('constraint_violated', False)]
        top = top_candidates[:n]
        
        if not top:
            return ""
        
        metric = self.optimization_metric
        lines = [f"\nTOP {len(top)} BEST ITERATIONS SO FAR:"]
        lines.append("(NOTE: These may not be global optima due to noise.)")
        for i, e in enumerate(top, 1):
            reward = e['reward']
            # Show commands instead of empty params
            cmd_results = e.get('command_results', [])
            if cmd_results:
                cmds_str = "; ".join(r.get('command', '?') for r in cmd_results)
                # Truncate if too long
                if len(cmds_str) > 120:
                    cmds_str = cmds_str[:117] + "..."
                lines.append(f"{i}. Iter {e['iteration']}: {metric}={reward:,.2f} (commands: {cmds_str})")
            else:
                lines.append(f"{i}. Iter {e['iteration']}: {metric}={reward:,.2f} (no commands)")
            
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------ #
    # Override legacy update message for command-centric display
    # ------------------------------------------------------------------ #

    def _create_legacy_update_message(self,
                               metrics: BenchmarkMetrics,
                               current_params: Dict[str, Any],
                               iteration: int,
                               best_reward: float,
                               aggregation_interval_s: Optional[float] = None,
                               phase_instruction_override: Optional[str] = None,
                               final_freeze_request: bool = False) -> str:
        """Create update message with command-centric history (Single Loop)."""
        # Build history using our command-aware summary
        history_summary = self._build_history_summary(best_reward)

        # Metric value for this iteration
        reward = metrics.get_metric(self.optimization_metric)

        # Compact constraint status
        constraint_block = ""
        if getattr(self.config, "constraint_metric", None) and self.config.constraint_threshold is not None:
            constraint_value = metrics.get_metric(self.config.constraint_metric)
            direction = getattr(self.config, "constraint_direction", "less_than")
            if constraint_value is not None:
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

        # Aggregation interval info
        aggregation_info = ""
        if aggregation_interval_s is not None:
            aggregation_info = f" (mean over past {aggregation_interval_s:.1f}s)"

        phase_text = (
            phase_instruction_override
            if phase_instruction_override is not None
            else self._get_phase_instruction(iteration)
        )
        if final_freeze_request:
            next_step = (
                f"- {phase_text}\n"
                "- Based on the performance history and the commands run on each iteration above, "
                "propose the final commands to leave in place for the stable measurement phase.\n"
                '- Return a JSON object with "analysis" (your reasoning) and "commands" (list of shell commands).\n'
            )
        else:
            next_step = (
                f"- {phase_text}\n"
                f"- Based on the performance history and the commands run on each iteration above, "
                f"propose the next set of commands for iteration #{iteration + 1}.\n"
                '- Return a JSON object with "analysis" (your reasoning) and "commands" (list of shell commands).\n'
            )

        update = f"""{history_summary}
Current iteration:
- Iteration #{iteration}: {self.optimization_metric}={reward:,.2f}{aggregation_info}
{constraint_block}
Next step:
{next_step}"""
        return update

    # ------------------------------------------------------------------ #
    # Override dual-loop update message (used when history is passed)
    # ------------------------------------------------------------------ #

    def _create_update_message(self, metrics: BenchmarkMetrics,
                               current_params: Dict[str, Any],
                               iteration: int,
                               best_reward: float,
                               history: Optional[List[Dict[str, Any]]] = None,
                               baseline_index: int = 0,
                               aggregation_interval_s: Optional[float] = None,
                               phase_instruction_override: Optional[str] = None,
                               final_freeze_request: bool = False) -> str:
        """Create update message for command tuner."""
        
        # If no history provided (single loop), use legacy path
        if history is None:
            return self._create_legacy_update_message(
                metrics,
                current_params,
                iteration,
                best_reward,
                aggregation_interval_s,
                phase_instruction_override=phase_instruction_override,
                final_freeze_request=final_freeze_request,
            )
        
        # Dual-loop path: format provided history
        history_block = ""
        if history:
             history_block = "PERFORMANCE HISTORY & COMMANDS EXECUTED:\n"
             recent_history = history[-10:]
             for entry in recent_history:
                 history_block += self._format_history_entry(entry) + "\n"
        
        # Current status
        reward = metrics.get_metric(self.optimization_metric)
        reward_str = f"{reward:,.2f}" if reward is not None else "None"
        phase_text = (
            phase_instruction_override
            if phase_instruction_override is not None
            else self._get_phase_instruction(iteration)
        )
        
        msg = f"{history_block}\n"
        msg += f"Iteration #{iteration} Result: {self.optimization_metric}={reward_str}\n"
        msg += f"\n{phase_text}\n"
        if final_freeze_request:
            msg += (
                "\nBased on the performance history and commands run above, provide the "
                "final set of commands to leave in place for the stable measurement phase."
            )
        else:
            msg += (
                "\nBased on the performance history and commands run above, provide the "
                "next set of commands to execute."
            )
        
        return msg

    # ------------------------------------------------------------------ #
    # Format history entry (used by dual-loop path)
    # ------------------------------------------------------------------ #

    def _format_history_entry(self, entry: Dict[str, Any]) -> str:
        """Format a single history entry with command results."""
        # Metric/Reward
        metric_val = entry.get('reward')
        reward_str = f"{metric_val:,.2f}" if metric_val is not None else "None"
        
        # Commands and results - check both possible locations
        command_results = entry.get('command_results', [])
        if not command_results:
            # Also check tuner_timing (optimizer history format)
            tuner_timing = entry.get('tuner_timing', {})
            if tuner_timing:
                command_results = tuner_timing.get('command_results', [])
        
        command_info = ""
        if command_results:
            command_info = "\n    Commands executed:"
            for res in command_results:
                cmd = res.get('command', '?')
                rc = res.get('returncode', -1)
                stdout = res.get('stdout', '').strip()
                stderr = res.get('stderr', '').strip()
                
                status = "OK" if rc == 0 else f"FAILED(rc={rc})"
                command_info += f"\n      $ {cmd} -> {status}"
                if rc == 0 and stdout:
                    stdout_short = stdout[:200]
                    command_info += f"\n        stdout: {stdout_short}"
                if rc != 0 and stderr:
                    stderr_short = stderr[:200]
                    command_info += f"\n        stderr: {stderr_short}"
        else:
            command_info = "\n    (no commands in this iteration)"

        # Constraint info
        constraint_note = ""
        if entry.get('constraint_violated'):
             c_metric = getattr(self.config, 'constraint_metric', 'CONSTRAINT')
             details = entry.get('constraint_detail', '')
             constraint_note = f" [{c_metric}={details} CONSTRAINT VIOLATED]"

        line = f"  * Iteration {entry.get('iteration')}: {self.optimization_metric}={reward_str}{constraint_note}"
        line += command_info
        
        # Show justification from tuner_timing or top-level
        justification = entry.get('justification') or entry.get('llm_justification', '')
        if not justification:
            tuner_timing = entry.get('tuner_timing', {})
            if tuner_timing:
                justification = tuner_timing.get('justification', '')
        if justification:
            line += f"\n    Analysis: {justification}"
        
        return line

    # ------------------------------------------------------------------ #
    # Response handling
    # ------------------------------------------------------------------ #

    def _build_response_schema(self) -> Optional[Dict[str, Any]]:
        """Build a JSON schema for structured output."""
        return {
            "type": "object",
            "properties": {
                "analysis": {
                    "type": "string",
                    "description": "Reasoning for the proposed commands"
                },
                "commands": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "description": "Shell command to execute"
                    },
                    "description": "List of shell commands to execute in order"
                }
            },
            "required": ["analysis", "commands"]
        }

    def suggest_parameters(self, metrics: BenchmarkMetrics,
                           current_params: Dict[str, Any],
                           iteration: int,
                           best_reward: float = 0.0,
                           history: Optional[List[Dict[str, Any]]] = None,
                           baseline_index: int = 0,
                           aggregation_interval_s: Optional[float] = None,
                           phase_instruction_override: Optional[str] = None,
                           final_freeze_request: bool = False) -> TunerResponse:
        """Suggest new commands using LLM.
        
        Wraps parent's suggest_parameters and extracts commands from the
        _commands key that _parse_structured_response packs into params.
        After getting the response, stores the command results so that
        _record_trial can include them in trial_history.
        """
        response = super().suggest_parameters(
            metrics, current_params, iteration, best_reward, 
            history,
            baseline_index,
            aggregation_interval_s,
            phase_instruction_override=phase_instruction_override,
            final_freeze_request=final_freeze_request,
        )
        
        # Extract commands from parameters if present
        if "_commands" in response.parameters:
            commands = response.parameters.pop("_commands")
            response.commands = commands
            
        return response

    def _parse_structured_response(self, parsed_data: Any) -> Tuple[Dict[str, Any], Optional[str], List[str]]:
        """Parse structured response and pack commands into params temporarily."""
        if isinstance(parsed_data, dict):
            data = parsed_data
        elif hasattr(parsed_data, '__dict__'):
            data = parsed_data.__dict__
        else:
            return {}, None, ["Invalid response format"]

        analysis = str(data.get("analysis", ""))
        commands = data.get("commands", [])
        
        # Validate commands
        valid_commands = []
        warnings = []
        if isinstance(commands, list):
            for cmd in commands:
                if isinstance(cmd, str) and cmd.strip():
                    valid_commands.append(cmd.strip())
                else:
                    warnings.append(f"Ignored invalid command: {cmd}")

        valid_commands, normalization_warnings = self._normalize_command_list(valid_commands)
        warnings.extend(normalization_warnings)
        
        # Pack into params to pass through LLMTuner.suggest_parameters behavior
        params = {"_commands": valid_commands}
        
        return params, analysis, warnings

    def _parse_response(self, response_text: str) -> Tuple[Dict[str, Any], Optional[str], List[str]]:
        """Parse unstructured response (fallback)."""
        # Look for JSON block
        json_match = re.search(r'(\{.*?\})', response_text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                return self._parse_structured_response(data)
            except json.JSONDecodeError:
                pass
        
        return {}, None, ["Failed to parse JSON response"]
