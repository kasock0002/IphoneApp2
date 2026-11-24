"""Behavior tests using synthetic CLI fixtures, never a real Codex account."""

from __future__ import annotations

from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import stat
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import orbit_bridge as bridge


NOW = "2026-11-15T10:30:00Z"
TOKEN = "synthetic-test-token-not-a-secret-12345678"


def task(task_id="task-example-1", **changes):
    value = {
        "id": task_id,
        "title": "Синтетическая задача",
        "status": "running",
        "url": "https://chatgpt.com/codex/tasks/task-example-1",
        "updated_at": "2026-11-15T09:15:00Z",
        "environment_id": "environment-example",
        "environment_label": "Пример окружения",
        "summary": "Краткая сводка из тестового ответа.",
        "attempt_total": 5,
        "is_review": False,
    }
    value.update(changes)
    return value


@contextmanager
def running_server(cache=None):
    server = bridge.OrbitHTTPServer(("127.0.0.1", 0), TOKEN, cache or bridge.SnapshotCache())
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=1)


def request(server, path="/snapshot", *, authorization="Bearer " + TOKEN, method="GET"):
    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
    headers = {} if authorization is None else {"Authorization": authorization}
    try:
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


class SnapshotTests(unittest.TestCase):
    def test_real_task_fields_are_preserved_without_invented_project_progress(self):
        snapshot = bridge.build_snapshot([task()], generated_at=NOW)
        project = snapshot["projects"][0]
        self.assertEqual(snapshot["generatedAt"], NOW)
        self.assertIsNone(project["stage"])
        self.assertIsNone(project["progress"])
        self.assertEqual(project["agents"], [])
        self.assertEqual(project["reports"], [])
        self.assertEqual(project["steps"], [])
        self.assertEqual(project["source"], "codex-cloud")
        self.assertEqual(len(project["cloudTasks"]), 1)  # attempt_total is not an agent count.
        cloud_task = project["cloudTasks"][0]
        self.assertEqual(cloud_task["status"], "running")
        self.assertEqual(cloud_task["summary"], task()["summary"])
        self.assertEqual(cloud_task["updatedAt"], "2026-11-15T09:15:00Z")

    def test_empty_account_is_a_successful_empty_snapshot(self):
        self.assertEqual(bridge.build_snapshot([], generated_at=NOW), {"generatedAt": NOW, "projects": []})

    def test_tasks_group_by_environment_id_not_label(self):
        result = bridge.build_snapshot([
            task("one", environment_id="a", environment_label="Одинаковое имя"),
            task("two", environment_id="b", environment_label="Одинаковое имя"),
            task("three", environment_id="a", environment_label="Новое имя"),
            task("four", environment_id=None, environment_label="Не окружение"),
        ], generated_at=NOW)
        projects = result["projects"]
        self.assertEqual(len(projects), 3)
        self.assertEqual(len({project["id"] for project in projects}), 3)
        self.assertEqual([len(project["cloudTasks"]) for project in projects], [2, 1, 1])
        self.assertEqual(projects[2]["name"], "Облачный Codex")
        again = bridge.build_snapshot([task("different", environment_id="a")], generated_at=NOW)
        self.assertEqual(projects[0]["id"], again["projects"][0]["id"])

    def test_unknown_values_remain_unknown(self):
        snapshot = bridge.build_snapshot([task(
            status={"code": "running"}, summary={"private": "nested"}, updated_at=1700000000,
            url="https://untrusted.example/task",
        )], generated_at=NOW)
        project = snapshot["projects"][0]
        self.assertIsNone(project["updatedAt"])
        cloud_task = project["cloudTasks"][0]
        self.assertEqual(cloud_task["status"], "unknown")
        for field in ["summary", "url", "updatedAt"]:
            self.assertIsNone(cloud_task[field])

    def test_unrecognized_raw_status_is_not_mapped_to_working(self):
        snapshot = bridge.build_snapshot([task(status="some_future_state")], generated_at=NOW)
        self.assertEqual(snapshot["projects"][0]["cloudTasks"][0]["status"], "some_future_state")
        self.assertEqual(snapshot["projects"][0]["agents"], [])

    def test_updated_time_uses_latest_available_source_time(self):
        snapshot = bridge.build_snapshot([
            task("older", updated_at="2026-11-15T10:00:00+02:00"),
            task("unknown", updated_at=None),
            task("newer", updated_at="2026-11-15T09:00:00.999Z"),
        ], generated_at=NOW)
        project = snapshot["projects"][0]
        self.assertEqual(project["updatedAt"], "2026-11-15T09:00:00Z")
        self.assertEqual([item["id"] for item in project["cloudTasks"]], ["newer", "older", "unknown"])

    def test_dates_without_timezone_or_with_unknown_units_are_not_guessed(self):
        for value in ["2026-11-15T09:00:00", "2026-11-15", "invalid", 1700000000, None]:
            with self.subTest(value=value):
                self.assertIsNone(bridge.iso_date(value))

    def test_task_links_cannot_open_non_chatgpt_or_credential_urls(self):
        for url in ["http://chatgpt.com/task", "https://chatgpt.com.evil.example/task",
                    "https://user:secret@chatgpt.com/task", "https://chatgpt.com:8443/task",
                    "javascript:alert(1)", "https://chatgpt.com/\nprivate", None]:
            with self.subTest(url=url):
                self.assertIsNone(bridge.safe_task_url(url))
        self.assertEqual(bridge.safe_task_url("https://chatgpt.com/codex/tasks/example"),
                         "https://chatgpt.com/codex/tasks/example")
        self.assertEqual(bridge.safe_task_url("https://www.chatgpt.com/codex/tasks/example"),
                         "https://www.chatgpt.com/codex/tasks/example")

    def test_native_text_limits_fail_without_silent_truncation(self):
        for fields in [{"title": "x" * 301}, {"status": "x" * 81}]:
            with self.subTest(fields=fields), self.assertRaisesRegex(bridge.BridgeError, "текст не усекался"):
                bridge.build_snapshot([task(**fields)], generated_at=NOW)

    def test_malformed_or_duplicate_task_ids_fail_without_partial_export(self):
        for records in [[task("same"), task("same")], [task("")], [task(123)], ["not an object"]]:
            with self.subTest(records=records):
                with self.assertRaises(bridge.BridgeError):
                    bridge.build_snapshot(records, generated_at=NOW)

    def test_environment_and_project_caps_do_not_silently_truncate(self):
        with self.assertRaisesRegex(bridge.BridgeError, "12"):
            bridge.build_snapshot([task(str(i), environment_id=str(i)) for i in range(13)], generated_at=NOW)
        with self.assertRaisesRegex(bridge.BridgeError, "100"):
            bridge.build_snapshot([task(str(i)) for i in range(101)], generated_at=NOW)
        with self.assertRaises(bridge.BridgeError):
            bridge.build_snapshot([task(environment_id={"id": "unknown"})], generated_at=NOW)

    def test_oversized_snapshot_is_rejected_without_truncating_report_content(self):
        with self.assertRaisesRegex(bridge.BridgeError, "2 МБ"):
            bridge.build_snapshot([task(summary="x" * bridge.MAX_BYTES)], generated_at=NOW)


