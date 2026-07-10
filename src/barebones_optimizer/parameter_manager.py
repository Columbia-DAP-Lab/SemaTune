#!/usr/bin/env python3
"""
Scheduler parameter manager for Linux kernel scheduler parameters.

This module manages setting and validating OS scheduler parameters like
latency_ns, min_granularity_ns, etc. It's designed to be easily extensible
for new parameters.

Also contains parameter metadata including parameter types, per-core support,
and valid values for categorical parameters.
"""

import subprocess
import logging
import os
import re
import inspect
from typing import Dict, Optional, Set, Union, Any, List, Tuple

logger = logging.getLogger(__name__)

# Parameter metadata
PARAMETER_METADATA: Dict[str, Dict[str, Any]] = {
    # Scheduler parameters (continuous/discrete)
    "latency_ns": {
        "type": "continuous",
        "per_core": False,
        "description": "CFS scheduler latency in nanoseconds"
    },
    "min_granularity_ns": {
        "type": "continuous",
        "per_core": False,
        "description": "Minimum scheduling granularity in nanoseconds"
    },
    "wakeup_granularity_ns": {
        "type": "continuous",
        "per_core": False,
        "description": "Wakeup granularity in nanoseconds"
    },
    "migration_cost_ns": {
        "type": "continuous",
        "per_core": False,
        "description": "Migration cost in nanoseconds"
    },
    
    # DVFS/Turbo parameters
    "scaling_governor": {
        "type": "categorical",
        "per_core": True,
        "valid_values": ["performance", "powersave", "ondemand", "conservative", "userspace", "schedutil"],
        "description": "CPU frequency scaling governor"
    },
    "scaling_min_freq": {
        "type": "continuous",
        "per_core": True,
        "description": "Minimum CPU frequency in kHz (0 = use system default)"
    },
    "scaling_max_freq": {
        "type": "continuous",
        "per_core": True,
        "description": "Maximum CPU frequency in kHz (0 = use system default)"
    },
    "epp": {
        "type": "categorical",
        "per_core": True,
        "valid_values": ["default", "performance", "balance_performance", "balance_power", "power"],
        "description": "Energy-Performance Preference"
    },
    "min_perf_pct": {
        "type": "continuous",
        "per_core": True,
        "description": "Minimum performance percentage (0-100)"
    },
    "max_perf_pct": {
        "type": "continuous",
        "per_core": True,
        "description": "Maximum performance percentage (0-100)"
    },
    "turbo": {
        "type": "categorical",
        "per_core": False,
        "valid_values": [True, False],
        "description": "Turbo boost enable/disable"
    },
    
    # C-states / PM QoS
    "pmqos": {
        "type": "continuous",
        "per_core": True,
        "description": "PM QoS resume latency in microseconds"
    },
    "cstate_max": {
        "type": "categorical",
        "per_core": True,
        "valid_values": ["unlimited", "none", "poll", "C1", "C1E", "C2", "C6", "C6only"],
        "description": "Maximum C-state allowed (unlimited=all enabled, none=poll only, poll=POLL, C1=C1, C1E=C1E, C2=C2, C6=C6, C6only=C6 only)"
    },
    
    # NAPI & busy polling
    "busy_poll": {
        "type": "continuous",
        "per_core": True,
        "description": "Busy poll timeout in microseconds"
    },
    "napi_busy_poll": {
        "type": "continuous",
        "per_core": True,
        "description": "Alias for busy_poll (net.core.busy_poll)"
    },
    "busy_read": {
        "type": "continuous",
        "per_core": True,
        "description": "Busy read timeout in microseconds"
    },
    "netdev_budget": {
        "type": "continuous",
        "per_core": True,
        "description": "Netdev budget (packets per NAPI poll)"
    },
    "netdev_budget_usecs": {
        "type": "continuous",
        "per_core": True,
        "description": "Netdev budget time in microseconds"
    },
    
    # VM writeback and memory reclaim behavior
    "vm_swappiness": {
        "type": "continuous",
        "per_core": False,
        "description": "Relative aggressiveness of swap usage (vm.swappiness)"
    },
    "vm_dirty_ratio": {
        "type": "continuous",
        "per_core": False,
        "description": "Max percent of dirty memory before writeback blocks tasks"
    },
    "vm_dirty_background_ratio": {
        "type": "continuous",
        "per_core": False,
        "description": "Percent of dirty memory at which background writeback starts"
    },
    "vm_dirty_expire_centisecs": {
        "type": "continuous",
        "per_core": False,
        "description": "Age threshold for dirty data before considered old (centiseconds)"
    },
    "vm_dirty_writeback_centisecs": {
        "type": "continuous",
        "per_core": False,
        "description": "Periodic writeback interval for dirty pages (centiseconds)"
    },
    "zone_reclaim_mode": {
        "type": "continuous",
        "per_core": False,
        "description": "NUMA zone reclaim policy bitmask (vm.zone_reclaim_mode)"
    },
    
    # Scheduler and NUMA global controls
    "numa_balancing": {
        "type": "categorical",
        "per_core": False,
        "valid_values": [True, False],
        "description": "Enable or disable automatic NUMA balancing"
    },
    "sched_autogroup_enabled": {
        "type": "categorical",
        "per_core": False,
        "valid_values": [True, False],
        "description": "Enable or disable scheduler autogrouping"
    },
    "sched_cfs_bandwidth_slice_us": {
        "type": "continuous",
        "per_core": False,
        "description": "CFS bandwidth controller slice interval in microseconds"
    },
    
    # Additional network stack controls
    "somaxconn": {
        "type": "continuous",
        "per_core": False,
        "description": "Maximum listen backlog (net.core.somaxconn)"
    },
    "netdev_max_backlog": {
        "type": "continuous",
        "per_core": False,
        "description": "Maximum number of incoming packets queued by the network stack"
    },
    "rmem_default": {
        "type": "continuous",
        "per_core": False,
        "description": "Default receive socket buffer size in bytes"
    },
    "wmem_default": {
        "type": "continuous",
        "per_core": False,
        "description": "Default send socket buffer size in bytes"
    },
    "rmem_max": {
        "type": "continuous",
        "per_core": False,
        "description": "Maximum receive socket buffer size in bytes"
    },
    "wmem_max": {
        "type": "continuous",
        "per_core": False,
        "description": "Maximum send socket buffer size in bytes"
    },
    "tcp_fin_timeout": {
        "type": "continuous",
        "per_core": False,
        "description": "FIN-WAIT-2 timeout in seconds"
    },
    "tcp_tw_reuse": {
        "type": "categorical",
        "per_core": False,
        "valid_values": [0, 1, 2],
        "description": "TIME-WAIT socket reuse mode (0=off, 1=global, 2=loopback only)"
    },
    "tcp_mtu_probing": {
        "type": "categorical",
        "per_core": False,
        "valid_values": [0, 1, 2],
        "description": "TCP path MTU probing mode"
    },
    "tcp_timestamps": {
        "type": "categorical",
        "per_core": False,
        "valid_values": [0, 1],
        "description": "Enable or disable TCP timestamps"
    },
    "tcp_sack": {
        "type": "categorical",
        "per_core": False,
        "valid_values": [0, 1],
        "description": "Enable or disable TCP selective acknowledgments"
    },
    "tcp_window_scaling": {
        "type": "categorical",
        "per_core": False,
        "valid_values": [0, 1],
        "description": "Enable or disable TCP window scaling"
    },
    "tcp_fastopen": {
        "type": "continuous",
        "per_core": False,
        "description": "TCP Fast Open bitmask mode"
    },
    "tcp_congestion_control": {
        "type": "categorical",
        "per_core": False,
        "valid_values": ["reno", "cubic", "bbr"],
        "description": "TCP congestion control algorithm"
    },
}

