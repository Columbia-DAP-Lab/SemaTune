# Mutilate Internal Network Configuration

This directory contains mutilate benchmark configurations configured to use the internal network (10.10.1.x).

## Network Setup

- **Server (node0)**: `10.10.1.1` on interface `enp6s0f0`
- **Client (node1)**: `10.10.1.2` on interface `enp6s0f0`

## Configuration Files

- `mutilate_config_llm.json` - LLM-based tuner configuration
- `mutilate_config_mlos.json` - MLOS tuner configuration
- `mutilate_config_fixed.json` - Fixed parameters (no tuning)

## Key Configuration Parameters

All configs use:
- `mutilate_client_host`: `10.10.1.2` (client's internal IP)
- `mutilate_target`: `10.10.1.1:11211` (server's internal IP)

## Client Setup

On the client machine (node1), ensure `mutilate_client.py` has:

```python
SERVER_HOST = "10.10.1.1"  # Server's internal IP
```

## Usage

### 1. Start Optimizer on Server (node0)

```bash
# LLM tuner
python3 src/barebones_optimizer/main.py -c config/mutilate_internal_network/mutilate_config_llm.json

# Or MLOS tuner
python3 src/barebones_optimizer/main.py -c config/mutilate_internal_network/mutilate_config_mlos.json

# Or fixed parameters
python3 src/barebones_optimizer/main.py -c config/mutilate_internal_network/mutilate_config_fixed.json
```

### 2. Start Client on Client Machine (node1)

```bash
python3 mutilate_client.py
```

## Verification

Before running, verify network connectivity:

```bash
# On server (node0)
ping -c 3 10.10.1.2

# On client (node1)
ping -c 3 10.10.1.1
telnet 10.10.1.1 11211  # Test memcached port
telnet 10.10.1.1 19876  # Test coordination port
```

## Customization

To adjust the configuration:

1. **Load**: Modify `mutilate_qps` (queries per second)
2. **Threads**: Adjust `mutilate_threads` and `mutilate_clients`
3. **Distribution**: Change `mutilate_iadist` (e.g., `"fixed:0"` for no delay)
4. **Cores**: Modify `pin_to_cores` (e.g., `"0-7"` or `null` for no pinning)
5. **Parameters**: Adjust `parameter_ranges` and `fixed_parameters` as needed

## Notes

- All traffic will use the internal network (10.10.1.x)
- Memcached and coordination server bind to `0.0.0.0`, accepting connections on all interfaces
- Ensure both machines can reach each other on the internal network before running



