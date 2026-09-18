from __future__ import annotations

import argparse
import base64
import binascii
import json
import mimetypes
import os
import posixpath
import re
import secrets
import shlex
import socket
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
INDEX_FILE = ROOT / "sandbox_agent.html"
MAX_FILE_BYTES = 2 * 1024 * 1024
EXEC_IDLE_TIMEOUT_SECONDS = 180
DEFAULT_TEMPLATE_ID = "py-libreoffice-skills"
ENVD_PORT = 49983
CONTROL_PLANE_USER_AGENT = "sandbox-agent/0.1"
MAX_CONNECT_FRAME_BYTES = 8 * 1024 * 1024

state_lock = threading.Lock()
lifecycle_lock = threading.Lock()
state: dict[str, Any] = {
    "sandbox_id": None,
    "domain": None,
    "envd_access_token": None,
    "traffic_access_token": None,
    "envd_version": None,
    "created_at": None,
}


def env_config() -> tuple[str, str]:
    base_url = os.environ.get("SANDBOX_API_BASE_URL", "").strip().rstrip("/")
    template_id = os.environ.get("SANDBOX_TEMPLATE_ID", DEFAULT_TEMPLATE_ID).strip()
    return base_url, template_id


def config_error() -> str | None:
    base_url, _ = env_config()
    if not base_url:
        return "SANDBOX_API_BASE_URL is not configured"
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return "SANDBOX_API_BASE_URL must be an absolute http(s) base URL"
    return None


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def remote_url(path: str) -> str:
    base_url, _ = env_config()
    return f"{base_url}{path}"


def sandbox_direct_url(path: str) -> str:
    with state_lock:
        sandbox_id = state["sandbox_id"]
        domain = state["domain"]
    if not sandbox_id or not domain:
        raise RuntimeError("Sandbox response did not include sandboxID and domain")
    clean_domain = str(domain).strip()
    if not re.fullmatch(r"[A-Za-z0-9.-]+", clean_domain) or clean_domain.startswith(".") or clean_domain.endswith("."):
        raise RuntimeError("Sandbox returned an invalid routing domain")
    if not re.fullmatch(r"[A-Za-z0-9-]+", str(sandbox_id)):
        raise RuntimeError("Sandbox returned an invalid sandbox ID")
    return f"https://{ENVD_PORT}-{sandbox_id}.{clean_domain}{path}"


def sandbox_direct_headers(*, accept: str = "*/*") -> dict[str, str]:
    with state_lock:
        sandbox_id = state["sandbox_id"]
        envd_access_token = state["envd_access_token"]
    if not sandbox_id:
        raise RuntimeError("No active sandbox. Provision a workspace first.")
    headers = {
        "Accept": accept,
        "User-Agent": CONTROL_PLANE_USER_AGENT,
        "E2b-Sandbox-Id": str(sandbox_id),
        "E2b-Sandbox-Port": str(ENVD_PORT),
    }
    if envd_access_token:
        headers["X-Access-Token"] = str(envd_access_token)
    return headers