class CLICollectionTests(unittest.TestCase):
    def test_stop_event_prevents_following_page_and_partial_export(self):
        stop = threading.Event()

        def page(*_):
            stop.set()
            return {"tasks": [task()], "cursor": "next"}

        pages = Mock(side_effect=page)
        with self.assertRaisesRegex(bridge.BridgeError, "остановлено"):
            bridge.fetch_cloud_tasks(run_page=pages, stop_event=stop)
        self.assertEqual(pages.call_count, 1)

    def test_pagination_collects_all_pages(self):
        pages = Mock(side_effect=[{"tasks": [task("one")], "cursor": "next-page"},
                                  {"tasks": [task("two")]}])
        result = bridge.fetch_cloud_tasks(environment_id="env", run_page=pages)
        self.assertEqual([item["id"] for item in result], ["one", "two"])
        self.assertEqual(pages.call_args_list[0].args, (None, "env"))
        self.assertEqual(pages.call_args_list[1].args, ("next-page", "env"))

    def test_repeated_cursor_or_duplicate_tasks_fail(self):
        repeated_cursor = Mock(side_effect=[{"tasks": [task("one")], "cursor": "again"},
                                           {"tasks": [task("two")], "cursor": "again"}])
        with self.assertRaisesRegex(bridge.BridgeError, "курсор"):
            bridge.fetch_cloud_tasks(run_page=repeated_cursor)
        duplicate = Mock(side_effect=[{"tasks": [task("one")], "cursor": "next"}, {"tasks": [task("one")]}])
        with self.assertRaisesRegex(bridge.BridgeError, "идентификатор"):
            bridge.fetch_cloud_tasks(run_page=duplicate)

    def test_pagination_failure_does_not_return_partial_result(self):
        pages = Mock(side_effect=[{"tasks": [task()], "cursor": "next"}, bridge.BridgeError("offline")])
        with self.assertRaises(bridge.BridgeError):
            bridge.fetch_cloud_tasks(run_page=pages)

    def test_page_cap_and_changed_json_shape_fail(self):
        with self.assertRaisesRegex(bridge.BridgeError, "лимит"):
            bridge.fetch_cloud_tasks(max_pages=1, run_page=lambda *_: {"tasks": [task()], "cursor": "next"})
        for payload in [{}, {"tasks": {}}, {"tasks": [], "cursor": 42}]:
            with self.subTest(payload=payload), self.assertRaises(bridge.BridgeError):
                bridge.fetch_cloud_tasks(run_page=lambda *_: payload)

    @patch("orbit_bridge.subprocess.run")
    def test_cli_invocation_uses_argv_timeout_and_json(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, stdout='{"tasks": []}', stderr="")
        self.assertEqual(bridge.run_cli_page("cursor with spaces", "environment"), {"tasks": []})
        args, options = run.call_args
        self.assertEqual(args[0], ["codex", "cloud", "list", "--json", "--limit", "20", "--cursor", "cursor with spaces", "--env", "environment"])
        self.assertEqual(options["timeout"], 30)
        self.assertNotIn("shell", options)

    @patch("orbit_bridge.subprocess.run")
    def test_cli_errors_do_not_relay_credentials_or_private_endpoints(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, stdout="MY_PRIVATE_DATA", stderr="Authorization: Bearer MY_SECRET https://private.example/account")
        with self.assertRaises(bridge.BridgeError) as caught:
            bridge.run_cli_page(None, None)
        message = str(caught.exception)
        for private in ["MY_SECRET", "MY_PRIVATE_DATA", "private.example"]:
            self.assertNotIn(private, message)

    @patch("orbit_bridge.subprocess.run")
    def test_missing_cli_timeout_and_non_json_have_actionable_errors(self, run):
        for outcome in [FileNotFoundError(), subprocess.TimeoutExpired("codex", 30),
                        subprocess.CompletedProcess([], 0, stdout="not json", stderr="")]:
            with self.subTest(outcome=type(outcome).__name__):
                if isinstance(outcome, Exception):
                    run.side_effect = outcome
                else:
                    run.side_effect = None
                    run.return_value = outcome
                with self.assertRaises(bridge.BridgeError):
                    bridge.run_cli_page(None, None)


