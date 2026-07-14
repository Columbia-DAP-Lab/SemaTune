# Mutilate benchmark setup and usage

Mutilate is a two-node benchmark. The **server** runs TuxBot and a memcached
process whose operating-system controls are tuned. The **client** runs the
pinned Mutilate load generator and returns measured latency and throughput over
an internal control connection.

Both hosts must be Ubuntu 22.04 x86-64 machines with non-interactive sudo and
the same TuxBot revision. Live tuning is intended only for dedicated or
disposable bare-metal hosts.

## Automated role setup

Pass both allocation addresses on both machines. For a server at `10.10.1.2`
and load generator at `10.10.1.3`:

```bash
# On 10.10.1.2
scripts/setup.sh --memcached-server \
  --server-ip 10.10.1.2 --client-ip 10.10.1.3

# On 10.10.1.3
scripts/setup.sh --memcached-client \
  --server-ip 10.10.1.2 --client-ip 10.10.1.3
```

The server mode includes the normal base TuxBot installation and installs
memcached, but leaves the distribution service disabled because the benchmark
adapter starts and stops its own instance. The client mode installs only the
pinned Mutilate build closure and creates
`sematune-mutilate-client.service`. Both modes verify that their role address
is assigned locally and that the peer is routable.

The generated `functional_example/mutilate.env` is local, ignored by Git, and
contains no credential. Rerun the same setup command after changing addresses,
moving the checkout, or updating the client service code.

Full benchmark installation can configure the server role at the same time:

```bash
scripts/setup.sh --full \
  --server-ip 10.10.1.2 --client-ip 10.10.1.3
```

Without the paired addresses, `--full` keeps its historical software-only
behavior. The base installation never installs Mutilate or memcached.

## Client service

The load-generator service is enabled and started by setup. It binds the
control connection to the configured client address, advertises its checkout's
absolute Mutilate binary path, and reconnects after every completed or failed
experiment. It is safe to start the client before the optimizer.

```bash
systemctl status sematune-mutilate-client --no-pager
journalctl -u sematune-mutilate-client -f
```

For foreground diagnosis, stop the service and run its wrapper:

```bash
sudo systemctl stop sematune-mutilate-client
scripts/run_mutilate_client.sh
```

Rerun the client setup command to restore and restart the managed service.

## Reduced real-provider Functional run

On the server:

```bash
functional_example/run_mutilate.sh --dry-run

export GEMINI_API_KEY='<provided-key>'
functional_example/run_mutilate.sh --quick --real-llm \
  --output-dir results/functional_mutilate_real
```

The runner materializes a deployment-specific copy of the canonical Mutilate
TuxBot-App configuration. It uses one default baseline, three tuning, and two
stable five-second windows. Actor and Speculator both use Gemini 2.5
Flash-Lite. The canonical reproduction JSON is not modified.

The runner captures all host controls before launching the optimizer and
restores and byte-verifies them after success, failure, timeout, or a signal.
It accepts a run only when:

- the optimizer records all six expected windows;
- every window contains at least two valid samples;
- throughput, goodput, and average/p95/p99 latency are finite and positive;
- both Actor and Speculator contain real API token evidence and no replay file
  was used; and
- host restoration passes without byte mismatches.

Successful output includes the raw optimizer history, generated config, logs,
machine and restoration records, and `mutilate_summary.json` plus
`mutilate_summary.csv`.

## Runtime configuration

The benchmark retains these configuration fields for custom runs:

- `mutilate_client_host`: expected source address of the load generator;
- `mutilate_target`: memcached server and port, such as
  `10.10.1.2:11211`;
- `mutilate_control_port`: coordination port, default `19876`;
- `mutilate_threads`, `mutilate_clients`, `mutilate_qps`,
  `mutilate_iadist`, and `mutilate_depth`: load shape; and
- `mutilate_memcached_bin`: server executable. The client service advertises
  its local pinned `mutilate` path during handshake.

Memcached binds only to the host in `mutilate_target`; the control listener
binds to the same internal host and rejects peers other than
`mutilate_client_host`. TCP ports `11211` and `19876` therefore need to be
reachable only across the private link.

## Troubleshooting

If the optimizer times out waiting for a client, inspect the client unit and
confirm both checkouts contain the same generated addresses. From the client,
`ping <server-ip>` must succeed. The Functional server log is
`logs/mutilate_sematune_app.log` beneath the selected output directory.

If loading or measurement fails, the client journal includes the exact
Mutilate exit status. Failed or unparsable epochs are counted separately;
zero-filled measurements are never passed to the optimizer. Setup can be
rerun safely on either node to rebuild the pinned binary and refresh the unit.
