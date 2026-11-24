"""End-to-end tests of explicit agent briefs entering the existing CLI/HTTP pipeline."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import orbit_bridge as bridge
from test_orbit_bridge import task, running_server, request


class BriefPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.briefs = self.root / "briefs"
        self.briefs.mkdir()
        self.snapshot = bridge.build_snapshot([task()])
        self.project_id = self.snapshot["projects"][0]["id"]
        self.brief = {
            "projectId": self.project_id,
            "observedAt": "2025-01-01T10:30:00Z",
            "producer": "Синтетическая команда для теста",
            "name": "Аналитика · тест",
            "stage": "development",
            "progress": 0.5,
            "agents": [{"id": "analyst", "name": "Аналитик", "role": "Исследование",
                        "status": "working", "currentTask": "Проверяет тестовый набор."}],
            "reports": [{"id": "report", "title": "Тестовая сводка", "summary": "Переданный результат.",
                         "createdAt": "2025-01-01T10:25:00Z", "highlights": ["Проверен набор."]}],
            "steps": [{"id": "next", "title": "Проверить выводы", "detail": "Сверить расчёты.", "isComplete": False}],
        }
        self.write_brief()

    def tearDown(self):
        self.temporary.cleanup()

    def write_brief(self):
        (self.briefs / "analytics.json").write_text(json.dumps(self.brief), encoding="utf-8")

    def test_export_merges_explicit_brief_without_losing_cloud_tasks(self):
        output = self.root / "result.json"
        with patch("orbit_bridge.fetch_cloud_tasks", return_value=[task()]) as fetch:
            result = bridge.main(["export", "--output", str(output), "--briefs-dir", str(self.briefs)])
        self.assertEqual(result, 0)
        fetch.assert_called_once_with(environment_id=None)
        project = json.loads(output.read_text())["projects"][0]
        self.assertEqual(project["name"], self.brief["name"])
        self.assertEqual(project["agents"], self.brief["agents"])
        self.assertEqual(project["reports"], self.brief["reports"])
        self.assertEqual(project["steps"], self.brief["steps"])
        self.assertEqual(project["briefUpdatedAt"], self.brief["observedAt"])
        self.assertEqual(project["briefSource"], self.brief["producer"])
        self.assertEqual(project["cloudTasks"], self.snapshot["projects"][0]["cloudTasks"])
        self.assertEqual(project["source"], "codex-cloud")

    def test_invalid_brief_preserves_previous_file_and_http_snapshot(self):
        output = self.root / "snapshot.json"
        cache = bridge.SnapshotCache()
        self.assertTrue(bridge.refresh_once(cache, output, fetch=lambda **_: [task()],
                                           briefs_directory=self.briefs))
        original = output.read_bytes()
        self.brief["progress"] = True
        self.write_brief()
        with self.assertLogs("orbit-bridge", level="ERROR"):
            self.assertFalse(bridge.refresh_once(cache, output, fetch=lambda **_: [task()],
                                                briefs_directory=self.briefs))
        payload, stale = cache.read()
        self.assertTrue(stale)
        self.assertEqual(payload, original)
        self.assertEqual(output.read_bytes(), original)
        with running_server(cache) as server:
            status, headers, body = request(server)
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Orbit-Stale"], "true")
        self.assertEqual(json.loads(body)["projects"][0]["agents"], self.brief["agents"])

    def test_offline_validation_does_not_call_codex(self):
        source = self.root / "source.json"
        source.write_bytes(bridge.encode_snapshot(self.snapshot))
        output = self.root / "enriched.json"
        with patch("orbit_bridge.fetch_cloud_tasks") as fetch:
            result = bridge.main(["validate-briefs", "--snapshot", str(source),
                                  "--briefs-dir", str(self.briefs), "--output", str(output)])
        self.assertEqual(result, 0)
        fetch.assert_not_called()
        self.assertEqual(json.loads(output.read_bytes())["projects"][0]["progress"], 0.5)

    def test_incorrect_project_mapping_does_not_publish_a_phantom_project(self):
        self.brief["projectId"] = "does-not-match"
        self.write_brief()
        output = self.root / "result.json"
        output.write_bytes(b"previous-snapshot")
        with patch("orbit_bridge.fetch_cloud_tasks", return_value=[task()]), self.assertLogs("orbit-bridge", level="ERROR"):
            result = bridge.main(["export", "--output", str(output), "--briefs-dir", str(self.briefs)])
        self.assertEqual(result, 1)
        self.assertEqual(output.read_bytes(), b"previous-snapshot")

    def test_malformed_offline_snapshot_returns_an_actionable_failure(self):
        source = self.root / "invalid.json"
        for payload in [b"not-json", b"null", b'{"projects":[]}']:
            source.write_bytes(payload)
            with self.assertLogs("orbit-bridge", level="ERROR"):
                result = bridge.main(["validate-briefs", "--snapshot", str(source), "--briefs-dir", str(self.briefs)])
            self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