class RefreshTests(unittest.TestCase):
    def test_success_writes_private_file_and_failure_preserves_original_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = bridge.SnapshotCache()
            path = Path(directory) / "snapshot.json"
            self.assertTrue(bridge.refresh_once(cache, path, fetch=lambda **_: [task()]))
            original, stale = cache.read()
            self.assertFalse(stale)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertLogs("orbit-bridge", level="ERROR"):
                success = bridge.refresh_once(cache, path, fetch=Mock(side_effect=bridge.BridgeError("offline")))
            self.assertFalse(success)
            last, stale = cache.read()
            self.assertTrue(stale)
            self.assertEqual(last, original)
            self.assertEqual(path.read_bytes(), original)

    def test_first_failure_does_not_publish_old_file_as_current(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text('{"generatedAt":"old"}')
            cache = bridge.SnapshotCache()
            with self.assertLogs("orbit-bridge", level="ERROR"):
                bridge.refresh_once(cache, path, fetch=Mock(side_effect=bridge.BridgeError("offline")))
            self.assertEqual(cache.read(), (None, True))
            self.assertEqual(path.read_text(), '{"generatedAt":"old"}')

    def test_failed_atomic_replace_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_bytes(b"previous")
            with patch("orbit_bridge.os.replace", side_effect=OSError("cannot replace")):
                with self.assertRaises(bridge.BridgeError):
                    bridge.write_snapshot(path, b"next")
            self.assertEqual(path.read_bytes(), b"previous")
            self.assertEqual(list(Path(directory).glob(".orbit-*")), [])


class HTTPTests(unittest.TestCase):
    def test_health_contains_readiness_only_and_initial_snapshot_is_unavailable(self):
        with running_server() as server:
            status, headers, body = request(server, "/health", authorization=None)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"ready": False})
            self.assertEqual(headers["Cache-Control"], "no-store")
            status, _, body = request(server)
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(body), {"error": "snapshot_not_ready"})

    def test_snapshot_requires_bearer_even_if_query_contains_token(self):
        cache = bridge.SnapshotCache()
        cache.publish(bridge.encode_snapshot(bridge.build_snapshot([], generated_at=NOW)))
        with running_server(cache) as server:
            for authorization in [None, "Bearer wrong", TOKEN, "Basic " + TOKEN]:
                with self.subTest(authorization=authorization):
                    status, _, body = request(server, authorization=authorization)
                    self.assertEqual(status, 401)
                    self.assertNotIn(TOKEN.encode(), body)
            status, _, _ = request(server, "/snapshot?token=" + TOKEN, authorization=None)
            self.assertEqual(status, 404)

    def test_duplicate_authorization_headers_are_rejected(self):
        with running_server() as server:
            connection = http.client.HTTPConnection(*server.server_address, timeout=3)
            try:
                connection.putrequest("GET", "/snapshot")
                connection.putheader("Authorization", "Bearer " + TOKEN)
                connection.putheader("Authorization", "Bearer " + TOKEN)
                connection.endheaders()
                response = connection.getresponse()
                self.assertEqual(response.status, 401)
                response.read()
            finally:
                connection.close()

    def test_stale_header_and_original_payload_are_preserved(self):
        cache = bridge.SnapshotCache()
        payload = bridge.encode_snapshot(bridge.build_snapshot([task()], generated_at=NOW))
        cache.publish(payload)
        with running_server(cache) as server:
            status, headers, body = request(server)
            self.assertEqual(status, 200)
            self.assertEqual(headers["X-Orbit-Stale"], "false")
            self.assertEqual(body, payload)
            cache.mark_stale()
            status, headers, body = request(server)
            self.assertEqual(status, 200)
            self.assertEqual(headers["X-Orbit-Stale"], "true")
            self.assertEqual(json.loads(body)["generatedAt"], NOW)
            self.assertEqual(body, payload)
            status, _, body = request(server, "/health", authorization=None)
            self.assertEqual(json.loads(body), {"ready": True})

    def test_requests_never_launch_cli_and_other_paths_or_methods_are_rejected(self):
        with running_server() as server, patch("orbit_bridge.subprocess.run") as cli:
            self.assertEqual(request(server, "/anything")[0], 404)
            self.assertEqual(request(server, method="POST")[0], 405)
            self.assertEqual(request(server, method="HEAD")[0], 405)
            cli.assert_not_called()

    def test_listener_and_token_constraints(self):
        for host in ["0.0.0.0", "192.168.1.3", "example.com", "::1"]:
            with self.subTest(host=host), self.assertRaises(bridge.BridgeError):
                bridge.loopback_host(host)
        self.assertEqual(bridge.loopback_host("localhost"), "127.0.0.1")
        for token_value in [None, "short", " " * 32, "а" * 32, "a" * 31 + "\n", "a" * 31 + "\x00", "a" * 31 + "\x7f", "a" * 513]:
            with self.subTest(token_value=token_value), self.assertRaises(bridge.BridgeError):
                bridge.validate_token(token_value)


if __name__ == "__main__":
    unittest.main()
