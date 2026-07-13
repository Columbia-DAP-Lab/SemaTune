#!/usr/bin/env python3
"""Tuner interfaces with optional implementations loaded on first use.

The Functional example needs only Fixed and LLM tuners.  Keeping optional
classical tuners lazy prevents importing PyTorch, SMAC, MLOS, or ConfigSpace
when an evaluator runs the minimal example.
"""

from importlib import import_module
from typing import Any

from optimizer.tuners.base import TunerInterface, TunerResponse


_LAZY_EXPORTS = {
    "FixedTuner": ("optimizer.tuners.fixed", "FixedTuner"),
    "LLMTuner": ("optimizer.tuners.llm", "LLMTuner"),
    "QLearningTuner": ("optimizer.tuners.qlearning", "QLearningTuner"),
    "BayesianOptimizerTuner": ("optimizer.tuners.bayesian", "BayesianOptimizerTuner"),
    "DQNTuner": ("optimizer.tuners.dqn", "DQNTuner"),
    "MLOSTuner": ("optimizer.tuners.mlos_tuner", "MLOSTuner"),
    "LLMTrimmingTuner": ("optimizer.tuners.llm_trimming", "LLMTrimmingTuner"),
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
