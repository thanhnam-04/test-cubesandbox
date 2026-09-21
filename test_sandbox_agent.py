import io
import importlib.util
import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import sandbox_agent_server as app
import sandbox_slide_load_runner as slide_load_runner
import agent_workload_runner as agent_workloads
import benchmark_suite_client as benchmark_suite
import slide_two_client_load_test as slide_load_test


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

    def test_agent_task_runs_through_real_sandbox_adapter_and_validates_artifacts(self):
        report = {
            "workload": "code", "output_dir": "/output/agent-runs/abcdef123456",
            "artifacts": [{"path": "/output/agent-runs/abcdef123456/calculator.py", "bytes": 42}],
            "artifact_count": 1, "elapsed_seconds": 0.2, "details": {"tests_passed": 3},
        }
        captured = {"exit_code": 0, "stdout": json.dumps(report), "stderr": "",
                    "metrics": {"peak_rss_kb": 12000}}
        with patch.object(app.secrets, "token_hex", return_value="abcdef123456"), \
             patch.object(app, "run_process_capture", return_value=captured) as run:
            result = app.execute_agent_task("code")
        command, cwd = run.call_args.args
        self.assertEqual(cwd, "/output")
        self.assertTrue(command.startswith("python3 -u -c "))
        self.assertIn(" code abcdef123456 ", command)
        self.assertTrue(run.call_args.kwargs["measure"])
        self.assertEqual(result["report"]["details"]["tests_passed"], 3)
        self.assertEqual(result["metrics"]["peak_rss_kb"], 12000)

    def test_agent_task_endpoint_validates_task_and_returns_report(self):
        handler = object.__new__(app.Handler)
        handler.send_json = Mock()
        handler.read_json = Mock(return_value={"task": "unknown"})
        with patch.dict(os.environ, {"SANDBOX_API_BASE_URL": "https://api.example.test"}):
            with self.assertRaises(ValueError):
                handler.run_agent_task()
        handler.read_json = Mock(return_value={"task": "data"})
        expected = {"ok": True, "task": "data"}
        with patch.dict(os.environ, {"SANDBOX_API_BASE_URL": "https://api.example.test"}), \
             patch.object(app, "execute_agent_task", return_value=expected):
            handler.run_agent_task()
        handler.send_json.assert_called_with(200, expected)

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
             patch.object(app, "run_command_for_setup") as initialize, \
             patch.object(app, "verify_sandbox_memory_limit", return_value=2 * 1024 ** 3):
            handler._provision_locked()
        initialize.assert_called_once_with("mkdir -p /output /scratch")
        self.assertEqual(app.state["sandbox_id"], "new123")
        status, payload = handler.send_json.call_args.args
        self.assertEqual(status, 201)
        self.assertNotIn("sandboxID", payload)
        self.assertNotIn("domain", payload)
        self.assertEqual(payload["memory_limit_gib"], 2.0)

    def test_sandbox_memory_limit_must_be_two_gib_or_less(self):
        gib = 1024 ** 3
        with patch.object(app, "run_process_capture", return_value={
                "exit_code": 0,
                "stdout": json.dumps({"cgroup": None, "mem_total": 2 * gib}), "stderr": ""}):
            self.assertEqual(app.verify_sandbox_memory_limit(), 2 * gib)
        for value in (
                json.dumps({"cgroup": None, "mem_total": 2 * gib + 1}),
                json.dumps({"cgroup": str(2 * gib + 1), "mem_total": 4 * gib}),
                "invalid"):
            with self.subTest(value=value), \
                 patch.object(app, "run_process_capture", return_value={
                     "exit_code": 0, "stdout": value, "stderr": ""}), \
                 self.assertRaises(RuntimeError):
                app.verify_sandbox_memory_limit()

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

    def test_benchmark_command_rejects_unrecognized_tasks(self):
        with self.assertRaisesRegex(ValueError, "unknown benchmark task"):
            app.benchmark_command("print('unsafe')", "custom; echo bad")
        command = app.benchmark_command("print('ok')", "cpu")
        self.assertIn("python3 -c", command)
        agent_command = app.benchmark_command("print('ok')", "agent", b"print('worker')")
        self.assertIn(" agent ", agent_command)
        self.assertIn(base64.b64encode(b"print('worker')").decode("ascii"), agent_command)
        with self.assertRaisesRegex(ValueError, "requires worker source"):
            app.benchmark_command("print('ok')", "agent")

    def test_runner_source_compiles(self):
        compile(app.BENCHMARK_FILE.read_text(encoding="utf-8"), "benchmark_runner.py", "exec")
        compile(app.MEASURED_RUNNER_FILE.read_text(encoding="utf-8"), "measured_exec_runner.py", "exec")
        compile(app.SLIDE_LOAD_RUNNER_FILE.read_text(encoding="utf-8"), "sandbox_slide_load_runner.py", "exec")
        compile(app.AGENT_WORKLOAD_FILE.read_text(encoding="utf-8"), "agent_workload_runner.py", "exec")
        compile(app.AGENT_TASK_RUNNER_FILE.read_text(encoding="utf-8"), "sandbox_agent_task_runner.py", "exec")

    def test_web_exposes_automated_benchmark_profiles_and_downloads(self):
        source = app.INDEX_FILE.read_text(encoding="utf-8")
        for element_id in ("suiteProfile", "suiteRunButton", "suiteProgressBar",
                           "suiteVerdict", "suiteJsonButton", "suiteMarkdownButton"):
            self.assertIn(f'id="{element_id}"', source)
        self.assertIn("standard: {", source)
        self.assertIn("tổng 65 load task", source)
        self.assertIn("tổng 441 load task", source)
        self.assertIn("function runBenchmarkSuite()", source)
        self.assertIn("function suiteMarkdown(report)", source)

    def test_automated_benchmark_suite_collects_and_renders_results(self):
        profile = {
            "basic_rounds": 1,
            "agent_rounds": 1,
            "loads": [("workflow", 2, 1, 0.1)],
        }
        hardware = {
            "logical_cpus_visible": 2,
            "memory_limit_bytes": 1930 * 1024 * 1024,
        }
        responses = [
            {"active": True},
            {"hardware": hardware, "results": [{
                "task": "cpu", "elapsed_seconds": 1.5,
                "cpu_percent_one_core": 100, "peak_rss_kb": 6000,
                "disk_read_bytes": 0, "disk_write_bytes": 0,
            }]},
            {"hardware": hardware, "results": [{
                "task": "agent", "elapsed_seconds": 12.0,
                "cpu_percent_one_core": 95, "peak_rss_kb": 900000,
                "detail": {"artifact_count": 10, "checks_passed": 6, "stages": [
                    {"name": "data", "elapsed_seconds": 1.2, "rss_after_kb": 50000},
                ]},
            }]},
            {"report": {
                "workload": "workflow", "simulated_users": 2, "tasks_per_user": 1,
                "requested_tasks": 2, "succeeded": 2, "failed": 0,
                "total_seconds": 4.0, "latency_p50_seconds": 3.0,
                "latency_p95_seconds": 3.5, "max_concurrent_tasks": 2,
                "sandbox_memory_peak_sampled_bytes": 800 * 1024 * 1024,
                "sandbox_memory_limit_bytes": 1930 * 1024 * 1024,
                "sandbox_oom_kills_during_run": 0,
            }},
        ]
        with patch.dict(benchmark_suite.PROFILES, {"test": profile}), \
             patch.object(benchmark_suite, "request_json", side_effect=responses), \
             patch.object(benchmark_suite.time, "monotonic", side_effect=[10.0, 16.0]):
            report = benchmark_suite.run_suite("http://127.0.0.1:8787", "test")
        self.assertEqual(report["elapsed_seconds"], 6.0)
        self.assertEqual(report["load_runs"][0]["report"]["succeeded"], 2)
        rendered = benchmark_suite.markdown_report(report)
        self.assertIn("Kết luận tự động: **ĐẠT**", rendered)
        self.assertIn("Throughput (task/s)", rendered)
        self.assertIn("| 1 | data | 1.2 |", rendered)
        report["load_runs"][0]["report"]["sandbox_memory_peak_sampled_bytes"] = None
        self.assertIn("Kết luận tự động: **ĐẠT CÓ ĐIỀU KIỆN**",
                      benchmark_suite.markdown_report(report))

    def test_automated_benchmark_suite_requires_active_sandbox(self):
        with patch.object(benchmark_suite, "request_json", return_value={"active": False}), \
             self.assertRaisesRegex(RuntimeError, "không có CubeSandbox"):
            benchmark_suite.run_suite("http://127.0.0.1:8787", "quick")

    def test_load_test_options_validate_bounds_and_types(self):
        self.assertEqual(app.load_test_options({}), (2, 1, 0.0, "slide"))
        self.assertEqual(app.load_test_options({"users": 8, "tasks_per_user": 5, "stagger_seconds": 0.5}),
                         (8, 5, 0.5, "slide"))
        self.assertEqual(app.load_test_options({"workload": "workflow"}), (2, 1, 0.0, "workflow"))
        for body in ({"users": 0}, {"users": 9}, {"users": True},
                     {"tasks_per_user": 0}, {"tasks_per_user": 11},
                     {"users": 8, "tasks_per_user": 13},
                     {"stagger_seconds": -1}, {"stagger_seconds": 3},
                     {"stagger_seconds": True}, {"workload": "unknown"}, {"workload": 4}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                app.load_test_options(body)

    def test_slide_load_percentile_for_small_samples(self):
        self.assertIsNone(slide_load_runner.percentile([], 0.95))
        self.assertEqual(slide_load_runner.percentile([2.0], 0.95), 2.0)
        self.assertEqual(slide_load_runner.percentile([1.0, 3.0], 0.5), 2.0)

    def test_slide_load_runner_rejects_unbounded_direct_invocation(self):
        template = app.SLIDE_TEMPLATE_FILE.read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            slide_load_runner.run_experiment(1000, 1, 0, template)
        with self.assertRaises(ValueError):
            slide_load_runner.run_experiment(1, 1, 0, template, "unknown", "print(1)")

    def test_agent_workload_code_and_search(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            code = root / "code"
            code.mkdir()
            self.assertEqual(agent_workloads.run("code", code)["tests_passed"], 8)
            search = root / "search"
            search.mkdir()
            result = agent_workloads.run("search", search)
            self.assertEqual(result["matches"], 295)
            self.assertEqual(result["files_scanned"], 5000)
            self.assertEqual(len(result["citations"]), 5)

    def test_search_reuses_one_read_only_corpus_for_different_queries(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            corpus = root / "uploaded-corpus"
            manifest = agent_workloads.prepare_search_corpus(corpus, file_count=50)
            before = sorted(path.relative_to(corpus) for path in corpus.rglob("*"))
            for index, query in enumerate(agent_workloads.SEARCH_QUERIES[:2]):
                output = root / f"result-{index}"
                output.mkdir()
                result = agent_workloads.run("search", output, corpus, query)
                self.assertTrue(result["shared_read_only_corpus"])
                self.assertEqual(result["matches"], manifest["expected_matches"][query])
                self.assertTrue(result["citations"])
                self.assertTrue((output / "search-results.json").is_file())
            self.assertEqual(before, sorted(path.relative_to(corpus) for path in corpus.rglob("*")))

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow is not installed")
    def test_agent_workload_images(self):
        with tempfile.TemporaryDirectory() as name:
            result = agent_workloads.run("images", Path(name))
        self.assertEqual(result["images_processed"], 12)
        self.assertGreater(result["contact_bytes"], 1000)

    def test_slide_load_endpoint_returns_report(self):
        handler = object.__new__(app.Handler)
        handler.read_json = Mock(return_value={"users": 2, "tasks_per_user": 3, "stagger_seconds": 0.5})
        handler.send_json = Mock()
        report = {"simulated_users": 2, "requested_tasks": 6, "succeeded": 6}
        with patch.dict(os.environ, {"SANDBOX_API_BASE_URL": "https://api.example.test"}), \
             patch.object(app, "run_slide_load_experiment", return_value=report) as run:
            handler.run_slide_load_test()
        run.assert_called_once_with(2, 3, 0.5, "slide")
        status, payload = handler.send_json.call_args.args
        self.assertEqual(status, 200)
        self.assertEqual(payload["report"], report)

    def test_slide_load_runner_is_launched_inside_sandbox_scratch(self):
        fake_process = {"exit_code": 0, "stdout": '{"simulated_users":2}', "stderr": ""}
        with patch.object(app, "run_process_capture", return_value=fake_process) as run:
            report = app.run_slide_load_experiment(2, 1, 0.0)
        self.assertEqual(report["simulated_users"], 2)
        command, cwd = run.call_args.args
        self.assertEqual(cwd, "/scratch")
        self.assertTrue(command.startswith("python3 -u -c "))
        self.assertIn(" 2 1 0.0 ", command)
        self.assertIn(" slide ", command)

    def test_agent_workload_source_sent_to_sandbox(self):
        fake_process = {"exit_code": 0, "stdout": '{"workload":"data"}', "stderr": ""}
        with patch.object(app, "run_process_capture", return_value=fake_process) as run:
            report = app.run_slide_load_experiment(1, 1, 0, "data")
        self.assertEqual(report["workload"], "data")
        self.assertIn(" data ", run.call_args.args[0])

    @unittest.skipUnless(sys.platform.startswith("linux") and os.path.isdir("/scratch"),
                         "sandbox load runner requires Linux /scratch")
    def test_two_slide_tasks_run_inside_linux_scratch(self):
        template = app.SLIDE_TEMPLATE_FILE.read_text(encoding="utf-8")
        report = slide_load_runner.run_experiment(2, 1, 0, template)
        self.assertEqual(report["requested_tasks"], 2)
        self.assertEqual(report["succeeded"], 2, report["results"])
        self.assertEqual([item["slide_count"] for item in report["results"]], [5, 5])
        self.assertGreaterEqual(report["max_concurrent_tasks"], 1)
        self.assertLessEqual(report["max_concurrent_tasks"], 2)

    def test_measured_stream_extracts_peak_ram_without_leaking_marker(self):
        marker = "__TEST_METRIC__"
        stream = FakeResponse(
            connect_frame({"event": {"data": {"stdout": base64.b64encode(b"hello\n").decode()}}})
            + connect_frame({"event": {"data": {"stderr": base64.b64encode(b"warning\n\n__TEST_MET").decode()}}})
            + connect_frame({"event": {"data": {"stderr": base64.b64encode(b'RIC__{"peak_rss_kb":4864,"elapsed_seconds":2.0}\n').decode()}}})
            + connect_frame({"event": {"end": {"exitCode": 0}}})
            + connect_frame({"metadata": {}}, flags=2)
        )
        events = list(app.iter_measured_events(stream, marker))
        self.assertEqual(events, [
            {"stream": "stdout", "data": "hello\n"},
            {"stream": "stderr", "data": "warning\n"},
            {"metrics": {"peak_rss_kb": 4864, "elapsed_seconds": 2.0}},
            {"exitCode": 0},
        ])

    def test_measured_capture_returns_ram_even_for_nonzero_exit(self):
        marker = "__TEST_METRIC__"
        stream = FakeResponse(
            connect_frame({"event": {"data": {"stderr": base64.b64encode(b'\n__TEST_METRIC__{"peak_rss_kb":10240}\n').decode()}}})
            + connect_frame({"event": {"end": {"exitCode": 7}}})
            + connect_frame({"metadata": {}}, flags=2)
        )
        with patch.object(app, "open_measured_process", return_value=(stream, marker)):
            result = app.run_process_capture("exit 7", measure=True)
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["metrics"]["peak_rss_kb"], 10240)
        self.assertNotIn(marker, result["stderr"])

    def test_measured_stream_reports_unavailable_metric_if_wrapper_did_not_report(self):
        stream = FakeResponse(
            connect_frame({"event": {"end": {"exitCode": 137}}})
            + connect_frame({"metadata": {}}, flags=2)
        )
        events = list(app.iter_measured_events(stream, "__TEST_METRIC__"))
        self.assertIsNone(events[0]["metrics"]["peak_rss_kb"])
        self.assertEqual(events[1], {"exitCode": 137})

    def test_exec_route_streams_ram_metric_event(self):
        marker = "__TEST_METRIC__"
        stream = FakeResponse(
            connect_frame({"event": {"data": {"stderr": base64.b64encode(b'\n__TEST_METRIC__{"peak_rss_kb":8192}\n').decode()}}})
            + connect_frame({"event": {"end": {"exitCode": 0}}})
            + connect_frame({"metadata": {}}, flags=2)
        )
        handler = object.__new__(app.Handler)
        handler.read_json = Mock(return_value={"command": "pwd", "cwd": "/output"})
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = io.BytesIO()
        with patch.dict(os.environ, {"SANDBOX_API_BASE_URL": "https://api.example.test"}), \
             patch.object(app, "open_measured_process", return_value=(stream, marker)) as run:
            handler.exec_command()
        run.assert_called_once_with("pwd", "/output")
        events = [json.loads(line) for line in handler.wfile.getvalue().splitlines()]
        self.assertEqual(events, [{"metrics": {"peak_rss_kb": 8192}}, {"exitCode": 0}])

    def test_create_slide_response_includes_ram_metric(self):
        handler = object.__new__(app.Handler)
        handler.read_json = Mock(return_value={"script": "print('ok')"})
        handler.send_json = Mock()
        measured = {"exit_code": 0, "stdout": "", "stderr": "", "metrics": {"peak_rss_kb": 12000}}
        with patch.dict(os.environ, {"SANDBOX_API_BASE_URL": "https://api.example.test"}), \
             patch.object(app, "request_file", side_effect=[(200, b"[]", {}), (200, b"<html></html>", {})]), \
             patch.object(app, "run_process_capture", return_value=measured) as run:
            handler.create_slide()
        run.assert_called_once_with("python3 /output/create_slide.py", "/output", measure=True)
        status, payload = handler.send_json.call_args.args
        self.assertEqual(status, 200)
        self.assertEqual(payload["result"]["metrics"]["peak_rss_kb"], 12000)

    def test_two_client_slide_scripts_have_separate_outputs_and_markers(self):
        template = slide_load_test.TEMPLATE_PATH.read_text(encoding="utf-8")
        first = slide_load_test.prepare_script(template, "/output/test_A.html", "marker-A")
        second = slide_load_test.prepare_script(template, "/output/test_B.html", "marker-B")
        self.assertIn('path = Path("/output/test_A.html")', first)
        self.assertIn('path = Path("/output/test_B.html")', second)
        self.assertIn("<title>marker-A</title>", first)
        self.assertNotIn("marker-B", first)
        self.assertIn("<title>marker-B</title>", second)
        self.assertNotIn("marker-A", second)
        self.assertEqual(first.count(slide_load_test.ORIGINAL_OUTPUT), 0)

    def test_benchmark_endpoint_validates_tasks_before_execution(self):
        handler = object.__new__(app.Handler)
        handler.send_json = Mock()
        for invalid in ([], ["cpu", "cpu"], ["custom"], ["cpu", 1], "cpu"):
            handler.read_json = Mock(return_value={"tasks": invalid})
            with patch.dict(os.environ, {"SANDBOX_API_BASE_URL": "https://api.example.test"}), \
                 patch.object(app, "benchmark_result") as run:
                with self.assertRaises(ValueError):
                    handler.run_benchmarks()
                run.assert_not_called()

    def test_benchmark_endpoint_returns_metrics_by_task(self):
        handler = object.__new__(app.Handler)
        handler.read_json = Mock(return_value={"tasks": ["memory", "cpu"]})
        handler.send_json = Mock()
        with patch.dict(os.environ, {"SANDBOX_API_BASE_URL": "https://api.example.test"}), \
             patch.object(app, "benchmark_result", side_effect=[
                 {"logical_cpus_visible": 2},
                 {"task": "memory", "peak_rss_kb": 140000},
                 {"task": "cpu", "cpu_user_seconds": 1.5},
             ]) as run:
            handler.run_benchmarks()
        status, payload = handler.send_json.call_args.args
        self.assertEqual(status, 200)
        self.assertEqual([item["task"] for item in payload["results"]], ["memory", "cpu"])
        self.assertEqual(payload["hardware"]["logical_cpus_visible"], 2)
        self.assertEqual([call.args[1] for call in run.call_args_list], ["hardware", "memory", "cpu"])

    def test_benchmark_suite_keeps_other_results_if_one_task_fails(self):
        handler = object.__new__(app.Handler)
        handler.read_json = Mock(return_value={"tasks": ["memory", "cpu"]})
        handler.send_json = Mock()
        with patch.dict(os.environ, {"SANDBOX_API_BASE_URL": "https://api.example.test"}), \
             patch.object(app, "benchmark_result", side_effect=[{}, RuntimeError("memory failed"), {"task": "cpu"}]):
            handler.run_benchmarks()
        status, payload = handler.send_json.call_args.args
        self.assertEqual(status, 200)
        self.assertEqual(payload["results"][0], {"task": "memory", "error": "memory failed"})
        self.assertEqual(payload["results"][1], {"task": "cpu"})

    @unittest.skipUnless(sys.platform.startswith("linux"), "runner requires Linux /proc and resource")
    def test_cpu_runner_reports_real_process_metrics(self):
        source = app.BENCHMARK_FILE.read_text(encoding="utf-8")
        completed = subprocess.run([sys.executable, "-c", source, "cpu"],
                                   capture_output=True, text=True, timeout=10, check=True)
        result = json.loads(completed.stdout)
        self.assertEqual(result["task"], "cpu")
        self.assertGreater(result["elapsed_seconds"], 0)
        self.assertGreater(result["peak_rss_kb"], 0)
        self.assertGreater(result["detail"]["sha256_iterations"], 0)

    @unittest.skipUnless(sys.platform.startswith("linux"), "measured runner requires Linux resource")
    def test_measured_runner_reports_peak_ram_of_real_child(self):
        source = app.MEASURED_RUNNER_FILE.read_text(encoding="utf-8")
        command = "python3 -c 'data = bytearray(16 * 1024 * 1024); print(len(data))'"
        marker = "__TEST_METRIC__"
        completed = subprocess.run([sys.executable, "-u", "-c", source, command, marker],
                                   capture_output=True, text=True, timeout=15, check=True)
        self.assertIn("16777216", completed.stdout)
        metric_line = next(line for line in completed.stderr.splitlines() if line.startswith(marker))
        metrics = json.loads(metric_line[len(marker):])
        self.assertGreater(metrics["peak_rss_kb"], 16000)


if __name__ == "__main__":
    unittest.main()
