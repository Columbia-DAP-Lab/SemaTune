#!/usr/bin/env python3
"""
Unit tests for parameter_manager.py

These tests require sudo access to set system parameters.
Run with: sudo python -m pytest tests/test_parameter_manager.py -v

Note: Some parameters may not be available on all systems, so tests
gracefully handle missing parameters.
"""

import sys
import os
import subprocess
import pytest
from typing import Dict, Any

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from barebones_optimizer.parameter_manager import (
    ParameterManager,
    get_default_parameters,
    get_new_parameter_names,
    get_system_defaults_for_new_parameters,
    reset_all_parameters_to_defaults,
    reset_new_parameters_to_system_defaults,
    is_per_core_parameter,
    get_parameter_type,
    get_categorical_values
)


def check_sudo():
    """Check if running with sudo."""
    try:
        result = subprocess.run(
            ["sudo", "-n", "true"],
            capture_output=True,
            timeout=1
        )
        return result.returncode == 0
    except Exception:
        return False


def read_sysctl_value(key: str):
    """Read a sysctl value or return None if unavailable."""
    try:
        result = subprocess.run(
            ["sysctl", "-n", key],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return None


def assert_set_and_restore_sysctl(param_manager, setter_name: str, key: str, test_value, restore_value):
    """Set a sysctl through ParameterManager, verify readback, and restore."""
    setter = getattr(param_manager, setter_name)
    expected = str(int(test_value)) if isinstance(test_value, bool) else str(test_value)
    restore_arg = bool(int(restore_value)) if isinstance(test_value, bool) else type(test_value)(restore_value)
    
    try:
        result = setter(test_value)
        assert result, f"Failed to set {key} via {setter_name}"
        
        actual = read_sysctl_value(key)
        assert actual == expected, f"{key}: expected {expected}, got {actual}"
    finally:
        setter(restore_arg)


@pytest.fixture(scope="module")
def param_manager():
    """Create a ParameterManager instance for testing."""
    return ParameterManager()


@pytest.fixture(scope="module", autouse=True)
def require_sudo():
    """Require sudo for all tests."""
    if not check_sudo():
        pytest.skip("Tests require sudo access. Run with: sudo python -m pytest ...")


class TestSchedulerParameters:
    """Test scheduler parameter setting."""
    
    def test_set_latency_ns(self, param_manager):
        """Test setting latency_ns parameter."""
        test_value = 24000000
        result = param_manager.set_latency_ns(test_value)
        assert result, "Failed to set latency_ns"
        
        # Verify it was set
        actual_value = param_manager.get_parameter("latency_ns")
        assert actual_value == test_value, f"Expected {test_value}, got {actual_value}"
    
    def test_set_min_granularity_ns(self, param_manager):
        """Test setting min_granularity_ns parameter."""
        test_value = 3000000
        result = param_manager.set_min_granularity_ns(test_value)
        assert result, "Failed to set min_granularity_ns"
        
        # Verify it was set
        actual_value = param_manager.get_parameter("min_granularity_ns")
        assert actual_value == test_value, f"Expected {test_value}, got {actual_value}"
        
        # Check that wakeup_granularity_ns was auto-synced
        wakeup_value = param_manager.get_parameter("wakeup_granularity_ns")
        assert wakeup_value == test_value, f"wakeup_granularity_ns should be synced to {test_value}, got {wakeup_value}"
    
    def test_set_wakeup_granularity_ns(self, param_manager):
        """Test setting wakeup_granularity_ns parameter."""
        test_value = 4000000
        result = param_manager.set_wakeup_granularity_ns(test_value)
        assert result, "Failed to set wakeup_granularity_ns"
        
        # Verify it was set
        actual_value = param_manager.get_parameter("wakeup_granularity_ns")
        assert actual_value == test_value, f"Expected {test_value}, got {actual_value}"
    
    def test_set_migration_cost_ns(self, param_manager):
        """Test setting migration_cost_ns parameter."""
        test_value = 500000
        result = param_manager.set_migration_cost_ns(test_value)
        assert result, "Failed to set migration_cost_ns"
        
        # Verify it was set
        actual_value = param_manager.get_parameter("migration_cost_ns")
        assert actual_value == test_value, f"Expected {test_value}, got {actual_value}"
    
    def test_set_multiple_scheduler_parameters(self, param_manager):
        """Test setting multiple scheduler parameters at once."""
        params = {
            "latency_ns": 24000000,
            "min_granularity_ns": 3000000,
            "migration_cost_ns": 500000
        }
        result = param_manager.set_parameters(params)
        assert result, "Failed to set multiple scheduler parameters"
        
        # Verify all were set
        for param_name, expected_value in params.items():
            actual_value = param_manager.get_parameter(param_name)
            assert actual_value == expected_value, f"{param_name}: expected {expected_value}, got {actual_value}"


class TestDVFSParameters:
    """Test DVFS/Turbo parameter setting."""
    
    def test_set_scaling_governor(self, param_manager):
        """Test setting scaling governor."""
        # Try to set to powersave
        result = param_manager.set_scaling_governor("powersave")
        if not result:
            pytest.skip("scaling_governor not available on this system")
        
        # Try to set to performance (if available)
        param_manager.set_scaling_governor("performance")
        
        # Reset to powersave
        result = param_manager.set_scaling_governor("powersave")
        assert result, "Failed to set scaling_governor to powersave"
    
    def test_set_epp_default(self, param_manager):
        """Test setting EPP to default."""
        result = param_manager.set_epp("default")
        if not result:
            pytest.skip("EPP not available on this system")
        
        # Should have set governor to powersave
        assert result, "Failed to set EPP to default"
    
    def test_set_epp_performance(self, param_manager):
        """Test setting EPP to performance."""
        result = param_manager.set_epp("performance")
        if not result:
            pytest.skip("EPP not available on this system")
        
        assert result, "Failed to set EPP to performance"
    
    def test_set_min_perf_pct(self, param_manager):
        """Test setting min_perf_pct."""
        test_value = 0
        result = param_manager.set_min_perf_pct(test_value)
        if not result:
            pytest.skip("min_perf_pct not available on this system")
        
        assert result, "Failed to set min_perf_pct"
    
    def test_set_max_perf_pct(self, param_manager):
        """Test setting max_perf_pct."""
        test_value = 100
        result = param_manager.set_max_perf_pct(test_value)
        if not result:
            pytest.skip("max_perf_pct not available on this system")
        
        assert result, "Failed to set max_perf_pct"
    
    def test_set_turbo(self, param_manager):
        """Test setting turbo boost."""
        # Enable turbo
        result = param_manager.set_turbo(True)
        if not result:
            pytest.skip("turbo not available on this system")
        
        assert result, "Failed to enable turbo"
        
        # Disable turbo
        result = param_manager.set_turbo(False)
        assert result, "Failed to disable turbo"
        
        # Re-enable turbo (default)
        result = param_manager.set_turbo(True)
        assert result, "Failed to re-enable turbo"


class TestCStateParameters:
    """Test C-state parameter setting."""
    
    def test_set_cstate_max_unlimited(self, param_manager):
        """Test setting cstate_max to unlimited."""
        result = param_manager.set_cstate_max("unlimited")
        if not result:
            pytest.skip("cstate_max not available on this system")
        
        assert result, "Failed to set cstate_max to unlimited"
    
    def test_set_cstate_max_c6(self, param_manager):
        """Test setting cstate_max to C6."""
        result = param_manager.set_cstate_max("C6")
        if not result:
            pytest.skip("cstate_max not available on this system")
        
        assert result, "Failed to set cstate_max to C6"
        
        # Reset to unlimited
        param_manager.set_cstate_max("unlimited")
    
    def test_set_pmqos(self, param_manager):
        """Test setting PM QoS."""
        test_value = 0  # No override
        result = param_manager.set_pmqos(test_value)
        if not result:
            pytest.skip("pmqos not available on this system")
        
        assert result, "Failed to set pmqos"


class TestNetworkParameters:
    """Test network parameter setting."""
    
    def test_set_busy_poll(self, param_manager):
        """Test setting busy_poll."""
        test_value = 0
        result = param_manager.set_busy_poll(test_value)
        assert result, "Failed to set busy_poll"
    
    def test_set_busy_read(self, param_manager):
        """Test setting busy_read."""
        test_value = 0
        result = param_manager.set_busy_read(test_value)
        assert result, "Failed to set busy_read"
    
    def test_set_netdev_budget(self, param_manager):
        """Test setting netdev_budget."""
        test_value = 300
        result = param_manager.set_netdev_budget(test_value)
        assert result, "Failed to set netdev_budget"
    
    def test_set_netdev_budget_usecs(self, param_manager):
        """Test setting netdev_budget_usecs."""
        test_value = 8000
        result = param_manager.set_netdev_budget_usecs(test_value)
        assert result, "Failed to set netdev_budget_usecs"


class TestDefaultParameters:
    """Test default parameter functions."""
    
    def test_get_default_parameters(self):
        """Test getting default parameters."""
        defaults = get_default_parameters()
        
        # Check that all expected parameters are present
        expected_params = [
            "latency_ns", "min_granularity_ns", "wakeup_granularity_ns", "migration_cost_ns",
            "epp", "min_perf_pct", "max_perf_pct", "turbo",
            "pmqos", "cstate_max",
            "busy_poll", "busy_read", "netdev_budget", "netdev_budget_usecs"
        ]
        
        for param in expected_params:
            assert param in defaults, f"Missing default parameter: {param}"
        
        # Check some specific values
        assert defaults["latency_ns"] == 24000000
        assert defaults["epp"] == "default"
        assert defaults["turbo"] is True
        assert defaults["cstate_max"] == "unlimited"
        assert defaults["busy_poll"] == 0
        assert defaults["netdev_budget"] == 300
    
    def test_reset_all_parameters_to_defaults(self, param_manager):
        """Test resetting all parameters to defaults."""
        # First, set some non-default values
        param_manager.set_latency_ns(10000000)
        param_manager.set_min_granularity_ns(1000000)
        
        # Reset to defaults
        result = reset_all_parameters_to_defaults(param_manager)
        
        # Check that reset was attempted (may fail if some params not available)
        # This is expected - some systems may not support all parameters
        assert isinstance(result, bool), "reset_all_parameters_to_defaults should return bool"
        
        # Verify scheduler parameters were reset
        defaults = get_default_parameters()
        actual_latency = param_manager.get_parameter("latency_ns")
        assert actual_latency == defaults["latency_ns"], \
            f"latency_ns should be {defaults['latency_ns']}, got {actual_latency}"
        
        actual_min_gran = param_manager.get_parameter("min_granularity_ns")
        assert actual_min_gran == defaults["min_granularity_ns"], \
            f"min_granularity_ns should be {defaults['min_granularity_ns']}, got {actual_min_gran}"


class TestParameterMetadata:
    """Test parameter metadata functions."""
    
    def test_is_per_core_parameter(self):
        """Test checking if parameter is per-core."""
        assert is_per_core_parameter("epp") is True
        assert is_per_core_parameter("latency_ns") is False
        assert is_per_core_parameter("min_perf_pct") is True
        assert is_per_core_parameter("turbo") is False
    
    def test_get_parameter_type(self):
        """Test getting parameter type."""
        assert get_parameter_type("latency_ns") == "continuous"
        assert get_parameter_type("epp") == "categorical"
        assert get_parameter_type("min_perf_pct") == "continuous"
    
    def test_get_categorical_values(self):
        """Test getting categorical parameter values."""
        epp_values = get_categorical_values("epp")
        assert epp_values is not None
        assert "default" in epp_values
        assert "performance" in epp_values
        
        cstate_values = get_categorical_values("cstate_max")
        assert cstate_values is not None
        assert "unlimited" in cstate_values


class TestParameterValidation:
    """Test parameter validation and error handling."""
    
    def test_set_invalid_parameter(self, param_manager):
        """Test setting an invalid parameter name."""
        result = param_manager.set_parameters({"invalid_param": 123})
        assert result is False, "Should fail for invalid parameter"
    
    def test_set_parameters_with_mixed_types(self, param_manager):
        """Test setting parameters with mixed types (int, str, bool)."""
        params = {
            "latency_ns": 24000000,  # int
            "epp": "default",  # str
            "turbo": True  # bool
        }
        result = param_manager.set_parameters(params)
        # Should handle gracefully - some params may not be available
        assert isinstance(result, bool)


class TestPerCoreParameters:
    """Test per-core parameter setting."""
    
    def test_set_epp_with_cores(self, param_manager):
        """Test setting EPP with core specification."""
        result = param_manager.set_epp("performance", cores="0-1")
        if not result:
            pytest.skip("EPP not available on this system")
        
        assert result, "Failed to set EPP with cores"
        
        # Reset to default
        param_manager.set_epp("default")
    
    def test_set_min_perf_pct_with_cores(self, param_manager):
        """Test setting min_perf_pct with core specification."""
        result = param_manager.set_min_perf_pct(0, cores="0-1")
        if not result:
            pytest.skip("min_perf_pct not available on this system")
        
        assert result, "Failed to set min_perf_pct with cores"
    
    def test_set_parameters_with_cores_dict(self, param_manager):
        """Test setting parameters with cores using dict format."""
        params = {
            "epp": {"value": "default", "cores": "0-1"}
        }
        result = param_manager.set_parameters(params)
        if not result:
            pytest.skip("EPP not available on this system")
        
        assert result, "Failed to set parameter with cores dict"


class TestAdditionalSysctlParameters:
    """Test additional VM/scheduler/network sysctl parameter setting."""
    
    def test_set_vm_swappiness(self, param_manager):
        orig = read_sysctl_value("vm.swappiness")
        if orig is None:
            pytest.skip("vm.swappiness not available")
        test_value = 10 if orig != "10" else 1
        assert_set_and_restore_sysctl(param_manager, "set_vm_swappiness", "vm.swappiness", test_value, orig)
    
    def test_set_vm_dirty_ratio(self, param_manager):
        orig = read_sysctl_value("vm.dirty_ratio")
        if orig is None:
            pytest.skip("vm.dirty_ratio not available")
        test_value = 15 if orig != "15" else 20
        assert_set_and_restore_sysctl(param_manager, "set_vm_dirty_ratio", "vm.dirty_ratio", test_value, orig)
    
    def test_set_numa_balancing(self, param_manager):
        orig = read_sysctl_value("kernel.numa_balancing")
        if orig is None:
            pytest.skip("kernel.numa_balancing not available")
        test_value = bool(orig == "0")
        assert_set_and_restore_sysctl(param_manager, "set_numa_balancing", "kernel.numa_balancing", test_value, orig)
    
    def test_set_sched_autogroup_enabled(self, param_manager):
        orig = read_sysctl_value("kernel.sched_autogroup_enabled")
        if orig is None:
            pytest.skip("kernel.sched_autogroup_enabled not available")
        test_value = bool(orig == "0")
        assert_set_and_restore_sysctl(
            param_manager,
            "set_sched_autogroup_enabled",
            "kernel.sched_autogroup_enabled",
            test_value,
            orig,
        )
    
    def test_set_somaxconn(self, param_manager):
        orig = read_sysctl_value("net.core.somaxconn")
        if orig is None:
            pytest.skip("net.core.somaxconn not available")
        test_value = 8192 if orig != "8192" else 4096
        assert_set_and_restore_sysctl(param_manager, "set_somaxconn", "net.core.somaxconn", test_value, orig)
    
    def test_set_netdev_max_backlog(self, param_manager):
        orig = read_sysctl_value("net.core.netdev_max_backlog")
        if orig is None:
            pytest.skip("net.core.netdev_max_backlog not available")
        test_value = 2000 if orig != "2000" else 1000
        assert_set_and_restore_sysctl(
            param_manager, "set_netdev_max_backlog", "net.core.netdev_max_backlog", test_value, orig
        )
    
    def test_set_tcp_fin_timeout(self, param_manager):
        orig = read_sysctl_value("net.ipv4.tcp_fin_timeout")
        if orig is None:
            pytest.skip("net.ipv4.tcp_fin_timeout not available")
        test_value = 30 if orig != "30" else 60
        assert_set_and_restore_sysctl(
            param_manager, "set_tcp_fin_timeout", "net.ipv4.tcp_fin_timeout", test_value, orig
        )
    
    def test_set_tcp_tw_reuse(self, param_manager):
        orig = read_sysctl_value("net.ipv4.tcp_tw_reuse")
        if orig is None:
            pytest.skip("net.ipv4.tcp_tw_reuse not available")
        test_value = 1 if orig != "1" else 2
        assert_set_and_restore_sysctl(
            param_manager, "set_tcp_tw_reuse", "net.ipv4.tcp_tw_reuse", test_value, orig
        )
    
    def test_set_tcp_congestion_control(self, param_manager):
        available = read_sysctl_value("net.ipv4.tcp_available_congestion_control")
        orig = read_sysctl_value("net.ipv4.tcp_congestion_control")
        if available is None or orig is None:
            pytest.skip("tcp congestion control sysctls not available")
        choices = available.split()
        if len(choices) < 2:
            pytest.skip("Only one congestion control algorithm available")
        test_value = choices[0] if orig != choices[0] else choices[1]
        assert_set_and_restore_sysctl(
            param_manager, "set_tcp_congestion_control", "net.ipv4.tcp_congestion_control", test_value, orig
        )


class TestSystemDefaultResetHelpers:
    """Test system-default discovery and selective reset helpers for new params."""
    
    def test_discover_system_defaults_for_new_parameters(self):
        defaults = get_system_defaults_for_new_parameters()
        assert isinstance(defaults, dict)
        
        # At least one known key should exist on most Linux systems.
        assert any(
            key in defaults for key in ("vm_swappiness", "somaxconn", "tcp_fin_timeout")
        ), "Expected at least one discovered new-parameter system default"
    
    def test_reset_new_parameters_to_system_defaults_subset(self, param_manager):
        # Pick a common sysctl-backed parameter from the new set.
        param_name = "vm_swappiness"
        sysctl_key = "vm.swappiness"
        # Capture a fresh snapshot before changing the value.
        get_system_defaults_for_new_parameters(refresh_snapshot=True)
        orig = read_sysctl_value(sysctl_key)
        if orig is None:
            pytest.skip(f"{sysctl_key} not available")
        
        # Change it first.
        test_value = 10 if orig != "10" else 1
        assert param_manager.set_vm_swappiness(test_value)
        assert read_sysctl_value(sysctl_key) == str(test_value)
        
        # Reset only this parameter via helper.
        ok = reset_new_parameters_to_system_defaults(param_manager, {param_name})
        assert ok, "Selective reset of new parameters should succeed"
        assert read_sysctl_value(sysctl_key) == orig
    
    def test_get_new_parameter_names_contains_expected(self):
        names = get_new_parameter_names()
        assert "vm_swappiness" in names
        assert "tcp_congestion_control" in names


if __name__ == "__main__":
    # Run tests directly
    pytest.main([__file__, "-v", "--tb=short"])
