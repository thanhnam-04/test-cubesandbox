# Sandbox Agent demo

`sandbox_agent.html` + `sandbox_agent_server.py` provide a local simulated agent
entry point. The backend follows the lifecycle and runtime routes in
`sandbox_api.md` and `backend-exec-design (2).md`. The browser does not receive
the sandbox ID, routing domain, or access tokens.

The UI exposes all sandbox operations:

| UI action | Remote API |
| --- | --- |
| Provision sandbox | `POST /sandboxes` |
| Run command / stream output | `POST https://49983-{sandboxID}.{domain}/process.Process/Start` (Connect+JSON) |
| Read `/output` file | `GET https://49983-{sandboxID}.{domain}/files?path=...` |
| Write `/output` file | `POST https://49983-{sandboxID}.{domain}/files?path=...` (multipart `file` part) |
| Destroy sandbox | `DELETE /sandboxes/{id}` |

Run it with the real control-plane configuration:

```bash
export SANDBOX_API_BASE_URL='https://cube-api.meowmeow.io.vn'
python sandbox_agent_server.py
```

The prototype uses `py-libreoffice-skills` as the default template ID. Override
it only when needed:

```bash
export SANDBOX_TEMPLATE_ID='another-existing-template'
```

The server rejects a missing or invalid `SANDBOX_API_BASE_URL` at startup.
Then open <http://127.0.0.1:8787> and click **Provision sandbox**.

The backend's `/api/agent/plan` endpoint is a deterministic mock agent. It
recognizes these examples and sends one action to the matching sandbox route:

```text
/run pwd
/run find /output -maxdepth 2 -type f -print
/write /output/hello.txt
hello from the agent
/read /output/hello.txt
liệt kê file
kiểm tra LibreOffice
```

For envd routing, the API table shows `http://` while §2.2 of the newer
backend design specifies the TLS runtime URL. This demo uses the newer
`https://49983-{sandboxID}.{domain}` form.

Process execution uses a Connect+JSON envelope and converts the streamed
`ProcessEvent` frames to browser-friendly NDJSON. The proxy decodes base64
stdout/stderr, checks the exit code and Connect trailer, and adds
`X-Access-Token` when the create response returns an `envdAccessToken`.
Each interactive `/api/sandbox/exec` and slide-generation process is wrapped
by `measured_exec_runner.py`; the original command's output still streams.
At completion the proxy emits a `metrics` event for exec, or includes
`result.metrics` for slide creation. `peak_rss_kb` is the Linux-reported
maximum resident memory of the waited child command, including interpreter
overhead. It is not the sum of concurrent processes or the whole sandbox's
memory use. If a process or sandbox is terminated before the wrapper can
report, this metric may be unavailable. The setup process is not user-facing
and is not measured.
File transfers retry transient failures up to three times. Process execution
is never retried automatically.

This directory has no platform chat service, LLM provider hook, history,
authenticated user/chat IDs, client event protocol, or permanent storage API.
The mock agent holds one sandbox in server memory and does not implement the
full turn loop or `/output` sync/restore from `backend-exec-design (2).md`.
Destroying the sandbox discards its files. Connect those platform services
before treating this as a persistent multiuser agent.

## Benchmark hardware and tasks

After provisioning, select CPU, RAM, disk, and/or compression under
**Benchmark sandbox** and click **Chạy benchmark**. The server executes only
these four predefined tasks; user-supplied shell commands are not accepted by
the benchmark route. CPU runs for roughly 1.5 seconds, RAM touches and holds
128 MiB for one second, disk writes and reads 32 MiB of temporary data in
`/scratch`, and compression processes 16 MiB in memory. The disk temporary
file is closed/unlinked by the runner. `/api/sandbox/exec` also reports peak
RAM automatically for manually run commands; the preset benchmark reports
additional per-task CPU and disk statistics.

An API client can run a subset:

