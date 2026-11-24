"""Synthetic write-only receiver tests. No Codex account or HTTP is used."""
from __future__ import annotations

import json
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

import brief_receiver as receiver


PROJECT = "test-project-one"
OTHER = "test-project-two"
WRITE_TOKEN = "synthetic-write-token-for-project-one-123456"
OTHER_TOKEN = "synthetic-write-token-for-project-two-654321"
READ_TOKEN = "synthetic-read-only-token-not-write-123456"
AUTH = "Bearer " + WRITE_TOKEN


def brief(**changes):
    value = {
        "projectId": PROJECT,
        "observedAt": "2025-01-01T10:00:00Z",
        "producer": "Синтетический тестовый агент",
        "stage": "development",
        "progress": 0.5,
        "agents": [{"id": "analyst", "name": "Аналитик", "role": "Проверка",
                    "status": "working", "currentTask": "Проверяет тестовые данные."}],
    }
    value.update(changes)
    return value


def payload(value=None):
    return json.dumps(brief() if value is None else value, ensure_ascii=False).encode("utf-8")


class WriterConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "writers.json"

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def test_separate_project_tokens_load_without_modification(self):
        expected = {PROJECT: WRITE_TOKEN, OTHER: OTHER_TOKEN}
        self.write(expected)
        self.assertEqual(receiver.load_writer_config(self.path, READ_TOKEN), expected)

    def test_read_token_cannot_be_reused_as_a_writer_token(self):
        self.write({PROJECT: READ_TOKEN})
        with self.assertRaises(receiver.BriefUnavailable):
            receiver.load_writer_config(self.path, READ_TOKEN)

    def test_duplicate_tokens_cannot_grant_multiple_projects(self):
        self.write({PROJECT: WRITE_TOKEN, OTHER: WRITE_TOKEN})
        with self.assertRaises(receiver.BriefUnavailable):
            receiver.load_writer_config(self.path, READ_TOKEN)

    def test_invalid_tokens_ids_shapes_and_project_count_fail(self):
        invalid = [{}, [], {" ": WRITE_TOKEN}, {"x" * 121: WRITE_TOKEN},
                   {PROJECT: "short"}, {PROJECT: "x" * 513}, {PROJECT: "x " * 32},
                   {PROJECT: "ж" * 40}, {PROJECT: 42},
                   {str(index): f"synthetic-distinct-token-for-project-{index:04d}" for index in range(13)}]
        for value in invalid:
            with self.subTest(value_type=type(value).__name__):
                self.write(value)
                with self.assertRaises(receiver.BriefUnavailable):
                    receiver.load_writer_config(self.path, READ_TOKEN)

    def test_duplicate_json_keys_and_large_or_broken_files_fail(self):
        inputs = [b"not json", b"null", b"\xff", b"x" * (receiver.MAX_CONFIG_BYTES + 1),
                  ('{"'+PROJECT+'":"'+WRITE_TOKEN+'","'+PROJECT+'":"'+OTHER_TOKEN+'"}').encode()]
        for value in inputs:
            with self.subTest(length=len(value)):
                self.path.write_bytes(value)
                with self.assertRaises(receiver.BriefUnavailable):
                    receiver.load_writer_config(self.path, READ_TOKEN)

    def test_missing_symlink_and_non_regular_config_fail_without_target_data_in_error(self):
        with self.assertRaises(receiver.BriefUnavailable):
            receiver.load_writer_config(self.path, READ_TOKEN)
        secret = self.path.parent / "private-secret-file"
        secret.write_text("SENSITIVE_PRIVATE_CONTENT")
        self.path.symlink_to(secret)
        with self.assertRaises(receiver.BriefUnavailable) as caught:
            receiver.load_writer_config(self.path, READ_TOKEN)
        self.assertNotIn("SENSITIVE_PRIVATE_CONTENT", str(caught.exception))
        self.assertNotIn(str(secret), str(caught.exception))
        self.path.unlink()
        self.path.mkdir()
        with self.assertRaises(receiver.BriefUnavailable):
            receiver.load_writer_config(self.path, READ_TOKEN)


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.directory = self.root / "incoming"
        self.receiver = receiver.BriefReceiver(self.directory, {PROJECT: WRITE_TOKEN, OTHER: OTHER_TOKEN})
        self.known = {PROJECT, OTHER}

    def tearDown(self):
        self.temporary.cleanup()

    def send(self, value=None, authorization=AUTH, known=None):
        return self.receiver.accept(payload(value), authorization, self.known if known is None else known)

    def stored(self):
        paths = list(self.directory.glob("*.json"))
        self.assertEqual(len(paths), 1)
        return paths[0], json.loads(paths[0].read_text())

    def test_accepts_known_project_as_private_atomic_overlay_document(self):
        result = self.send()
        path, stored = self.stored()
        self.assertTrue(result.changed)
        self.assertEqual(result.project_id, PROJECT)
        self.assertEqual(result.observed_at, "2025-01-01T10:00:00Z")
        self.assertEqual(stored, brief())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertTrue(path.name.startswith("incoming-"))
        self.assertNotIn(PROJECT, path.name)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_invalid_tokens_are_rejected_before_json_parsing(self):
        headers = ["", WRITE_TOKEN, "Basic " + WRITE_TOKEN, "Bearer " + READ_TOKEN,
                   "Bearer " + "x" * 40, "Bearer " + WRITE_TOKEN + "\n", "Bearer " + "ж" * 40]
        for header in headers:
            with self.subTest(header_kind=header.split(" ")[0]), self.assertRaises(receiver.AuthError):
                self.receiver.accept(b"not json", header, self.known)
        self.assertFalse(list(self.directory.iterdir()))

    def test_public_authorize_checks_project_token_without_reading_storage(self):
        with patch.object(self.receiver, "_existing_briefs") as storage:
            self.assertEqual(self.receiver.authorize(AUTH), PROJECT)
            self.assertEqual(self.receiver.authorize("Bearer " + OTHER_TOKEN), OTHER)
            with self.assertRaises(receiver.AuthError):
                self.receiver.authorize("Bearer " + READ_TOKEN)
        storage.assert_not_called()

    def test_project_writer_cannot_write_other_or_unknown_project(self):
        for document, known in [(brief(projectId=OTHER), self.known),
                                (brief(projectId="unknown"), self.known), (brief(), set())]:
            with self.subTest(project=document["projectId"]), self.assertRaises(receiver.AuthError):
                self.send(document, known=known)
        self.assertFalse(list(self.directory.iterdir()))

    def test_token_configuration_is_copied_at_startup(self):
        config = {PROJECT: WRITE_TOKEN}
        standalone = receiver.BriefReceiver(self.root / "second", config)
        config[PROJECT] = OTHER_TOKEN
        self.assertTrue(standalone.accept(payload(), AUTH, self.known).changed)
        with self.assertRaises(receiver.AuthError):
            standalone.accept(payload(), "Bearer " + OTHER_TOKEN, self.known)

    def test_broken_json_duplicate_keys_and_nonfinite_values_do_not_write(self):
        inputs = [b"not json", b"\xff", b'{"projectId":"'+PROJECT.encode()+b'","projectId":"'+PROJECT.encode()+b'"}',
                  b'{"projectId":"'+PROJECT.encode()+b'","progress":NaN}']
        for value in inputs:
            with self.subTest(length=len(value)), self.assertRaises(receiver.BriefRejected):
                self.receiver.accept(value, AUTH, self.known)
        self.assertFalse(list(self.directory.iterdir()))

    def test_oversized_document_is_rejected(self):
        with self.assertRaises(receiver.BriefRejected):
            self.receiver.accept(b"x" * (receiver.MAX_INCOMING_BYTES + 1), AUTH, self.known)
        self.assertFalse(list(self.directory.iterdir()))

    def test_validation_rejects_future_time_boolean_progress_and_arbitrary_fields(self):
        invalid = [brief(observedAt="2999-01-01T00:00:00Z"), brief(progress=True),
                   brief(progress=1.01), brief(stage="invented"), brief(unknown="private"),
                   brief(agents=[{"id": "incomplete"}])]
        for value in invalid:
            with self.subTest(field=list(value)[-1]), self.assertRaises(receiver.BriefRejected):
                self.send(value)
        self.assertFalse(list(self.directory.iterdir()))

    def test_exact_retry_is_idempotent_without_rewriting(self):
        self.send()
        path, _ = self.stored()
        original = path.read_bytes()
        with patch("brief_receiver.os.replace") as replace:
            retried = self.send(brief(observedAt="2025-01-01T12:00:00+02:00"))
        self.assertFalse(retried.changed)
        replace.assert_not_called()
        self.assertEqual(path.read_bytes(), original)

    def test_older_or_same_time_different_content_cannot_replace_observation(self):
        self.send()
        path, _ = self.stored()
        original = path.read_bytes()
        for value in [brief(observedAt="2025-01-01T09:59:59Z"), brief(progress=0.75)]:
            with self.subTest(time=value["observedAt"]), self.assertRaises(receiver.BriefConflict):
                self.send(value)
        self.assertEqual(path.read_bytes(), original)

    def test_newer_subsecond_observation_is_accepted_and_order_preserved(self):
        first = brief(observedAt="2025-01-01T10:00:00.100Z")
        second = brief(observedAt="2025-01-01T10:00:00.200Z", progress=0.75)
        self.send(first)
        self.assertTrue(self.send(second).changed)
        with self.assertRaises(receiver.BriefConflict):
            self.send(first)
        _, stored = self.stored()
        self.assertEqual(stored["observedAt"], "2025-01-01T10:00:00.2Z")
        self.assertEqual(stored["progress"], 0.75)

    def test_nanosecond_observation_order_is_not_truncated_to_microseconds(self):
        first = brief(observedAt="2025-01-01T10:00:00.1000001Z")
        second = brief(observedAt="2025-01-01T10:00:00.1000009Z", progress=0.75)
        self.send(first)
        self.assertTrue(self.send(second).changed)
        with self.assertRaises(receiver.BriefConflict):
            self.send(first)
        self.assertFalse(self.send(brief(observedAt="2025-01-01T12:00:00.100000900+02:00", progress=0.75)).changed)
        _, stored = self.stored()
        self.assertEqual(stored["observedAt"], second["observedAt"])

    def test_changed_report_subseconds_at_same_observation_are_a_conflict(self):
        record = {"id": "report", "title": "Тест", "summary": "Тест", "highlights": [],
                  "createdAt": "2025-01-01T09:00:00.100Z"}
        self.send(brief(reports=[record]))
        record["createdAt"] = "2025-01-01T09:00:00.200Z"
        with self.assertRaises(receiver.BriefConflict):
            self.send(brief(reports=[record]))

    def test_local_brief_conflict_does_not_create_duplicate_source(self):
        local = self.directory / "operator-owned.json"
        local.write_bytes(payload())
        before = local.read_bytes()
        with self.assertRaises(receiver.BriefConflict):
            self.send(brief(observedAt="2025-01-01T11:00:00Z"))
        self.assertEqual(local.read_bytes(), before)
        self.assertEqual(list(self.directory.glob("*.json")), [local])

    def test_target_symlink_is_rejected_without_touching_link_target(self):
        self.send()
        path, _ = self.stored()
        path.unlink()
        secret = self.root / "outside-secret.json"
        secret.write_bytes(payload())
        path.symlink_to(secret)
        before = secret.read_bytes()
        with self.assertRaises(receiver.BriefUnavailable):
            self.send(brief(observedAt="2025-01-01T11:00:00Z"))
        self.assertEqual(secret.read_bytes(), before)
        self.assertTrue(path.is_symlink())

    def test_arbitrary_project_id_cannot_escape_storage_directory(self):
        dangerous = "../../outside"
        instance = receiver.BriefReceiver(self.directory, {dangerous: WRITE_TOKEN})
        instance.accept(payload(brief(projectId=dangerous)), AUTH, {dangerous})
        paths = list(self.directory.iterdir())
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].parent, self.directory)
        self.assertFalse((self.root.parent / "outside").exists())

    def test_atomic_replace_failure_keeps_previous_file_and_cleans_temporary(self):
        self.send()
        path, _ = self.stored()
        original = path.read_bytes()
        with patch("brief_receiver.os.replace", side_effect=OSError("PRIVATE_PATH")):
            with self.assertRaises(receiver.BriefUnavailable) as caught:
                self.send(brief(observedAt="2025-01-01T11:00:00Z", progress=0.75))
        self.assertNotIn("PRIVATE_PATH", str(caught.exception))
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_preflight_rejection_preserves_previous_file_and_skips_unchanged_retry(self):
        self.send()
        path, _ = self.stored()
        before = path.read_bytes()

        def reject(_proposed):
            raise receiver.BriefRejected("Предложенный снимок превышает лимит.")

        checked = receiver.BriefReceiver(self.directory, {PROJECT: WRITE_TOKEN}, preflight=reject)
        self.assertFalse(checked.accept(payload(), AUTH, self.known).changed)
        with self.assertRaises(receiver.BriefRejected):
            checked.accept(payload(brief(observedAt="2025-01-01T11:00:00Z")), AUTH, self.known)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_preflight_receives_all_existing_and_pending_canonical_briefs(self):
        self.send()
        self.send(brief(projectId=OTHER), authorization="Bearer " + OTHER_TOKEN)
        captured = []

        def inspect(proposed):
            captured.append(json.loads(json.dumps(proposed)))
            # Receiver-owned candidate bytes remain immutable to the callback.
            proposed[PROJECT]["producer"] = "not persisted"

        checked = receiver.BriefReceiver(self.directory, {PROJECT: WRITE_TOKEN, OTHER: OTHER_TOKEN}, preflight=inspect)
        checked.accept(payload(brief(observedAt="2025-01-01T13:00:00+02:00", progress=0.75)), AUTH, self.known)
        self.assertEqual(set(captured[0]), self.known)
        self.assertEqual(captured[0][PROJECT]["observedAt"], "2025-01-01T11:00:00Z")
        self.assertEqual(captured[0][PROJECT]["progress"], 0.75)
        self.assertEqual(captured[0][OTHER]["observedAt"], "2025-01-01T10:00:00Z")
        persisted = [json.loads(path.read_text()) for path in self.directory.glob("*.json")]
        self.assertNotIn("not persisted", [value["producer"] for value in persisted])

    def test_concurrent_same_time_writers_publish_exactly_one_observation(self):
        results, failures = [], []
        barrier = threading.Barrier(2)

        def submit(progress):
            barrier.wait()
            try:
                results.append(self.send(brief(progress=progress)))
            except Exception as error:
                failures.append(error)

        workers = [threading.Thread(target=submit, args=(value,)) for value in [0.25, 0.75]]
        for worker in workers: worker.start()
        for worker in workers: worker.join(timeout=2)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], receiver.BriefConflict)
        _, stored = self.stored()
        self.assertIn(stored["progress"], [0.25, 0.75])

    def test_hidden_temporary_files_are_ignored_but_broken_visible_briefs_block(self):
        (self.directory / ".in-progress.json").write_text("not ready")
        (self.directory / "other.tmp").write_text("not ready")
        self.assertTrue(self.send().changed)
        (self.directory / "bad.json").write_text("PRIVATE BROKEN CONTENT")
        with self.assertRaises(receiver.BriefUnavailable) as caught:
            self.send(brief(observedAt="2025-01-01T11:00:00Z"))
        self.assertNotIn("PRIVATE BROKEN CONTENT", str(caught.exception))

    def test_receiver_refuses_symlink_directory(self):
        alias = self.root / "alias"
        alias.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(receiver.BriefUnavailable):
            receiver.BriefReceiver(alias, {PROJECT: WRITE_TOKEN})


if __name__ == "__main__":
    unittest.main()
