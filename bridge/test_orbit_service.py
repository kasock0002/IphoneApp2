"""Synthetic setup tests; no real Codex account or public service is accessed."""
from contextlib import redirect_stderr, redirect_stdout
import http.client
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import MagicMock, Mock, patch

import orbit_service as service


def snapshot(*identifiers):
    return {"generatedAt": "2026-11-19T10:30:00Z", "projects": [
        {"id": identifier, "name": "Синтетический проект", "subtitle": "Пример",
         "symbol": "cloud", "tintHex": "92F2C6", "stage": None, "progress": None,
         "updatedAt": None, "agents": [], "reports": [], "steps": [],
         "source": "codex-cloud", "cloudTasks": []}
        for identifier in identifiers
    ]}


def fake_response(payload=b'{"ready":true}', status=200):
    response = MagicMock()
    response.__enter__.return_value = response
    response.status = status
    response.read.return_value = payload
    opener = Mock()
    opener.open.return_value = response
    return opener


class ServiceSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state_dir = self.root / "service"
        self.source = self.root / "source.json"
        self.write_snapshot("project-one", "project-two")

    def tearDown(self):
        self.temporary.cleanup()

    def write_snapshot(self, *identifiers):
        self.source.write_text(json.dumps(snapshot(*identifiers)), encoding="utf-8")

    def initialize(self):
        return service.initialize_state(self.state_dir, self.source)[0]

    def test_init_creates_separate_private_tokens_without_cli_access(self):
        with patch("orbit_service.orbit_bridge.fetch_cloud_tasks") as fetch:
            state, created = service.initialize_state(self.state_dir, self.source)
        self.assertTrue(created)
        fetch.assert_not_called()
        self.assertEqual(set(state.writers), {"project-one", "project-two"})
        self.assertEqual(len({state.read_token, *state.writers.values()}), 3)
        self.assertEqual(stat.S_IMODE(self.state_dir.stat().st_mode), 0o700)
        for filename in ["config.json", "writers.json", "snapshot.json"]:
            self.assertEqual(stat.S_IMODE((self.state_dir / filename).stat().st_mode), 0o600)
        config = json.loads((self.state_dir / "config.json").read_text())
        self.assertEqual(set(config), {"version", "readToken"})
        self.assertNotIn(state.read_token, repr(state))

    def test_repeated_init_preserves_tokens_reports_and_skips_source(self):
        state = self.initialize()
        before = (self.state_dir / "writers.json").read_bytes()
        marker = self.state_dir / "briefs" / "keep-me.txt"
        marker.write_text("existing brief marker")
        with patch("orbit_service._source_snapshot", side_effect=AssertionError("must not fetch")):
            again, created = service.initialize_state(self.state_dir, self.root / "missing.json")
        self.assertFalse(created)
        self.assertEqual(state.read_token, again.read_token)
        self.assertEqual(before, (self.state_dir / "writers.json").read_bytes())
        self.assertTrue(marker.exists())

    def test_incomplete_state_is_not_silently_reinitialized(self):
        self.state_dir.mkdir(mode=0o700)
        marker = self.state_dir / "private-remaining-file"
        marker.write_text("keep")
        with self.assertRaises(service.ServiceError):
            service.initialize_state(self.state_dir, self.source)
        self.assertEqual(marker.read_text(), "keep")
        self.assertFalse((self.state_dir / "config.json").exists())

    def test_failed_initialization_does_not_leave_partial_state_or_tokens(self):
        actual_write = service._write_private

        def interrupted(path, data):
            if path.name == "writers.json":
                raise OSError("PRIVATE_IO_DETAIL")
            return actual_write(path, data)

        with patch("orbit_service._write_private", side_effect=interrupted):
            with self.assertRaises(service.ServiceError) as caught:
                self.initialize()
        self.assertNotIn("PRIVATE_IO_DETAIL", str(caught.exception))
        self.assertFalse(self.state_dir.exists())
        self.assertEqual(list(self.root.glob(".orbit-setup-*")), [])

    def test_empty_existing_private_directory_can_be_initialized(self):
        self.state_dir.mkdir(mode=0o700)
        self.assertTrue(service.initialize_state(self.state_dir, self.source)[1])

    def test_source_snapshot_validation_rejects_duplicates_bad_dates_and_oversize(self):
        values = [snapshot("same", "same"), {"generatedAt": "bad", "projects": []},
                  {"generatedAt": "2026-11-19T10:30:00Z", "projects": [{"id": " "}]}]
        for value in values:
            with self.subTest(value=value):
                self.source.write_text(json.dumps(value))
                with self.assertRaises(service.ServiceError):
                    self.initialize()
                self.assertFalse(self.state_dir.exists())
        self.source.write_bytes(b"x" * (service.orbit_bridge.MAX_BYTES + 1))
        with self.assertRaises(service.ServiceError):
            self.initialize()

    def test_non_regular_source_and_state_are_rejected(self):
        link = self.root / "source-link.json"
        link.symlink_to(self.source)
        with self.assertRaises(service.ServiceError):
            service.initialize_state(self.state_dir, link)
        self.state_dir.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(service.ServiceError):
            self.initialize()

    def test_private_state_and_files_cannot_be_world_readable_or_symlinks(self):
        self.initialize()
        for filename in ["config.json", "writers.json"]:
            path = self.state_dir / filename
            path.chmod(0o644)
            with self.assertRaises(service.ServiceError):
                service.load_state(self.state_dir)
            path.chmod(0o600)
            data = path.read_bytes()
            path.unlink()
            target = self.root / ("target-" + filename)
            target.write_bytes(data)
            target.chmod(0o600)
            path.symlink_to(target)
            with self.assertRaises(service.ServiceError):
                service.load_state(self.state_dir)
            path.unlink()
            path.write_bytes(data)
            path.chmod(0o600)
        self.state_dir.chmod(0o755)
        with self.assertRaises(service.ServiceError):
            service.load_state(self.state_dir)

    def test_invalid_state_versions_and_shared_tokens_are_rejected(self):
        state = self.initialize()
        config_path = self.state_dir / "config.json"
        original = config_path.read_bytes()
        for version in [True, 2, "1"]:
            config_path.write_text(json.dumps({"version": version, "readToken": state.read_token}))
            with self.assertRaises(service.ServiceError):
                service.load_state(self.state_dir)
        config_path.write_bytes(original)
        (self.state_dir / "writers.json").write_text(json.dumps({"one": state.read_token}))
        with self.assertRaises(service.ServiceError):
            service.load_state(self.state_dir)

    def test_run_automatically_initializes_via_official_cli_functions(self):
        with patch("orbit_service.orbit_bridge.fetch_cloud_tasks", return_value=[]) as fetch, \
             patch("orbit_service.orbit_bridge.build_snapshot", return_value=snapshot()), \
             patch("orbit_service.orbit_bridge.main", return_value=0) as run:
            self.assertEqual(service.run_service(self.state_dir), 0)
        fetch.assert_called_once_with()
        self.assertNotIn("--writer-config", run.call_args.args[0])
        self.assertTrue((self.state_dir / "writers.json").exists())

    def test_run_passes_token_only_in_environment_and_restores_existing_value(self):
        state = self.initialize()

        def run(arguments):
            self.assertEqual(os.environ["ORBIT_BRIDGE_TOKEN"], state.read_token)
            self.assertNotIn(state.read_token, arguments)
            self.assertFalse(set(state.writers.values()).intersection(arguments))
            self.assertIn("--writer-config", arguments)
            self.assertIn("127.0.0.1", arguments)
            return 7

        with patch.dict(os.environ, {"ORBIT_BRIDGE_TOKEN": "previous-value"}), \
             patch("orbit_service.orbit_bridge.main", side_effect=run):
            self.assertEqual(service.run_service(self.state_dir, port=9876), 7)
            self.assertEqual(os.environ["ORBIT_BRIDGE_TOKEN"], "previous-value")

    def test_run_restores_absent_environment_even_when_bridge_raises(self):
        self.initialize()
        with patch.dict(os.environ):
            os.environ.pop("ORBIT_BRIDGE_TOKEN", None)
            with patch("orbit_service.orbit_bridge.main", side_effect=RuntimeError("synthetic")):
                with self.assertRaises(RuntimeError):
                    service.run_service(self.state_dir)
            self.assertNotIn("ORBIT_BRIDGE_TOKEN", os.environ)

    def test_cli_auth_failure_creates_no_state_and_hides_account_details(self):
        with patch("orbit_service.orbit_bridge.fetch_cloud_tasks",
                   side_effect=service.orbit_bridge.BridgeError("PRIVATE_ACCOUNT_DATA")):
            with self.assertRaises(service.ServiceError) as caught:
                service.initialize_state(self.state_dir)
        self.assertNotIn("PRIVATE_ACCOUNT_DATA", str(caught.exception))
        self.assertIn("codex login", str(caught.exception))
        self.assertFalse(self.state_dir.exists())

    def test_add_project_preserves_read_and_existing_writer_tokens(self):
        self.write_snapshot("one")
        before = self.initialize()
        self.write_snapshot("one", "two")
        self.assertTrue(service.add_project(self.state_dir, "two", self.source))
        after = service.load_state(self.state_dir)
        self.assertEqual(after.read_token, before.read_token)
        self.assertEqual(after.writers["one"], before.writers["one"])
        self.assertEqual(set(after.writers), {"one", "two"})
        self.assertEqual(len({after.read_token, *after.writers.values()}), 3)
        with patch("orbit_service._source_snapshot", side_effect=AssertionError("must not fetch")):
            self.assertFalse(service.add_project(self.state_dir, "two", self.root / "missing.json"))

    def test_unknown_project_and_failed_atomic_update_preserve_writer_map(self):
        self.initialize()
        original = (self.state_dir / "writers.json").read_bytes()
        with self.assertRaises(service.ServiceError):
            service.add_project(self.state_dir, "not-found", self.source)
        self.write_snapshot("new-project")
        with patch("orbit_service._write_private", side_effect=OSError("PRIVATE_IO_DETAIL")):
            with self.assertRaises(OSError):
                service.add_project(self.state_dir, "new-project", self.source)
        self.assertEqual((self.state_dir / "writers.json").read_bytes(), original)

    def test_concurrent_project_additions_do_not_lose_existing_or_new_tokens(self):
        self.write_snapshot("existing")
        original = self.initialize()
        self.write_snapshot("existing", "new-one", "new-two")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda identifier: service.add_project(self.state_dir, identifier, self.source),
                                    ["new-one", "new-two"]))
        self.assertEqual(results, [True, True])
        after = service.load_state(self.state_dir)
        self.assertEqual(set(after.writers), {"existing", "new-one", "new-two"})
        self.assertEqual(after.writers["existing"], original.writers["existing"])
        self.assertEqual(after.read_token, original.read_token)

    def test_project_writer_limit_does_not_rotate_existing_secrets(self):
        self.write_snapshot(*[str(index) for index in range(12)])
        self.initialize()
        original = (self.state_dir / "writers.json").read_bytes()
        self.write_snapshot("thirteen")
        with self.assertRaises(service.ServiceError):
            service.add_project(self.state_dir, "thirteen", self.source)
        self.assertEqual((self.state_dir / "writers.json").read_bytes(), original)

    def test_exported_profile_matches_phone_contract_and_is_private(self):
        state = self.initialize()
        output = self.root / "orbit-connection.json"
        service.export_connection(self.state_dir, "https://example.com/snapshot", output)
        profile = json.loads(output.read_text())
        self.assertEqual(profile, {"format": "orbit-connection", "version": 1,
                                  "endpoint": "https://example.com/snapshot", "token": state.read_token})
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertLess(output.stat().st_size, service.MAX_PROFILE_BYTES)

    def test_profile_rejects_unsafe_endpoints_and_preserves_existing_output(self):
        self.initialize()
        output = self.root / "profile.json"
        output.write_text("previous output")
        for address in ["http://example.com", "https://user:secret@example.com/snapshot",
                        "https://example.com/snapshot?token=secret", "https://example.com/snapshot?",
                        "https://example.com/#", "https://example.com:0/snapshot", "https://example.com:65536/",
                        "https://bad host/snapshot", "https://example.com/\nprivate", "https:///snapshot",
                        "https://exam%20ple.example/snapshot", "https://example.com/non\u00a0breaking-space",
                        "https://exam%0Aple.example/snapshot", "https://example.com/zero\u200bwidth"]:
            with self.subTest(address=address), self.assertRaises(service.ServiceError):
                service.export_connection(self.state_dir, address, output)
            self.assertEqual(output.read_text(), "previous output")

    def test_profile_cannot_overwrite_service_files_or_follow_output_symlink(self):
        self.initialize()
        before = (self.state_dir / "config.json").read_bytes()
        with self.assertRaises(service.ServiceError):
            service.export_connection(self.state_dir, "https://example.com/snapshot", self.state_dir / "config.json")
        self.assertEqual((self.state_dir / "config.json").read_bytes(), before)
        target = self.root / "target.json"
        target.write_text("keep")
        output = self.root / "link.json"
        output.symlink_to(target)
        with self.assertRaises(service.ServiceError):
            service.export_connection(self.state_dir, "https://example.com/snapshot", output)
        self.assertEqual(target.read_text(), "keep")

    def test_oversized_profile_is_not_written(self):
        self.initialize()
        output = self.root / "large-profile.json"
        with self.assertRaises(service.ServiceError):
            service.export_connection(self.state_dir, "https://example.com/" + "x" * service.MAX_PROFILE_BYTES, output)
        self.assertFalse(output.exists())

    def test_profile_cannot_poison_brief_directory_directly_or_through_parent_alias(self):
        self.initialize()
        existing = self.state_dir / "briefs" / "incoming-existing.json"
        existing.write_text("keep existing report")
        existing.chmod(0o600)
        alias = self.root / "briefs-alias"
        alias.symlink_to(self.state_dir / "briefs", target_is_directory=True)
        for output in [self.state_dir / "briefs" / "connection.json", existing,
                       alias / "connection.json", self.state_dir / "nested" / "connection.json"]:
            with self.subTest(output=output), self.assertRaises(service.ServiceError):
                service.export_connection(self.state_dir, "https://example.com/snapshot", output)
        self.assertEqual(existing.read_text(), "keep existing report")
        self.assertEqual(list((self.state_dir / "briefs").iterdir()), [existing])
        self.assertFalse((self.state_dir / "nested").exists())

    def test_status_only_probes_loopback_health_without_authorization(self):
        self.initialize()
        opener = fake_response()
        self.assertTrue(service.service_ready(self.state_dir, port=9876, opener=opener))
        arguments, options = opener.open.call_args
        self.assertEqual(arguments[0].full_url, "http://127.0.0.1:9876/health")
        self.assertFalse(arguments[0].has_header("Authorization"))
        self.assertEqual(options["timeout"], 3)
        self.assertFalse(service.service_ready(self.state_dir, opener=fake_response(b'{"ready":false}')))

    def test_status_rejects_bad_or_private_protocol_responses_safely(self):
        self.initialize()
        for response in [fake_response(b"not-json"), fake_response(b'{"ready":"yes"}'),
                         fake_response(b'{"ready":true,"private":"data"}'), fake_response(status=302),
                         fake_response(b"x" * 4097)]:
            with self.assertRaises(service.ServiceError):
                service.service_ready(self.state_dir, opener=response)
        opener = Mock()
        opener.open.side_effect = http.client.BadStatusLine("PRIVATE_PROTOCOL_LINE")
        with self.assertRaises(service.ServiceError) as caught:
            service.service_ready(self.state_dir, opener=opener)
        self.assertNotIn("PRIVATE_PROTOCOL_LINE", str(caught.exception))
        self.assertIsNone(service._NoRedirects().redirect_request(None, None, 302, "redirect", {}, "https://remote.example"))

    def test_cli_messages_do_not_print_tokens_or_account_details(self):
        out, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(errors):
            self.assertEqual(service.main(["init", "--state", str(self.state_dir), "--snapshot", str(self.source)]), 0)
            self.assertEqual(service.main(["connection", "--state", str(self.state_dir), "--endpoint",
                                           "https://example.com/snapshot", "--output", str(self.root / "phone.json")]), 0)
        state = service.load_state(self.state_dir)
        for token in [state.read_token, *state.writers.values()]:
            self.assertNotIn(token, out.getvalue() + errors.getvalue())
        self.assertNotIn("Синтетический проект", out.getvalue() + errors.getvalue())

    def test_cli_unknown_token_argument_is_not_echoed(self):
        output = io.StringIO()
        with redirect_stderr(output), self.assertRaises(SystemExit):
            service.main(["run", "--token", "PRIVATE_ACCIDENTAL_TOKEN"])
        self.assertNotIn("PRIVATE_ACCIDENTAL_TOKEN", output.getvalue())


if __name__ == "__main__":
    unittest.main()
