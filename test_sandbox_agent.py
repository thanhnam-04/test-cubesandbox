import io
import json
import os
import unittest
from unittest.mock import Mock, patch

import sandbox_agent_server as app


def connect_frame(payload, flags=0):
    raw = json.dumps(payload).encode("utf-8")
    return bytes([flags]) + len(raw).to_bytes(4, "big") + raw


class FakeResponse(io.BytesIO):
    def __init__(self, body, content_type="application/connect+json"):
        super().__init__(body)
        self.headers = {"Content-Type": content_type}
        self.status = 200


class SandboxContractTests(unittest.TestCase):
    def setUp(self):
        self.previous_state = app.state.copy()
        app.state.update({
            "sandbox_id": "abc123",
            "domain": "cube.example.test",
            "envd_access_token": "secret-token",
        })

    def tearDown(self):
        app.state.clear()
        app.state.update(self.previous_state)

    def test_config_requires_explicit_base_url(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIn("not configured", app.config_error())
            self.assertEqual(app.env_config()[1], "py-libreoffice-skills")

    def test_runtime_url_and_headers_keep_sandbox_backend_side(self):
        self.assertEqual(
            app.sandbox_direct_url("/process.Process/Start"),
            "https://49983-abc123.cube.example.test/process.Process/Start",
        )
        self.assertEqual(app.sandbox_direct_headers()["X-Access-Token"], "secret-token")
        self.assertEqual(app.sandbox_direct_headers()["User-Agent"], "sandbox-agent/0.1")

    def test_control_plane_uses_service_user_agent(self):
        with patch.dict(os.environ, {"SANDBOX_API_BASE_URL": "https://api.example.test"}), \
             patch.object(app, "urlopen", return_value=FakeResponse(b"{}")) as send:
            app.remote_json_request("GET", "/sandboxes")
        request = send.call_args.args[0]
        self.assertEqual(request.get_header("User-agent"), "sandbox-agent/0.1")

    def test_process_request_is_connect_framed(self):
        with patch.object(app, "urlopen", return_value=FakeResponse(b"")) as send:
            app.open_process("printf hello", "/output")
        request = send.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/connect+json")
        self.assertEqual(request.get_header("Connect-protocol-version"), "1")
        self.assertEqual(request.data[0], 0)
        size = int.from_bytes(request.data[1:5], "big")
        payload = json.loads(request.data[5:5 + size])
        self.assertEqual(payload["process"], {"cmd": "/bin/sh", "args": ["-c", "printf hello"], "cwd": "/output"})

    def test_stream_decodes_output_and_rejects_trailer_error(self):
        good = FakeResponse(
            connect_frame({"event": {"data": {"stdout": "aGVsbG8K"}}})
            + connect_frame({"event": {"end": {"exitCode": 0}}})
            + connect_frame({"metadata": {}}, flags=2)
        )
        self.assertEqual(list(app.iter_process_events(good)), [
            {"stream": "stdout", "data": "hello\n"}, {"exitCode": 0},
        ])
        snake_case_end = FakeResponse(
            connect_frame({"event": {"end": {"exit_code": 0}}})
            + connect_frame({"metadata": {}}, flags=2)
        )
        self.assertEqual(list(app.iter_process_events(snake_case_end)), [{"exitCode": 0}])
        status_end = FakeResponse(
            connect_frame({"event": {"end": {"exited": True, "status": "exit status 0"}}})
            + connect_frame({"metadata": {}}, flags=2)
        )
        self.assertEqual(list(app.iter_process_events(status_end)), [{"exitCode": 0}])
        bad = FakeResponse(
            connect_frame({"event": {"end": {"exitCode": 0}}})
            + connect_frame({"error": {"code": "unavailable"}}, flags=2)
        )
        with self.assertRaisesRegex(RuntimeError, "Sandbox process failed"):
            list(app.iter_process_events(bad))

    def test_upload_is_multipart_and_paths_are_scoped(self):
        body, content_type = app.multipart_file_body(b"\x00\xffabc")
        self.assertIn('name="file"', body.decode("latin1"))
        self.assertIn(b"\x00\xffabc", body)
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        self.assertEqual(app.encode_file_path("/output/a b.txt"), "/output/a%20b.txt")
        with self.assertRaises(ValueError):
            app.safe_output_path("/output/../etc/passwd")
        with self.assertRaises(ValueError):
            app.safe_output_path("/scratch/file.txt")
        with patch.object(app, "urlopen", return_value=FakeResponse(b"[]", "application/json")) as send:
            app.request_file("POST", "/output/a b.txt", b"\x00\xffabc")
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "https://49983-abc123.cube.example.test/files?path=/output/a%20b.txt")
        self.assertTrue(request.get_header("Content-type").startswith("multipart/form-data; boundary="))
        self.assertIn(b'Content-Disposition: form-data; name="file"', request.data)
        self.assertIn(b"\x00\xffabc", request.data)

    def test_mock_agent_routes_explicit_and_fixed_prompts(self):
        self.assertEqual(app.plan_mock_turn("/write /output/a.txt\n"), {
            "kind": "write_file", "path": "/output/a.txt", "content": "",
        })
        self.assertEqual(app.plan_mock_turn("/write /output/a.txt\n  text\n")["content"], "  text\n")
        self.assertEqual(app.plan_mock_turn("kiểm tra LibreOffice")["command"], "libreoffice --version")
        self.assertEqual(app.plan_mock_turn("làm báo cáo tự động")["kind"], "message")
        handler = object.__new__(app.Handler)
        handler.path = "/api/agent/plan"
        handler.read_json = Mock(return_value={"prompt": "kiểm tra LibreOffice"})
        handler.send_json = Mock()
        handler.do_POST()
        self.assertEqual(handler.send_json.call_args.args[1]["command"], "libreoffice --version")

    def test_invalid_tool_calls_are_rejected_before_execution(self):
        def call(name, arguments):
            return app.to_sandbox_call({"function": {"name": name, "arguments": arguments}})

        self.assertEqual(call("sandbox_exec", "{")["kind"], "error")
        self.assertEqual(call("sandbox_exec", "{}")["message"], "command must be a string")
        self.assertEqual(call("sandbox_exec", '{"command":"pwd","working_directory":null}')["kind"], "error")
        self.assertEqual(call("write_file", '{"path":"/output/a"}')["kind"], "error")
        self.assertEqual(call("write_file", '{"path":"/output/a","content":""}')["content"], "")
        self.assertEqual(call("unknown", "{}")["message"], "Unknown tool")

    def test_provision_requires_domain_and_initializes_workspace(self):
        app.state.update({"sandbox_id": None, "domain": None})
        handler = object.__new__(app.Handler)
        handler.send_json = Mock()
        incomplete = json.dumps({"sandboxID": "new123"}).encode()
        with patch.dict(os.environ, {"SANDBOX_API_BASE_URL": "https://api.example.test"}), \
             patch.object(app, "remote_json_request", return_value=(201, incomplete, {})), \
             patch.object(app, "run_command_for_setup") as initialize:
            handler._provision_locked()
        self.assertIsNone(app.state["sandbox_id"])
        initialize.assert_not_called()

        complete = json.dumps({"sandboxID": "new123", "domain": "cube.example.test"}).encode()
        handler.send_json.reset_mock()
        with patch.dict(os.environ, {"SANDBOX_API_BASE_URL": "https://api.example.test"}), \
             patch.object(app, "remote_json_request", return_value=(201, complete, {})), \
             patch.object(app, "run_command_for_setup") as initialize:
            handler._provision_locked()
        initialize.assert_called_once_with("mkdir -p /output /scratch")
        self.assertEqual(app.state["sandbox_id"], "new123")
        status, payload = handler.send_json.call_args.args
        self.assertEqual(status, 201)
        self.assertNotIn("sandboxID", payload)
        self.assertNotIn("domain", payload)

    def test_provision_error_preserves_remote_status_and_detail(self):
        app.state.update({"sandbox_id": None, "domain": None, "envd_access_token": None})
        handler = object.__new__(app.Handler)
        handler.send_json = Mock()
        with patch.dict(os.environ, {"SANDBOX_API_BASE_URL": "https://api.example.test"}), \
             patch.object(app, "remote_json_request", return_value=(500, b'{"message":"template not found"}', {})):
            handler._provision_locked()
        status, payload = handler.send_json.call_args.args
        self.assertEqual(status, 502)
        self.assertEqual(payload["remote_status"], 500)
        self.assertEqual(payload["detail"]["message"], "template not found")


if __name__ == "__main__":
    unittest.main()
