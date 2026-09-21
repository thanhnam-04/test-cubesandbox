"""Run bounded, standard-library-only benchmarks inside a Linux sandbox.

Called with `python3 -c <this file's contents> <task>`. No files are kept.
"""

import hashlib
import base64
import json
import os
import resource
import shutil
import sys
import tempfile
import time
import zlib
from pathlib import Path


def run_agent_workflow(worker_source):
    """Execute a multi-tool agent workload and validate every produced artifact."""
    namespace = {"__name__": "agent_benchmark_workload"}
    exec(worker_source, namespace)
    required = ("make_data", "make_documents", "run_code", "make_images", "search_files")
    if any(not callable(namespace.get(name)) for name in required):
        raise RuntimeError("agent workload source is missing required stages")
    stages = []

    def stage(name, callback):
        started = time.monotonic()
        available_before = memory_info().get("MemAvailable")
        result = callback()
        available_after = memory_info().get("MemAvailable")
        stages.append({
            "name": name,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "rss_after_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "memory_available_before_bytes": available_before,
            "memory_available_after_bytes": available_after,
            "result": result,
        })
        return result

    def memory_pressure():
        mem = memory_info()
        cgroup_limit = read_number(cgroup_path("memory.max"))
        cgroup_current = read_number(cgroup_path("memory.current"))
        totals = [value for value in (mem.get("MemTotal"), cgroup_limit) if value]
        available_values = [value for value in (mem.get("MemAvailable"),) if value]
        if cgroup_limit is not None and cgroup_current is not None:
            available_values.append(max(0, cgroup_limit - cgroup_current))
        total = min(totals) if totals else None
        available = min(available_values) if available_values else None
        if not total or not available:
            raise RuntimeError("cannot determine safe agent memory pressure target")
        reserve = max(384 * 1024 * 1024, int(total * 0.20))
        target = min(int(total * 0.70), max(0, available - reserve))
        chunk_size = 32 * 1024 * 1024
        target = target // chunk_size * chunk_size
        if target < 256 * 1024 * 1024:
            raise RuntimeError("not enough free memory for the agent pressure stage")
        chunks = []
        allocated = 0
        while allocated < target:
            try:
                chunk = bytearray(min(chunk_size, target - allocated))
            except MemoryError:
                break
            for position in range(0, len(chunk), 4096):
                chunk[position] = 1
            chunks.append(chunk)
            allocated += len(chunk)
        if allocated < 256 * 1024 * 1024:
            raise RuntimeError("agent memory pressure allocation was too small")
        time.sleep(0.5)
        return {
            "allocated_bytes": allocated,
            "target_bytes": target,
            "reserved_bytes": reserve,
            "total_memory_bytes": total,
            "cgroup_limit_bytes": cgroup_limit,
            "cgroup_current_before_bytes": cgroup_current,
        }

    with tempfile.TemporaryDirectory(prefix="agent-benchmark-", dir="/scratch") as name:
        root = __import__("pathlib").Path(name)
        data = stage("data", lambda: namespace["make_data"](root, rows=150000))
        documents = stage(
            "documents", lambda: namespace["make_documents"](root, chart=root / "chart.png"))
        code = stage("code", lambda: namespace["run_code"](root))
        images = stage("images", lambda: namespace["make_images"](root))
        search = stage("search", lambda: namespace["search_files"](root))
        pressure = stage("memory_pressure", memory_pressure)
        files = [path for path in root.rglob("*") if path.is_file() and not path.is_symlink()]
        artifact_bytes = sum(path.stat().st_size for path in files)
        if (data.get("rows") != 150000 or documents.get("pdf_pages") != 10
                or code.get("tests_passed") != 8 or images.get("images_processed") != 12
                or search.get("files_scanned") != 5000
                or pressure.get("allocated_bytes", 0) < 256 * 1024 * 1024):
            raise RuntimeError("agent benchmark validation failed")
        return {
            "stages": stages,
            "artifact_count": len(files),
            "artifact_bytes": artifact_bytes,
            "checks_passed": 6,
            "scenario": "data → documents → code/tests → images → file search → RAM pressure",
        }


def cgroup_path(name):
    """Locate a cgroup v2 file for this process, including nested cgroups."""
    try:
        for line in Path("/proc/self/cgroup").read_text(encoding="ascii").splitlines():
            hierarchy, controllers, relative = line.split(":", 2)
            if hierarchy == "0" and not controllers:
                nested = Path("/sys/fs/cgroup") / relative.lstrip("/") / name
                if nested.exists():
                    return nested
    except (OSError, ValueError):
        pass
    return Path("/sys/fs/cgroup") / name


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
    cpu_quota_status = "unavailable"
    try:
        with cgroup_path("cpu.max").open(encoding="ascii") as stream:
            quota, period = stream.read().split()[:2]
            if quota == "max":
                cpu_quota_status = "unlimited"
            else:
                cpu_max = round(int(quota) / int(period), 2)
                cpu_quota_status = "limited"
    except (OSError, ValueError, ZeroDivisionError):
        pass
    limit = read_number(cgroup_path("memory.max"))
    return {
        "logical_cpus_visible": os.cpu_count(),
        "cpu_quota_cores": cpu_max,
        "cpu_quota_status": cpu_quota_status,
        "memory_total_bytes": mem.get("MemTotal"),
        "memory_available_bytes": mem.get("MemAvailable"),
        "memory_limit_bytes": limit,
        "memory_current_bytes": read_number(cgroup_path("memory.current")),
        "scratch_total_bytes": disk.total,
        "scratch_free_bytes": disk.free,
    }


def run_task(task, worker_source=None):
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
    elif task == "agent":
        if not worker_source:
            raise ValueError("agent benchmark requires worker source")
        detail = run_agent_workflow(worker_source)
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
        worker = (base64.b64decode(sys.argv[2], validate=True).decode("utf-8")
                  if selected == "agent" and len(sys.argv) > 2 else None)
        result = hardware() if selected == "hardware" else run_task(selected, worker)
        print(json.dumps(result, separators=(",", ":")))
    except (OSError, ValueError, MemoryError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
