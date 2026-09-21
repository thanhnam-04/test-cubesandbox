"""Simulate two clients creating slides in the current shared sandbox.

Run after provisioning: python slide_two_client_load_test.py
Optional: --stagger-seconds 1 to start the second client one second later.
This does not simulate two authenticated users; the demo has no user accounts.
"""

import argparse
import json
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


TEMPLATE_PATH = Path(__file__).resolve().parent / "slide_template.py"
ORIGINAL_OUTPUT = 'path = Path("/output/slide_html_dep.html")'
ORIGINAL_TITLE = "<title>Modern HTML Slide Deck</title>"


def prepare_script(template: str, output_path: str, marker: str) -> str:
    if template.count(ORIGINAL_OUTPUT) != 1 or template.count(ORIGINAL_TITLE) != 1:
        raise ValueError("slide_template.py has changed; check its output path and title")
    return (template.replace(ORIGINAL_OUTPUT, f'path = Path("{output_path}")', 1)
            .replace(ORIGINAL_TITLE, f"<title>{marker}</title>", 1))


def get_json(url: str) -> dict:
    with urlopen(url, timeout=15) as response:
        return json.load(response)


def create_slide(base_url: str, barrier: threading.Barrier, label: str,
                 script: str, script_path: str, output_path: str,
                 marker: str, delay_seconds: float) -> dict:
    barrier.wait(timeout=15)
    if delay_seconds:
        time.sleep(delay_seconds)
    started = time.perf_counter()
    try:
        payload = json.dumps({
            "script": script,
            "script_path": script_path,
            "output_path": output_path,
        }).encode("utf-8")
        request = Request(
            f"{base_url}/api/agent/create-slide",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=90) as response:
            result = json.load(response)
        if result.get("artifact_path") != output_path:
            raise RuntimeError("server returned an unexpected output path")
        preview_url = urljoin(base_url + "/", result["preview_url"])
        with urlopen(preview_url, timeout=30) as response:
            html = response.read().decode("utf-8")
        if f"<title>{marker}</title>" not in html:
            raise RuntimeError("slide content belongs to another client or is incomplete")
        if html.count('<section class="slide') != 5:
            raise RuntimeError("expected five slides in the output")
        peak_kb = ((result.get("result") or {}).get("metrics") or {}).get("peak_rss_kb")
        return {
            "client": label,
            "ok": True,
            "seconds": round(time.perf_counter() - started, 2),
            "slide_count": 5,
            "artifact_path": output_path,
            "bytes": len(html.encode("utf-8")),
            "peak_rss_mib": round(peak_kb / 1024, 1) if peak_kb is not None else None,
        }
    except HTTPError as error:
        full_detail = error.read().decode("utf-8", errors="replace")
        detail = full_detail[:500]
        try:
            failure = json.loads(full_detail)
            peak_kb = ((failure.get("result") or {}).get("metrics") or {}).get("peak_rss_kb")
        except (ValueError, AttributeError):
            peak_kb = None
        return {"client": label, "ok": False, "seconds": round(time.perf_counter() - started, 2),
                "error": f"HTTP {error.code}: {detail}",
                "peak_rss_mib": round(peak_kb / 1024, 1) if peak_kb is not None else None}
    except (URLError, OSError, RuntimeError, ValueError, KeyError) as error:
        return {"client": label, "ok": False, "seconds": round(time.perf_counter() - started, 2),
                "error": str(error)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Test two simultaneous slide-creation clients")
    parser.add_argument("--url", default="http://127.0.0.1:8787", help="local agent server URL")
    parser.add_argument("--stagger-seconds", type=float, default=0,
                        help="delay client B after client A starts")
    args = parser.parse_args()
    if args.stagger_seconds < 0:
        parser.error("--stagger-seconds must be non-negative")
    base_url = args.url.rstrip("/")
    try:
        status = get_json(f"{base_url}/api/status")
    except (HTTPError, URLError, OSError, ValueError) as error:
        parser.error(f"cannot reach the local agent server: {error}")
    if not status.get("active"):
        parser.error("provision a sandbox in the UI before running this test")

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    run_id = uuid.uuid4().hex[:12]
    barrier = threading.Barrier(2)
    work = []
    for label, delay in (("A", 0), ("B", args.stagger_seconds)):
        marker = f"two-client-{run_id}-{label}"
        script_path = f"/output/slide_test_{run_id}_{label}.py"
        output_path = f"/output/slide_test_{run_id}_{label}.html"
        work.append((base_url, barrier, label,
                     prepare_script(template, output_path, marker),
                     script_path, output_path, marker, delay))

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create_slide, *item) for item in work]
        results = [future.result() for future in futures]
    print(json.dumps({
        "shared_sandbox": True,
        "total_seconds": round(time.perf_counter() - started, 2),
        "results": results,
    }, ensure_ascii=False, indent=2))
    return 0 if all(result["ok"] for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
