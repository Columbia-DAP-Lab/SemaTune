#!/usr/bin/env python3
"""
Main entry point for single-loop OS parameter tuning.

This module provides a command-line interface for running single-loop
optimization with fixed or LLM tuners.
"""

import os
import sys
import logging
import argparse
import signal
import atexit

# Add src directory to path so barebones_optimizer package can be imported
# This allows the script to be run directly without PYTHONPATH or python -m
_script_dir = os.path.dirname(os.path.abspath(__file__))
_src_dir = os.path.dirname(_script_dir)
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from barebones_optimizer.config import SimpleConfig
from barebones_optimizer.benchmarks.sysbench import SysbenchBenchmark
from barebones_optimizer.benchmarks.sysbench import SysbenchContinuousBenchmark
from barebones_optimizer.benchmarks.benchbase import BenchBaseBenchmark
from barebones_optimizer.benchmarks.mutilate_benchmark import MutilateBenchmark
from barebones_optimizer.benchmarks.tailbench import TailbenchBenchmark
from barebones_optimizer.benchmarks.dcperf import DCPerfSparkBenchmark, DCPerfMediawikiBenchmark, DCPerfDjangoBenchmark
from barebones_optimizer.tuners import (
    FixedTuner, LLMTuner, HumanTuner, QLearningTuner,
    BayesianOptimizerTuner, DQNTuner, MLOSTuner, SimpleSMACTuner,
    LLMCommandTuner
)
from barebones_optimizer.optimizer import SimpleOptimizer
from barebones_optimizer.benchmarks.benchmark_registry import BenchmarkType

# Configure logging (FileHandler only if writable, else stdout only)
_log_handlers = [logging.StreamHandler()]
try:
    _log_handlers.append(logging.FileHandler("barebones_optimizer.log"))
except (OSError, PermissionError):
    pass
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s - %(asctime)s - %(filename)s:%(lineno)d - %(message)s',
    handlers=_log_handlers
)
logger = logging.getLogger("main")

# Global reference to optimizer for signal handling
_global_optimizer = None
_global_benchmark = None
_cleanup_in_progress = False


def cleanup_handler(signum=None, frame=None):
    """Handle cleanup on signal or exit.
    
    If interrupted during parameter reset, exits early without retrying.
    Ensures all child processes are terminated.
    On second signal during cleanup, force exits immediately.
    """
    global _global_optimizer, _global_benchmark, _cleanup_in_progress
    
    # If already cleaning up, force exit immediately on second signal
    if _cleanup_in_progress:
        if signum is not None:
            logger.warning(f"Received second signal {signum} during cleanup - forcing immediate exit")
            os._exit(1)
        return  # Don't run cleanup twice from atexit
    
    _cleanup_in_progress = True
    
    if signum is not None:
        logger.info(f"Received signal {signum}, cleaning up...")
    else:
        logger.info("Performing cleanup on exit...")
    
    # Cleanup benchmark first (kill running processes)
    if _global_benchmark is not None:
        try:
            if hasattr(_global_benchmark, 'cleanup'):
                logger.info("Calling benchmark cleanup (killing processes)...")
                _global_benchmark.cleanup()
        except KeyboardInterrupt:
            logger.warning("Interrupted during benchmark cleanup - forcing exit")
            # Don't retry, just exit
            os._exit(1)
        except Exception as e:
            logger.error(f"Error during benchmark cleanup: {e}", exc_info=True)
    
    # Then cleanup optimizer (save history, reset params)
    if _global_optimizer is not None:
        try:
            logger.info("Saving optimizer history and resetting parameters...")
            result = _global_optimizer._finish("interrupted" if signum else "normal_exit")
            if result.get('history_file'):
                logger.info(f"History saved to: {result['history_file']}")
        except KeyboardInterrupt:
            # If interrupted during _finish (likely during parameter reset), exit early
            logger.warning("Interrupted during cleanup - exiting without completing parameter reset")
            logger.warning("Use Ctrl+C during parameter reset to exit early")
            os._exit(1)
        except Exception as e:
            logger.error(f"Error during optimizer cleanup: {e}", exc_info=True)


