"""Behavioral tests for explicit project briefs; no network or real account."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import project_briefs as briefs


NOW = datetime(2026, 11, 15, 10, 30, tzinfo=timezone.utc)


def snapshot():
    return {
        "generatedAt": "2026-11-15T10:30:00Z",
        "projects": [{
            "id": "codex-example-project", "name": "Пример окружения", "subtitle": "Задачи Codex",
            "symbol": "cloud", "tintHex": "92F2C6", "stage": None, "progress": None,
            "updatedAt": "2026-11-15T09:00:00Z", "agents": [], "reports": [], "steps": [],
            "cloudTasks": [{"id": "task-1", "title": "Пример", "status": "source_state"}],
            "source": "codex-cloud",
        }],
    }


def agent(**changes):
    result = {"id": "agent-1", "name": "Пример аналитика", "role": "Аналитик",
              "status": "working", "currentTask": "Проверяет исходные данные."}
    result.update(changes)
    return result


def report(**changes):
    result = {"id": "report-1", "title": "Пример отчёта", "summary": "Проверена структура данных.",
              "createdAt": "2026-11-15T10:10:00Z", "highlights": ["Следующий шаг — сверить расчёты."]}
    result.update(changes)
    return result


def step(**changes):
    result = {"id": "step-1", "title": "Проверить расчёты", "detail": "Сверить контрольные примеры.",
              "isComplete": False}
    result.update(changes)
    return result


def brief(**changes):
    result = {
        "projectId": "codex-example-project", "observedAt": "2026-11-15T10:15:00Z",
        "producer": "Тестовый агент", "name": "Аналитика", "stage": "development", "progress": 0.4,
        "agents": [agent()], "reports": [report()], "steps": [step()],
    }
    result.update(changes)
    return result


class ProjectBriefTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.clock = patch("project_briefs._now", return_value=NOW)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.temporary.cleanup()

    def write(self, value, name="project.json"):
        path = self.directory / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def merge(self, value=None):
        return briefs.enrich_snapshot(snapshot() if value is None else value, self.directory)

    def test_unconfigured_overlay_returns_an_independent_copy(self):
        original = snapshot()
        enriched = briefs.enrich_snapshot(original, None)
        self.assertEqual(enriched, original)
        enriched["projects"][0]["cloudTasks"][0]["title"] = "Changed"
        self.assertEqual(original["projects"][0]["cloudTasks"][0]["title"], "Пример")

    def test_explicit_agent_facts_enrich_project_without_modifying_cloud_tasks(self):
        self.write(brief())
        original = snapshot()
        enriched = self.merge(original)
        project = enriched["projects"][0]
        self.assertEqual(project["name"], "Аналитика")
        self.assertEqual(project["stage"], "development")
        self.assertEqual(project["progress"], 0.4)
        self.assertEqual(project["agents"], [agent()])
        self.assertEqual(project["reports"], [report()])
        self.assertEqual(project["steps"], [step()])
        self.assertEqual(project["briefUpdatedAt"], "2026-11-15T10:15:00Z")
        self.assertEqual(project["briefSource"], "Тестовый агент")
        self.assertEqual(project["updatedAt"], "2026-11-15T10:15:00Z")
        for key in ["id", "tintHex", "source", "cloudTasks"]:
            self.assertEqual(project[key], original["projects"][0][key])
        self.assertEqual(enriched["generatedAt"], original["generatedAt"])
        self.assertEqual(original, snapshot())

    def test_only_present_fields_replace_existing_facts(self):
        original = snapshot()
        original["projects"][0].update({"agents": [agent()], "progress": 0.75, "stage": "testing"})
        self.write({"projectId": "codex-example-project", "observedAt": "2026-11-15T10:15:00Z",
                    "producer": "Проверка", "reports": [report()]})
        project = self.merge(original)["projects"][0]
        self.assertEqual(project["agents"], [agent()])
        self.assertEqual(project["progress"], 0.75)
        self.assertEqual(project["stage"], "testing")
        self.assertEqual(project["reports"], [report()])

    def test_explicit_nulls_and_empty_lists_clear_only_supplied_facts(self):
        self.write(brief(stage=None, progress=None, agents=[], reports=[], steps=[]))
        project = self.merge()["projects"][0]
        self.assertIsNone(project["stage"])
        self.assertIsNone(project["progress"])
        for field in ["agents", "reports", "steps"]:
            self.assertEqual(project[field], [])
        self.assertEqual(len(project["cloudTasks"]), 1)

    def test_brief_timestamp_stays_old_when_cloud_snapshot_is_newer(self):
        original = snapshot()
        original["projects"][0]["updatedAt"] = "2026-11-15T10:25:00Z"
        self.write(brief(observedAt="2026-11-15T10:15:00Z"))
        project = self.merge(original)["projects"][0]
        self.assertEqual(project["updatedAt"], "2026-11-15T10:25:00Z")
        self.assertEqual(project["briefUpdatedAt"], "2026-11-15T10:15:00Z")

    def test_dates_are_compared_with_timezone_and_normalized_to_utc(self):
        self.write(brief(observedAt="2026-11-15T12:15:00.125+02:00", reports=[report(createdAt="2026-11-15T12:10:00+02:00")]))
        project = self.merge()["projects"][0]
        self.assertEqual(project["briefUpdatedAt"], "2026-11-15T10:15:00Z")
        self.assertEqual(project["reports"][0]["createdAt"], "2026-11-15T10:10:00Z")
        self.assertEqual(project["updatedAt"], "2026-11-15T10:15:00Z")

    def test_project_time_can_come_from_brief_when_cloud_time_is_unknown(self):
        original = snapshot()
        original["projects"][0]["updatedAt"] = None
        self.write(brief())
        self.assertEqual(self.merge(original)["projects"][0]["updatedAt"], "2026-11-15T10:15:00Z")

    def test_unmatched_project_is_an_error_without_creating_phantom_projects(self):
        self.write(brief(projectId="private-unknown-id"))
        for original in [snapshot(), {"generatedAt": "2026-11-15T10:30:00Z", "projects": []}]:
            before = deepcopy(original)
            with self.subTest(project_count=len(original["projects"])), self.assertRaises(briefs.BriefError) as error:
                self.merge(original)
            self.assertNotIn("private-unknown-id", str(error.exception))
            self.assertEqual(original, before)

    def test_duplicate_briefs_are_rejected(self):
        self.write(brief(), "one.json")
        self.write(brief(producer="Другой агент"), "two.json")
        with self.assertRaises(briefs.BriefError):
            self.merge()

    def test_missing_directory_file_or_symlink_directory_is_not_empty_success(self):
        file = self.write(brief())
        link = self.directory / "linked-directory"
        link.symlink_to(self.directory, target_is_directory=True)
        for path in [self.directory / "missing", file, link]:
            with self.subTest(path=path.name), self.assertRaises(briefs.BriefError):
                briefs.enrich_snapshot(snapshot(), path)

    def test_symlink_named_json_is_rejected_before_reading_contents(self):
        target = self.write(brief(), "real.txt")
        (self.directory / "link.json").symlink_to(target)
        with self.assertRaises(briefs.BriefError):
            self.merge()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO is a POSIX file type")
    def test_non_regular_file_is_rejected_without_blocking(self):
        os.mkfifo(self.directory / "pipe.json")
        with self.assertRaises(briefs.BriefError):
            self.merge()

    def test_only_visible_json_files_participate_in_overlay(self):
        (self.directory / ".incomplete.json").write_text("not json")
        (self.directory / "agent.json.tmp").write_text("not json")
        self.assertEqual(self.merge(), snapshot())
        self.write(brief(), "filename-does-not-select-project.json")
        self.assertEqual(self.merge()["projects"][0]["name"], "Аналитика")

    def test_directory_and_file_size_caps_reject_without_partial_result(self):
        for index in range(13):
            self.write(brief(), f"{index}.json")
        with self.assertRaises(briefs.BriefError):
            self.merge()
        for path in self.directory.iterdir():
            path.unlink()
        (self.directory / "large.json").write_bytes(b" " * (briefs.MAX_BRIEF_BYTES + 1))
        with self.assertRaises(briefs.BriefError):
            self.merge()

    def test_malformed_json_duplicate_keys_and_non_json_numbers_fail(self):
        for contents in ["not json", '{"projectId":"one","projectId":"two"}',
                         json.dumps(brief(progress=float("nan"))), '{"projectId": NaN}', "[1,2]"]:
            with self.subTest(contents=contents[:30]):
                (self.directory / "project.json").write_text(contents)
                with self.assertRaises(briefs.BriefError):
                    self.merge()

    def test_future_or_ambiguous_timestamps_are_rejected(self):
        for value in ["2026-11-15T10:36:00Z", "2026-11-15T10:15:00", "2026-11-15", 1700000000, "invalid"]:
            with self.subTest(value=value):
                self.write(brief(observedAt=value))
                with self.assertRaises(briefs.BriefError):
                    self.merge()
        self.write(brief(observedAt="2026-11-15T10:35:00Z"))
        self.assertEqual(self.merge()["projects"][0]["briefUpdatedAt"], "2026-11-15T10:35:00Z")

    def test_report_cannot_claim_a_date_after_observation_allowance(self):
        self.write(brief(reports=[report(createdAt="2026-11-15T10:20:01Z")]))
        with self.assertRaises(briefs.BriefError):
            self.merge()

    def test_missing_metadata_unknown_fields_or_metadata_only_are_rejected(self):
        variants = [brief(typo="private-content"), {"projectId": "codex-example-project", "stage": "testing"},
                    {key: value for key, value in brief().items() if key in ["projectId", "observedAt", "producer"]}]
        for value in variants:
            with self.subTest(keys=list(value)):
                self.write(value)
                with self.assertRaises(briefs.BriefError) as error:
                    self.merge()
                self.assertNotIn("private-content", str(error.exception))

    def test_progress_rejects_booleans_nonfinite_out_of_range_and_giant_integers(self):
        for value in [True, False, -0.1, 1.1, "0.5", 10 ** 500]:
            with self.subTest(type=type(value).__name__):
                self.write(brief(progress=value))
                with self.assertRaises(briefs.BriefError):
                    self.merge()

    def test_stage_and_agent_status_must_be_explicit_supported_values(self):
        for value in [brief(stage="finished"), brief(stage={}), brief(agents=[agent(status="running")])]:
            self.write(value)
            with self.assertRaises(briefs.BriefError):
                self.merge()

    def test_duplicate_or_blank_record_ids_and_wrong_boolean_are_rejected(self):
        variants = [brief(agents=[agent(), agent()]), brief(reports=[report(), report()]),
                    brief(steps=[step(), step()]), brief(agents=[agent(id=" \n ")]),
                    brief(steps=[step(isComplete=1)])]
        for value in variants:
            self.write(value)
            with self.assertRaises(briefs.BriefError):
                self.merge()

    def test_nested_unknown_fields_collection_limits_and_string_limits_are_checked(self):
        variants = [brief(agents=[agent(extra="typo")]), brief(agents=[agent(id=str(i)) for i in range(13)]),
                    brief(reports=[report(id=str(i)) for i in range(51)]), brief(steps=[step(id=str(i)) for i in range(51)]),
                    brief(name="x" * 81), brief(producer=" "), brief(subtitle="x" * 301),
                    brief(reports=[report(highlights=["one"] * 51)]), brief(agents=[agent(currentTask="x" * 4001)])]
        for index, value in enumerate(variants):
            with self.subTest(index=index):
                self.write(value)
                with self.assertRaises(briefs.BriefError):
                    self.merge()

    def test_all_briefs_validate_before_caller_can_publish_any_result(self):
        self.write(brief(), "a-valid.json")
        self.write(brief(projectId="wrong"), "z-invalid.json")
        original = snapshot()
        with self.assertRaises(briefs.BriefError):
            self.merge(original)
        self.assertEqual(original, snapshot())


if __name__ == "__main__":
    unittest.main()