```http
POST /api/sandbox/benchmarks
Content-Type: application/json

{"tasks":["cpu","memory"]}
```

Omit `tasks` to run all four. The JSON response has `hardware` (visible CPU
count, cgroup v2 CPU quota and memory limit/current when available, `/proc`
memory, `/scratch` disk capacity) and `results` in the requested order.
Each result includes elapsed and process CPU seconds, CPU percent relative
to one core, peak process RSS in Linux KiB, and kernel-accounted process disk
read/write bytes. `peak_rss_kb` includes the Python runner's overhead; it is
not the maximum memory of the whole sandbox. `/proc/meminfo` may reflect host
memory instead of the cgroup limit. Cached disk reads may report zero physical
read bytes. Results are a local diagnostic, not a concurrent multiuser load
test; they are not saved between page refreshes.

## Two-client slide creation test

Start the local server and provision a sandbox in the UI first. Then run:

```powershell
python .\slide_two_client_load_test.py
```

To start the second client one second later:

```powershell
python .\slide_two_client_load_test.py --stagger-seconds 1
```

The test starts two concurrent requests to `/api/agent/create-slide`, using
`slide_template.py`. Each request uses unique script and HTML output paths
under `/output` and a unique HTML title. It downloads both generated slides
and checks that each contains its own title and five slides. The JSON output
reports per-client success, duration, peak RAM when available, and overall duration. The four
generated files are left in `/output` so you can inspect them; running the
test again uses new names. This is a shared-sandbox concurrency test, **not**
isolation between authenticated users: the demo has no user accounts.

## Load experiment inside the sandbox

After provisioning, use **Thử tải agent trong sandbox** in the UI. Choose a
workload: `slide` (five-slide HTML), `documents` (DOCX plus PDF), `data`
(30,000-row CSV plus chart), `code` (generate code and run three tests),
`images` (four images plus contact sheet), `search` (search 1,200 files), or
`workflow` (CSV analysis, chart, DOCX/PDF report, code tests). The non-slide
jobs require the libraries listed in `backend-exec-design.md` to be installed
in the sandbox template. Missing dependencies show as per-task failures.

Set
`Users` (1–20), `Task / user` (1–10, at most 100 tasks in total), and optional
`Trễ giữa users` (0–2 seconds). Start with 2 users × 1 task and increase
gradually; the test intentionally creates several processes at once.

For an API client:

```http
POST /api/sandbox/load-test
Content-Type: application/json

{"users":2,"tasks_per_user":3,"stagger_seconds":0,"workload":"workflow"}
```

The local backend sends the runners and the current `slide_template.py` into
one sandbox process. All simulated user threads, slide-generating child
processes, and measurements execute **inside that sandbox**, with no request
to `127.0.0.1:8787` from the sandbox. A user runs their tasks sequentially;
different users can run concurrently. Each task generates and validates a
five-slide HTML file or a bounded agent-like workload under a unique temporary
`/scratch` path; all these
temporary files are removed when the experiment ends. The response includes
requested/completed/successful/failed task counts, maximum task concurrency,
per-task duration and peak RSS, p50/p95 successful-task latency, and cgroup v2
`memory.current` before/after plus the highest 50 ms sample during the run
when available. The report also includes the change in cgroup `oom_kill`
when available. This sampled sandbox peak can miss short spikes and is not
the sum of per-task RSS. The result measures one shared sandbox and does not
establish authentication, user isolation, or capacity for one sandbox per user.
Peak RSS for a task is the maximum observed process peak in its tree, **not**
the sum of all child processes; use the sandbox cgroup samples to assess total
memory. If the whole sandbox is killed, the HTTP request may fail before a
report can be returned. Start with 1 user and increase gradually for heavy
workloads; up to 20 simulated users / 100 total tasks is a safety bound, not a
promise that the sandbox can handle that many. This is a scripted tool-workload
simulation, not an LLM agent loop or real multi-user authentication.
