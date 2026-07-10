# Mutilate Internal Network Setup Guide

This guide explains how to configure mutilate to use an internal network (10.x.x.x) instead of the external network.

## Network Information

Based on your setup:
- **Server (node0)**: `10.10.1.1/24` on interface `enp6s0f0`
- **Client (node1)**: `10.10.1.2/24` on interface `enp6s0f0`

## Quick Setup Steps

### 1. Server Configuration

Update your configuration file (e.g., `config/simple_config.json`) with internal network IPs:

```json
{
  "benchmark": "mutilate",
  "mutilate_client_host": "10.10.1.2",        // Client's internal IP
  "mutilate_target": "10.10.1.1:11211",        // Server's internal IP
  "mutilate_threads": 8,
  "mutilate_clients": 8,
  "mutilate_qps": 500000,
  "mutilate_iadist": "fixed:0",
  "mutilate_depth": 1,
  "mutilate_bin_path": "~/mutilate/mutilate",
  "mutilate_memcached_bin": "memcached",
  ...
}
```

**Key points:**
- `mutilate_client_host`: Must be the client's internal IP (`10.10.1.2`)
- `mutilate_target`: Must be the server's internal IP with port (`10.10.1.1:11211`)

### 2. Client Script Configuration

On the client machine (node1), edit `mutilate_client.py` and update:

```python
SERVER_HOST = "10.10.1.1"  # Server's internal IP
```

### 3. Verify Network Connectivity

Before running the benchmark, verify connectivity:

**On server (node0):**
```bash
ping -c 3 10.10.1.2
telnet 10.10.1.2 22  # Test SSH connectivity
```

**On client (node1):**
```bash
ping -c 3 10.10.1.1
telnet 10.10.1.1 11211  # Test memcached port
telnet 10.10.1.1 19876  # Test coordination port
```

### 4. Firewall Considerations

Ensure ports are open on the internal interface:

**On server (node0):**
```bash
# Check if ports are listening
sudo netstat -tlnp | grep -E '11211|19876'

# If using firewall, allow ports on internal interface
sudo ufw allow from 10.10.1.0/24 to any port 11211
sudo ufw allow from 10.10.1.0/24 to any port 19876
```

## How It Works

1. **Memcached**: Binds to `0.0.0.0:11211` (all interfaces), so it accepts connections on both external and internal networks. The client connects using the IP specified in `mutilate_target`.

2. **TCP Coordination Server**: Binds to `0.0.0.0:19876` (all interfaces), so it accepts connections on both networks. The client connects using the IP specified in `SERVER_HOST`.

3. **Traffic Flow**:
   - Client → Server (port 19876): Coordination messages
   - Client → Server (port 11211): Memcached requests/responses

All traffic will use the internal network (10.10.1.x) when configured as above.

## Troubleshooting

### Client Cannot Connect

1. **Check IP addresses**: Verify both server and client are using internal IPs consistently
2. **Check routing**: Ensure both machines can reach each other on the internal network
   ```bash
   # On server
   ip route get 10.10.1.2
   
   # On client
   ip route get 10.10.1.1
   ```
3. **Check interface**: Ensure `enp6s0f0` is UP on both machines
   ```bash
   ip link show enp6s0f0
   ```

### Memcached Not Reachable

1. **Verify memcached is listening on all interfaces**:
   ```bash
   sudo netstat -tlnp | grep 11211
   # Should show: 0.0.0.0:11211
   ```

2. **Test from client**:
   ```bash
   telnet 10.10.1.1 11211
   # Should connect successfully
   ```

### Wrong Network Interface Used

If traffic is going over the external network instead of internal:

1. Check routing table priority:
   ```bash
   ip route show
   # Look for routes to 10.10.1.0/24
   ```

2. Verify interface metrics (lower metric = higher priority):
   ```bash
   ip addr show enp6s0f0
   # Check if metric is set appropriately
   ```

## Example: Complete Setup

**Server (node0) - config.json:**
```json
{
  "benchmark": "mutilate",
  "mutilate_client_host": "10.10.1.2",
  "mutilate_target": "10.10.1.1:11211",
  ...
}
```

**Client (node1) - mutilate_client.py:**
```python
SERVER_HOST = "10.10.1.1"
```

**Run on server:**
```bash
python3 src/barebones_optimizer/main.py -c config.json
```

**Run on client:**
```bash
python3 mutilate_client.py
```

All communication will use the internal network (10.10.1.x).



