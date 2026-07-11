#!/usr/bin/env python3
"""
Benchmark registry defining all supported benchmarks and their properties.

This module provides a centralized registry of benchmarks with their commands,
setup requirements, and other metadata.
"""

from enum import Enum
from dataclasses import dataclass
from typing import List, Optional, Dict, Any


@dataclass
class BenchmarkInfo:
    """Information about a benchmark."""
    name: str
    script: str  # Path to script/lua file
    requires_setup: bool  # Whether pre_execute setup is needed
    requires_cleanup: bool  # Whether cleanup is needed
    base_command: List[str]  # Base command parts (without script)
    description: str = ""
    default_options: Dict[str, Any] = None  # Default options
    
    def __post_init__(self):
        if self.default_options is None:
            self.default_options = {}


class BenchmarkType(Enum):
    """Enumeration of all supported benchmark types."""
    
    # Sysbench OLTP benchmarks (require setup/cleanup)
    SYSBENCH_OLTP = BenchmarkInfo(
        name="sysbench_oltp",
        script="/usr/share/sysbench/oltp_read_write.lua",
        requires_setup=True,
        requires_cleanup=True,
        base_command=["sysbench"],
        description="Sysbench OLTP read-write benchmark (requires database)"
    )
    
    SYSBENCH_OLTP_CONTINUOUS = BenchmarkInfo(
        name="sysbench_oltp_continuous",
        script="/usr/share/sysbench/oltp_read_write.lua",
        requires_setup=True,
        requires_cleanup=True,
        base_command=["sysbench"],
        description="Sysbench OLTP read-write benchmark (continuous mode, no restarts)"
    )
    
    # Sysbench fileio benchmarks (no setup/cleanup)
    SYSBENCH_FILEIO_SEQWR = BenchmarkInfo(
        name="sysbench_fileio_seqwr",
        script="/usr/share/sysbench/fileio.lua",
        requires_setup=False,
        requires_cleanup=False,
        base_command=["sysbench"],
        description="Sysbench file I/O sequential write test",
        default_options={
            "file_num": 128,
            "file_block_size": 16384,
            "file_total_size": "2G",
            "file_test_mode": "seqwr",
            "file_io_mode": "sync"
        }
    )
    
    SYSBENCH_FILEIO_SEQRDR = BenchmarkInfo(
        name="sysbench_fileio_seqrdr",
        script="/usr/share/sysbench/fileio.lua",
        requires_setup=False,
        requires_cleanup=False,
        base_command=["sysbench"],
        description="Sysbench file I/O sequential rewrite test",
        default_options={
            "file_num": 128,
            "file_block_size": 16384,
            "file_total_size": "2G",
            "file_test_mode": "seqrewr",
            "file_io_mode": "sync"
        }
    )
    
    SYSBENCH_FILEIO_SEQRD = BenchmarkInfo(
        name="sysbench_fileio_seqrd",
        script="/usr/share/sysbench/fileio.lua",
        requires_setup=False,
        requires_cleanup=False,
        base_command=["sysbench"],
        description="Sysbench file I/O sequential read test",
        default_options={
            "file_num": 128,
            "file_block_size": 16384,
            "file_total_size": "2G",
            "file_test_mode": "seqrd",
            "file_io_mode": "sync"
        }
    )
    
    SYSBENCH_FILEIO_RNDRD = BenchmarkInfo(
        name="sysbench_fileio_rndrd",
        script="/usr/share/sysbench/fileio.lua",
        requires_setup=False,
        requires_cleanup=False,
        base_command=["sysbench"],
        description="Sysbench file I/O random read test",
        default_options={
            "file_num": 128,
            "file_block_size": 16384,
            "file_total_size": "2G",
            "file_test_mode": "rndrd",
            "file_io_mode": "sync"
        }
    )
    
    SYSBENCH_FILEIO_RNDWR = BenchmarkInfo(
        name="sysbench_fileio_rndwr",
        script="/usr/share/sysbench/fileio.lua",
        requires_setup=False,
        requires_cleanup=False,
        base_command=["sysbench"],
        description="Sysbench file I/O random write test",
        default_options={
            "file_num": 128,
            "file_block_size": 16384,
            "file_total_size": "2G",
            "file_test_mode": "rndwr",
            "file_io_mode": "sync"
        }
    )
    
    SYSBENCH_FILEIO_RNDRW = BenchmarkInfo(
        name="sysbench_fileio_rndrw",
        script="/usr/share/sysbench/fileio.lua",
        requires_setup=False,
        requires_cleanup=False,
        base_command=["sysbench"],
        description="Sysbench file I/O random read-write test",
        default_options={
            "file_num": 128,
            "file_block_size": 16384,
            "file_total_size": "2G",
            "file_test_mode": "rndrw",
            "file_io_mode": "sync",
            "file_rw_ratio": 1.5
        }
    )
    
    # Sysbench CPU benchmark (no setup/cleanup)
    SYSBENCH_CPU = BenchmarkInfo(
        name="sysbench_cpu",
        script="cpu",  # Built-in test in sysbench 1.0+, not a file
        requires_setup=False,
        requires_cleanup=False,
        base_command=["sysbench"],
        description="Sysbench CPU performance test",
        default_options={
            "cpu_max_prime": 20000
        }
    )
    
    # Sysbench memory benchmarks (no setup/cleanup)
    SYSBENCH_MEMORY_READ = BenchmarkInfo(
        name="sysbench_memory_read",
        script="/usr/share/sysbench/memory.lua",
        requires_setup=False,
        requires_cleanup=False,
        base_command=["sysbench"],
        description="Sysbench memory read operations test",
        default_options={
            "memory_block_size": "1K",
            "memory_total_size": "100G",
            "memory_scope": "global",
            "memory_oper": "read",
            "memory_access_mode": "seq"
        }
    )
    
    SYSBENCH_MEMORY_WRITE = BenchmarkInfo(
        name="sysbench_memory_write",
        script="/usr/share/sysbench/memory.lua",
        requires_setup=False,
        requires_cleanup=False,
        base_command=["sysbench"],
        description="Sysbench memory write operations test",
        default_options={
            "memory_block_size": "1K",
            "memory_total_size": "100G",
            "memory_scope": "global",
            "memory_oper": "write",
            "memory_access_mode": "seq"
        }
    )
    
    # Sysbench threads benchmark (no setup/cleanup)
    SYSBENCH_THREADS = BenchmarkInfo(
        name="sysbench_threads",
        script="/usr/share/sysbench/threads.lua",
        requires_setup=False,
        requires_cleanup=False,
        base_command=["sysbench"],
        description="Sysbench threads subsystem performance test"
    )
    
    # BenchBase/TPCC benchmark
    TPCC = BenchmarkInfo(
        name="tpcc",
        script="",  # Not applicable for BenchBase
        requires_setup=True,
        requires_cleanup=False,
        base_command=["java", "-jar", "benchbase.jar"],
        description="BenchBase TPCC benchmark (requires database)"
    )
    
    # BenchBase/YCSB benchmark
    YCSB = BenchmarkInfo(
        name="ycsb",
        script="",  # Not applicable for BenchBase
        requires_setup=True,
        requires_cleanup=False,
        base_command=["java", "-jar", "benchbase.jar"],
        description="BenchBase YCSB benchmark (requires database)"
    )
    
    # BenchBase/SIBench benchmark
    SIBENCH = BenchmarkInfo(
        name="sibench",
        script="",  # Not applicable for BenchBase
        requires_setup=True,
        requires_cleanup=False,
        base_command=["java", "-jar", "benchbase.jar"],
        description="BenchBase SIBench benchmark (requires database)"
    )

    # BenchBase/Wikipedia benchmark
    WIKIPEDIA = BenchmarkInfo(
        name="wikipedia",
        script="",  # Not applicable for BenchBase
        requires_setup=True,
        requires_cleanup=False,
        base_command=["java", "-jar", "benchbase.jar"],
        description="BenchBase Wikipedia benchmark (requires database)"
    )

    # BenchBase/Twitter benchmark
    TWITTER = BenchmarkInfo(
        name="twitter",
        script="",  # Not applicable for BenchBase
        requires_setup=True,
        requires_cleanup=False,
        base_command=["java", "-jar", "benchbase.jar"],
        description="BenchBase Twitter benchmark (requires database)"
    )
    
    # BenchBase/AuctionMark benchmark
    AUCTIONMARK = BenchmarkInfo(
        name="auctionmark",
        script="",  # Not applicable for BenchBase
        requires_setup=True,
        requires_cleanup=False,
        base_command=["java", "-jar", "benchbase.jar"],
        description="BenchBase AuctionMark benchmark (requires database)"
    )
    
    # BenchBase/OTMetrics benchmark
    OTMETRICS = BenchmarkInfo(
        name="otmetrics",
        script="",  # Not applicable for BenchBase
        requires_setup=True,
        requires_cleanup=False,
        base_command=["java", "-jar", "benchbase.jar"],
        description="BenchBase OTMetrics benchmark (requires database)"
    )
    
    # DCPerf SparkBench benchmark
    DCPERF_SPARK = BenchmarkInfo(
        name="dcperf_spark",
        script="",  # Not applicable — uses benchpress_cli.py
        requires_setup=True,
        requires_cleanup=False,
        base_command=["./benchpress_cli.py", "run"],
        description="DCPerf SparkBench benchmark (Spark SQL, end-to-end, run-to-completion)"
    )
    
    # DCPerf MediaWiki benchmark
    DCPERF_MEDIAWIKI = BenchmarkInfo(
        name="dcperf_mediawiki",
        script="",  # Not applicable — uses benchpress_cli.py
        requires_setup=True,
        requires_cleanup=False,
        base_command=["./benchpress_cli.py", "run"],
        description="DCPerf MediaWiki benchmark (HHVM + nginx + wrk, end-to-end, run-to-completion)"
    )
    
    # DCPerf Django workload benchmark
    DCPERF_DJANGO = BenchmarkInfo(
        name="dcperf_django",
        script="",  # Not applicable — uses benchpress_cli.py
        requires_setup=True,
        requires_cleanup=False,
        base_command=["./benchpress_cli.py", "run"],
        description="DCPerf DjangoBench benchmark (Django + Cassandra + Siege, end-to-end, run-to-completion)"
    )
    
    # Mutilate benchmark
    MUTILATE = BenchmarkInfo(
        name="mutilate",
        script="",  # Not applicable
        requires_setup=True,  # Needs memcached + client connection
        requires_cleanup=True,  # Needs cleanup
        base_command=[],  # Not applicable
        description="Distributed mutilate benchmark (memcached + remote client)"
    )
    
    # Tailbench benchmark suite
    TAILBENCH = BenchmarkInfo(
        name="tailbench",
        script="",  # Not applicable
        requires_setup=True,  # Starts continuous benchmark in pre_execute
        requires_cleanup=True,  # Stops continuous benchmark
        base_command=[],  # Command varies by app
        description="Tailbench latency-critical applications (8 apps: img-dnn, masstree, moses, shore, silo, specjbb, sphinx, xapian)"
    )

    @classmethod
    def from_string(cls, name: str) -> 'BenchmarkType':
        """Get benchmark type from string name.
        
        Args:
            name: Benchmark name string
            
        Returns:
            BenchmarkType enum value
            
        Raises:
            ValueError: If benchmark name is not found
        """
        for benchmark_type in cls:
            if benchmark_type.value.name == name:
                return benchmark_type
        
        raise ValueError(
            f"Unknown benchmark: {name}. "
            f"Available benchmarks: {[bt.value.name for bt in cls]}"
        )
    
    @classmethod
    def list_all(cls) -> List[str]:
        """List all available benchmark names.
        
        Returns:
            List of benchmark name strings
        """
        return [bt.value.name for bt in cls]
    
    @classmethod
    def get_info(cls, name: str) -> BenchmarkInfo:
        """Get benchmark info by name.
        
        Args:
            name: Benchmark name string
            
        Returns:
            BenchmarkInfo object
        """
        return cls.from_string(name).value