def setup_signal_handlers():
    """Setup signal handlers for graceful shutdown."""
    # Register cleanup on normal exit
    atexit.register(cleanup_handler)
    
    # Register signal handlers
    signal.signal(signal.SIGINT, cleanup_handler)
    signal.signal(signal.SIGTERM, cleanup_handler)
    logger.info("Signal handlers registered")


def create_benchmark(config: SimpleConfig):
    """Create benchmark instance based on config.
    
    Args:
        config: Configuration object
        
    Returns:
        Benchmark instance
    """
    benchmark_name = config.benchmark
    
    # Get benchmark info from registry
    try:
        benchmark_type = BenchmarkType.from_string(benchmark_name)
    except ValueError as e:
        raise ValueError(f"Unknown benchmark: {benchmark_name}. Available: {BenchmarkType.list_all()}") from e
    
    # Create appropriate benchmark instance
    if benchmark_name == "sysbench_oltp_continuous":
        # Special case for continuous OLTP
        return SysbenchContinuousBenchmark(config)
    elif benchmark_name in ("tpcc", "ycsb", "sibench", "wikipedia", "twitter", "auctionmark", "otmetrics"):
        return BenchBaseBenchmark(config)
    elif benchmark_name == "mutilate":
        return MutilateBenchmark(config)
    elif benchmark_name == "tailbench":
        return TailbenchBenchmark(config)
    elif benchmark_name == "dcperf_spark":
        return DCPerfSparkBenchmark(config)
    elif benchmark_name == "dcperf_mediawiki":
        return DCPerfMediawikiBenchmark(config)
    elif benchmark_name == "dcperf_django":
        return DCPerfDjangoBenchmark(config)
    elif benchmark_name.startswith("sysbench"):
        # All other sysbench benchmarks use unified implementation
        return SysbenchBenchmark(config)
    else:
        raise ValueError(f"Unknown benchmark: {benchmark_name}")


def create_tuner(config: SimpleConfig):
    """Create tuner instance based on config.
    
    Args:
        config: Configuration object
        
    Returns:
        Tuner instance
    """
    from barebones_optimizer.main_helpers import create_tuner_from_config
    return create_tuner_from_config(config)


def create_trimming_tuner(config: SimpleConfig):
    """Create LLM trimming tuner if enabled in config.
    
    Args:
        config: Configuration object
        
    Returns:
        LLMTrimmingTuner instance if enabled, None otherwise
    """
    if not config.trimming_enabled:
        return None
    
    from barebones_optimizer.tuners import LLMTrimmingTuner
    import copy
    
    # Create a config copy with the trimming model if specified
    trimming_config = copy.copy(config)
    
    # Logic to determine trimming model name:
    # 1. Explicit trimming_model_name in config (highest priority)
    # 2. If strategy="dual_loop": use speculator/secondary model
    # 3. Else (single_loop): use actor model or default
    
    trimming_strategy = getattr(config, 'trimming_strategy', 'single_loop')
    target_model_name = None
    
    if config.trimming_model_name:
        target_model_name = config.trimming_model_name
    elif trimming_strategy == "dual_loop":
        # Prefer speculator/secondary for dual_loop strategy
        target_model_name = getattr(config, 'llm_speculator_model', None) or getattr(config, 'llm_secondary_model', None)
        
        # Fallback if no spec model configured
        if not target_model_name:
            logger.warning("trimming_strategy='dual_loop' but no speculator/secondary model found. Falling back to default.")
            target_model_name = config.llm_model_name
    else:
        # Prefer actor/default for single_loop strategy
        target_model_name = getattr(config, 'llm_actor_model', None) or config.llm_model_name

    if target_model_name:
        trimming_config.llm_model_name = target_model_name
    
    logger.info(f"Creating trimming tuner (model: {trimming_config.llm_model_name}, "
                f"cycles: {config.trimming_cycles}, strategy: {trimming_strategy})")
    
    return LLMTrimmingTuner(trimming_config, agent_type="single")