# Parameter dependencies (can be extended)
PARAMETER_DEPENDENCIES = {
    "min_granularity_ns": ["latency_ns", "wakeup_granularity_ns"],
    "wakeup_granularity_ns": ["latency_ns", "min_granularity_ns"],
    "latency_ns": ["min_granularity_ns", "wakeup_granularity_ns"],
}

# New parameters that must rely on live system defaults (read via sysctl)
NEW_PARAMETER_SYSCTL_KEYS: Dict[str, str] = {
    "vm_swappiness": "vm.swappiness",
    "vm_dirty_ratio": "vm.dirty_ratio",
    "vm_dirty_background_ratio": "vm.dirty_background_ratio",
    "vm_dirty_expire_centisecs": "vm.dirty_expire_centisecs",
    "vm_dirty_writeback_centisecs": "vm.dirty_writeback_centisecs",
    "zone_reclaim_mode": "vm.zone_reclaim_mode",
    "numa_balancing": "kernel.numa_balancing",
    "sched_autogroup_enabled": "kernel.sched_autogroup_enabled",
    "sched_cfs_bandwidth_slice_us": "kernel.sched_cfs_bandwidth_slice_us",
    "somaxconn": "net.core.somaxconn",
    "netdev_max_backlog": "net.core.netdev_max_backlog",
    "rmem_default": "net.core.rmem_default",
    "wmem_default": "net.core.wmem_default",
    "rmem_max": "net.core.rmem_max",
    "wmem_max": "net.core.wmem_max",
    "tcp_fin_timeout": "net.ipv4.tcp_fin_timeout",
    "tcp_tw_reuse": "net.ipv4.tcp_tw_reuse",
    "tcp_mtu_probing": "net.ipv4.tcp_mtu_probing",
    "tcp_timestamps": "net.ipv4.tcp_timestamps",
    "tcp_sack": "net.ipv4.tcp_sack",
    "tcp_window_scaling": "net.ipv4.tcp_window_scaling",
    "tcp_fastopen": "net.ipv4.tcp_fastopen",
    "tcp_congestion_control": "net.ipv4.tcp_congestion_control",
}

NEW_PARAMETER_BOOL_NAMES: Set[str] = {"numa_balancing", "sched_autogroup_enabled"}

# Snapshot of live defaults captured at run start for new parameters.
_NEW_PARAMETER_DEFAULT_SNAPSHOT: Optional[Dict[str, Union[int, str, bool]]] = None

def get_parameter_type(param_name: str) -> str:
    """Get parameter type: 'continuous', 'discrete', or 'categorical'."""
    meta = PARAMETER_METADATA.get(param_name, {})
    return meta.get("type", "continuous")


def is_per_core_parameter(param_name: str) -> bool:
    """Check if parameter supports per-core application."""
    meta = PARAMETER_METADATA.get(param_name, {})
    return meta.get("per_core", False)


def get_categorical_values(param_name: str) -> Optional[List[Any]]:
    """Get valid values for categorical parameter."""
    meta = PARAMETER_METADATA.get(param_name, {})
    return meta.get("valid_values", None)


def get_parameter_description(param_name: str) -> str:
    """Get parameter description."""
    meta = PARAMETER_METADATA.get(param_name, {})
    return meta.get("description", param_name)


def get_parameter_dependencies(param_name: str) -> List[str]:
    """Get list of parameters that depend on or are related to this parameter."""
    return PARAMETER_DEPENDENCIES.get(param_name, [])


def get_new_parameter_names() -> Set[str]:
    """Get the set of parameters that must use live system defaults."""
    return set(NEW_PARAMETER_SYSCTL_KEYS.keys())


def _read_sysctl_value(sysctl_key: str) -> Optional[str]:
    """Read a sysctl key value as raw string."""
    commands = [["sysctl", "-n", sysctl_key]]
    if os.geteuid() != 0:
        commands.append(["sudo", "-n", "sysctl", "-n", sysctl_key])
    
    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            return result.stdout.strip()
        except Exception:
            continue
    return None


def _discover_system_defaults_for_new_parameters() -> Dict[str, Union[int, str, bool]]:
    """Discover defaults for new parameters from the running system."""
    defaults: Dict[str, Union[int, str, bool]] = {}
    
    for param_name, sysctl_key in NEW_PARAMETER_SYSCTL_KEYS.items():
        raw_value = _read_sysctl_value(sysctl_key)
        if raw_value is None:
            logger.warning(f"Could not read system default for {param_name} ({sysctl_key})")
            continue
        
        if param_name in NEW_PARAMETER_BOOL_NAMES:
            defaults[param_name] = (raw_value == "1")
            continue
        
        if param_name == "tcp_congestion_control":
            defaults[param_name] = raw_value
            continue
        
        try:
            defaults[param_name] = int(raw_value)
        except ValueError:
            logger.warning(
                f"Unexpected non-integer value for {param_name} ({sysctl_key}): {raw_value}"
            )
    
    return defaults


def get_system_defaults_for_new_parameters(refresh_snapshot: bool = False) -> Dict[str, Union[int, str, bool]]:
    """Get live defaults for new parameters (snapshot-aware).
    
    The first call captures a snapshot of current values. Later calls return that
    snapshot unless refresh_snapshot=True is requested.
    """
    global _NEW_PARAMETER_DEFAULT_SNAPSHOT
    
    if _NEW_PARAMETER_DEFAULT_SNAPSHOT is None or refresh_snapshot:
        _NEW_PARAMETER_DEFAULT_SNAPSHOT = _discover_system_defaults_for_new_parameters()
    
    return dict(_NEW_PARAMETER_DEFAULT_SNAPSHOT)


def reset_new_parameters_to_system_defaults(
    param_manager: "ParameterManager",
    parameters_to_reset: Optional[Set[str]] = None,
    refresh_snapshot: bool = False
) -> bool:
    """Reset new parameters to live system defaults.
    
    Args:
        param_manager: ParameterManager instance
        parameters_to_reset: Optional subset to reset. If None, reset all new parameters.
        refresh_snapshot: If True, capture a fresh snapshot from live system before reset.
    
    Returns:
        True if all targeted parameters were reset successfully.
    """
    discovered_defaults = get_system_defaults_for_new_parameters(refresh_snapshot=refresh_snapshot)
    if not discovered_defaults:
        logger.warning("No system defaults discovered for new parameters")
        return False
    
    if parameters_to_reset is None:
        targets = set(discovered_defaults.keys())
    else:
        targets = set(parameters_to_reset) & set(discovered_defaults.keys())
    
    if not targets:
        logger.info("No new parameters selected for system-default reset")
        return True
    
    params = {k: discovered_defaults[k] for k in sorted(targets)}
    logger.info(f"Resetting new parameters to system defaults: {sorted(params.keys())}")
    return param_manager.set_parameters(params)



