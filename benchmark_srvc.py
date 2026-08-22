#!/usr/bin/env python3
"""Benchmark /srvc endpoint performance"""
import time
import psutil
from pathlib import Path


def read_cgroup_memory():
    """Simulate the cgroup reading logic from /srvc"""
    cgroup_mem_limit = None
    cgroup_mem_usage = None

    try:
        mem_max_path = Path("/sys/fs/cgroup/memory.max")
        mem_current_path = Path("/sys/fs/cgroup/memory.current")

        if mem_max_path.exists():
            mem_max = mem_max_path.read_text().strip()
            if mem_max != "max":
                cgroup_mem_limit = int(mem_max)

        if mem_current_path.exists():
            cgroup_mem_usage = int(mem_current_path.read_text().strip())
    except Exception:
        pass

    if cgroup_mem_limit is None:
        try:
            mem_limit_path = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
            mem_usage_path = Path("/sys/fs/cgroup/memory/memory.usage_in_bytes")

            if mem_limit_path.exists():
                limit = int(mem_limit_path.read_text().strip())
                if limit < (1024**4):
                    cgroup_mem_limit = limit

            if mem_usage_path.exists():
                cgroup_mem_usage = int(mem_usage_path.read_text().strip())
        except Exception:
            pass

    return cgroup_mem_limit, cgroup_mem_usage


def benchmark_srvc_endpoint():
    """Measure execution time of /srvc logic"""
    start = time.perf_counter()

    # CPU stats
    cpu_percent = psutil.cpu_percent(interval=0.1)
    cpu_count = psutil.cpu_count(logical=True)
    cpu_freq = psutil.cpu_freq()

    # Memory with cgroup
    cgroup_mem_limit, cgroup_mem_usage = read_cgroup_memory()

    if cgroup_mem_limit and cgroup_mem_usage:
        mem_total = cgroup_mem_limit
        mem_used = cgroup_mem_usage
        mem_available = mem_total - mem_used
        mem_percent = (mem_used / mem_total) * 100 if mem_total > 0 else 0
    else:
        mem = psutil.virtual_memory()
        mem_total = mem.total
        mem_used = mem.used
        mem_available = mem.available
        mem_percent = mem.percent

    # Swap & disk
    swap = psutil.swap_memory()
    disk = psutil.disk_usage('/')

    # Build response (simulating JSON serialization)
    response = {
        "cpu": {
            "percent": round(cpu_percent, 1),
            "count": cpu_count,
            "freq_mhz": round(cpu_freq.current, 1) if cpu_freq else None,
        },
        "memory": {
            "total_gb": round(mem_total / (1024**3), 2),
            "used_gb": round(mem_used / (1024**3), 2),
            "available_gb": round(mem_available / (1024**3), 2),
            "percent": round(mem_percent, 1),
            "is_cgroup_limited": cgroup_mem_limit is not None,
        },
        "swap": {
            "total_gb": round(swap.total / (1024**3), 2),
            "used_gb": round(swap.used / (1024**3), 2),
            "percent": round(swap.percent, 1),
        },
        "disk": {
            "total_gb": round(disk.total / (1024**3), 2),
            "used_gb": round(disk.used / (1024**3), 2),
            "free_gb": round(disk.free / (1024**3), 2),
            "percent": round(disk.percent, 1),
        },
    }

    end = time.perf_counter()
    return (end - start) * 1000  # Convert to ms


def main():
    print("Benchmarking /srvc endpoint performance...\n")

    iterations = 100
    times = []

    # Warmup
    for _ in range(10):
        benchmark_srvc_endpoint()

    # Actual benchmark
    for i in range(iterations):
        elapsed = benchmark_srvc_endpoint()
        times.append(elapsed)
        if (i + 1) % 10 == 0:
            print(f"Progress: {i + 1}/{iterations}")

    avg = sum(times) / len(times)
    min_time = min(times)
    max_time = max(times)
    p50 = sorted(times)[len(times) // 2]
    p95 = sorted(times)[int(len(times) * 0.95)]
    p99 = sorted(times)[int(len(times) * 0.99)]

    print("\n" + "="*50)
    print("Results (ms per request):")
    print("="*50)
    print(f"  Average:  {avg:.2f} ms")
    print(f"  Median:   {p50:.2f} ms")
    print(f"  Min:      {min_time:.2f} ms")
    print(f"  Max:      {max_time:.2f} ms")
    print(f"  P95:      {p95:.2f} ms")
    print(f"  P99:      {p99:.2f} ms")
    print("="*50)

    # Throughput estimate
    rps = 1000 / avg  # requests per second
    print(f"\n  Est. throughput: ~{rps:.0f} req/s")
    print(f"  CPU overhead per request: ~0.{int(avg*10):02d}% (assuming single core)")

    # Load scenarios
    print("\n" + "="*50)
    print("Load scenarios:")
    print("="*50)
    print(f"  1 req/sec:   {avg * 1:.2f} ms/sec   ({(avg * 1 / 1000) * 100:.3f}% CPU)")
    print(f"  10 req/sec:  {avg * 10:.2f} ms/sec  ({(avg * 10 / 1000) * 100:.2f}% CPU)")
    print(f"  100 req/sec: {avg * 100:.0f} ms/sec ({(avg * 100 / 1000) * 100:.1f}% CPU)")


if __name__ == "__main__":
    main()
