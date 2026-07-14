# Validation environment

This records the machine used for the Artifact Functional validation. It was
captured on 2026-07-14 without hostnames, addresses, MACs, serial numbers, or
credentials. The paper environment is separately frozen in `versions.json`.

## Hardware

| Item | Validation-machine value |
| --- | --- |
| Platform | CloudLab Wisconsin `c220`, x86-64 bare metal |
| CPU | 2 x Intel Xeon Silver 4114 at 2.20 GHz |
| Topology | 2 sockets, 10 cores/socket, 2 threads/core, 40 logical CPUs |
| NUMA | 2 nodes; CPUs `0-9,20-29` and `10-19,30-39` |
| Cache | 640 KiB L1d, 640 KiB L1i, 20 MiB L2, 27.5 MiB L3 |
| RAM | 187 GiB reported by Linux (192 GiB nominal), plus 8 GiB swap |
| Local disks | Intel SSDSC2BB480G7K 480 GB SSD; Seagate ST1200MM0088 1.2 TB HDD |
| Artifact filesystem | ext4, 1.5 TB total on `/mydata` |
| Network controllers | 2 x Intel X550; 2 x Intel X710 10GbE SFP+ |

The Functional allocation uses CPUs `0-9` for controls and performance
counters and CPUs `10-19` for Sysbench. Hyperthreads remain available but are
not part of that allocation.

## Software

| Item | Validation-machine value |
| --- | --- |
| OS | Ubuntu 22.04.2 LTS (Jammy) |
| Kernel | `5.15.0-177-generic` |
| Python | 3.10.12; Ubuntu package `3.10.12-1~22.04.16` |
| pip | 26.0.1 |
| Java | OpenJDK 21.0.11, Ubuntu package `21.0.11+10-1~22.04.2` |
| PostgreSQL client/server | Ubuntu package `14.23-0ubuntu0.22.04.1` |
| Sysbench | Ubuntu package `1.0.20+ds-2` |

The dependency source commits are:

| Source | Commit |
| --- | --- |
| BenchBase | `54d30feb1f9c8b88cca7715fc19de1622cfd1b82` |
| TailBench | `2f3098b539a9a3413086fc77e29637937bafd116` |
| DCPerf | `5d8d16d63cf28311ee85a2f63ce7506ade67dbef` |
| Mutilate | `d65c6ef7c2f78ae05a9db3e37d7f6ddff1c0af64` |

At capture time, the generic `perf` dispatcher warned that its tools did not
match kernel `5.15.0-177-generic`; the read-only Functional dry run still
passed. `scripts/setup.sh --base` installs the matching `linux-tools` package.
Rerun that setup after a kernel upgrade and before any live evaluator run.

## Compare another machine

Run the read-only capture script from the repository root:

```bash
scripts/capture_environment.sh
```

It prints only hardware classes, capacities, software versions, dependency
commits, and artifact footprints. It deliberately omits machine identity and
network addressing.
