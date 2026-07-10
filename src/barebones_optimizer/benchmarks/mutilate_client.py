#!/usr/bin/env python3
"""
Mutilate Client Script
Run this on the remote client machine to generate load for the benchmark.
"""

import socket
import json
import subprocess
import time
import os
import signal
import sys
import logging

# --- CONFIGURATION ---
# Update this to your server's IP address
SERVER_HOST = "10.10.1.3"
SERVER_PORT = 19876

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Protocol constants (must match server)
MSG_READY = "CLIENT_READY"
MSG_STAGE_PREPARE = "STAGE_PREPARE"
MSG_STAGE_START = "STAGE_START"
MSG_STAGE_END = "STAGE_END"
MSG_PERF_DATA = "PERF_DATA"
MSG_ALL_DONE = "ALL_DONE"
MSG_ACK = "ACK"

class MutilateClient:
    def __init__(self, server_host, server_port):
        self.server_host = server_host
        self.server_port = server_port
        self.sock = None
        self.config = {}
        self.mutilate_proc = None
        
    def connect(self):
        """Connect to the server."""
        logger.info(f"Connecting to server at {self.server_host}:{self.server_port}...")
        while True:
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.connect((self.server_host, self.server_port))
                logger.info("Connected to server!")
                break
            except ConnectionRefusedError:
                logger.info("Connection refused, retrying in 2 seconds...")
                time.sleep(2)
            except Exception as e:
                logger.error(f"Connection error: {e}")
                time.sleep(2)

    def _send_msg(self, msg_type, data=None):
        """Send JSON message to server."""
        msg = {'type': msg_type, 'data': data or {}}
        msg_bytes = json.dumps(msg).encode() + b'\n'
        self.sock.sendall(msg_bytes)

    def _recv_msg(self):
        """Receive JSON message from server."""
        buffer = b''
        while b'\n' not in buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("Server disconnected")
            buffer += chunk
        msg_bytes, _ = buffer.split(b'\n', 1)
        return json.loads(msg_bytes.decode())

    def run(self):
        """Main execution loop - runs forever, reconnecting after each experiment."""
        while True:  # Outer loop: reconnect after each experiment
            try:
                self.connect()
                
                # 1. Send READY signal
                # We can send local capabilities here if needed
                self._send_msg(MSG_READY, {"hostname": socket.gethostname()})
                
                # 2. Receive Configuration
                ack = self._recv_msg()
                if ack['type'] != MSG_ACK:
                    raise ValueError(f"Expected ACK with config, got {ack['type']}")
                
                self.config = ack['data']
                logger.info(f"Received configuration: {json.dumps(self.config, indent=2)}")
                
                # Validate config
                required_keys = ['mutilate_bin', 'target', 'threads', 'clients', 'qps', 'depth']
                for k in required_keys:
                    if k not in self.config:
                        logger.warning(f"Missing config key: {k}, using defaults or failing")

                mutilate_bin = os.path.expanduser(self.config.get('mutilate_bin', '~/mutilate/mutilate'))
                if not os.path.exists(mutilate_bin):
                    logger.error(f"Mutilate binary not found at {mutilate_bin}")
                    # Don't exit yet, maybe it's in PATH
                    if subprocess.run(["which", "mutilate"], stdout=subprocess.DEVNULL).returncode == 0:
                        mutilate_bin = "mutilate"
                        logger.info("Found mutilate in PATH")
                    else:
                        logger.error("Please install mutilate or update path")
                        # Close connection and try again
                        if self.sock:
                            self.sock.close()
                            self.sock = None
                        time.sleep(5)
                        continue

                # Load data into memcached once per experiment
                logger.info("Loading data into memcached...")
                load_cmd = [
                    mutilate_bin,
                    "-s", self.config['target'],
                    "--loadonly",
                    "--records=10000"  # Load 10k records
                ]
                try:
                    logger.info(f"Running: {' '.join(load_cmd)}")
                    subprocess.run(load_cmd, check=True, timeout=30, capture_output=True)
                    logger.info("✓ Data loaded successfully")
                except subprocess.TimeoutExpired:
                    logger.error("Data loading timeout - memcached may not be reachable")
                    # Close connection and try again
                    if self.sock:
                        self.sock.close()
                        self.sock = None
                    time.sleep(5)
                    continue
                except subprocess.CalledProcessError as e:
                    logger.error(f"Data loading failed: {e.stderr.decode() if e.stderr else 'Unknown error'}")
                    # Close connection and try again
                    if self.sock:
                        self.sock.close()
                        self.sock = None
                    time.sleep(5)
                    continue
                except Exception as e:
                    logger.error(f"Data loading error: {e}")
                    # Close connection and try again
                    if self.sock:
                        self.sock.close()
                        self.sock = None
                    time.sleep(5)
                    continue

                # Main Loop for this experiment
                while True:
                    msg = self._recv_msg()

                    if msg['type'] == MSG_ALL_DONE:
                        logger.info("Received ALL_DONE signal. Optimization complete.")
                        logger.info("Closing connection and waiting for next experiment...")
                        break  # Break inner loop, will reconnect in outer loop

                    elif msg['type'] == MSG_STAGE_START:
                        stage_num = msg['data'].get('stage_num', 0)
                        logger.info(f"Starting Stage {stage_num}...")

                        # Run mutilate in 1-second epochs until STAGE_END.
                        # This ensures each run completes and produces proper statistics.
                        samples = []
                        sample_count = 0

                        # Ack start immediately
                        self._send_msg(MSG_ACK)

                        # Set socket to non-blocking to check for end signal
                        self.sock.setblocking(False)

                        # Run epochs until we receive STAGE_END
                        while True:
                            # Check for STAGE_END message (non-blocking)
                            try:
                                import select
                                ready = select.select([self.sock], [], [], 0.1)
                                if ready[0]:
                                    # Message waiting
                                    self.sock.setblocking(True)
                                    msg_check = self._recv_msg()
                                    if msg_check['type'] == MSG_STAGE_END:
                                        logger.info(f"Ending Stage {stage_num}...")
                                        break
                                    # Put socket back to non-blocking
                                    self.sock.setblocking(False)
                            except Exception:
                                pass

                            # Run one 1-second mutilate epoch
                            cmd = [
                                mutilate_bin,
                                "-s", self.config['target'],
                                "--noload",
                                "-T", str(self.config.get('threads', 4)),
                                "-c", str(self.config.get('clients', 4)),
                                "-q", str(self.config.get('qps', 1000)),
                                "-d", str(self.config.get('depth', 1)),
                                "-t", "1",  # 1 second per epoch
                                "-W", "0"   # No warmup
                            ]

                            # Add distribution if specified
                            iadist = self.config.get('iadist')
                            if iadist:
                                cmd.extend(["--iadist", iadist])

                            # Run mutilate
                            try:
                                result = subprocess.run(
                                    cmd,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    universal_newlines=True,
                                    timeout=5
                                )

                                # Parse output
                                sample = self._parse_mutilate_output(result.stdout)
                                samples.append(sample)
                                sample_count += 1

                                # Log progress for first few samples, then every 5
                                if sample_count <= 3 or sample_count % 5 == 0:
                                    p99 = sample.get('p99_us', 0) or 0
                                    thr = sample.get('throughput', 0) or 0
                                    if p99 > 0 and thr > 0:
                                        logger.info(
                                            f"  Sample {sample_count}: p99={p99:.1f}us, thr={thr:.0f} ops/s"
                                        )
                                    else:
                                        logger.info(f"  Sample {sample_count}: ERROR (no metrics)")
                            except subprocess.TimeoutExpired:
                                logger.warning(f"  Sample {sample_count}: timeout")
                                samples.append({'avg_us': 0, 'p95_us': 0, 'p99_us': 0, 'throughput': 0})
                            except Exception as e:
                                logger.warning(f"  Sample {sample_count}: error - {e}")
                                samples.append({'avg_us': 0, 'p95_us': 0, 'p99_us': 0, 'throughput': 0})

                        # Restore blocking mode
                        self.sock.setblocking(True)

                        logger.info(f"Collected {len(samples)} samples, sending to server...")

                        # Send data back
                        self._send_msg(MSG_PERF_DATA, {'samples': samples})

                    # MSG_STAGE_END is handled inline during MSG_STAGE_START

            except ConnectionError as e:
                logger.warning(f"Connection lost: {e}")
                logger.info("Will reconnect when server is available...")
            except Exception as e:
                logger.error(f"Error: {e}", exc_info=True)
                logger.info("Will try to reconnect in 5 seconds...")
                time.sleep(5)
            finally:
                # Clean up connection
                if self.sock:
                    try:
                        self.sock.close()
                    except:
                        pass
                    self.sock = None
                if self.mutilate_proc:
                    try:
                        self.mutilate_proc.kill()
                    except:
                        pass
                    self.mutilate_proc = None
                
                # Wait a bit before reconnecting
                logger.info("Waiting for next server connection...")
                time.sleep(2)

    def _parse_mutilate_output(self, output):
        """Parse standard mutilate output from a single epoch."""
        # Mutilate output format is typically:
        # read       20.6 us       15.5 us       28.5 us ...
        # Total QPS = 48234.2 (144703 / 3.0s)
        
        avg_lat = 0.0
        p95_lat = 0.0
        p99_lat = 0.0
        throughput = 0.0
        
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("read"):
                parts = line.split()
                # Standard mutilate format:
                # read    avg    min    5th    10th   90th   95th   99th
                try:
                    # Find all numbers in the line
                    nums = []
                    for p in parts:
                        try:
                            nums.append(float(p))
                        except ValueError:
                            pass
                            
                    if len(nums) >= 4:
                        avg_lat = nums[0]  # avg
                        p99_lat = nums[-1]  # last percentile (99th)
                        p95_lat = nums[-2]  # second to last (95th)
                except Exception:
                    pass
            
            if "Total QPS" in line:
                # Total QPS = 48234.2 (144703 / 3.0s)
                try:
                    import re
                    m = re.search(r'Total QPS\s*=\s*([0-9.]+)', line)
                    if m:
                        throughput = float(m.group(1))
                except:
                    pass

        return {
            'avg_us': avg_lat,
            'p95_us': p95_lat,
            'p99_us': p99_lat,
            'throughput': throughput
        }

if __name__ == "__main__":
    if len(sys.argv) > 1:
        SERVER_HOST = sys.argv[1]
    
    client = MutilateClient(SERVER_HOST, SERVER_PORT)
    client.run()
