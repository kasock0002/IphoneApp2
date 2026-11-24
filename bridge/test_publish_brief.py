"""Publisher protocol tests use fake openers; no remote account or network calls."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from http.client import BadStatusLine
import io
import json
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

import publish_brief as publisher


TOKEN = "synthetic-project-write-token-0123456789"
ENDPOINT = "https://orbit.example/briefs"


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "brief.json"
        self.payload = json.dumps({
            "projectId": "example-project", "producer": "Тестовый агент",
            "observedAt": "2026-11-15T10:30:00Z", "stage": "development",
        }, ensure_ascii=False).encode("utf-8")
        self.path.write_bytes(self.payload)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def opener(status=202):
        response = MagicMock()
        response.status = status
        response.__enter__.return_value = response
        client = Mock()
        client.open.return_value = response
        return client, response

    def test_put_uses_bearer_json_exact_body_and_bounded_timeout(self):
        client, response = self.opener()
        self.assertIsNone(publisher.publish_brief(ENDPOINT, self.path, TOKEN, opener=client))
        args, kwargs = client.open.call_args
        request = args[0]
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(request.data, self.payload)
        self.assertEqual(kwargs, {"timeout": 20})
        response.read.assert_not_called()

    def test_default_opener_verifies_tls_and_includes_redirect_blocker(self):
        client, _ = self.opener()
        with patch("publish_brief.build_opener", return_value=client) as build:
            publisher.publish_brief(ENDPOINT, self.path, TOKEN)
        handlers = build.call_args.args
        self.assertTrue(any(isinstance(handler, publisher.NoRedirects) for handler in handlers))
        https = next(handler for handler in handlers if isinstance(handler, publisher.HTTPSHandler))
        self.assertEqual(https._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(https._context.check_hostname)

    def test_redirect_handler_never_builds_a_request_for_any_redirect(self):
        blocker = publisher.NoRedirects()
        original = Request(ENDPOINT, data=self.payload, headers={"Authorization": "Bearer " + TOKEN}, method="PUT")
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.assertIsNone(blocker.redirect_request(original, None, status, "redirect", {}, "https://other.example/briefs"))

    def test_invalid_endpoint_is_rejected_before_opening_any_connection(self):
        invalid = [
            "http://orbit.example/briefs", "https:///briefs", "https://user:secret@orbit.example/briefs",
            "https://orbit.example/briefs?token=private", "https://orbit.example/briefs?",
            "https://orbit.example/briefs#fragment", "https://orbit.example/briefs#",
            "https://orbit.example:invalid/briefs", "https://orbit.example:0/briefs",
            "https://orbit.example/\nbriefs", "https://orbit.example/a b", "",
        ]
        for endpoint in invalid:
            client = Mock()
            with self.subTest(endpoint=endpoint), self.assertRaises(publisher.PublishError):
                publisher.publish_brief(endpoint, self.path, TOKEN, opener=client)
            client.open.assert_not_called()

    def test_token_must_be_environment_compatible_visible_ascii(self):
        for token in [None, "", "x" * 31, "x" * 513, "x" * 32 + " ", "x" * 32 + "\n", "x" * 32 + "Я", "x" * 32 + chr(127)]:
            with self.subTest(token_type=type(token).__name__), self.assertRaises(publisher.PublishError):
                publisher.validate_token(token)
        for token in ["x" * 32, "x" * 512, TOKEN]:
            self.assertEqual(publisher.validate_token(token), token)

    def test_oversized_or_missing_file_does_not_open_a_connection(self):
        self.path.write_bytes(b"x" * (publisher.MAX_BRIEF_BYTES + 1))
        client = Mock()
        with self.assertRaisesRegex(publisher.PublishError, "256"):
            publisher.publish_brief(ENDPOINT, self.path, TOKEN, opener=client)
        client.open.assert_not_called()
        self.path.unlink()
        with self.assertRaises(publisher.PublishError):
            publisher.read_brief(self.path)

    def test_invalid_json_duplicate_keys_and_invalid_project_ids_are_rejected(self):
        documents = [b"not json", b"null", b"[]", b'{"projectId":"a","projectId":"b"}',
                     b'{"projectId":"   "}', b'{"projectId":4}', b'{"projectId":"a","progress":NaN}',
                     b"\xff", json.dumps({"projectId": "x" * 121}).encode()]
        for document in documents:
            self.path.write_bytes(document)
            with self.subTest(document_type=len(document)), self.assertRaises(publisher.PublishError):
                publisher.read_brief(self.path)

    def test_non_regular_file_is_rejected(self):
        with self.assertRaises(publisher.PublishError):
            publisher.read_brief(self.root)
        link = self.root / "link.json"
        link.symlink_to(self.path)
        with self.assertRaises(publisher.PublishError):
            publisher.read_brief(link)

    def test_server_statuses_have_safe_actionable_messages_and_never_echo_payloads(self):
        expectations = {401: "токен", 403: "токен", 409: "observedAt", 413: "256", 422: "BRIEFS.md",
                        503: "опрос", 302: "перенаправ", 429: "позже", 404: "HTTP 404"}
        for status, expected in expectations.items():
            error = HTTPError(ENDPOINT + "/PRIVATE_PATH", status, TOKEN, {}, io.BytesIO(b"PRIVATE_REPORT"))
            client = Mock()
            client.open.side_effect = error
            with self.subTest(status=status), self.assertRaises(publisher.PublishError) as caught:
                publisher.publish_brief(ENDPOINT, self.path, TOKEN, opener=client)
            message = str(caught.exception)
            self.assertIn(expected, message)
            for private in (TOKEN, "PRIVATE_PATH", "PRIVATE_REPORT", ENDPOINT):
                self.assertNotIn(private, message)

    def test_only_202_is_successful_even_if_an_opener_returns_other_statuses(self):
        for status in (200, 201, 204, 302, 500):
            client, _ = self.opener(status)
            with self.subTest(status=status), self.assertRaises(publisher.PublishError):
                publisher.publish_brief(ENDPOINT, self.path, TOKEN, opener=client)

    def test_network_errors_never_echo_secret_or_internal_url(self):
        for error in [URLError(TOKEN + ENDPOINT), TimeoutError(TOKEN), OSError(ENDPOINT)]:
            client = Mock()
            client.open.side_effect = error
            with self.subTest(error_type=type(error).__name__), self.assertRaises(publisher.PublishError) as caught:
                publisher.publish_brief(ENDPOINT, self.path, TOKEN, opener=client)
            self.assertNotIn(TOKEN, str(caught.exception))
            self.assertNotIn(ENDPOINT, str(caught.exception))

    def test_protocol_errors_do_not_escape_cli_or_reveal_raw_response_lines(self):
        client = Mock()
        client.open.side_effect = BadStatusLine("PRIVATE_PROTOCOL_LINE " + TOKEN + " " + ENDPOINT)
        stderr = io.StringIO()
        with patch.dict("publish_brief.os.environ", {"ORBIT_PROJECT_WRITE_TOKEN": TOKEN}, clear=True), \
             patch("publish_brief.build_opener", return_value=client), redirect_stderr(stderr):
            result = publisher.main(["--url", ENDPOINT, "--file", str(self.path)])
        self.assertEqual(result, 1)
        self.assertIn("Не удалось отправить", stderr.getvalue())
        for private in ("PRIVATE_PROTOCOL_LINE", TOKEN, ENDPOINT, "Traceback"):
            self.assertNotIn(private, stderr.getvalue())

    def test_cli_reads_token_only_from_environment_and_prints_no_content(self):
        stdout = io.StringIO()
        with patch.dict("publish_brief.os.environ", {"ORBIT_PROJECT_WRITE_TOKEN": TOKEN}, clear=True), \
             patch("publish_brief.publish_brief") as publish, redirect_stdout(stdout):
            result = publisher.main(["--url", ENDPOINT, "--file", str(self.path)])
        self.assertEqual(result, 0)
        publish.assert_called_once_with(ENDPOINT, self.path, TOKEN)
        self.assertIn("принята", stdout.getvalue())
        for private in (TOKEN, ENDPOINT, "example-project", str(self.path)):
            self.assertNotIn(private, stdout.getvalue())

    def test_cli_errors_and_accidental_token_argument_do_not_echo_secrets(self):
        stderr = io.StringIO()
        with patch.dict("publish_brief.os.environ", {}, clear=True), redirect_stderr(stderr):
            result = publisher.main(["--url", ENDPOINT, "--file", str(self.path)])
        self.assertEqual(result, 1)
        self.assertIn("ORBIT_PROJECT_WRITE_TOKEN", stderr.getvalue())
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
            publisher.main(["--url", ENDPOINT, "--file", str(self.path), "--token", TOKEN])
        self.assertEqual(caught.exception.code, 2)
        self.assertNotIn(TOKEN, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
