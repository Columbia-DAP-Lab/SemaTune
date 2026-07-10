# Parameter Manager Tests

Unit tests for the `parameter_manager.py` module.

## Requirements

- Python 3.7+
- pytest (`pip install pytest`)
- sudo access (tests modify system parameters)

## Running Tests

**Important**: These tests require sudo access because they modify system parameters.

### Run all tests:
```bash
sudo python -m pytest tests/test_parameter_manager.py -v
```

### Run specific test class:
```bash
sudo python -m pytest tests/test_parameter_manager.py::TestSchedulerParameters -v
```

### Run specific test:
```bash
sudo python -m pytest tests/test_parameter_manager.py::TestSchedulerParameters::test_set_latency_ns -v
```

### Run with more verbose output:
```bash
sudo python -m pytest tests/test_parameter_manager.py -v -s
```

### Run and show print statements:
```bash
sudo python -m pytest tests/test_parameter_manager.py -v -s --capture=no
```

## Test Coverage

The tests cover:

1. **Scheduler Parameters**:
   - `latency_ns`
   - `min_granularity_ns` (with auto-sync to `wakeup_granularity_ns`)
   - `wakeup_granularity_ns`
   - `migration_cost_ns`
   - Setting multiple scheduler parameters

2. **DVFS/Turbo Parameters**:
   - `scaling_governor`
   - `epp` (with "default" value)
   - `min_perf_pct`
   - `max_perf_pct`
   - `turbo`

3. **C-state Parameters**:
   - `cstate_max` (including "unlimited")
   - `pmqos`

4. **Network Parameters**:
   - `busy_poll`
   - `busy_read`
   - `netdev_budget`
   - `netdev_budget_usecs`

5. **Default Parameters**:
   - `get_default_parameters()`
   - `reset_all_parameters_to_defaults()`

6. **Parameter Metadata**:
   - `is_per_core_parameter()`
   - `get_parameter_type()`
   - `get_categorical_values()`

7. **Per-Core Parameters**:
   - Setting parameters with core specifications
   - Using dict format with cores

8. **Error Handling**:
   - Invalid parameter names
   - Mixed parameter types

## Notes

- Some parameters may not be available on all systems (e.g., EPP, turbo, C-states). Tests gracefully skip these with `pytest.skip()`.
- Tests verify that parameters are actually set by reading them back.
- The `reset_all_parameters_to_defaults()` test ensures all parameters are reset to their default values.

## Troubleshooting

If tests fail:

1. **Permission denied**: Make sure you're running with `sudo`
2. **Parameter not available**: Some parameters may not be supported on your system. Tests will skip these automatically.
3. **Import errors**: Make sure you're in the project root and `src/` is in your Python path.