def read_exact(stream: Any, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    value = b"".join(chunks)
    if len(value) != size:
        raise RuntimeError("Sandbox process stream ended mid-frame")
    return value


def iter_process_events(response: Any):
    """Yield validated, decoded stdout/stderr and an exit code from Connect frames."""
    if "application/connect+json" not in response.headers.get("Content-Type", "").lower():
        raise RuntimeError("Sandbox returned a non-Connect process stream")
    saw_end = False
    saw_trailer = False
    while True:
        first_byte = response.read(1)
        if not first_byte:
            break
        header = first_byte + read_exact(response, 4)
        flags = header[0]
        frame_size = int.from_bytes(header[1:5], byteorder="big")
        if frame_size > MAX_CONNECT_FRAME_BYTES or flags & ~0x02:
            raise RuntimeError("Invalid or oversized Connect process frame")
        payload = read_exact(response, frame_size)
        try:
            event = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("Sandbox sent invalid Connect JSON") from error
        if flags & 0x02:
            saw_trailer = True
            if event.get("error"):
                raise RuntimeError(f"Sandbox process failed: {event['error']}")
            break
        inner = event.get("event", {})
        if not isinstance(inner, dict):
            raise RuntimeError("Sandbox sent malformed ProcessEvent")
        data = inner.get("data", {})
        if isinstance(data, dict):
            for stream in ("stdout", "stderr"):
                if stream in data:
                    try:
                        decoded = base64.b64decode(data[stream], validate=True).decode("utf-8", errors="replace")
                    except (TypeError, ValueError, binascii.Error) as error:
                        raise RuntimeError("Sandbox sent invalid base64 process output") from error
                    yield {"stream": stream, "data": decoded}
        end = inner.get("end")
        if end is not None:
            if not isinstance(end, dict):
                raise RuntimeError(f"Sandbox process ended with invalid payload: {end!r}")
            exit_code = end.get("exitCode")
            if type(exit_code) is not int:
                exit_code = end.get("exit_code")
            if type(exit_code) is not int and isinstance(end.get("status"), str):
                match = re.fullmatch(r"exit status (-?\d+)", end["status"].strip())
                if match:
                    exit_code = int(match.group(1))
            if type(exit_code) is not int:
                preview = json.dumps(end, ensure_ascii=False, separators=(",", ":"))[:1000]
                raise RuntimeError(f"Sandbox process ended without an exitCode: {preview}")
            saw_end = True
            yield {"exitCode": exit_code}
    if not saw_end or not saw_trailer:
        raise RuntimeError("Sandbox process stream ended before exitCode or Connect trailer")


def encode_connect_message(payload: dict[str, Any]) -> bytes:
    body = json_bytes(payload)
    return b"\x00" + len(body).to_bytes(4, "big") + body


def multipart_file_body(content: bytes) -> tuple[bytes, str]:
    boundary = f"sandbox-{secrets.token_hex(16)}"
    prefix = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="upload"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("ascii")
    return prefix + content + f"\r\n--{boundary}--\r\n".encode("ascii"), f"multipart/form-data; boundary={boundary}"


def encode_file_path(path: str) -> str:
    return "/".join(quote(segment, safe="") for segment in path.split("/"))


def request_file(method: str, path: str, content: bytes | None = None) -> tuple[int, bytes, dict[str, str]]:
    if content is None:
        body = None
        headers = sandbox_direct_headers(accept="application/octet-stream")
    else:
        body, content_type = multipart_file_body(content)
        headers = {**sandbox_direct_headers(accept="application/json"), "Content-Type": content_type}
    url = sandbox_direct_url(f"/files?path={encode_file_path(path)}")
    last_error: Exception | None = None
    for attempt in range(3):
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=EXEC_IDLE_TIMEOUT_SECONDS) as response:
                return response.status, response.read(), dict(response.headers.items())
        except HTTPError as error:
            if error.code < 500 and error.code != 429:
                raise
            last_error = error
        except (URLError, OSError, socket.timeout) as error:
            last_error = error
        if attempt < 2:
            time.sleep(0.2 * (attempt + 1))
    assert last_error is not None
    raise last_error


def read_remote_error(error: HTTPError) -> str:
    try:
        body = error.read().decode("utf-8", errors="replace")
    except OSError:
        body = ""
    return body or error.reason or "Remote sandbox request failed"


