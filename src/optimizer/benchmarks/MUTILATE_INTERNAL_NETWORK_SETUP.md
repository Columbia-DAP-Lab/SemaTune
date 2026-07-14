# Mutilate internal-network reference

Use the private data-plane addresses as explicit setup arguments. For the
current two-node allocation:

| Role | Address | Processes |
| --- | --- | --- |
| SemaTune server | `10.10.1.2` | optimizer, memcached, control listener |
| Load generator | `10.10.1.3` | managed Mutilate client |

## Commands

On the server:

```bash
scripts/setup.sh --memcached-server \
  --server-ip 10.10.1.2 --client-ip 10.10.1.3
functional_example/run_mutilate.sh --dry-run
```

On the load generator, from the same Git revision:

```bash
scripts/setup.sh --memcached-client \
  --server-ip 10.10.1.2 --client-ip 10.10.1.3
systemctl status sematune-mutilate-client --no-pager
```

The service may remain running while the server is idle. It repeatedly attempts
the private control endpoint and receives the memcached target and workload
shape only after the optimizer accepts it.

## Network behavior

- Memcached binds to `10.10.1.2:11211`, not all host interfaces.
- The coordination listener binds to `10.10.1.2:19876`.
- The client binds its outgoing control socket to `10.10.1.3`.
- The server verifies the accepted peer address before sending configuration.
- Mutilate connects directly to `10.10.1.2:11211`, so its data traffic follows
  the internal route.

Executable code does not depend on an allocation-specific address. Setup
writes the supplied values to the ignored `functional_example/mutilate.env` on
each node, and the Functional runner overwrites the canonical source config's
historical addresses when it materializes the run. Supplying different valid
addresses is sufficient for another allocation.

If a host firewall is active, allow TCP `11211` and `19876` only from the
configured client address; do not expose an unauthenticated memcached service
on a public interface.

For the real-provider execution command and result acceptance rules, see
[`MUTILATE_README.md`](MUTILATE_README.md).