class ParameterManager:
    """Manages Linux parameters with extensible support."""
    
    def __init__(self):
        """Initialize parameter manager and mount debugfs if needed."""
        # Define parameter paths - easy to extend by adding new entries
        self.param_paths = {
            "latency_ns": "/sys/kernel/debug/sched/latency_ns",
            "min_granularity_ns": "/sys/kernel/debug/sched/min_granularity_ns",
            "wakeup_granularity_ns": "/sys/kernel/debug/sched/wakeup_granularity_ns",
            "migration_cost_ns": "/sys/kernel/debug/sched/migration_cost_ns"
        }
        self._ensure_debugfs_mounted()
    
    def add_parameter(self, name: str, path: str) -> None:
        """Add a new parameter to manage.
        
        Args:
            name: Parameter name (e.g., "new_param_ns")
            path: Full path to the parameter file (e.g., "/sys/kernel/debug/sched/new_param_ns")
        """
        self.param_paths[name] = path
        logger.info(f"Added parameter {name} with path {path}")
    
    def _ensure_debugfs_mounted(self) -> None:
        """Ensure that debugfs is mounted."""
        try:
            result = subprocess.run(
                ["sudo", "ls", "/sys/kernel/debug/sched"],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                logger.warning("Scheduler debug directory not found, trying to mount debugfs")
                subprocess.run(
                    ["sudo", "mount", "-t", "debugfs", "none", "/sys/kernel/debug"],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                logger.info("Mounted debugfs successfully")
        except Exception as e:
            logger.error(f"Failed to ensure debugfs is mounted: {e}")
    
    def get_parameter(self, param_name: str) -> Optional[int]:
        """Get current value of a parameter.
        
        Args:
            param_name: Name of the parameter to get
            
        Returns:
            Current parameter value, or None if not found/error
        """
        if param_name not in self.param_paths:
            logger.warning(f"Unknown parameter: {param_name}")
            return None
        
        try:
            param_path = self.param_paths[param_name]
            result = subprocess.run(
                ["sudo", "cat", param_path],
                capture_output=True,
                text=True,
                check=True
            )
            return int(result.stdout.strip())
        except Exception as e:
            logger.error(f"Error getting {param_name}: {e}")
            return None
    
    def _set_parameter_internal(self, param_name: str, value: int) -> bool:
        """Internal method to set a single parameter value.
        
        Args:
            param_name: Name of the parameter
            value: Value to set (no validation applied)
            
        Returns:
            True if successful, False otherwise
        """
        if param_name not in self.param_paths:
            logger.warning(f"Unknown parameter: {param_name}")
            return False
        
        try:
            param_path = self.param_paths[param_name]
            cmd = ["sudo", "sh", "-c", f"echo {value} > {param_path}"]
            result = subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            logger.info(f"Set {param_name} = {value}")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Error setting {param_name}={value}: {e.stderr.decode() if e.stderr else str(e)}")
            return False
        except Exception as e:
            logger.error(f"Error setting {param_name}: {e}")
            return False
    
    def set_latency_ns(self, value: int) -> bool:
        """Set latency_ns parameter.
        
        Args:
            value: Value in nanoseconds (no validation applied)
            
        Returns:
            True if successful, False otherwise
        """
        return self._set_parameter_internal("latency_ns", value)
    
    def set_min_granularity_ns(self, value: int, sync_wakeup: bool = True) -> bool:
        """Set min_granularity_ns parameter.
        
        Optionally auto-synchronizes wakeup_granularity_ns to the same value.
        Auto-sync is disabled when wakeup_granularity_ns is being tuned independently.
        
        Args:
            value: Value in nanoseconds (no validation applied)
            sync_wakeup: If True, auto-synchronize wakeup_granularity_ns to the same value.
                        If False, skip auto-synchronization (default: True)
            
        Returns:
            True if successful, False otherwise
        """
        success = self._set_parameter_internal("min_granularity_ns", value)
        if success and sync_wakeup:
            # Auto-synchronize wakeup_granularity_ns
            logger.info(f"Auto-synchronizing wakeup_granularity_ns to {value}")
            self._set_parameter_internal("wakeup_granularity_ns", value)
        return success
    
    def set_wakeup_granularity_ns(self, value: int) -> bool:
        """Set wakeup_granularity_ns parameter.
        
        Args:
            value: Value in nanoseconds (no validation applied)
            
        Returns:
            True if successful, False otherwise
        """
        return self._set_parameter_internal("wakeup_granularity_ns", value)
    
    def set_migration_cost_ns(self, value: int) -> bool:
        """Set migration_cost_ns parameter.
        
        Args:
            value: Value in nanoseconds (no validation applied)
            
        Returns:
            True if successful, False otherwise
        """
        return self._set_parameter_internal("migration_cost_ns", value)
    
    def set_parameters(self, parameters: Dict[str, Union[int, str, Dict[str, Any]]]) -> bool:
        """Set multiple scheduler parameters.
        
        This method dispatches to individual set_<param_name> methods for each
        parameter in the dictionary. Parameters can be:
        - Simple value: `{"param_name": 123}` - for parameters that only take a value
        - Dict with value and cores: `{"param_name": {"value": 123, "cores": "0-3"}}` - for parameters that support cores
        - String value: `{"epp": "performance"}` - for string parameters like EPP
        - Dict with string value and cores: `{"epp": {"value": "performance", "cores": "0-3"}}`
        
        Special handling: If wakeup_granularity_ns is being tuned independently,
        auto-synchronization from min_granularity_ns is disabled.
        
        Args:
            parameters: Dictionary of parameter name -> value or dict with value/cores
            
        Returns:
            True if all parameters were set successfully, False otherwise
        """
        # Check if wakeup_granularity_ns is being tuned independently
        wakeup_being_tuned = "wakeup_granularity_ns" in parameters
        
        success = True
        for param_name, param_value in parameters.items():
            # Construct method name: set_<param_name>
            method_name = f"set_{param_name}"
            
            # Check if method exists
            if hasattr(self, method_name):
                method = getattr(self, method_name)
                
                # Handle different value formats
                if isinstance(param_value, dict):
                    # Dict format: {"value": X, "cores": "0-3"} or {"value": "performance", "cores": "0-3"}
                    value = param_value.get("value")
                    cores = param_value.get("cores", None)
                    
                    # Check method signature to see if it accepts cores parameter
                    sig = inspect.signature(method)
                    
                    # Special handling for min_granularity_ns: disable auto-sync if wakeup_granularity_ns is being tuned
                    if param_name == "min_granularity_ns" and wakeup_being_tuned:
                        if "sync_wakeup" in sig.parameters:
                            if not method(value, sync_wakeup=False):
                                success = False
                        else:
                            # Fallback if signature doesn't have sync_wakeup (shouldn't happen)
                            if not method(value):
                                success = False
                    elif "cores" in sig.parameters:
                        # Method supports cores parameter
                        if not method(value, cores=cores):
                            success = False
                    else:
                        # Method doesn't support cores, just use value
                        if not method(value):
                            success = False
                else:
                    # Simple value format (backward compatible)
                    # Special handling for min_granularity_ns: disable auto-sync if wakeup_granularity_ns is being tuned
                    if param_name == "min_granularity_ns" and wakeup_being_tuned:
                        sig = inspect.signature(method)
                        if "sync_wakeup" in sig.parameters:
                            if not method(param_value, sync_wakeup=False):
                                success = False
                        else:
                            # Fallback if signature doesn't have sync_wakeup (shouldn't happen)
                            if not method(param_value):
                                success = False
                    else:
                        if not method(param_value):
                            success = False
            elif param_name in self.param_paths:
                # Fallback: use internal method if parameter is registered
                # Only supports simple int values
                if isinstance(param_value, dict):
                    value = param_value.get("value")
                    if not isinstance(value, int):
                        logger.warning(f"Parameter {param_name} only supports integer values")
                        success = False
                        continue
                else:
                    value = param_value
                    if not isinstance(value, int):
                        logger.warning(f"Parameter {param_name} only supports integer values")
                        success = False
                        continue
                
                logger.debug(f"Using internal method for parameter: {param_name}")
                if not self._set_parameter_internal(param_name, value):
                    success = False
            else:
                logger.warning(f"Unknown parameter: {param_name}")
                success = False
        
        return success
    
    def get_all_parameters(self) -> Dict[str, Optional[int]]:
        """Get current values of all managed parameters.
        
        Returns:
            Dictionary of parameter name -> current value (None if error)
        """
        return {name: self.get_parameter(name) for name in self.param_paths.keys()}
    
    def _parse_cores_spec(self, cores_spec: Optional[str]) -> Optional[Set[int]]:
        """Parse core specification string into set of integers.
        
        Args:
            cores_spec: Core specification like "0-3", "0,1,2", "all", or None
            
        Returns:
            Set of core numbers, or None if "all" or None (meaning all cores)
        """
        if cores_spec is None or cores_spec.lower() == "all":
            return None
        
        cores = set()
        for part in cores_spec.split(","):
            part = part.strip()
            if "-" in part:
                start, end = part.split("-", 1)
                cores.update(range(int(start), int(end) + 1))
            else:
                cores.add(int(part))
        return cores
    
    def _write_sysfs(self, path: str, value: str) -> bool:
        """Write value to sysfs file with snapshot/restore support.
        
        Args:
            path: Path to sysfs file
            value: Value to write
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Save old value if not already saved (for restore on exit)
            if not os.path.exists(path):
                logger.warning(f"Path does not exist: {path}")
                return False
            
            # Use sudo to write
            cmd = ["sudo", "sh", "-c", f"echo {value} > {path}"]
            result = subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            return True
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.decode() if e.stderr else str(e)
            if "I/O error" in err_msg:
                logger.warning(f"Failed to write (I/O error): {path}={value} - {err_msg.strip()}")
            else:
                logger.error(f"Error writing {path}={value}: {err_msg}")
            return False
        except Exception as e:
            logger.error(f"Error writing {path}: {e}")
            return False
    
    def _set_sysctl(self, key: str, value: Union[int, str, bool]) -> bool:
        """Set a sysctl key.
        
        Attempts direct sysctl first. If not root, falls back to non-interactive sudo.
        """
        value_str = str(int(value)) if isinstance(value, bool) else str(value)
        commands = [["sysctl", "-w", f"{key}={value_str}"]]
        if os.geteuid() != 0:
            commands.append(["sudo", "-n", "sysctl", "-w", f"{key}={value_str}"])
        
        last_error = ""
        for cmd in commands:
            try:
                subprocess.run(
                    cmd,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                return True
            except subprocess.CalledProcessError as e:
                last_error = e.stderr.strip() if e.stderr else str(e)
            except Exception as e:
                last_error = str(e)
        
        logger.error(f"Error setting sysctl {key}={value_str}: {last_error}")
        return False
    
    # ===== Group A: DVFS/Turbo parameters =====
    
    def set_scaling_governor(self, value: str, cores: Optional[str] = None) -> bool:
        """Set CPU scaling governor.
        
        Args:
            value: Governor name (e.g., "powersave", "performance")
            cores: Core specification like "0-3", "0,1,2", "all", or None for all cores
            
        Returns:
            True if successful, False otherwise
        """
        cores_set = self._parse_cores_spec(cores)
        polroot = "/sys/devices/system/cpu/cpufreq"
        if not os.path.exists(polroot):
            logger.warning("cpufreq not present; skipping scaling governor")
            return False
        
        success = False
        cores_applied = set()
        
        for name in os.listdir(polroot):
            if not name.startswith("policy"):
                continue
            
            base = os.path.join(polroot, name)
            
            # Check if this policy contains target cores
            if cores_set is not None:
                cpus_file = os.path.join(base, "affected_cpus")
                if os.path.exists(cpus_file):
                    try:
                        with open(cpus_file, "r") as f:
                            policy_cores = set(int(cpu) for cpu in f.read().strip().split())
                        if not (cores_set & policy_cores):
                            continue
                        cores_applied.update(cores_set & policy_cores)
                    except (ValueError, IOError):
                        continue
            
            govf = os.path.join(base, "scaling_governor")
            
            # Set governor
            if os.path.exists(govf):
                if self._write_sysfs(govf, value):
                    success = True
        
        if success:
            if cores_set is not None:
                cores_str = ",".join(str(c) for c in sorted(cores_applied))
                logger.info(f"Set scaling_governor={value} (cores: {cores_str})")
            else:
                logger.info(f"Set scaling_governor={value} (all cores)")
        return success
    
    def set_scaling_min_freq(self, value: int, cores: Optional[str] = None) -> bool:
        """Set minimum CPU frequency.
        
        Args:
            value: Minimum frequency in kHz (0 = use system default)
            cores: Core specification like "0-3", "0,1,2", "all", or None for all cores
            
        Returns:
            True if successful, False otherwise
        """
        cores_set = self._parse_cores_spec(cores)
        polroot = "/sys/devices/system/cpu/cpufreq"
        if not os.path.exists(polroot):
            logger.warning("cpufreq not present; skipping scaling_min_freq")
            return False
        
        success = False
        cores_applied = set()
        
        for name in os.listdir(polroot):
            if not name.startswith("policy"):
                continue
            
            base = os.path.join(polroot, name)
            
            # Check if this policy contains target cores
            if cores_set is not None:
                cpus_file = os.path.join(base, "affected_cpus")
                if os.path.exists(cpus_file):
                    try:
                        with open(cpus_file, "r") as f:
                            policy_cores = set(int(cpu) for cpu in f.read().strip().split())
                        if not (cores_set & policy_cores):
                            continue
                        cores_applied.update(cores_set & policy_cores)
                    except (ValueError, IOError):
                        continue
            
            minfreqf = os.path.join(base, "scaling_min_freq")
            
            # Set minimum frequency
            if os.path.exists(minfreqf):
                if value == 0:
                    # Use system default - read from cpuinfo_min_freq
                    cpuinfof = os.path.join(base, "cpuinfo_min_freq")
                    if os.path.exists(cpuinfof):
                        try:
                            with open(cpuinfof, "r") as f:
                                default_freq = int(f.read().strip())
                            if self._write_sysfs(minfreqf, str(default_freq)):
                                success = True
                        except (ValueError, IOError):
                            pass
                else:
                    if self._write_sysfs(minfreqf, str(value)):
                        success = True
        
        if success:
            if cores_set is not None:
                cores_str = ",".join(str(c) for c in sorted(cores_applied))
                logger.info(f"Set scaling_min_freq={value}kHz (cores: {cores_str})")
            else:
                logger.info(f"Set scaling_min_freq={value}kHz (all cores)")
        return success
    
    def set_scaling_max_freq(self, value: int, cores: Optional[str] = None) -> bool:
        """Set maximum CPU frequency.
        
        Args:
            value: Maximum frequency in kHz (0 = use system default)
            cores: Core specification like "0-3", "0,1,2", "all", or None for all cores
            
        Returns:
            True if successful, False otherwise
        """
        cores_set = self._parse_cores_spec(cores)
        polroot = "/sys/devices/system/cpu/cpufreq"
        if not os.path.exists(polroot):
            logger.warning("cpufreq not present; skipping scaling_max_freq")
            return False
        
        success = False
        cores_applied = set()
        
        for name in os.listdir(polroot):
            if not name.startswith("policy"):
                continue
            
            base = os.path.join(polroot, name)
            
            # Check if this policy contains target cores
            if cores_set is not None:
                cpus_file = os.path.join(base, "affected_cpus")
                if os.path.exists(cpus_file):
                    try:
                        with open(cpus_file, "r") as f:
                            policy_cores = set(int(cpu) for cpu in f.read().strip().split())
                        if not (cores_set & policy_cores):
                            continue
                        cores_applied.update(cores_set & policy_cores)
                    except (ValueError, IOError):
                        continue
            
            maxfreqf = os.path.join(base, "scaling_max_freq")
            
            # Set maximum frequency
            if os.path.exists(maxfreqf):
                if value == 0:
                    # Use system default - read from cpuinfo_max_freq
                    cpuinfof = os.path.join(base, "cpuinfo_max_freq")
                    if os.path.exists(cpuinfof):
                        try:
                            with open(cpuinfof, "r") as f:
                                default_freq = int(f.read().strip())
                            if self._write_sysfs(maxfreqf, str(default_freq)):
                                success = True
                        except (ValueError, IOError):
                            pass
                else:
                    if self._write_sysfs(maxfreqf, str(value)):
                        success = True
        
        if success:
            if cores_set is not None:
                cores_str = ",".join(str(c) for c in sorted(cores_applied))
                logger.info(f"Set scaling_max_freq={value}kHz (cores: {cores_str})")
            else:
                logger.info(f"Set scaling_max_freq={value}kHz (all cores)")
        return success
    
    def set_epp(self, value: str, cores: Optional[str] = None) -> bool:
        """Set Energy-Performance Preference (EPP).
        
        ALWAYS sets scaling governor to powersave before adjusting EPP.
        
        Args:
            value: EPP value ("default", "performance", "balance_performance", "balance_power", "power")
            cores: Core specification like "0-3", "0,1,2", "all", or None for all cores
            
        Returns:
            True if successful, False otherwise
        """
        cores_set = self._parse_cores_spec(cores)
        polroot = "/sys/devices/system/cpu/cpufreq"
        if not os.path.exists(polroot):
            logger.warning("cpufreq not present; skipping EPP")
            return True  # Return True to avoid breaking optimization flow
        
        success = False
        cores_applied = set()
        epp_supported = False
        
        for name in os.listdir(polroot):
            if not name.startswith("policy"):
                continue
            
            base = os.path.join(polroot, name)
            
            # Check if this policy contains target cores
            if cores_set is not None:
                cpus_file = os.path.join(base, "affected_cpus")
                if os.path.exists(cpus_file):
                    try:
                        with open(cpus_file, "r") as f:
                            policy_cores = set(int(cpu) for cpu in f.read().strip().split())
                        if not (cores_set & policy_cores):
                            continue
                        cores_applied.update(cores_set & policy_cores)
                    except (ValueError, IOError):
                        continue
            
            eppf = os.path.join(base, "energy_performance_preference")
            govf = os.path.join(base, "scaling_governor")
            
            # ALWAYS set governor to powersave FIRST (required for EPP)
            # If we can't set governor, skip this policy entirely
            if os.path.exists(govf):
                if not self._write_sysfs(govf, "powersave"):
                    # If we can't set governor, skip this policy
                    continue
            else:
                # No governor file - skip this policy
                continue
            
            # Now set EPP (including "default" value)
            # Only set EPP if governor was successfully set
            if os.path.exists(eppf):
                epp_supported = True
                if self._write_sysfs(eppf, value):
                    success = True
        
        if success:
            if cores_set is not None:
                cores_str = ",".join(str(c) for c in sorted(cores_applied))
                logger.info(f"Set EPP={value} (cores: {cores_str})")
            else:
                logger.info(f"Set EPP={value} (all cores)")
        elif not epp_supported:
            logger.warning("EPP (energy_performance_preference) not supported on this system")
            return True  # Return True if not supported (soft failure)
            
        return success
    
    def set_min_perf_pct(self, value: int, cores: Optional[str] = None) -> bool:
        """Set minimum performance percentage.
        
        Args:
            value: Percentage value (0-100)
            cores: Core specification like "0-3", "0,1,2", "all", or None for all cores
            
        Returns:
            True if successful, False otherwise
        """
        cores_set = self._parse_cores_spec(cores)
        
        if cores_set is None:
            # Global setting
            p = "/sys/devices/system/cpu/intel_pstate/min_perf_pct"
            if os.path.exists(p):
                if self._write_sysfs(p, str(value)):
                    logger.info(f"Set min_perf_pct={value} (global)")
                    return True
        else:
            # Per-policy setting (falls back to global if per-policy not available)
            polroot = "/sys/devices/system/cpu/cpufreq"
            if not os.path.exists(polroot):
                # Fallback to global
                p = "/sys/devices/system/cpu/intel_pstate/min_perf_pct"
                if os.path.exists(p):
                    if self._write_sysfs(p, str(value)):
                        cores_str = ",".join(str(c) for c in sorted(cores_set))
                        logger.info(f"Set min_perf_pct={value} (global, cores: {cores_str})")
                        return True
                return False
            
            # Try to set per-policy (but intel_pstate is global, so we just set global)
            p = "/sys/devices/system/cpu/intel_pstate/min_perf_pct"
            if os.path.exists(p):
                if self._write_sysfs(p, str(value)):
                    cores_str = ",".join(str(c) for c in sorted(cores_set))
                    logger.info(f"Set min_perf_pct={value} (cores: {cores_str})")
                    return True
        
        return False
    
    def set_max_perf_pct(self, value: int, cores: Optional[str] = None) -> bool:
        """Set maximum performance percentage.
        
        Args:
            value: Percentage value (0-100)
            cores: Core specification like "0-3", "0,1,2", "all", or None for all cores
            
        Returns:
            True if successful, False otherwise
        """
        cores_set = self._parse_cores_spec(cores)
        
        if cores_set is None:
            # Global setting
            p = "/sys/devices/system/cpu/intel_pstate/max_perf_pct"
            if os.path.exists(p):
                if self._write_sysfs(p, str(value)):
                    logger.info(f"Set max_perf_pct={value} (global)")
                    return True
        else:
            # Per-policy setting (falls back to global)
            polroot = "/sys/devices/system/cpu/cpufreq"
            if not os.path.exists(polroot):
                p = "/sys/devices/system/cpu/intel_pstate/max_perf_pct"
                if os.path.exists(p):
                    if self._write_sysfs(p, str(value)):
                        cores_str = ",".join(str(c) for c in sorted(cores_set))
                        logger.info(f"Set max_perf_pct={value} (global, cores: {cores_str})")
                        return True
                return False
            
            p = "/sys/devices/system/cpu/intel_pstate/max_perf_pct"
            if os.path.exists(p):
                if self._write_sysfs(p, str(value)):
                    cores_str = ",".join(str(c) for c in sorted(cores_set))
                    logger.info(f"Set max_perf_pct={value} (cores: {cores_str})")
                    return True
        
        return False
    
    def set_turbo(self, value: bool) -> bool:
        """Set turbo boost on/off.
        
        Args:
            value: True to enable turbo, False to disable
            
        Returns:
            True if successful, False otherwise
        """
        p = "/sys/devices/system/cpu/intel_pstate/no_turbo"
        if os.path.exists(p):
            # no_turbo: 0 = turbo on, 1 = turbo off
            turbo_val = "0" if value else "1"
            if self._write_sysfs(p, turbo_val):
                logger.info(f"Set turbo={'ON' if value else 'OFF'}")
                return True
        return False
    
    # ===== Group B: C-states / PM QoS parameters =====
    
    def set_pmqos(self, value: int, cores: Optional[str] = None) -> bool:
        """Set PM QoS resume latency (per-CPU).
        
        Args:
            value: Latency in microseconds
            cores: Core specification like "0-3", "0,1,2", "all", or None for all cores
            
        Returns:
            True if successful, False otherwise
        """
        cores_set = self._parse_cores_spec(cores)
        root = "/sys/devices/system/cpu"
        set_count = 0
        
        for cpu in os.listdir(root):
            if not (cpu.startswith("cpu") and cpu[3:].isdigit()):
                continue
            try:
                cpu_num = int(cpu[3:])
            except ValueError:
                continue
            
            # Skip if cores filter is specified and this CPU is not in the filter
            if cores_set is not None and cpu_num not in cores_set:
                continue
            
            f = os.path.join(root, cpu, "power", "pm_qos_resume_latency_us")
            if os.path.exists(f):
                if self._write_sysfs(f, str(value)):
                    set_count += 1
        
        if set_count > 0:
            if cores_set is not None:
                cores_str = ",".join(str(c) for c in sorted(cores_set))
                logger.info(f"Set pm_qos_resume_latency_us={value}µs (cores: {cores_str}, {set_count} CPUs)")
            else:
                logger.info(f"Set pm_qos_resume_latency_us={value}µs (all cores, {set_count} CPUs)")
            return True
        return False
    
    def set_cstate_max(self, level: str, cores: Optional[str] = None) -> bool:
        """Set maximum C-state allowed.
        
        Args:
            level: C-state level ("unlimited", "none", "C1", "C1E", "C2", "C6", etc.)
            cores: Core specification like "0-3", "0,1,2", "all", or None for all cores
            
        Returns:
            True if successful, False otherwise
        """
        cores_set = self._parse_cores_spec(cores)
        
        # Handle "unlimited" - enable all C-states
        if level.strip().lower() == "unlimited":
            root = "/sys/devices/system/cpu"
            enabled_count = 0
            
            for cpu in os.listdir(root):
                if not (cpu.startswith("cpu") and cpu[3:].isdigit()):
                    continue
                try:
                    cpu_num = int(cpu[3:])
                except ValueError:
                    continue
                
                # Skip if not in target cores
                if cores_set is not None and cpu_num not in cores_set:
                    continue
                
                cdir = os.path.join(root, cpu, "cpuidle")
                if not os.path.exists(cdir):
                    continue
                
                for st in os.listdir(cdir):
                    if not st.startswith("state"):
                        continue
                    base = os.path.join(cdir, st)
                    dis = os.path.join(base, "disable")
                    
                    if os.path.exists(dis):
                        if self._write_sysfs(dis, "0"):  # 0 = enabled
                            enabled_count += 1
            
            if enabled_count > 0:
                cores_str = cores if cores else "all"
                logger.info(f"Set cstate_max=unlimited (cores: {cores_str}) - enabled {enabled_count} C-states")
                return True
            return False
        
        def _cnum(v: str) -> int:
            v_lower = v.strip().lower()
            if v_lower in ("none", "poll", "0"):
                return 0
            if v_lower == "c6only":
                return 999  # Special marker
            if v_lower == "c1e":
                return 2
            m = re.match(r"c?(\d+)$", v_lower)
            if not m:
                return 1
            return int(m.group(1))
        
        allow_c = _cnum(level)
        c6only_mode = (level.strip().lower() == "c6only")
        
        root = "/sys/devices/system/cpu"
        disabled_count = 0
        enabled_count = 0
        
        for cpu in os.listdir(root):
            if not (cpu.startswith("cpu") and cpu[3:].isdigit()):
                continue
            cpu_num = int(cpu[3:])
            
            # Skip if not in target cores
            if cores_set is not None and cpu_num not in cores_set:
                continue
            
            cdir = os.path.join(root, cpu, "cpuidle")
            if not os.path.exists(cdir):
                continue
            
            for st in os.listdir(cdir):
                if not st.startswith("state"):
                    continue
                base = os.path.join(cdir, st)
                dis = os.path.join(base, "disable")
                namef = os.path.join(base, "name")
                
                if not os.path.exists(dis):
                    continue
                
                # Read C-state name
                cname = ""
                try:
                    with open(namef) as f:
                        cname = f.read().strip().upper()
                except Exception:
                    cname = st
                
                # Parse C-number
                cnum = None
                if cname == "POLL":
                    cnum = 0
                elif cname == "C1":
                    cnum = 1
                elif cname == "C1E":
                    cnum = 2
                else:
                    m = re.search(r'C(\d+)', cname)
                    if m:
                        cnum = int(m.group(1))
                
                # Decide whether to disable
                if c6only_mode:
                    if cname == "POLL":
                        self._write_sysfs(dis, "0")
                        enabled_count += 1
                    elif cnum == 6:
                        self._write_sysfs(dis, "0")
                        enabled_count += 1
                    else:
                        self._write_sysfs(dis, "1")
                        disabled_count += 1
                elif allow_c == 0:
                    if cname == "POLL":
                        self._write_sysfs(dis, "0")
                        enabled_count += 1
                    else:
                        self._write_sysfs(dis, "1")
                        disabled_count += 1
                elif cnum is None or cnum == 0:
                    self._write_sysfs(dis, "0")
                    enabled_count += 1
                elif cnum > allow_c:
                    self._write_sysfs(dis, "1")
                    disabled_count += 1
                else:
                    self._write_sysfs(dis, "0")
                    enabled_count += 1
        
        if enabled_count > 0 or disabled_count > 0:
            cores_str = cores if cores else "all"
            logger.info(f"Set cstate_max={level} (cores: {cores_str}) - enabled {enabled_count}, disabled {disabled_count}")
            return True
        return False
    
    # ===== Group C: NAPI & busy polling (sysctl) =====
    
    def set_busy_poll(self, value: int, cores: Optional[str] = None) -> bool:
        """Set busy_poll sysctl and optionally bind NIC IRQs to cores.
        
        Args:
            value: Busy poll value in microseconds
            cores: Core specification for IRQ binding like "0-3", "0,1,2", "all", or None
            
        Returns:
            True if successful, False otherwise
        """
        try:
            subprocess.run(
                ["sysctl", "-w", f"net.core.busy_poll={value}"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            logger.info(f"Set net.core.busy_poll={value}")
            
            # Optionally bind NIC IRQs to cores
            if cores is not None and cores.lower() != "all":
                cores_set = self._parse_cores_spec(cores)
                if cores_set:
                    self._bind_nic_irqs_to_cores(cores_set, cores)
            
            return True
        except Exception as e:
            logger.error(f"Error setting busy_poll: {e}")
            return False
    
    def set_napi_busy_poll(self, value: int, cores: Optional[str] = None) -> bool:
        """Set napi_busy_poll sysctl (alias for busy_poll).
        
        This is an alias for set_busy_poll since both refer to the same sysctl parameter.
        
        Args:
            value: Busy poll value in microseconds
            cores: Core specification for IRQ binding like "0-3", "0,1,2", "all", or None
            
        Returns:
            True if successful, False otherwise
        """
        return self.set_busy_poll(value, cores)
    
    def set_busy_read(self, value: int, cores: Optional[str] = None) -> bool:
        """Set busy_read sysctl and optionally bind NIC IRQs to cores.
        
        Args:
            value: Busy read value in microseconds
            cores: Core specification for IRQ binding like "0-3", "0,1,2", "all", or None
            
        Returns:
            True if successful, False otherwise
        """
        try:
            subprocess.run(
                ["sysctl", "-w", f"net.core.busy_read={value}"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            logger.info(f"Set net.core.busy_read={value}")
            
            # Optionally bind NIC IRQs to cores
            if cores is not None and cores.lower() != "all":
                cores_set = self._parse_cores_spec(cores)
                if cores_set:
                    self._bind_nic_irqs_to_cores(cores_set, cores)
            
            return True
        except Exception as e:
            logger.error(f"Error setting busy_read: {e}")
            return False
    
    def set_netdev_budget(self, value: int, cores: Optional[str] = None) -> bool:
        """Set netdev_budget sysctl and optionally bind NIC IRQs to cores.
        
        Args:
            value: Netdev budget value
            cores: Core specification for IRQ binding like "0-3", "0,1,2", "all", or None
            
        Returns:
            True if successful, False otherwise
        """
        try:
            subprocess.run(
                ["sysctl", "-w", f"net.core.netdev_budget={value}"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            logger.info(f"Set net.core.netdev_budget={value}")
            
            # Optionally bind NIC IRQs to cores
            if cores is not None and cores.lower() != "all":
                cores_set = self._parse_cores_spec(cores)
                if cores_set:
                    self._bind_nic_irqs_to_cores(cores_set, cores)
            
            return True
        except Exception as e:
            logger.error(f"Error setting netdev_budget: {e}")
            return False
    
    def set_netdev_budget_usecs(self, value: int, cores: Optional[str] = None) -> bool:
        """Set netdev_budget_usecs sysctl and optionally bind NIC IRQs to cores.
        
        Args:
            value: Netdev budget usecs value
            cores: Core specification for IRQ binding like "0-3", "0,1,2", "all", or None
            
        Returns:
            True if successful, False otherwise
        """
        try:
            subprocess.run(
                ["sysctl", "-w", f"net.core.netdev_budget_usecs={value}"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            logger.info(f"Set net.core.netdev_budget_usecs={value}")
            
            # Optionally bind NIC IRQs to cores
            if cores is not None and cores.lower() != "all":
                cores_set = self._parse_cores_spec(cores)
                if cores_set:
                    self._bind_nic_irqs_to_cores(cores_set, cores)
            
            return True
        except Exception as e:
            logger.error(f"Error setting netdev_budget_usecs: {e}")
            return False
    
    # ===== Group D: Additional VM / scheduler / network sysctl controls =====
    
    def set_vm_swappiness(self, value: int) -> bool:
        """Set vm.swappiness."""
        if self._set_sysctl("vm.swappiness", value):
            logger.info(f"Set vm.swappiness={value}")
            return True
        return False
    
    def set_vm_dirty_ratio(self, value: int) -> bool:
        """Set vm.dirty_ratio."""
        if self._set_sysctl("vm.dirty_ratio", value):
            logger.info(f"Set vm.dirty_ratio={value}")
            return True
        return False
    
    def set_vm_dirty_background_ratio(self, value: int) -> bool:
        """Set vm.dirty_background_ratio."""
        if self._set_sysctl("vm.dirty_background_ratio", value):
            logger.info(f"Set vm.dirty_background_ratio={value}")
            return True
        return False
    
    def set_vm_dirty_expire_centisecs(self, value: int) -> bool:
        """Set vm.dirty_expire_centisecs."""
        if self._set_sysctl("vm.dirty_expire_centisecs", value):
            logger.info(f"Set vm.dirty_expire_centisecs={value}")
            return True
        return False
    
    def set_vm_dirty_writeback_centisecs(self, value: int) -> bool:
        """Set vm.dirty_writeback_centisecs."""
        if self._set_sysctl("vm.dirty_writeback_centisecs", value):
            logger.info(f"Set vm.dirty_writeback_centisecs={value}")
            return True
        return False
    
    def set_zone_reclaim_mode(self, value: int) -> bool:
        """Set vm.zone_reclaim_mode bitmask."""
        if self._set_sysctl("vm.zone_reclaim_mode", value):
            logger.info(f"Set vm.zone_reclaim_mode={value}")
            return True
        return False
    
    def set_numa_balancing(self, value: Union[bool, int]) -> bool:
        """Set kernel.numa_balancing."""
        if self._set_sysctl("kernel.numa_balancing", value):
            logger.info(f"Set kernel.numa_balancing={int(bool(value))}")
            return True
        return False
    
    def set_sched_autogroup_enabled(self, value: Union[bool, int]) -> bool:
        """Set kernel.sched_autogroup_enabled."""
        if self._set_sysctl("kernel.sched_autogroup_enabled", value):
            logger.info(f"Set kernel.sched_autogroup_enabled={int(bool(value))}")
            return True
        return False
    
    def set_sched_cfs_bandwidth_slice_us(self, value: int) -> bool:
        """Set kernel.sched_cfs_bandwidth_slice_us."""
        if self._set_sysctl("kernel.sched_cfs_bandwidth_slice_us", value):
            logger.info(f"Set kernel.sched_cfs_bandwidth_slice_us={value}")
            return True
        return False
    
    def set_somaxconn(self, value: int) -> bool:
        """Set net.core.somaxconn."""
        if self._set_sysctl("net.core.somaxconn", value):
            logger.info(f"Set net.core.somaxconn={value}")
            return True
        return False
    
    def set_netdev_max_backlog(self, value: int) -> bool:
        """Set net.core.netdev_max_backlog."""
        if self._set_sysctl("net.core.netdev_max_backlog", value):
            logger.info(f"Set net.core.netdev_max_backlog={value}")
            return True
        return False
    
    def set_rmem_default(self, value: int) -> bool:
        """Set net.core.rmem_default."""
        if self._set_sysctl("net.core.rmem_default", value):
            logger.info(f"Set net.core.rmem_default={value}")
            return True
        return False
    
    def set_wmem_default(self, value: int) -> bool:
        """Set net.core.wmem_default."""
        if self._set_sysctl("net.core.wmem_default", value):
            logger.info(f"Set net.core.wmem_default={value}")
            return True
        return False
    
    def set_rmem_max(self, value: int) -> bool:
        """Set net.core.rmem_max."""
        if self._set_sysctl("net.core.rmem_max", value):
            logger.info(f"Set net.core.rmem_max={value}")
            return True
        return False
    
    def set_wmem_max(self, value: int) -> bool:
        """Set net.core.wmem_max."""
        if self._set_sysctl("net.core.wmem_max", value):
            logger.info(f"Set net.core.wmem_max={value}")
            return True
        return False
    
    def set_tcp_fin_timeout(self, value: int) -> bool:
        """Set net.ipv4.tcp_fin_timeout."""
        if self._set_sysctl("net.ipv4.tcp_fin_timeout", value):
            logger.info(f"Set net.ipv4.tcp_fin_timeout={value}")
            return True
        return False
    
    def set_tcp_tw_reuse(self, value: int) -> bool:
        """Set net.ipv4.tcp_tw_reuse."""
        if self._set_sysctl("net.ipv4.tcp_tw_reuse", value):
            logger.info(f"Set net.ipv4.tcp_tw_reuse={value}")
            return True
        return False
    
    def set_tcp_mtu_probing(self, value: int) -> bool:
        """Set net.ipv4.tcp_mtu_probing."""
        if self._set_sysctl("net.ipv4.tcp_mtu_probing", value):
            logger.info(f"Set net.ipv4.tcp_mtu_probing={value}")
            return True
        return False
    
    def set_tcp_timestamps(self, value: Union[bool, int]) -> bool:
        """Set net.ipv4.tcp_timestamps."""
        if self._set_sysctl("net.ipv4.tcp_timestamps", value):
            logger.info(f"Set net.ipv4.tcp_timestamps={int(bool(value))}")
            return True
        return False
    
    def set_tcp_sack(self, value: Union[bool, int]) -> bool:
        """Set net.ipv4.tcp_sack."""
        if self._set_sysctl("net.ipv4.tcp_sack", value):
            logger.info(f"Set net.ipv4.tcp_sack={int(bool(value))}")
            return True
        return False
    
    def set_tcp_window_scaling(self, value: Union[bool, int]) -> bool:
        """Set net.ipv4.tcp_window_scaling."""
        if self._set_sysctl("net.ipv4.tcp_window_scaling", value):
            logger.info(f"Set net.ipv4.tcp_window_scaling={int(bool(value))}")
            return True
        return False
    
    def set_tcp_fastopen(self, value: int) -> bool:
        """Set net.ipv4.tcp_fastopen."""
        if self._set_sysctl("net.ipv4.tcp_fastopen", value):
            logger.info(f"Set net.ipv4.tcp_fastopen={value}")
            return True
        return False
    
    def set_tcp_congestion_control(self, value: str) -> bool:
        """Set net.ipv4.tcp_congestion_control."""
        if self._set_sysctl("net.ipv4.tcp_congestion_control", value):
            logger.info(f"Set net.ipv4.tcp_congestion_control={value}")
            return True
        return False
    
    def _bind_nic_irqs_to_cores(self, cores: Set[int], cores_spec_str: str) -> int:
        """Bind all NIC IRQs to the specified cores.
        
        Args:
            cores: Set of core numbers
            cores_spec_str: Original cores specification string for logging
            
        Returns:
            Number of IRQs bound
        """
        # Get all network interfaces (excluding loopback)
        netdevs = []
        try:
            result = subprocess.run(
                ["ip", "link", "show"],
                capture_output=True,
                text=True,
                check=False
            )
            for line in result.stdout.split('\n'):
                if ': ' in line and 'lo:' not in line:
                    parts = line.split(':')
                    if len(parts) >= 2:
                        dev = parts[1].strip()
                        if dev and dev != 'lo':
                            netdevs.append(dev)
        except Exception:
            # Fallback: check common interface names
            for candidate in ['enp94s0f1', 'eno1', 'eth0']:
                try:
                    with open("/proc/interrupts", "r") as f:
                        if candidate in f.read():
                            netdevs.append(candidate)
                            break
                except Exception:
                    pass
        
        if not netdevs:
            logger.warning("No network interfaces found for IRQ binding")
            return 0
        
        # Find IRQs associated with these interfaces
        irqs = set()
        try:
            with open("/proc/interrupts", "r") as f:
                for line in f:
                    parts = line.split()
                    if not parts:
                        continue
                    irq_str = parts[0].rstrip(":")
                    if not irq_str.isdigit():
                        continue
                    irq_num = int(irq_str)
                    
                    for dev in netdevs:
                        if any(dev in part for part in parts[-3:]):
                            irqs.add(irq_num)
                            break
        except Exception as e:
            logger.warning(f"Failed to read /proc/interrupts: {e}")
            return 0
        
        if not irqs:
            logger.warning("No IRQs found for network interfaces")
            return 0
        
        # Format core list
        sorted_cores = sorted(cores)
        parts = []
        start = sorted_cores[0]
        end = start
        for i in range(1, len(sorted_cores)):
            if sorted_cores[i] == end + 1:
                end = sorted_cores[i]
            else:
                if start == end:
                    parts.append(str(start))
                else:
                    parts.append(f"{start}-{end}")
                start = sorted_cores[i]
                end = start
        if start == end:
            parts.append(str(start))
        else:
            parts.append(f"{start}-{end}")
        cpulist = ",".join(parts)
        
        # Bind each IRQ
        bound_count = 0
        for irq in irqs:
            irq_affinity = f"/proc/irq/{irq}/smp_affinity_list"
            if os.path.exists(irq_affinity):
                if self._write_sysfs(irq_affinity, cpulist):
                    bound_count += 1
        
        if bound_count > 0:
            logger.info(f"Bound {bound_count} NIC IRQ(s) to cores {cores_spec_str}")
        
        return bound_count


def get_default_parameters() -> Dict[str, Union[int, str, bool]]:
    """Get default Linux kernel parameter values.
    
    Returns:
        Dictionary of parameter name -> default value.
        New parameters are discovered from live system defaults via sysctl.
    """
    defaults: Dict[str, Union[int, str, bool]] = {
        # Scheduler parameters
        "latency_ns": 24000000,
        "min_granularity_ns": 3000000,
        "wakeup_granularity_ns": 4000000,
        "migration_cost_ns": 500000,
        
        # DVFS/Turbo parameters
        "scaling_governor": "powersave",  # Default governor
        "scaling_min_freq": 0,  # 0 = use system default
        "scaling_max_freq": 0,  # 0 = use system default
        "epp": "default",
        "min_perf_pct": 0,
        "max_perf_pct": 100,
        "turbo": True,  # Turbo ON
        
        # C-states / PM QoS
        "pmqos": 0,  # No override, governor decides
        "cstate_max": "unlimited",  # All C-states enabled
        
        # NAPI & busy polling
        "busy_poll": 0,
        "busy_read": 0,
        "netdev_budget": 300,  # packets
        "netdev_budget_usecs": 8000,  # microseconds
    }
    
    # Merge live system defaults for new parameters (do not hardcode these).
    defaults.update(get_system_defaults_for_new_parameters())
    return defaults


def reset_all_parameters_to_defaults(
    param_manager: ParameterManager,
    include_new_parameters: bool = False
) -> bool:
    """Reset all parameters to their default values.
    
    This function ensures proper ordering:
    1. Set scaling governor to powersave first (required for EPP)
    2. Then set all other parameters
    
    Args:
        param_manager: ParameterManager instance
        include_new_parameters: If True, also reset new parameters using discovered defaults.
                               If False, only reset the original parameter set.
        
    Returns:
        True if all parameters were reset successfully, False otherwise
    """
    defaults = get_default_parameters()
    if not include_new_parameters:
        defaults = {k: v for k, v in defaults.items() if k not in NEW_PARAMETER_SYSCTL_KEYS}
    
    success = True
    
    # First, set scaling governor to powersave (required before setting EPP)
    logger.info("Setting scaling governor to powersave...")
    if not param_manager.set_scaling_governor("powersave"):
        logger.warning("Failed to set scaling governor to powersave (may not be available)")
        # Continue anyway - some systems may not have cpufreq
    
    # Now set all other parameters
    params_to_set = {}
    for param_name, param_value in defaults.items():
        # Skip epp for now - we'll handle it specially after governor is set
        if param_name == "epp":
            continue
        params_to_set[param_name] = param_value
    
    # Set all parameters except EPP
    if params_to_set:
        logger.info(f"Resetting parameters to defaults: {list(params_to_set.keys())}")
        if not param_manager.set_parameters(params_to_set):
            success = False
    
    # Set EPP last (after governor is set)
    if "epp" in defaults:
        logger.info("Setting EPP to default...")
        if not param_manager.set_epp(defaults["epp"]):
            logger.warning("Failed to set EPP to default (may not be available)")
            # Don't fail overall if EPP is not available
    
    return success
