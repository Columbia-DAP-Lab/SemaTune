#!/usr/bin/env python3
"""Tuner interfaces with optional implementations loaded on first use.

The Functional example needs only Fixed and LLM tuners.  Keeping optional
classical tuners lazy prevents importing PyTorch, SMAC, MLOS, or ConfigSpace
when an evaluator runs the minimal example.
"""

from importlib import import_module
from typing import Any

from barebones_optimizer.tuners.base import TunerInterface, TunerResponse


_LAZY_EXPORTS = {
    "FixedTuner": ("barebones_optimizer.tuners.fixed", "FixedTuner"),
    "LLMTuner": ("barebones_optimizer.tuners.llm", "LLMTuner"),
    "HumanTuner": ("barebones_optimizer.tuners.human", "HumanTuner"),
    "QLearningTuner": ("barebones_optimizer.tuners.qlearning", "QLearningTuner"),
    "BayesianOptimizerTuner": ("barebones_optimizer.tuners.bayesian", "BayesianOptimizerTuner"),
    "DQNTuner": ("barebones_optimizer.tuners.dqn", "DQNTuner"),
    "MLOSTuner": ("barebones_optimizer.tuners.mlos_tuner", "MLOSTuner"),
    "SimpleSMACTuner": ("barebones_optimizer.tuners.simple_smac_tuner", "SimpleSMACTuner"),
    "LLMCommandTuner": ("barebones_optimizer.tuners.llm_command", "LLMCommandTuner"),
    "LLMTrimmingTuner": ("barebones_optimizer.tuners.llm_trimming", "LLMTrimmingTuner"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))


__all__ = ["TunerInterface", "TunerResponse", *_LAZY_EXPORTS]
