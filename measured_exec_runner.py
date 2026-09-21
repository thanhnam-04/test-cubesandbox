"""Linux-only process wrapper used by the backend for per-command metrics.

The child's stdout/stderr inherit the envd stream, preserving live output.
One tagged JSON line is written to stderr only after the child has exited.
"""

import json
import resource
import subprocess
import sys
import time


def main():
    command, marker = sys.argv[1:3]
    start = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    process = subprocess.Popen(["/bin/sh", "-c", command])
    exit_code = process.wait()
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    metrics = {
        "peak_rss_kb": after.ru_maxrss,
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "cpu_user_seconds": round(after.ru_utime - before.ru_utime, 3),
        "cpu_system_seconds": round(after.ru_stime - before.ru_stime, 3),
    }
    # A leading newline keeps the marker separate from stderr lacking a final newline.
    print("\n" + marker + json.dumps(metrics, separators=(",", ":")), file=sys.stderr, flush=True)
    return exit_code if exit_code >= 0 else 128 - exit_code


if __name__ == "__main__":
    sys.exit(main())
