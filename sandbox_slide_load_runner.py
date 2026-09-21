"""Run a bounded multi-user slide experiment wholly inside the Linux sandbox.

Each simulated user is a thread; each slide task is a separate Python process.
Output slides live in one temporary /scratch directory and are removed on exit.
"""

import base64
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ORIGINAL_PATH = 'path = Path("/output/slide_html_dep.html")'
ORIGINAL_TITLE = "<title>Modern HTML Slide Deck</title>"
WORKLOAD_TIMEOUTS = {"slide": 10, "documents": 75, "data": 75, "code": 45,
                     "images": 75, "search": 75, "workflow": 120}
MAX_SANDBOX_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
MAX_USERS = 8
MAX_TASKS = 80
WORKLOAD_CONCURRENCY = {"slide": 8, "documents": 4, "data": 4, "code": 8,
                        "images": 4, "search": 8, "workflow": 3}


def cgroup_v2_path(name):
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


def cgroup_number(name):
    candidates = [cgroup_v2_path(name)]
    if name == "memory.max":
        candidates.append(Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"))
    for path in candidates:
        try:
            value = path.read_text(encoding="ascii").strip()
            parsed = None if value == "max" else int(value)
            if parsed is not None and parsed < 2 ** 60:
                return parsed
        except (OSError, ValueError):
            pass
    return None


def memory_total_bytes():
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    position = fraction * (len(ordered) - 1)
    low = int(position)
    weight = position - low
    return round(ordered[low] * (1 - weight) + ordered[min(low + 1, len(ordered) - 1)] * weight, 3)


def run_experiment(users, tasks_per_user, stagger_seconds, template, workload="slide", worker_source=None):
    if (type(users) is not int or not 1 <= users <= MAX_USERS
            or type(tasks_per_user) is not int or not 1 <= tasks_per_user <= 10
            or users * tasks_per_user > MAX_TASKS
            or type(stagger_seconds) not in (int, float) or not 0 <= stagger_seconds <= 2):
        raise ValueError("load test exceeds the allowed users, tasks, or stagger bounds")
    if workload not in WORKLOAD_TIMEOUTS or (workload != "slide" and not worker_source):
        raise ValueError("unknown workload or missing worker source")
    if workload == "slide" and (template.count(ORIGINAL_PATH) != 1 or
                                template.count(ORIGINAL_TITLE) != 1):
        raise ValueError("slide template does not match the expected output path/title")
    results = []
    results_lock = threading.Lock()
    count_lock = threading.Lock()
    active_tasks = 0
    max_active_tasks = 0
    ready = threading.Barrier(users)
    stop_monitor = threading.Event()
    memory_before = cgroup_number("memory.current")
    memory_peak = memory_before
    limits = [value for value in (cgroup_number("memory.max"), memory_total_bytes())
              if value is not None]
    memory_limit = min(limits) if limits else None
    if memory_limit is None or memory_limit > MAX_SANDBOX_MEMORY_BYTES:
        raise ValueError("sandbox memory.max must be configured at 2 GiB or less")
    oom_kills_before = cgroup_events("oom_kill")
    parallel_limit = min(users, WORKLOAD_CONCURRENCY[workload])
    task_slots = threading.BoundedSemaphore(parallel_limit)
    shared_search_corpus = None
    search_queries = ()
    setup_seconds = 0.0
    shared_input = None

    def monitor_memory():
        nonlocal memory_peak
        while not stop_monitor.is_set():
            current = cgroup_number("memory.current")
            if current is not None:
                memory_peak = max(memory_peak or 0, current)
            stop_monitor.wait(0.05)

    def one_task(user_number, task_number, scratch):
        nonlocal active_tasks, max_active_tasks
        marker = f"load-user-{user_number}-task-{task_number}"
        task_dir = scratch / marker
        output = task_dir / f"{marker}.html"
        source = (template.replace(ORIGINAL_PATH, f'path = Path("{output}")', 1)
                  .replace(ORIGINAL_TITLE, f"<title>{marker}</title>", 1))
        if workload == "slide":
            args = [sys.executable, "-c", source]
        elif workload == "search":
            query = search_queries[((user_number - 1) * tasks_per_user + task_number - 1)
                                   % len(search_queries)]
            args = [sys.executable, "-c", worker_source, workload, str(task_dir),
                    str(shared_search_corpus), query]
            result_query = query
        else:
            args = [sys.executable, "-c", worker_source, workload, str(task_dir)]
        start = time.monotonic()
        result = {"user": user_number, "task": task_number, "ok": False,
                  "workload": workload, "elapsed_seconds": None, "peak_rss_kb": None}
        if workload == "search":
            result["query"] = result_query
        try:
            task_dir.mkdir()
            with tempfile.TemporaryFile(dir=task_dir) as stderr_file:
                process = subprocess.Popen(args,
                                           cwd="/scratch", stdout=subprocess.DEVNULL,
                                           stderr=stderr_file, start_new_session=True)
                with count_lock:
                    active_tasks += 1
                    max_active_tasks = max(max_active_tasks, active_tasks)
                try:
                    deadline = time.monotonic() + WORKLOAD_TIMEOUTS[workload]
                    while True:
                        pid, status, usage = os.wait4(process.pid, os.WNOHANG)
                        if pid:
                            process.returncode = os.waitstatus_to_exitcode(status)
                            result["peak_rss_kb"] = usage.ru_maxrss
                            result["exit_code"] = process.returncode
                            break
                        if time.monotonic() >= deadline:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            _, status, usage = os.wait4(process.pid, 0)
                            process.returncode = os.waitstatus_to_exitcode(status)
                            result["peak_rss_kb"] = usage.ru_maxrss
                            result["exit_code"] = process.returncode
                            result["error"] = f"task timed out after {WORKLOAD_TIMEOUTS[workload]} seconds"
                            break
                        time.sleep(0.02)
                finally:
                    with count_lock:
                        active_tasks -= 1
                if result["exit_code"] == 0:
                    if workload == "slide":
                        html = output.read_text(encoding="utf-8")
                        if f"<title>{marker}</title>" not in html or html.count('<section class="slide') != 5:
                            raise RuntimeError("slide output failed integrity check")
                        result["bytes"] = output.stat().st_size
                        result["slide_count"] = 5
                    else:
                        manifest = json.loads((task_dir / "result.json").read_text(encoding="utf-8"))
                        if manifest.get("workload") != workload or not isinstance(manifest.get("details"), dict):
                            raise RuntimeError("workload manifest failed integrity check")
                        result["details"] = manifest["details"]
                        result["peak_rss_kb"] = max(result["peak_rss_kb"], manifest.get("child_peak_rss_kb", 0))
                    result["ok"] = True
                else:
                    stderr_file.seek(0)
                    result["error"] = (result.get("error") or
                                       stderr_file.read(500).decode("utf-8", "replace").strip() or
                                       f"process exited with code {result['exit_code']}")
        except (OSError, RuntimeError, UnicodeError, ValueError, KeyError, TypeError) as error:
            result["error"] = str(error)
        finally:
            result["elapsed_seconds"] = round(time.monotonic() - start, 3)
            shutil.rmtree(task_dir, ignore_errors=True)
            with results_lock:
                results.append(result)

    def one_user(user_number, scratch):
        ready.wait(timeout=10)
        if stagger_seconds:
            time.sleep((user_number - 1) * stagger_seconds)
        for task_number in range(1, tasks_per_user + 1):
            with task_slots:
                one_task(user_number, task_number, scratch)

    started = time.monotonic()
    monitor = threading.Thread(target=monitor_memory, daemon=True)
    monitor.start()
    try:
        with tempfile.TemporaryDirectory(prefix="slide-load-", dir="/scratch") as scratch_name:
            scratch = Path(scratch_name)
            if workload == "search":
                namespace = {"__name__": "shared_search_setup"}
                exec(worker_source, namespace)
                prepare = namespace.get("prepare_search_corpus")
                queries = namespace.get("SEARCH_QUERIES")
                if not callable(prepare) or not isinstance(queries, tuple) or not queries:
                    raise RuntimeError("search worker is missing shared-corpus support")
                setup_started = time.monotonic()
                shared_search_corpus = scratch / "uploaded-corpus"
                shared_input = prepare(shared_search_corpus)
                search_queries = queries
                setup_seconds = round(time.monotonic() - setup_started, 3)
            with ThreadPoolExecutor(max_workers=users) as pool:
                futures = [pool.submit(one_user, number, scratch) for number in range(1, users + 1)]
                for future in futures:
                    future.result()
    finally:
        stop_monitor.set()
        monitor.join(timeout=1)
    total_seconds = round(time.monotonic() - started, 3)
    results.sort(key=lambda item: (item["user"], item["task"]))
    successes = [item for item in results if item["ok"]]
    durations = [item["elapsed_seconds"] for item in successes]
    peaks = [item["peak_rss_kb"] for item in results if item["peak_rss_kb"] is not None]
    oom_kills_after = cgroup_events("oom_kill")
    return {
        "simulated_users": users,
        "workload": workload,
        "tasks_per_user": tasks_per_user,
        "requested_tasks": users * tasks_per_user,
        "completed_tasks": len(results),
        "succeeded": len(successes),
        "failed": len(results) - len(successes),
        "stagger_seconds": stagger_seconds,
        "shared_input_setup_seconds": setup_seconds,
        "shared_input_files": None if shared_input is None else shared_input.get("file_count"),
        "shared_input_bytes": None if shared_input is None else shared_input.get("total_bytes"),
        "total_seconds": total_seconds,
        "max_concurrent_tasks": max_active_tasks,
        "configured_parallel_limit": parallel_limit,
        "latency_p50_seconds": percentile(durations, 0.5),
        "latency_p95_seconds": percentile(durations, 0.95),
        "max_task_peak_rss_kb": max(peaks) if peaks else None,
        "sandbox_memory_before_bytes": memory_before,
        "sandbox_memory_peak_sampled_bytes": memory_peak,
        "sandbox_memory_after_bytes": cgroup_number("memory.current"),
        "sandbox_memory_limit_bytes": memory_limit,
        "sandbox_oom_kills_during_run": (None if oom_kills_before is None or oom_kills_after is None
                                         else max(0, oom_kills_after - oom_kills_before)),
        "results": results,
    }


def cgroup_events(name):
    try:
        for line in cgroup_v2_path("memory.events").read_text(encoding="ascii").splitlines():
            key, value = line.split()
            if key == name:
                return int(value)
    except (OSError, ValueError):
        pass
    return None


if __name__ == "__main__":
    try:
        users = int(sys.argv[1])
        tasks_per_user = int(sys.argv[2])
        stagger_seconds = float(sys.argv[3])
        template = base64.b64decode(sys.argv[4], validate=True).decode("utf-8")
        workload = sys.argv[5] if len(sys.argv) > 5 else "slide"
        worker_source = (base64.b64decode(sys.argv[6], validate=True).decode("utf-8")
                         if len(sys.argv) > 6 else None)
        print(json.dumps(run_experiment(users, tasks_per_user, stagger_seconds, template,
                                        workload, worker_source),
                         separators=(",", ":")), flush=True)
    except (OSError, ValueError, RuntimeError, UnicodeError) as error:
        print(str(error), file=sys.stderr, flush=True)
        sys.exit(1)