def main():
    """Main entry point."""
    global _global_optimizer, _global_benchmark
    
    # Setup signal handlers FIRST
    setup_signal_handlers()
    
    parser = argparse.ArgumentParser(
        description="Single-loop OS Parameter Optimizer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
  # Run with config file
  python main.py -c config/config.json

  # Run with fixed tuner
  python main.py -c config/config.json --tuner fixed

  # Run with LLM tuner and replay
  python main.py -c config/config.json --tuner llm --replay history.json

Available benchmarks:
  {', '.join(BenchmarkType.list_all())}
        """
    )
    
    parser.add_argument(
        "-c", "--config",
        required=True,
        help="Path to configuration file (JSON format)"
    )
    
    parser.add_argument(
        "--tuner",
        choices=["fixed", "llm", "human", "qlearning", "bayesian", "dqn", "mlos", "simple_smac", "llm_command"],
        help="Override tuner type from config"
    )
    
    parser.add_argument(
        "--replay",
        help="Path to replay history JSON file"
    )
    
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Set the logging level"
    )
    
    args = parser.parse_args()
    
    # Set log level
    logging.getLogger().setLevel(getattr(logging, args.log_level))
    
    # Load configuration
    try:
        config = SimpleConfig.load(args.config)
    except FileNotFoundError:
        logger.error(f"Configuration file not found: {args.config}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)
    
    # Apply command-line overrides
    if args.tuner:
        config.tuner_type = args.tuner
        logger.info(f"Tuner type overridden to: {args.tuner}")
    
    if args.replay:
        config.llm_replay_file = args.replay
        logger.info(f"Replay file set to: {args.replay}")
    
    # Validate configuration
    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Configuration validation failed: {e}")
        sys.exit(1)
    # Create benchmark (common for all optimizers)
    try:
        benchmark = create_benchmark(config)
        _global_benchmark = benchmark
    except Exception as e:
        logger.error(f"Failed to create benchmark: {e}")
        sys.exit(1)
    
    # Create and run optimizer
    optimizer = None
    try:
        # Check if we should use dual-loop optimizer
        use_dual_loop = False
        if config.tuner_type == "llm":
            if getattr(config, '_explicit_dual_loop', False):
                use_dual_loop = True
                logger.info("Dual-loop configuration detected (llm_actor_model/llm_speculator_model explicitly set)")
        
        if use_dual_loop:
            from barebones_optimizer.dual_loop_optimizer import SimpleDualLoopOptimizer
            logger.info("Initializing SimpleDualLoopOptimizer...")
            optimizer = SimpleDualLoopOptimizer(config, benchmark)
        else:
            tuner = create_tuner(config)
            trimming_tuner = create_trimming_tuner(config)
            optimizer = SimpleOptimizer(config, benchmark, tuner, trimming_tuner=trimming_tuner)
        
        # Set global reference for signal handling
        _global_optimizer = optimizer
        
        result = optimizer.run()

        if result.get("terminated_reason") == "error":
            print("\n" + "="*80)
            print("OPTIMIZATION TERMINATED WITH ERROR")
            print("="*80)
            print(f"Iterations: {result['iterations']}")
            print(f"Total time: {result['total_time']:.1f}s")
            print(f"Best reward: {result['best_reward']:.4f}")
            print(f"Best parameters: {result['best_parameters']}")
            print(f"Termination reason: {result['terminated_reason']}")
            if result.get('history_file'):
                print(f"History saved to: {result['history_file']}")
            print("="*80)
            sys.exit(1)
        
        # Print summary
        print("\n" + "="*80)
        print("OPTIMIZATION COMPLETE")
        print("="*80)
        print(f"Iterations: {result['iterations']}")
        print(f"Total time: {result['total_time']:.1f}s")
        print(f"Best reward: {result['best_reward']:.4f}")
        print(f"Best parameters: {result['best_parameters']}")
        print(f"Termination reason: {result['terminated_reason']}")
        if result.get('history_file'):
            print(f"History saved to: {result['history_file']}")
        print("="*80)
        
    except KeyboardInterrupt:
        logger.info("Optimization interrupted by user")
        print("\nOptimization interrupted by user")
        # Signal handler will handle cleanup
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        print(f"Error: {e}")
        # Signal handler will handle cleanup
        sys.exit(1)


if __name__ == "__main__":
    main()
