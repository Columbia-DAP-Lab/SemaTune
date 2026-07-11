#!/usr/bin/env python3
"""
Helper functions extracted from main.py to allow use by optimizer.py
without circular imports.
"""

from barebones_optimizer.config import SimpleConfig


def create_tuner_from_config(config: SimpleConfig):
    """Create tuner instance based on config.
    
    This is extracted from main.py so optimizer.py can re-create the primary
    tuner after the trimming phase without circular imports.
    
    Args:
        config: Configuration object
        
    Returns:
        Tuner instance
    """
    if config.tuner_type == "fixed":
        from barebones_optimizer.tuners.fixed import FixedTuner
        return FixedTuner(config)
    elif config.tuner_type == "llm":
        from barebones_optimizer.tuners.llm import LLMTuner
        return LLMTuner(config, agent_type="single")
    elif config.tuner_type == "human":
        from barebones_optimizer.tuners.human import HumanTuner
        return HumanTuner(config)
    elif config.tuner_type == "qlearning":
        from barebones_optimizer.tuners.qlearning import QLearningTuner
        return QLearningTuner(config)
    elif config.tuner_type == "bayesian":
        from barebones_optimizer.tuners.bayesian import BayesianOptimizerTuner
        return BayesianOptimizerTuner(config)
    elif config.tuner_type == "dqn":
        from barebones_optimizer.tuners.dqn import DQNTuner
        return DQNTuner(config)
    elif config.tuner_type == "mlos":
        from barebones_optimizer.tuners.mlos_tuner import MLOSTuner
        return MLOSTuner(config)
    elif config.tuner_type == "simple_smac":
        from barebones_optimizer.tuners.simple_smac_tuner import SimpleSMACTuner
        return SimpleSMACTuner(config)
    elif config.tuner_type == "llm_command":
        from barebones_optimizer.tuners.llm_command import LLMCommandTuner
        return LLMCommandTuner(config, agent_type="single")
    else:
        raise ValueError(f"Unknown tuner type: {config.tuner_type}")
