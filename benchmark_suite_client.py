"""Run repeatable CubeSandbox benchmark matrices through the local agent backend."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROFILES = {
    "quick": {
        "basic_rounds": 1,
        "agent_rounds": 1,
        "loads": [
            ("workflow", 1, 1, 0.2),
            ("workflow", 2, 1, 0.2),
        ],
    },
    "standard": {
        "basic_rounds": 3,
        "agent_rounds": 3,
        "loads": [
            ("slide", 4, 3, 0.1),
            ("data", 2, 2, 0.2),
            ("documents", 2, 2, 0.2),
            ("code", 4, 3, 0.1),
            ("images", 2, 2, 0.2),
            ("search", 4, 2, 0.1),
            ("workflow", 1, 3, 0.2),
            ("workflow", 2, 3, 0.2),
            ("workflow", 4, 3, 0.2),
        ],
    },
    "full": {
        "basic_rounds": 5,
        "agent_rounds": 5,
        "loads": [
            (workload, users, 3, 0.2)
            for workload in ("slide", "data", "documents", "code", "images", "search", "workflow")
            for users in (1, 2, 4, 6, 8)
        ],
    },
}


def request_json(base_url: str, method: str, path: str, payload=None, timeout=300):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(base_url.rstrip("/") + path, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {error.code} {path}: {detail[:800]}") from error
    except (URLError, OSError, TimeoutError) as error:
        raise RuntimeError(f"Cannot reach {path}: {error}") from error


def mib(value):
    return None if value is None else round(value / 1048576, 2)


def median(values):
    cleaned = [value for value in values if isinstance(value, (int, float))]
    return None if not cleaned else round(statistics.median(cleaned), 3)


def display_mib(value):
    converted = mib(value)
    return "n/a" if converted is None else converted


def display_kib_as_mib(value):
    return display_mib(None if value is None else value * 1024)


def display_value(value):
    return "n/a" if value is None else value


def markdown_report(report):
    lines = [
        "# Báo cáo benchmark CubeSandbox tự động",
        "",
        f"- Thời gian bắt đầu: {report['started_at']}",
        f"- Profile: {report['profile']}",
        f"- Backend: {report['base_url']}",
        f"- Tổng thời gian suite: {report['elapsed_seconds']} giây",
        f"- Lỗi hoặc kết quả không đạt: {len(report['errors'])}",
        "",
    ]
    hardware = report.get("hardware") or {}
    lines.extend([
        "## Cấu hình sandbox",
        "",
        "| Thông số | Giá trị |",
        "|---|---:|",
        f"| Logical CPU | {hardware.get('logical_cpus_visible', 'n/a')} |",
        f"| CPU quota | {'Không giới hạn' if hardware.get('cpu_quota_status') == 'unlimited' else display_value(hardware.get('cpu_quota_cores'))} |",
        f"| Cgroup RAM limit | {display_mib(hardware.get('memory_limit_bytes'))} MiB |",
        f"| RAM total | {display_mib(hardware.get('memory_total_bytes'))} MiB |",
        f"| RAM available | {display_mib(hardware.get('memory_available_bytes'))} MiB |",
        f"| Scratch total | {display_mib(hardware.get('scratch_total_bytes'))} MiB |",
        f"| Scratch free | {display_mib(hardware.get('scratch_free_bytes'))} MiB |",
        "",
        "## Benchmark tài nguyên cơ bản",
        "",
        "| Lượt | Task | Thời gian (s) | CPU (%) | Peak RSS (MiB) | Read (MiB) | Write (MiB) |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ])
    for run in report["basic_runs"]:
        for result in run.get("results", []):
            lines.append(
                f"| {run['round']} | {result.get('task')} | {result.get('elapsed_seconds', 'n/a')} | "
                f"{result.get('cpu_percent_one_core', 'n/a')} | {display_kib_as_mib(result.get('peak_rss_kb'))} | "
                f"{mib(result.get('disk_read_bytes')) or 0} | {mib(result.get('disk_write_bytes')) or 0} |")
    lines.extend([
        "",
        "## Synthetic Agent Benchmark",
        "",
        "| Lượt | Tổng thời gian (s) | CPU (%) | Peak RSS (MiB) | Artifact | Checks |",
        "|---:|---:|---:|---:|---:|---:|",
    ])
    for run in report["agent_runs"]:
        result = next((item for item in run.get("results", []) if item.get("task") == "agent"), {})
        detail = result.get("detail") or {}
        lines.append(
            f"| {run['round']} | {result.get('elapsed_seconds', 'n/a')} | "
            f"{result.get('cpu_percent_one_core', 'n/a')} | {display_kib_as_mib(result.get('peak_rss_kb'))} | "
            f"{detail.get('artifact_count', 'n/a')} | {detail.get('checks_passed', 'n/a')}/6 |")
    lines.extend([
        "",
        "### Thời gian từng bước của Agent benchmark",
        "",
        "| Lượt | Bước | Thời gian (s) | RSS tích lũy sau bước (MiB) |",
        "|---:|---|---:|---:|",
    ])
    for run in report["agent_runs"]:
        result = next((item for item in run.get("results", []) if item.get("task") == "agent"), {})
        for stage in (result.get("detail") or {}).get("stages", []):
            lines.append(
                f"| {run['round']} | {stage.get('name', 'n/a')} | "
                f"{stage.get('elapsed_seconds', 'n/a')} | "
                f"{display_kib_as_mib(stage.get('rss_after_kb'))} |")
    lines.extend([
        "",
        "## Load test nhiều user",
        "",
        "| Workload | Users | Task/user | OK/Total | Tổng (s) | Throughput (task/s) | p50/p95 (s) | Đồng thời | Peak task (MiB) | Peak sandbox/limit (MiB) | OOM |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for item in report["load_runs"]:
        data = item.get("report") or {}
        total_seconds = data.get("total_seconds")
        succeeded = data.get("succeeded")
        throughput = (round(succeeded / total_seconds, 3)
                      if isinstance(succeeded, (int, float)) and total_seconds else "n/a")
        lines.append(
            f"| {data.get('workload', item['workload'])} | {data.get('simulated_users', item['users'])} | "
            f"{data.get('tasks_per_user', item['tasks_per_user'])} | "
            f"{data.get('succeeded', 'n/a')}/{data.get('requested_tasks', 'n/a')} | "
            f"{data.get('total_seconds', 'n/a')} | {throughput} | "
            f"{data.get('latency_p50_seconds', 'n/a')}/{data.get('latency_p95_seconds', 'n/a')} | "
            f"{data.get('max_concurrent_tasks', 'n/a')} | "
            f"{display_kib_as_mib(data.get('max_task_peak_rss_kb'))} | "
            f"{display_mib(data.get('sandbox_memory_peak_sampled_bytes'))}/"
            f"{display_mib(data.get('sandbox_memory_limit_bytes'))} | "
            f"{display_value(data.get('sandbox_oom_kills_during_run'))} |")
    basic_elapsed = [result.get("elapsed_seconds") for run in report["basic_runs"]
                     for result in run.get("results", []) if not result.get("error")]
    agent_elapsed = [next((item.get("elapsed_seconds") for item in run.get("results", [])
                           if item.get("task") == "agent"), None) for run in report["agent_runs"]]
    load_success = [item.get("report", {}).get("succeeded", 0) for item in report["load_runs"]]
    load_requested = [item.get("report", {}).get("requested_tasks", 0) for item in report["load_runs"]]
    requested = sum(load_requested)
    success_rate = None if not requested else round(100 * sum(load_success) / requested, 2)
    telemetry_complete = bool(report["load_runs"]) and all(
        item.get("report", {}).get("sandbox_memory_peak_sampled_bytes") is not None
        and item.get("report", {}).get("sandbox_oom_kills_during_run") is not None
        for item in report["load_runs"]
    )
    if report["errors"] or success_rate != 100:
        verdict = "CẦN XEM LẠI"
    elif telemetry_complete:
        verdict = "ĐẠT"
    else:
        verdict = "ĐẠT CÓ ĐIỀU KIỆN"
    lines.extend([
        "",
        "## Tổng hợp",
        "",
        f"- Kết luận tự động: **{verdict}**.",
        f"- Median benchmark cơ bản: {median(basic_elapsed)} giây.",
        f"- Median Agent benchmark: {median(agent_elapsed)} giây.",
        f"- Tỷ lệ task load test thành công: {success_rate if success_rate is not None else 'n/a'}%.",
        f"- Telemetry RAM cgroup/OOM đầy đủ: {'có' if telemetry_complete else 'không'}.",
        "- Peak RSS là peak của tiến trình task; không phải tổng RAM toàn sandbox nếu cgroup telemetry không khả dụng.",
        "- Latency load test hiện là service time và chưa bao gồm toàn bộ queue wait.",
    ])
    if report["errors"]:
        lines.extend(["", "## Lỗi", ""])
        lines.extend(f"- {error}" for error in report["errors"])
    return "\n".join(lines) + "\n"


def run_suite(base_url: str, profile_name: str):
    profile = PROFILES[profile_name]
    status = request_json(base_url, "GET", "/api/status", timeout=20)
    if not status.get("active"):
        raise RuntimeError(
            "không có CubeSandbox đang hoạt động; hãy giữ backend chạy, mở web "
            "và bấm 'Tạo sandbox' trước"
        )
    report = {
        "schema_version": 1,
        "started_at": datetime.now().astimezone().isoformat(),
        "base_url": base_url,
        "profile": profile_name,
        "hardware": None,
        "basic_runs": [],
        "agent_runs": [],
        "load_runs": [],
        "errors": [],
    }
    started = time.monotonic()
    for round_number in range(1, profile["basic_rounds"] + 1):
        try:
            result = request_json(base_url, "POST", "/api/sandbox/benchmarks",
                                  {"tasks": ["cpu", "memory", "disk", "mixed"]})
            report["hardware"] = report["hardware"] or result.get("hardware")
            results = result.get("results", [])
            report["basic_runs"].append({"round": round_number, "results": results})
            for failed in (item for item in results if item.get("error")):
                report["errors"].append(
                    f"basic round {round_number}/{failed.get('task', 'unknown')}: {failed['error']}")
        except RuntimeError as error:
            report["errors"].append(f"basic round {round_number}: {error}")
    for round_number in range(1, profile["agent_rounds"] + 1):
        try:
            result = request_json(base_url, "POST", "/api/sandbox/benchmarks", {"tasks": ["agent"]})
            report["hardware"] = report["hardware"] or result.get("hardware")
            results = result.get("results", [])
            report["agent_runs"].append({"round": round_number, "results": results})
            for failed in (item for item in results if item.get("error")):
                report["errors"].append(f"agent round {round_number}: {failed['error']}")
        except RuntimeError as error:
            report["errors"].append(f"agent round {round_number}: {error}")
    for workload, users, tasks_per_user, stagger_seconds in profile["loads"]:
        item = {"workload": workload, "users": users, "tasks_per_user": tasks_per_user,
                "stagger_seconds": stagger_seconds}
        try:
            result = request_json(base_url, "POST", "/api/sandbox/load-test", item)
            item["report"] = result.get("report", {})
            failed = item["report"].get("failed", 0)
            if failed:
                report["errors"].append(f"load {workload}/{users} users: {failed} task failed")
        except RuntimeError as error:
            item["error"] = str(error)
            report["errors"].append(f"load {workload}/{users} users: {error}")
        report["load_runs"].append(item)
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return report


def main():
    parser = argparse.ArgumentParser(description="Run a CubeSandbox benchmark suite and export reports")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="standard")
    parser.add_argument("--output-dir", default="benchmark-reports")
    args = parser.parse_args()
    try:
        report = run_suite(args.base_url, args.profile)
    except RuntimeError as error:
        parser.exit(2, f"Không thể chạy benchmark: {error}\n")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base_name = f"cubesandbox-{args.profile}-{stamp}"
    json_path = output_dir / f"{base_name}.json"
    markdown_path = output_dir / f"{base_name}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(markdown_path),
                      "errors": len(report["errors"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