def remote_json_request(
    method: str,
    path: str,
    *,
    payload: Any | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    body = None if payload is None else json_bytes(payload)
    request_headers = {"Accept": "application/json", "User-Agent": CONTROL_PLANE_USER_AGENT}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request_headers.update(headers or {})
    request = Request(remote_url(path), data=body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=EXEC_IDLE_TIMEOUT_SECONDS) as response:
            return response.status, response.read(), dict(response.headers.items())
    except HTTPError as error:
        return error.code, read_remote_error(error).encode("utf-8"), dict(error.headers.items())
    except URLError as error:
        raise RuntimeError(f"Sandbox control-plane unavailable: {error.reason}") from error


def require_sandbox_id() -> str:
    with state_lock:
        sandbox_id = state["sandbox_id"]
    if not sandbox_id:
        raise RuntimeError("No active sandbox. Provision a workspace first.")
    return str(sandbox_id)


def normalize_cwd(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("working_directory must be a non-empty string")
    if ".." in value.split("/"):
        raise ValueError("working_directory cannot contain '..'")
    if value.startswith("/"):
        normalized = posixpath.normpath(value)
    elif value == "scratch" or value.startswith("scratch/"):
        normalized = posixpath.normpath("/" + value)
    else:
        normalized = posixpath.normpath("/output/" + value)
    if normalized not in {"/output", "/scratch"} and not normalized.startswith(("/output/", "/scratch/")):
        raise ValueError("working_directory must be under /output or /scratch")
    return normalized


def open_process(command: str, cwd: str) -> Any:
    payload = {"process": {"cmd": "/bin/sh", "args": ["-c", command], "cwd": cwd}}
    request = Request(
        sandbox_direct_url("/process.Process/Start"),
        data=encode_connect_message(payload),
        headers={
            **sandbox_direct_headers(accept="application/connect+json"),
            "Content-Type": "application/connect+json",
            "Connect-Protocol-Version": "1",
            "Connect-Content-Encoding": "identity",
        },
        method="POST",
    )
    return urlopen(request, timeout=EXEC_IDLE_TIMEOUT_SECONDS)


def run_process_capture(command: str, cwd: str = "/output") -> dict[str, Any]:
    stdout: list[str] = []
    stderr: list[str] = []
    exit_code: int | None = None
    try:
        with open_process(command, cwd) as response:
            for event in iter_process_events(response):
                if event.get("stream") == "stdout":
                    stdout.append(event.get("data", ""))
                elif event.get("stream") == "stderr":
                    stderr.append(event.get("data", ""))
                elif "exitCode" in event:
                    exit_code = event["exitCode"]
    except HTTPError as error:
        detail = read_remote_error(error)
        return {"exit_code": 1, "stdout": "", "stderr": f"HTTP {error.code}: {detail}"}
    except (RuntimeError, URLError, OSError, socket.timeout) as error:
        return {"exit_code": 1, "stdout": "", "stderr": str(error)}
    if exit_code is None:
        return {"exit_code": 1, "stdout": "".join(stdout), "stderr": "Sandbox process ended without an exit code"}
    return {"exit_code": exit_code, "stdout": "".join(stdout), "stderr": "".join(stderr)}


def run_command_for_setup(command: str) -> None:
    try:
        with open_process(command, "/") as response:
            exit_code = None
            for event in iter_process_events(response):
                if "exitCode" in event:
                    exit_code = event["exitCode"]
            if exit_code != 0:
                raise RuntimeError(f"Sandbox workspace initialization exited with code {exit_code}")
    except HTTPError as error:
        detail = read_remote_error(error)
        raise RuntimeError(f"Sandbox workspace initialization failed (HTTP {error.code}): {detail}") from error
    except (URLError, OSError, socket.timeout) as error:
        raise RuntimeError(f"Sandbox workspace initialization failed: {error}") from error


def to_sandbox_call(tool_call: Any) -> dict[str, Any]:
    """Validate untrusted OpenAI-compatible function arguments before mutation."""
    function = tool_call.get("function") if isinstance(tool_call, dict) else None
    name = function.get("name") if isinstance(function, dict) else None
    arguments = function.get("arguments") if isinstance(function, dict) else None
    if not isinstance(arguments, str):
        return {"kind": "error", "name": name, "message": "Invalid JSON tool arguments"}
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return {"kind": "error", "name": name, "message": "Invalid JSON tool arguments"}
    if not isinstance(parsed, dict):
        return {"kind": "error", "name": name, "message": "Tool arguments must be a JSON object"}
    if name == "sandbox_exec":
        if "command" not in parsed or not isinstance(parsed["command"], str):
            return {"kind": "error", "name": name, "message": "command must be a string"}
        cwd = parsed.get("working_directory")
        if "working_directory" in parsed and not isinstance(cwd, str):
            return {"kind": "error", "name": name, "message": "working_directory must be a string"}
        return {"kind": "exec", "command": parsed["command"], "cwd": cwd if cwd is not None else "/output"}
    if name == "write_file":
        if not isinstance(parsed.get("path"), str) or not parsed["path"]:
            return {"kind": "error", "name": name, "message": "path must be a non-empty string"}
        if "content" not in parsed or not isinstance(parsed["content"], str):
            return {"kind": "error", "name": name, "message": "content must be an explicitly supplied string"}
        return {"kind": "write_file", "path": parsed["path"], "content": parsed["content"]}
    return {"kind": "error", "name": name, "message": "Unknown tool"}


def mock_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return to_sandbox_call({"id": "mock-call", "function": {"name": name, "arguments": json.dumps(arguments)}})


def plan_mock_turn(prompt: str) -> dict[str, Any]:
    """Small deterministic entry point until a real platform LLM hook exists."""
    raw = prompt.lstrip()
    if raw.startswith("/write "):
        head, separator, content = raw[7:].partition("\n")
        return mock_tool("write_file", {"path": head.strip(), "content": content if separator else ""})
    value = prompt.strip()
    lowered = value.lower()
    if value.startswith("/run "):
        return mock_tool("sandbox_exec", {"command": value[5:]})
    if value.startswith("/read "):
        return {"kind": "read_file", "path": value[6:].strip()}
    if any(term in lowered for term in ("liệt kê file", "danh sách file", "list files", "xem file")):
        return mock_tool("sandbox_exec", {"command": "find /output -maxdepth 2 -type f -print"})
    if "libreoffice" in lowered and any(term in lowered for term in ("kiểm tra", "version", "phiên bản", "check")):
        return mock_tool("sandbox_exec", {"command": "libreoffice --version"})
    if lowered in {"pwd", "thư mục hiện tại", "current directory"}:
        return mock_tool("sandbox_exec", {"command": "pwd"})
    return {"kind": "message", "message": "Agent giả lập hiểu /run, /read, /write, hoặc câu như 'liệt kê file', 'kiểm tra LibreOffice'."}


def safe_output_path(raw_path: str) -> str:
    path = raw_path.strip()
    for _ in range(2):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    if not path:
        raise ValueError("path is required")
    parts = path.split("/")
    if ".." in parts:
        raise ValueError("path traversal is not allowed")
    normalized = posixpath.normpath(path)
    if not normalized.startswith("/output/"):
        raise ValueError("file operations are limited to /output")
    return normalized


class Handler(BaseHTTPRequestHandler):
    server_version = "SandboxAgent/0.1"
    protocol_version = "HTTP/1.0"

    def log_message(self, format: str, *args: object) -> None:
        # Local routes never include the backend-held sandbox ID.
        if self.path.startswith("/api/"):
            print(f"[sandbox-agent] {self.command} {urlsplit(self.path).path}")

    def send_json(self, status: int, payload: Any) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, status: int, body: str, content_type: str = "text/plain; charset=utf-8") -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def read_body(self) -> bytes:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ValueError("invalid Content-Length") from error
        if length < 0 or length > MAX_FILE_BYTES:
            raise ValueError(f"request body must be between 0 and {MAX_FILE_BYTES} bytes")
        return self.rfile.read(length)

    def read_json(self) -> dict[str, Any]:
        raw = self.read_body()
        try:
            value = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSON: {error.msg}") from error
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/":
            self.serve_static()
            return
        if parsed.path == "/api/status":
            with state_lock:
                active = bool(state["sandbox_id"])
                response = {
                    "active": active,
                    "created_at": state["created_at"],
                    "configured": not bool(config_error()),
                    "template_configured": bool(env_config()[1]),
                }
            self.send_json(200, response)
            return
        if parsed.path == "/api/sandbox/files":
            self.proxy_file_get(parse_qs(parsed.query))
            return
        self.send_json(404, {"error": "route not found"})

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        try:
            if parsed.path == "/api/sandbox/provision":
                self.provision()
            elif parsed.path == "/api/agent/plan":
                body = self.read_json()
                prompt = body.get("prompt")
                if not isinstance(prompt, str) or not prompt.strip():
                    raise ValueError("prompt must be a non-empty string")
                self.send_json(200, plan_mock_turn(prompt))
            elif parsed.path == "/api/agent/create-slide":
                self.create_slide()
            elif parsed.path == "/api/sandbox/exec":
                self.exec_command()
            elif parsed.path == "/api/sandbox/files":
                self.proxy_file_write(parse_qs(parsed.query))
            else:
                self.send_json(404, {"error": "route not found"})
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
        except RuntimeError as error:
            self.send_json(503, {"error": str(error)})

    def do_DELETE(self) -> None:
        if urlsplit(self.path).path != "/api/sandbox":
            self.send_json(404, {"error": "route not found"})
            return
        if not lifecycle_lock.acquire(blocking=False):
            self.send_json(409, {"error": "sandbox lifecycle operation already in progress"})
            return
        try:
            self._destroy_locked()
        finally:
            lifecycle_lock.release()

    def _destroy_locked(self) -> None:
        try:
            sandbox_id = require_sandbox_id()
            status, body, _ = remote_json_request("DELETE", f"/sandboxes/{quote(sandbox_id, safe='')}")
            parsed_body: Any
            try:
                parsed_body = json.loads(body.decode("utf-8")) if body else None
            except json.JSONDecodeError:
                parsed_body = body.decode("utf-8", errors="replace")
            with state_lock:
                state.update({
                    "sandbox_id": None,
                    "domain": None,
                    "envd_access_token": None,
                    "traffic_access_token": None,
                    "envd_version": None,
                    "created_at": None,
                })
            if status >= 400:
                self.send_json(502, {"error": "sandbox destroy failed", "remote_status": status, "detail": parsed_body})
                return
            self.send_json(200, {"ok": True, "remote_status": status})
        except RuntimeError as error:
            # The mapping is cleared even when remote cleanup cannot be confirmed.
            with state_lock:
                state.update({
                    "sandbox_id": None,
                    "domain": None,
                    "envd_access_token": None,
                    "traffic_access_token": None,
                    "envd_version": None,
                    "created_at": None,
                })
            self.send_json(503, {"error": str(error)})

    def serve_static(self) -> None:
        try:
            body = INDEX_FILE.read_bytes()
        except OSError as error:
            self.send_json(500, {"error": f"cannot read UI: {error}"})
            return
        content_type = mimetypes.guess_type(INDEX_FILE.name)[0] or "text/html"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def provision(self) -> None:
        if not lifecycle_lock.acquire(blocking=False):
            self.send_json(409, {"error": "sandbox lifecycle operation already in progress"})
            return
        try:
            self._provision_locked()
        finally:
            lifecycle_lock.release()

    def _provision_locked(self) -> None:
        error = config_error()
        if error:
            self.send_json(503, {"error": error})
            return
        with state_lock:
            if state["sandbox_id"]:
                self.send_json(200, {"ok": True, "already_active": True})
                return

        _, template_id = env_config()
        if not template_id:
            self.send_json(503, {"error": "SANDBOX_TEMPLATE_ID is not configured"})
            return
        payload = {"templateID": template_id}
        status, body, _ = remote_json_request("POST", "/sandboxes", payload=payload)
        try:
            response = json.loads(body.decode("utf-8")) if body else {}
        except json.JSONDecodeError:
            response = {"detail": body.decode("utf-8", errors="replace")}
        if status >= 400:
            self.send_json(502, {
                "error": "sandbox provision failed",
                "remote_status": status,
                "detail": response,
            })
            return
        if not isinstance(response, dict):
            self.send_json(502, {"error": "sandbox provision returned invalid JSON"})
            return
        sandbox_id = response.get("sandboxID")
        domain = response.get("domain")
        if not isinstance(sandbox_id, str) or not sandbox_id or not isinstance(domain, str) or not domain:
            self.send_json(502, {"error": "sandbox response did not include sandboxID and domain"})
            return
        with state_lock:
            state.update({
                "sandbox_id": str(sandbox_id),
                "domain": domain,
                "envd_access_token": response.get("envdAccessToken"),
                "traffic_access_token": response.get("trafficAccessToken"),
                "envd_version": response.get("envdVersion"),
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        try:
            run_command_for_setup("mkdir -p /output /scratch")
        except RuntimeError as error:
            try:
                remote_json_request("DELETE", f"/sandboxes/{quote(sandbox_id, safe='')}")
            except RuntimeError:
                pass
            with state_lock:
                state.update({"sandbox_id": None, "domain": None, "envd_access_token": None,
                              "traffic_access_token": None, "envd_version": None, "created_at": None})
            self.send_json(502, {
                "error": "sandbox workspace initialization failed",
                "detail": str(error),
            })
            return
        self.send_json(201, {"ok": True, "created_at": state["created_at"]})

    def exec_command(self) -> None:
        if config_error():
            self.send_json(503, {"error": config_error()})
            return
        body = self.read_json()
        command = body.get("command")
        cwd = body.get("cwd", "/output")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        cwd = normalize_cwd(cwd)
        require_sandbox_id()
        try:
            response = open_process(command, cwd)
        except HTTPError as error:
            self.send_json(502, {"error": "sandbox exec failed", "remote_status": error.code})
            return
        except (URLError, OSError, socket.timeout) as error:
            self.send_json(503, {"error": "sandbox exec connection unavailable"})
            return

        self.send_response(response.status)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            for event in iter_process_events(response):
                self.wfile.write(json_bytes(event) + b"\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (RuntimeError, OSError, socket.timeout) as error:
            try:
                self.wfile.write(json_bytes({"error": str(error)}) + b"\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            response.close()

    def create_slide(self) -> None:
        if config_error():
            self.send_json(503, {"error": config_error()})
            return
        body = self.read_json()
        script = body.get("script")
        script_path = body.get("script_path", "/output/create_slide.py")
        output_path = body.get("output_path", "/output/slide.html")
        if not isinstance(script, str) or not script.strip():
            raise ValueError("script must be a non-empty string")
        script_path = safe_output_path(script_path)
        output_path = safe_output_path(output_path)
        if not script_path.endswith(".py"):
            raise ValueError("script_path must point to a .py file")
        require_sandbox_id()
        try:
            request_file("POST", script_path, script.encode("utf-8"))
        except (HTTPError, URLError, OSError, socket.timeout) as error:
            self.send_json(502, {"error": "slide script upload failed", "detail": str(error)})
            return

        result = run_process_capture(f"python3 {shlex.quote(script_path)}", "/output")
        if result["exit_code"] != 0:
            self.send_json(502, {"error": "slide generation failed", "result": result})
            return
        try:
            _, artifact, headers = request_file("GET", output_path)
        except (HTTPError, URLError, OSError, socket.timeout) as error:
            self.send_json(502, {
                "error": "slide generation did not produce the output file",
                "path": output_path,
                "detail": str(error),
                "result": result,
            })
            return
        self.send_json(200, {
            "ok": True,
            "artifact_path": output_path,
            "preview_url": f"/api/sandbox/files?path={quote(output_path, safe='/')}",
            "content_type": headers.get("Content-Type", "application/octet-stream"),
            "bytes": len(artifact),
            "result": result,
        })

    def proxy_file_get(self, query: dict[str, list[str]]) -> None:
        try:
            path = safe_output_path(query.get("path", [""])[0])
            require_sandbox_id()
        except (ValueError, RuntimeError) as error:
            self.send_json(400, {"error": str(error)})
            return
        if config_error():
            self.send_json(503, {"error": config_error()})
            return
        try:
            _, body, headers = request_file("GET", path)
        except HTTPError as error:
            self.send_json(502, {"error": "sandbox file read failed", "remote_status": error.code})
            return
        except (URLError, OSError, socket.timeout) as error:
            self.send_json(503, {"error": "sandbox file read connection unavailable"})
            return
        self.send_response(200)
        self.send_header("Content-Type", headers.get("Content-Type", "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def proxy_file_write(self, query: dict[str, list[str]]) -> None:
        try:
            path = safe_output_path(query.get("path", [""])[0])
            require_sandbox_id()
            body = self.read_body()
        except (ValueError, RuntimeError) as error:
            self.send_json(400, {"error": str(error)})
            return
        if config_error():
            self.send_json(503, {"error": config_error()})
            return
        try:
            remote_status, remote_body, _ = request_file("POST", path, body)
        except HTTPError as error:
            self.send_json(502, {"error": "sandbox file write failed", "remote_status": error.code})
            return
        except (URLError, OSError, socket.timeout) as error:
            self.send_json(503, {"error": "sandbox file write connection unavailable"})
            return
        try:
            entries = json.loads(remote_body.decode("utf-8")) if remote_body else None
        except json.JSONDecodeError:
            entries = None
        self.send_json(200, {"ok": True, "path": path, "bytes": len(body), "entries": entries, "remote_status": remote_status})


def main() -> None:
    parser = argparse.ArgumentParser(description="Local web proxy for the sandbox agent prototype")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    error = config_error()
    if error:
        parser.error(error)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Sandbox Agent UI: http://{args.host}:{args.port}")
    print(f"Control plane: {env_config()[0]}")
    print(f"Using template ID: {env_config()[1]}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping sandbox agent server.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
