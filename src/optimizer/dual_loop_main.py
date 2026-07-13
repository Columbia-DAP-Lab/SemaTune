#!/usr/bin/env python3
"""
Main entry point for dual-loop OS parameter tuning.

This module provides a command-line interface for running dual-loop
optimization with Actor (Reasoning) and Speculator (Quick) agents.
"""

import os
import sys
import logging
import argparse
import signal
import atexit

# Add src directory to path so optimizer package can be imported
_script_dir = os.path.dirname(os.path.abspath(__file__))
_src_dir = os.path.dirname(_script_dir)
if _src_dir in sys.path:
    sys.path.remove(_src_dir)
sys.path.insert(0, _src_dir)

from optimizer.config import SimpleConfig
from optimizer.benchmarks.sysbench import SysbenchBenchmark
from optimizer.benchmarks.sysbench import SysbenchContinuousBenchmark
from optimizer.benchmarks.benchbase import BenchBaseBenchmark
from optimizer.benchmarks.mutilate_benchmark import MutilateBenchmark
from optimizer.benchmarks.tailbench import TailbenchBenchmark
from optimizer.dual_loop_optimizer import SimpleDualLoopOptimizer
from optimizer.benchmarks.benchmark_registry import BenchmarkType

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s - %(asctime)s - %(filename)s:%(lineno)d - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("dual_loop_optimizer.log")
    ]
)
logger = logging.getLogger("dual_loop_main")

# Global reference for signal handlers
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
            logger.error(f"Error during benchmark cleanup: {e}")
    
    # Then cleanup optimizer (save history, reset params)
    if _global_optimizer is not None:
        try:
            logger.info("Saving optimizer history and resetting parameters...")
            # This calls _finish which handles history saving and parameter reset
            result = _global_optimizer._finish("interrupted" if signum else "normal_exit")
            if result.get('history_file'):
                logger.info(f"History saved to: {result['history_file']}")
        except KeyboardInterrupt:
            # If interrupted during _finish (likely during parameter reset), exit early
            logger.warning("Interrupted during cleanup - exiting without completing parameter reset")
            logger.warning("Use Ctrl+C during parameter reset to exit early")
            os._exit(1)
        except Exception as e:
            logger.error(f"Error during optimizer cleanup: {e}")


def setup_signal_handlers():
    """Setup signal handlers for graceful shutdown."""
    atexit.register(cleanup_handler)
    signal.signal(signal.SIGINT, cleanup_handler)
    signal.signal(signal.SIGTERM, cleanup_handler)
    logger.info("Signal handlers registered")


def create_benchmark(config: SimpleConfig):
    """Create benchmark instance based on config."""
    benchmark_name = config.benchmark
    
    try:
        BenchmarkType.from_string(benchmark_name)
    except ValueError as e:
        raise ValueError(f"Unknown benchmark: {benchmark_name}. Available: {BenchmarkType.list_all()}") from e
    
    if benchmark_name == "sysbench_oltp_continuous":
        return SysbenchContinuousBenchmark(config)
    elif benchmark_name in ("tpcc", "ycsb", "sibench", "wikipedia", "twitter", "auctionmark", "otmetrics"):
        return BenchBaseBenchmark(config)
    elif benchmark_name == "mutilate":
        return MutilateBenchmark(config)
    elif benchmark_name == "tailbench":
        return TailbenchBenchmark(config)
    elif benchmark_name.startswith("sysbench"):
        return SysbenchBenchmark(config)
    else:
        raise ValueError(f"Unknown benchmark: {benchmark_name}")


def main():
    """Main entry point."""
    global _global_optimizer, _global_benchmark
    
    setup_signal_handlers()
    
    parser = argparse.ArgumentParser(
        description="Dual-loop OS Parameter Optimizer (Actor-Speculator)",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument("-c", "--config", required=True, help="Path to configuration file")
    parser.add_argument("--replay", help="Path to replay history JSON file")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    
    args = parser.parse_args()
    
    logging.getLogger().setLevel(getattr(logging, args.log_level))
    
    # Load config
    try:
        config = SimpleConfig.load(args.config)
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)
    
    if args.replay:
        config.llm_replay_file = args.replay
        logger.info(f"Replay file set to: {args.replay}")
    
    # Guard: only run dual-loop if BOTH actor and speculator models are set
    if not getattr(config, '_explicit_dual_loop', False):
        logger.error(
            "This config does not define both 'llm_actor_model' and 'llm_speculator_model'. "
            "It is a single-loop config. Use 'python -m optimizer.main' instead."
        )
        sys.exit(1)

    # Force LLM tuner type for dual loop
    if config.tuner_type != "llm":
        logger.warning("Dual-loop optimizer requires LLM tuner; overriding tuner_type to 'llm'")
        config.tuner_type = "llm"
    
    # Create benchmark
    try:
        benchmark = create_benchmark(config)
        _global_benchmark = benchmark
    except Exception as e:
        logger.error(f"Failed to create benchmark: {e}")
        sys.exit(1)
    
    # Run optimizer
    try:
        optimizer = SimpleDualLoopOptimizer(config, benchmark)
        _global_optimizer = optimizer
        
        result = optimizer.run()

        if result.get("terminated_reason") == "error":
            print("\n" + "="*80)
            print("DUAL-LOOP OPTIMIZATION TERMINATED WITH ERROR")
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
        
        print("\n" + "="*80)
        print("DUAL-LOOP OPTIMIZATION COMPLETE")
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
        # Handled by signal handler
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
