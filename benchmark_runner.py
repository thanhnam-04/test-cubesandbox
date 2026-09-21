"""Run bounded, standard-library-only benchmarks inside a Linux sandbox.

Called with `python3 -c <this file's contents> <task>`. No files are kept.
"""

import hashlib
import json
import os
import resource
import shutil
import sys
import tempfile
import time
import zlib


def read_number(path):
    try:
        value = open(path, encoding="ascii").read().strip()
        return int(value) if value != "max" else None
    except (OSError, ValueError):
        return None


def memory_info():
    values = {}
    with open("/proc/meminfo", encoding="ascii") as stream:
        for line in stream:
            name, value = line.split(":", 1)
            if name in ("MemTotal", "MemAvailable"):
                values[name] = int(value.strip().split()[0]) * 1024
    return values


def io_info():
    try:
        with open("/proc/self/io", encoding="ascii") as stream:
            return {key: int(value) for key, value in
                    (line.split(":", 1) for line in stream)}
    except (OSError, ValueError):
        return {}


def hardware():
    mem = memory_info()
    disk = shutil.disk_usage("/scratch")
    cpu_max = None
    try:
        with open("/sys/fs/cgroup/cpu.max", encoding="ascii") as stream:
            quota, period = stream.read().split()[:2]
            if quota != "max":
                cpu_max = round(int(quota) / int(period), 2)
    except (OSError, ValueError, ZeroDivisionError):
        pass
    limit = read_number("/sys/fs/cgroup/memory.max")
    return {
        "logical_cpus_visible": os.cpu_count(),
        "cpu_quota_cores": cpu_max,
        "memory_total_bytes": mem.get("MemTotal"),
        "memory_available_bytes": mem.get("MemAvailable"),
        "memory_limit_bytes": limit,
        "memory_current_bytes": read_number("/sys/fs/cgroup/memory.current"),
        "scratch_total_bytes": disk.total,
        "scratch_free_bytes": disk.free,
    }


def run_task(task):
    started = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_SELF)
    io_before = io_info()
    detail = {}
    if task == "cpu":
        data = b"bounded sandbox CPU benchmark" * 128
        count = 0
        while time.monotonic() - started < 1.5:
            data = hashlib.sha256(data).digest()
            count += 1
        detail = {"sha256_iterations": count}
    elif task == "memory":
        size = 128 * 1024 * 1024
        data = bytearray(size)
        for position in range(0, size, 4096):
            data[position] = 1  # Touch each page so it is resident.
        time.sleep(1)
        detail = {"allocated_bytes": len(data)}
    elif task == "disk":
        size = 32 * 1024 * 1024
        block = b"x" * 4096
        # TemporaryFile unlinks itself at close, including on exceptions.
        with tempfile.TemporaryFile(dir="/scratch") as stream:
            for _ in range(size // len(block)):
                stream.write(block)
            stream.flush()
            os.fsync(stream.fileno())
            stream.seek(0)
            digest = hashlib.sha256()
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        detail = {"transferred_bytes": size, "sha256": digest.hexdigest()}
    elif task == "mixed":
        data = bytes(range(256)) * (16 * 1024 * 1024 // 256)
        packed = zlib.compress(data, 6)
        if zlib.decompress(packed) != data:
            raise RuntimeError("compression verification failed")
        detail = {"input_bytes": len(data), "compressed_bytes": len(packed)}
    else:
        raise ValueError("unknown benchmark task")
    elapsed = time.monotonic() - started
    after = resource.getrusage(resource.RUSAGE_SELF)
    io_after = io_info()
    cpu_seconds = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    return {
        "task": task,
        "elapsed_seconds": round(elapsed, 3),
        "cpu_user_seconds": round(after.ru_utime - before.ru_utime, 3),
        "cpu_system_seconds": round(after.ru_stime - before.ru_stime, 3),
        "cpu_percent_one_core": round(100 * cpu_seconds / elapsed, 1) if elapsed else None,
        "peak_rss_kb": after.ru_maxrss,  # Linux: KiB, per benchmark process.
        "disk_read_bytes": max(0, io_after.get("read_bytes", 0) - io_before.get("read_bytes", 0)),
        "disk_write_bytes": max(0, io_after.get("write_bytes", 0) - io_before.get("write_bytes", 0)),
        "detail": detail,
    }


if __name__ == "__main__":
    try:
        selected = sys.argv[1]
        result = hardware() if selected == "hardware" else run_task(selected)
        print(json.dumps(result, separators=(",", ":")))
    except (OSError, ValueError, MemoryError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
