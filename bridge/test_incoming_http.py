"""Synthetic end-to-end tests: project writer -> HTTP -> merge -> reader snapshot."""
from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import orbit_bridge as bridge
from brief_receiver import BriefReceiver
from test_orbit_bridge import task, TOKEN


WRITER = "synthetic-project-write-only-token-123456789"


@contextmanager
def incoming_server(cache, receiver):
    wake = threading.Event()
    server = bridge.OrbitHTTPServer(("127.0.0.1", 0), TOKEN, cache, receiver, wake)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server, wake
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def exchange(server, *, method="PUT", path="/briefs", payload=b"{}", token=WRITER, headers=None):
    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
    request_headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    request_headers.update(headers or {})
    try:
        connection.request(method, path, body=payload, headers=request_headers)
        result = connection.getresponse()
        return result.status, dict(result.getheaders()), result.read()
    finally:
        connection.close()


class IncomingHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.base = bridge.build_snapshot([task()])
        self.project_id = self.base["projects"][0]["id"]
        self.brief = {"projectId": self.project_id, "producer": "Тестовый агент",
                      "observedAt": "2025-01-01T10:30:00Z", "stage": "development", "progress": 0.5,
                      "reports": [{"id": "report", "title": "Полученный отчёт", "summary": "Явный результат.",
                                   "createdAt": "2025-01-01T10:30:00Z", "highlights": []}]}
        self.cache = bridge.SnapshotCache()
        self.cache.publish(bridge.encode_snapshot(self.base))
        self.briefs_dir = self.directory / "briefs"
        self.receiver = BriefReceiver(self.briefs_dir, {self.project_id: WRITER},
                                      preflight=bridge.brief_budget_guard(self.cache))

    def tearDown(self):
        self.temporary.cleanup()

    def data(self):
        return json.dumps(self.brief).encode("utf-8")

    def test_writer_upload_is_merged_then_visible_to_reader_only(self):
        with incoming_server(self.cache, self.receiver) as (server, wake):
            status, _, body = exchange(server, payload=self.data())
            self.assertEqual(status, 202)
            self.assertTrue(json.loads(body)["accepted"])
            self.assertTrue(wake.is_set())
            self.assertTrue(bridge.refresh_once(self.cache, self.directory / "snapshot.json",
                                               fetch=lambda **_: [task()], briefs_directory=self.briefs_dir))
            status, _, body = exchange(server, method="GET", path="/snapshot", payload=None, token=TOKEN)
            self.assertEqual(status, 200)
            received = json.loads(body)["projects"][0]
            self.assertEqual(received["reports"], self.brief["reports"])
            self.assertEqual(received["briefUpdatedAt"], self.brief["observedAt"])
            self.assertEqual(received["cloudTasks"], self.base["projects"][0]["cloudTasks"])
            status, _, body = exchange(server, method="GET", path="/snapshot", payload=None)
            self.assertEqual(status, 401)
            self.assertNotIn(b"cloudTasks", body)

    def test_reader_token_cannot_write_and_project_token_cannot_switch_projects(self):
        with incoming_server(self.cache, self.receiver) as (server, wake):
            self.assertEqual(exchange(server, payload=self.data(), token=TOKEN)[0], 401)
            self.brief["projectId"] = "another-project"
            self.assertEqual(exchange(server, payload=self.data())[0], 401)
            self.assertFalse(wake.is_set())
        self.assertEqual(list(self.briefs_dir.glob("*.json")), [])

    def test_identical_retry_is_accepted_without_another_refresh_signal(self):
        with incoming_server(self.cache, self.receiver) as (server, wake):
            self.assertEqual(exchange(server, payload=self.data())[0], 202)
            wake.clear()
            status, _, body = exchange(server, payload=self.data())
            self.assertEqual(status, 202)
            self.assertFalse(json.loads(body)["changed"])
            self.assertFalse(wake.is_set())

    def test_older_or_conflicting_brief_cannot_replace_current_one(self):
        with incoming_server(self.cache, self.receiver) as (server, _):
            self.assertEqual(exchange(server, payload=self.data())[0], 202)
            self.brief["progress"] = 0.9
            self.assertEqual(exchange(server, payload=self.data())[0], 409)
            self.brief["observedAt"] = "2024-01-01T10:30:00Z"
            self.brief["reports"][0]["createdAt"] = self.brief["observedAt"]
            self.assertEqual(exchange(server, payload=self.data())[0], 409)

    def test_bad_payloads_and_content_type_are_rejected_without_publication(self):
        with incoming_server(self.cache, self.receiver) as (server, wake):
            self.assertEqual(exchange(server, payload=b"not-json")[0], 422)
            self.assertEqual(exchange(server, payload=self.data(), headers={"Content-Type": "text/plain"})[0], 415)
            self.assertEqual(exchange(server, payload=b"x" * (256 * 1024 + 1))[0], 413)
            self.assertEqual(exchange(server, payload=self.data(), headers={"Transfer-Encoding": "chunked"})[0], 400)
            self.assertEqual(exchange(server, payload=self.data(), path="/briefs?project=other")[0], 404)
            self.assertFalse(wake.is_set())

    def test_receiver_waits_for_first_known_snapshot(self):
        with incoming_server(bridge.SnapshotCache(), self.receiver) as (server, _):
            self.assertEqual(exchange(server, payload=self.data())[0], 503)

    def test_receiver_endpoint_is_disabled_without_writer_configuration(self):
        with incoming_server(self.cache, None) as (server, _):
            self.assertEqual(exchange(server, payload=self.data())[0], 404)

    def test_worker_coalesces_uploads_with_a_minimum_gap(self):
        stop, wake = Mock(), Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = True
        with patch("orbit_bridge.time.monotonic", return_value=100), patch("orbit_bridge.refresh_once") as refresh:
            bridge.refresh_loop(stop, self.cache, self.directory / "snapshot.json", interval=60,
                                environment_id=None, wake_event=wake)
        refresh.assert_called_once()
        wake.wait.assert_called_once_with(60)
        stop.wait.assert_called_once_with(10)

    def test_invalid_writer_is_rejected_before_waiting_for_request_body(self):
        with incoming_server(self.cache, self.receiver) as (server, _):
            connection = http.client.HTTPConnection(*server.server_address, timeout=1)
            try:
                connection.putrequest("PUT", "/briefs")
                connection.putheader("Authorization", "Bearer invalid")
                connection.putheader("Content-Type", "application/json")
                connection.putheader("Content-Length", "10000")
                connection.endheaders()  # Deliberately never send a body.
                response = connection.getresponse()
                self.assertEqual(response.status, 401)
                response.read()
            finally:
                connection.close()

    def test_global_snapshot_budget_rejects_before_writing_any_brief(self):
        large_task = task(summary="x" * (bridge.MAX_BYTES - 60000))
        base = bridge.build_snapshot([large_task])
        self.cache.publish(bridge.encode_snapshot(base), base)
        self.brief["reports"] = [
            {"id": str(index), "title": "Отчёт", "summary": "s" * 20000,
             "createdAt": self.brief["observedAt"], "highlights": []}
            for index in range(6)
        ]
        with incoming_server(self.cache, self.receiver) as (server, wake):
            self.assertEqual(exchange(server, payload=self.data())[0], 422)
            self.assertFalse(wake.is_set())
        self.assertEqual(list(self.briefs_dir.glob("*.json")), [])
        self.assertEqual(self.cache.read()[0], bridge.encode_snapshot(base))

    def test_budget_includes_already_accepted_briefs_before_worker_refresh(self):
        second = task("second", environment_id="second-environment", summary="short")
        base = bridge.build_snapshot([task(summary="x" * (bridge.MAX_BYTES - 185000)), second])
        second_id = next(project["id"] for project in base["projects"] if project["id"] != self.project_id)
        second_token = "synthetic-second-project-writer-token-123456789"
        self.cache.publish(bridge.encode_snapshot(base), base)
        receiver = BriefReceiver(self.briefs_dir, {self.project_id: WRITER, second_id: second_token},
                                 preflight=bridge.brief_budget_guard(self.cache))
        self.brief["reports"] = [
            {"id": str(index), "title": "Отчёт", "summary": "s" * 20000,
             "createdAt": self.brief["observedAt"], "highlights": []}
            for index in range(6)
        ]
        with incoming_server(self.cache, receiver) as (server, _):
            self.assertEqual(exchange(server, payload=self.data())[0], 202)
            self.brief["projectId"] = second_id
            self.assertEqual(exchange(server, payload=self.data(), token=second_token)[0], 422)
        self.assertEqual(len(list(self.briefs_dir.glob("*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
