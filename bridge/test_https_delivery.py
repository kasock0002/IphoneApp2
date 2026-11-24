"""Real loopback TLS delivery using synthetic tasks and a temporary test CA.

This verifies the Python service path, not URLSession or the iPhone UI. No test
disables certificate verification, contacts Codex, or keeps keys in the source.
"""
from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import orbit_bridge as bridge
from brief_receiver import BriefReceiver
from publish_brief import PublishError, publish_brief
from test_orbit_bridge import task


READ_TOKEN = "synthetic-tls-reader-token-not-a-secret-123456789"
WRITE_TOKEN = "synthetic-tls-project-writer-token-123456789"


class _LocalTLSServer(bridge.OrbitHTTPServer):
    """A test TLS terminator with a bounded handshake, before HTTP handling."""
    def __init__(self, address, token, cache, receiver, wake, tls_context):
        self._tls_context = tls_context
        super().__init__(address, token, cache, receiver, wake)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
        try:
            return self._tls_context.wrap_socket(connection, server_side=True), address
        except BaseException:
            connection.close()
            raise


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required for temporary TLS test certificates")
class HTTPSDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        temporary = tempfile.TemporaryDirectory(prefix="orbit-test-tls-")
        cls.addClassCleanup(temporary.cleanup)
        cls.certificates = Path(temporary.name)
        ca_config = cls.certificates / "ca.cnf"
        ca_config.write_text(
            "[req]\n"
            "distinguished_name=dn\n"
            "prompt=no\n"
            "x509_extensions=ca_extensions\n"
            "[dn]\n"
            "CN=Orbit temporary test CA\n"
            "[ca_extensions]\n"
            "basicConstraints=critical,CA:TRUE\n"
            "keyUsage=critical,keyCertSign,cRLSign\n"
            "subjectKeyIdentifier=hash\n",
            encoding="utf-8",
        )
        server_config = cls.certificates / "server.cnf"
        server_config.write_text(
            "[req]\n"
            "distinguished_name=dn\n"
            "prompt=no\n"
            "[dn]\n"
            "CN=localhost\n"
            "[server_extensions]\n"
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\n"
            "subjectAltName=@alternative_names\n"
            "[alternative_names]\n"
            "DNS.1=localhost\n"
            "IP.1=127.0.0.1\n",
            encoding="utf-8",
        )
        cls.ca = cls.certificates / "ca.pem"
        key = cls.certificates / "server.key"
        certificate = cls.certificates / "server.pem"
        commands = [
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256",
             "-days", "1", "-config", str(ca_config), "-keyout", str(cls.certificates / "ca.key"),
             "-out", str(cls.ca)],
            ["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes", "-sha256",
             "-config", str(server_config), "-keyout", str(key),
             "-out", str(cls.certificates / "server.csr")],
            ["openssl", "x509", "-req", "-sha256", "-days", "1",
             "-in", str(cls.certificates / "server.csr"), "-CA", str(cls.ca),
             "-CAkey", str(cls.certificates / "ca.key"), "-CAcreateserial", "-out", str(certificate),
             "-extfile", str(server_config), "-extensions", "server_extensions"],
        ]
        try:
            for command in commands:
                subprocess.run(command, capture_output=True, check=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError("Temporary TLS certificate generation failed") from None
        cls.server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.server_context.load_cert_chain(certificate, key)

    def wait_for(self, predicate, *, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            # This waits only for a local worker, never for an external service.
            threading.Event().wait(0.02)
        self.fail("The bounded local TLS delivery deadline expired")

    @contextmanager
    def service(self):
        with tempfile.TemporaryDirectory(prefix="orbit-tls-delivery-") as directory:
            root = Path(directory)
            cache = bridge.SnapshotCache()
            base = bridge.build_snapshot([task()])
            identifier = base["projects"][0]["id"]
            briefs = root / "briefs"
            receiver = BriefReceiver(briefs, {identifier: WRITE_TOKEN},
                                      preflight=bridge.brief_budget_guard(cache))
            stop, wake = threading.Event(), threading.Event()
            server = _LocalTLSServer(("127.0.0.1", 0), READ_TOKEN, cache, receiver, wake,
                                     self.server_context)
            http_thread = threading.Thread(target=server.serve_forever,
                                           kwargs={"poll_interval": 0.01}, daemon=True)
            original_refresh = bridge.refresh_once

            def synthetic_refresh(*args, **kwargs):
                return original_refresh(*args, **kwargs, fetch=lambda **_: [task()])

            refresh_thread = threading.Thread(
                target=bridge.refresh_loop,
                args=(stop, cache, root / "snapshot.json"),
                kwargs={"interval": 1, "environment_id": None,
                        "briefs_directory": briefs, "wake_event": wake},
                daemon=True,
            )
            # The real serial refresh loop runs. Only its data source is synthetic;
            # no official CLI process or remote account can be reached by this test.
            with patch("orbit_bridge.refresh_once", side_effect=synthetic_refresh):
                http_thread.start()
                refresh_thread.start()
                try:
                    self.wait_for(lambda: cache.read()[0] is not None)
                    yield server, cache, root, briefs, base, identifier
                finally:
                    stop.set()
                    wake.set()
                    server.shutdown()
                    server.server_close()
                    refresh_thread.join(timeout=3)
                    http_thread.join(timeout=3)
                    self.assertFalse(refresh_thread.is_alive(), "Local refresh worker did not stop")
                    self.assertFalse(http_thread.is_alive(), "Local TLS listener did not stop")

    def document(self, identifier):
        return {
            "projectId": identifier,
            "producer": "Синтетический агент HTTPS",
            "observedAt": "2025-01-01T10:30:00Z",
            "stage": "testing",
            "progress": 0.75,
            "agents": [{"id": "explicit-analyst", "name": "Пример аналитика", "role": "Проверка",
                        "status": "working", "currentTask": "Проверяет тестовый набор."}],
            "reports": [{"id": "tls-report", "title": "Отчёт, переданный по TLS",
                         "summary": "Синтетический наблюдаемый результат.",
                         "createdAt": "2025-01-01T10:25:00Z", "highlights": ["Проверен пример."]}],
            "steps": [{"id": "next", "title": "Просмотреть выводы", "detail": "Следующий тестовый шаг.",
                       "isComplete": False}],
        }

    def snapshot_request(self, server, token):
        context = ssl.create_default_context(cafile=str(self.ca))
        connection = http.client.HTTPSConnection(*server.server_address, timeout=2, context=context)
        try:
            connection.request("GET", "/snapshot", headers={"Authorization": "Bearer " + token})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_trusted_tls_upload_reaches_worker_and_reader_snapshot(self):
        with self.service() as (server, cache, root, _, base, identifier):
            document = self.document(identifier)
            source = root / "project-brief.json"
            source.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            endpoint = f"https://127.0.0.1:{server.server_address[1]}/briefs"
            with patch.dict(os.environ, {"SSL_CERT_FILE": str(self.ca),
                                         "NO_PROXY": "127.0.0.1,localhost",
                                         "no_proxy": "127.0.0.1,localhost"}):
                # Exercise the publisher's default TLS context and real transport.
                publish_brief(endpoint, source, WRITE_TOKEN)
            self.wait_for(lambda: bool(json.loads(cache.read()[0])["projects"][0]["reports"]))
            status, headers, payload = self.snapshot_request(server, READ_TOKEN)
            self.assertEqual(status, 200)
            self.assertEqual(headers["X-Orbit-Stale"], "false")
            received = json.loads(payload)["projects"][0]
            self.assertEqual(received["cloudTasks"], base["projects"][0]["cloudTasks"])
            self.assertEqual(received["reports"], document["reports"])
            self.assertEqual(received["agents"], document["agents"])
            self.assertEqual(len(received["agents"]), 1)  # CLI attempt_total=5 is not an agent count.
            self.assertEqual(received["steps"], document["steps"])
            self.assertEqual(received["briefSource"], document["producer"])
            self.assertEqual(received["briefUpdatedAt"], document["observedAt"])
            self.assertEqual(received["stage"], "testing")
            self.assertEqual(received["progress"], 0.75)
            self.assertEqual(received["source"], "codex-cloud")
            status, _, payload = self.snapshot_request(server, WRITE_TOKEN)
            self.assertEqual(status, 401)
            self.assertNotIn(b"cloudTasks", payload)

    def test_untrusted_certificate_is_rejected_without_storing_a_brief(self):
        with self.service() as (server, _, root, briefs, _, identifier):
            source = root / "project-brief.json"
            source.write_text(json.dumps(self.document(identifier)), encoding="utf-8")
            endpoint = f"https://127.0.0.1:{server.server_address[1]}/briefs"
            with patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost",
                                         "no_proxy": "127.0.0.1,localhost"}):
                # Use ordinary default trust, without the temporary private CA.
                # patch.dict restores inherited certificate settings after the test.
                os.environ.pop("SSL_CERT_FILE", None)
                os.environ.pop("SSL_CERT_DIR", None)
                with self.assertRaises(PublishError) as caught:
                    publish_brief(endpoint, source, WRITE_TOKEN)
            self.assertEqual(list(briefs.glob("*.json")), [])
            self.assertNotIn(WRITE_TOKEN, str(caught.exception))
            self.assertNotIn(endpoint, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
