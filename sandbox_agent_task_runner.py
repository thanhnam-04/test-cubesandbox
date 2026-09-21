"""Run one bounded agent-like task inside CubeSandbox and retain its artifacts."""

import base64
import json
import os
import re
import resource
import subprocess
import sys
import time
from pathlib import Path


WORKLOAD_TIMEOUTS = {"slide": 30, "documents": 90, "data": 90, "code": 60,
                     "images": 90, "search": 90, "workflow": 150}
ORIGINAL_PATH = 'path = Path("/output/slide_html_dep.html")'
ORIGINAL_TITLE = "<title>Modern HTML Slide Deck</title>"
MAX_ARTIFACTS_RETURNED = 80


def decode_source(value):
    return base64.b64decode(value, validate=True).decode("utf-8")


def list_artifacts(directory):
    files = []
    total = 0
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        total += 1
        if len(files) < MAX_ARTIFACTS_RETURNED:
            files.append({"path": str(path), "bytes": path.stat().st_size})
    return files, total


def run(workload, output_name, worker_source, slide_template):
    if workload not in WORKLOAD_TIMEOUTS:
        raise ValueError("unknown agent task")
    if not re.fullmatch(r"[a-f0-9]{12}", output_name):
        raise ValueError("invalid run id")
    output_dir = Path("/output/agent-runs", output_name)
    output_dir.mkdir(parents=True, exist_ok=False)

    if workload == "slide":
        if slide_template.count(ORIGINAL_PATH) != 1 or slide_template.count(ORIGINAL_TITLE) != 1:
            raise ValueError("slide template does not match expected markers")
        slide_path = output_dir / "slides.html"
        source = (slide_template.replace(ORIGINAL_PATH, f'path = Path("{slide_path}")', 1)
                  .replace(ORIGINAL_TITLE, f"<title>Agent run {output_name}</title>", 1))
        command = [sys.executable, "-c", source]
    elif workload == "search":
        corpus = f"/scratch/agent-search-{output_name}/uploaded-corpus"
        command = [sys.executable, "-c", worker_source, workload, str(output_dir),
                   corpus, "NEEDLE-ERROR-42", "cleanup"]
    else:
        command = [sys.executable, "-c", worker_source, workload, str(output_dir)]

    started = time.monotonic()
    process = subprocess.run(command, cwd="/output", capture_output=True, text=True,
                             timeout=WORKLOAD_TIMEOUTS[workload], check=False)
    elapsed = round(time.monotonic() - started, 3)
    if process.returncode != 0:
        raise RuntimeError((process.stderr or process.stdout or
                            f"task exited with code {process.returncode}")[-1000:])

    details = {}
    if workload == "slide":
        content = (output_dir / "slides.html").read_text(encoding="utf-8")
        slide_count = content.count('<section class="slide')
        if slide_count != 5:
            raise RuntimeError("slide integrity check failed")
        details = {"slide_count": slide_count, "title": f"Agent run {output_name}"}
    else:
        manifest_path = output_dir / "result.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("workload") != workload or not isinstance(manifest.get("details"), dict):
            raise RuntimeError("task result manifest is invalid")
        details = manifest["details"]

    artifacts, artifact_count = list_artifacts(output_dir)
    summary = {
        "run_id": output_name,
        "workload": workload,
        "output_dir": str(output_dir),
        "elapsed_seconds": elapsed,
        "peak_rss_kb": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        "details": details,
        "artifacts": artifacts,
        "artifact_count": artifact_count,
        "artifacts_truncated": artifact_count > len(artifacts),
    }
    (output_dir / "agent-result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["artifacts"], summary["artifact_count"] = list_artifacts(output_dir)
    summary["artifacts_truncated"] = summary["artifact_count"] > len(summary["artifacts"])
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    try:
        run(sys.argv[1], sys.argv[2], decode_source(sys.argv[3]), decode_source(sys.argv[4]))
    except (OSError, ValueError, RuntimeError, UnicodeError, json.JSONDecodeError,
            subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr, flush=True)
        sys.exit(1)
