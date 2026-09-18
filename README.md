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
File transfers retry transient failures up to three times. Process execution
is never retried automatically.

This directory has no platform chat service, LLM provider hook, history,
authenticated user/chat IDs, client event protocol, or permanent storage API.
The mock agent holds one sandbox in server memory and does not implement the
full turn loop or `/output` sync/restore from `backend-exec-design (2).md`.
Destroying the sandbox discards its files. Connect those platform services
before treating this as a persistent multiuser agent.
