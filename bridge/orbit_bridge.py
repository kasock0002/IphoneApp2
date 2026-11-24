#!/usr/bin/env python3
"""Export official Codex CLI cloud tasks as an Orbit point-in-time snapshot.

No private Codex endpoints are called by this module. CLI authentication stays
on its host; the HTTP listener is loopback-only and intended for an HTTPS proxy.
"""

from __future__ import annotations

import argparse
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlsplit

try:
    from .project_briefs import BriefError, enrich_snapshot, merge_brief_documents
except ImportError:
    from project_briefs import BriefError, enrich_snapshot, merge_brief_documents

try:
    from .brief_receiver import (BriefReceiver, AuthError, BriefRejected, BriefConflict,
                                 BriefUnavailable, load_writer_config)
except ImportError:
    from brief_receiver import (BriefReceiver, AuthError, BriefRejected, BriefConflict,
                                BriefUnavailable, load_writer_config)


MAX_PROJECTS = 12
MAX_TASKS_PER_PROJECT = 100
MAX_TASKS = MAX_PROJECTS * MAX_TASKS_PER_PROJECT
MAX_PAGES = 100
MAX_BYTES = 2 * 1024 * 1024
CLI_TIMEOUT = 30
LOG = logging.getLogger("orbit-bridge")
ISO_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


class BridgeError(Exception):
    """An actionable message safe to show locally, without CLI output or tokens."""


def iso_date(value: Any) -> str | None:
    """Accept timestamp strings with an explicit timezone; never guess units."""
    if not isinstance(value, str) or not ISO_PATTERN.fullmatch(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_task_url(value: Any) -> str | None:
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        return None
    try:
        parts = urlsplit(value)
        if (parts.scheme != "https" or parts.hostname not in ("chatgpt.com", "www.chatgpt.com")
                or parts.username is not None or parts.password is not None
                or parts.port not in (None, 443)):
            return None
        return value
    except ValueError:
        return None


def run_cli_page(cursor: str | None, environment_id: str | None) -> dict[str, Any]:
    command = ["codex", "cloud", "list", "--json", "--limit", "20"]
    if cursor is not None:
        command.extend(["--cursor", cursor])
    if environment_id is not None:
        command.extend(["--env", environment_id])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=CLI_TIMEOUT, check=False)
    except FileNotFoundError as error:
        raise BridgeError("Codex CLI не найден. Установите официальный CLI и выполните codex login на этом компьютере.") from error
    except subprocess.TimeoutExpired as error:
        raise BridgeError("Codex CLI не ответил за 30 секунд. Проверьте соединение и повторите обновление.") from error
    except OSError as error:
        raise BridgeError("Не удалось запустить Codex CLI. Проверьте установку и права запуска.") from error
    if result.returncode:
        # CLI stderr may contain account URLs or credentials; never relay it.
        raise BridgeError(f"Codex CLI завершился с кодом {result.returncode}. Проверьте сеть и авторизацию через CLI на этом компьютере.")
    if len(result.stdout.encode("utf-8")) > MAX_BYTES:
        raise BridgeError("Одна страница ответа CLI превышает 2 МБ. Экспорт остановлен без усечения данных.")
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise BridgeError("Codex CLI вернул некорректный JSON. Проверьте актуальную версию CLI.") from error
    if not isinstance(payload, dict):
        raise BridgeError("Изменился формат ответа Codex CLI: ожидался JSON-объект.")
    return payload


def fetch_cloud_tasks(
    *, environment_id: str | None = None, max_pages: int = MAX_PAGES,
    run_page: Callable[[str | None, str | None], dict[str, Any]] = run_cli_page,
    stop_event: threading.Event | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(max_pages, int) or not 1 <= max_pages <= MAX_PAGES:
        raise BridgeError("Лимит страниц должен быть от 1 до 100.")
    cursor: str | None = None
    seen_cursors: set[str] = set()
    seen_ids: set[str] = set()
    tasks: list[dict[str, Any]] = []
    for _ in range(max_pages):
        if stop_event is not None and stop_event.is_set():
            raise BridgeError("Обновление остановлено до завершения; неполный снимок не сохранён.")
        payload = run_page(cursor, environment_id)
        if stop_event is not None and stop_event.is_set():
            raise BridgeError("Обновление остановлено до завершения; неполный снимок не сохранён.")
        if not isinstance(payload, dict) or not isinstance(payload.get("tasks"), list):
            raise BridgeError("Изменился формат Codex CLI: ожидался список tasks.")
        page = payload["tasks"]
        if len(page) > 20:
            raise BridgeError("Ответ CLI превысил запрошенный размер страницы; экспорт остановлен.")
        for task in page:
            task_id = valid_task_id(task)
            if task_id in seen_ids:
                raise BridgeError("CLI повторил идентификатор задачи. Снимок не заменён; повторите обновление.")
            seen_ids.add(task_id)
            tasks.append(task)
            if len(tasks) > MAX_TASKS:
                raise BridgeError("Найдено больше 1200 задач. Выберите окружение флагом --env; данные не усекались.")
        next_cursor = payload.get("cursor")
        if next_cursor is None or next_cursor == "":
            return tasks
        if not isinstance(next_cursor, str) or len(next_cursor) > 8192:
            raise BridgeError("CLI вернул некорректный курсор пагинации.")
        if next_cursor in seen_cursors:
            raise BridgeError("CLI повторил курсор пагинации. Неполный снимок не сохранён.")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise BridgeError("Достигнут лимит страниц. Неполный снимок не сохранён; используйте --env.")


def valid_task_id(task: Any) -> str:
    if not isinstance(task, dict):
        raise BridgeError("CLI вернул запись задачи, которая не является объектом.")
    task_id = task.get("id")
    if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 120:
        raise BridgeError("В ответе CLI отсутствует допустимый идентификатор задачи.")
    return task_id


def build_snapshot(tasks: list[dict[str, Any]], *, generated_at: str | None = None) -> dict[str, Any]:
    timestamp = iso_date(generated_at) if generated_at is not None else utc_now()
    if timestamp is None:
        raise BridgeError("Время снимка должно быть ISO 8601 с часовым поясом.")
    groups: OrderedDict[str | None, dict[str, Any]] = OrderedDict()
    seen_ids: set[str] = set()
    for task in tasks:
        task_id = valid_task_id(task)
        if task_id in seen_ids:
            raise BridgeError("Список содержит повторяющиеся идентификаторы задач.")
        seen_ids.add(task_id)
        raw_environment = task.get("environment_id")
        if raw_environment is not None and not isinstance(raw_environment, str):
            raise BridgeError("Изменился формат environment_id в ответе Codex CLI.")
        environment = raw_environment.strip() if raw_environment else None
        environment = environment or None
        if environment not in groups:
            if len(groups) >= MAX_PROJECTS:
                raise BridgeError("Найдено больше 12 окружений. Выберите окружение через --env; данные не усекались.")
            label = task.get("environment_label")
            name = label.strip() if environment and isinstance(label, str) and label.strip() else (
                "Окружение Codex" if environment else "Облачный Codex"
            )
            if len(name) > 80:
                name = name[:79] + "…"
            group_key = "environment:" + environment if environment else "unassigned"
            groups[environment] = {
                "id": "codex-" + hashlib.sha256(group_key.encode("utf-8")).hexdigest()[:40],
                "name": name,
                "subtitle": "Облачные задачи Codex · группировка по окружению" if environment else "Облачные задачи без указанного окружения",
                "symbol": "cloud",
                "tintHex": "92F2C6",
                "stage": None,
                "progress": None,
                "updatedAt": None,
                "agents": [],
                "reports": [],
                "steps": [],
                "cloudTasks": [],
                "source": "codex-cloud",
            }
        project = groups[environment]
        if len(project["cloudTasks"]) >= MAX_TASKS_PER_PROJECT:
            raise BridgeError("В окружении больше 100 задач. Снимок не заменён: текущий формат Orbit не вмещает все задачи.")
        title = task.get("title")
        title = title.strip() if isinstance(title, str) and title.strip() else "Без названия"
        status = task.get("status")
        status = status if isinstance(status, str) and status.strip() else "unknown"
        if len(title) > 300 or len(status) > 80:
            raise BridgeError("Название задачи длиннее 300 символов или статус длиннее 80. Снимок не заменён; текст не усекался.")
        updated_at = iso_date(task.get("updated_at"))
        project["cloudTasks"].append({
            "id": task_id,
            "title": title,
            "status": status,
            "url": safe_task_url(task.get("url")),
            "updatedAt": updated_at,
            "summary": task["summary"] if isinstance(task.get("summary"), str) else None,
        })
        if updated_at is not None and (project["updatedAt"] is None or updated_at > project["updatedAt"]):
            project["updatedAt"] = updated_at
    projects = list(groups.values())
    for project in projects:
        project["cloudTasks"].sort(key=lambda task: task["updatedAt"] or "", reverse=True)
    projects.sort(key=lambda project: project["updatedAt"] or "", reverse=True)
    snapshot = {"generatedAt": timestamp, "projects": projects}
    encode_snapshot(snapshot)  # Enforce the same file cap before returning to callers.
    return snapshot


def encode_snapshot(snapshot: dict[str, Any]) -> bytes:
    payload = (json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(payload) > MAX_BYTES:
        raise BridgeError("Снимок превышает 2 МБ. Данные не усекались; выберите окружение через --env.")
    return payload


def write_snapshot(path: Path, payload: bytes) -> None:
    """Atomically replace a snapshot only after complete collection and validation."""
    temporary: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".orbit-", suffix=".json", dir=path.parent)
        with os.fdopen(fd, "wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as error:
        raise BridgeError("Не удалось сохранить снимок. Проверьте права на каталог выходного файла.") from error
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


class SnapshotCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._payload: bytes | None = None
        self._base_snapshot: dict[str, Any] | None = None
        self._stale = True

    def publish(self, payload: bytes, base_snapshot: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._payload = payload
            self._base_snapshot = deepcopy(base_snapshot) if base_snapshot is not None else None
            self._stale = False

    def mark_stale(self) -> None:
        with self._lock:
            self._stale = True

    def read(self) -> tuple[bytes | None, bool]:
        with self._lock:
            return self._payload, self._stale

    def base_snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            if self._base_snapshot is not None:
                return deepcopy(self._base_snapshot)
            return json.loads(self._payload) if self._payload is not None else None


def brief_budget_guard(cache: SnapshotCache) -> Callable[[dict[str, dict[str, Any]]], None]:
    def check(candidates: dict[str, dict[str, Any]]) -> None:
        base = cache.base_snapshot()
        if base is None:
            raise BriefUnavailable("Сначала нужен успешный снимок Codex.")
        try:
            encode_snapshot(merge_brief_documents(base, list(candidates.values())))
        except (BridgeError, BriefError, ValueError, TypeError):
            raise BriefRejected("Сводка не помещается в итоговый снимок или содержит неверную привязку проекта.") from None
    return check


def refresh_once(
    cache: SnapshotCache, path: Path, *, environment_id: str | None = None,
    fetch: Callable[..., list[dict[str, Any]]] = fetch_cloud_tasks,
    stop_event: threading.Event | None = None,
    briefs_directory: Path | None = None,
) -> bool:
    try:
        arguments: dict[str, Any] = {"environment_id": environment_id}
        if stop_event is not None:
            arguments["stop_event"] = stop_event
        tasks = fetch(**arguments)
        if stop_event is not None and stop_event.is_set():
            return False
        base_snapshot = build_snapshot(tasks)
        snapshot = enrich_snapshot(base_snapshot, briefs_directory)
        payload = encode_snapshot(snapshot)
        if stop_event is not None and stop_event.is_set():
            return False
        write_snapshot(path, payload)
        cache.publish(payload, base_snapshot)
        LOG.info("Снимок обновлён.")
        return True
    except (BridgeError, BriefError) as error:
        if stop_event is not None and stop_event.is_set():
            return False
        cache.mark_stale()
        LOG.error("Обновление не удалось: %s", error)
        return False
    except Exception:
        # Do not log arbitrary exception data, which could contain CLI credentials.
        cache.mark_stale()
        LOG.error("Обновление не удалось из-за внутренней ошибки; предыдущий снимок сохранён.")
        return False


def refresh_loop(stop: threading.Event, cache: SnapshotCache, path: Path, *, interval: int,
                 environment_id: str | None, briefs_directory: Path | None = None,
                 wake_event: threading.Event | None = None) -> None:
    next_allowed = 0.0
    while not stop.is_set():
        # Coalesce uploads; a writer cannot cause a new CLI poll on every HTTP request.
        delay = max(0.0, next_allowed - time.monotonic())
        if delay and stop.wait(delay):
            return
        if wake_event is not None:
            wake_event.clear()
        refresh_once(cache, path, environment_id=environment_id, stop_event=stop,
                     briefs_directory=briefs_directory)
        next_allowed = time.monotonic() + min(10, interval)
        if wake_event is None:
            if stop.wait(interval):
                return
        else:
            wake_event.wait(interval)


def validate_token(token: str | None) -> str:
    if token is None or not 32 <= len(token) <= 512 or any(not 33 <= ord(char) <= 126 for char in token):
        raise BridgeError("Задайте ORBIT_BRIDGE_TOKEN: от 32 до 512 печатных ASCII-символов без пробелов. Например, создайте его через secrets.token_urlsafe(32).")
    return token


def loopback_host(host: str) -> str:
    if host == "localhost":
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise BridgeError("--host должен быть локальным IPv4-адресом loopback, например 127.0.0.1.") from error
    if not address.is_loopback or address.version != 4:
        raise BridgeError("HTTP bridge слушает только локальный IPv4 loopback. Для iPhone используйте HTTPS reverse proxy.")
    return host


class OrbitHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], token: str, cache: SnapshotCache,
                 receiver: BriefReceiver | None = None,
                 wake_event: threading.Event | None = None) -> None:
        self.token = validate_token(token)
        self.cache = cache
        self.receiver = receiver
        self.wake_event = wake_event
        super().__init__((loopback_host(address[0]), address[1]), OrbitHandler)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(10)
        return request, address


class OrbitHandler(BaseHTTPRequestHandler):
    server: OrbitHTTPServer
    server_version = "OrbitBridge"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *args: Any) -> None:
        # Never log request paths, query strings, Authorization or account data.
        pass

    def send_json(self, status: int, payload: bytes, *, stale: bool | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if stale is not None:
            self.send_header("X-Orbit-Stale", "true" if stale else "false")
        if status == 401:
            self.send_header("WWW-Authenticate", "Bearer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/health":
            payload, _ = self.server.cache.read()
            self.send_json(200, json.dumps({"ready": payload is not None}).encode("utf-8"))
            return
        if self.path != "/snapshot":
            self.send_json(404, b'{"error":"not_found"}')
            return
        headers = self.headers.get_all("Authorization", [])
        authorization = headers[0] if len(headers) == 1 else ""
        expected = "Bearer " + self.server.token
        try:
            authorized = secrets.compare_digest(authorization, expected)
        except TypeError:
            authorized = False
        if not authorized:
            self.send_json(401, b'{"error":"unauthorized"}')
            return
        payload, stale = self.server.cache.read()
        if payload is None:
            self.send_json(503, b'{"error":"snapshot_not_ready"}')
            return
        self.send_json(200, payload, stale=stale)

    def do_POST(self) -> None:
        self.close_connection = True
        self.send_json(405, b'{"error":"method_not_allowed"}')

    def do_PUT(self) -> None:
        self.close_connection = True
        if self.path != "/briefs" or self.server.receiver is None:
            self.send_json(404, b'{"error":"not_found"}')
            return
        headers = self.headers.get_all("Authorization", [])
        authorization = headers[0] if len(headers) == 1 else ""
        try:
            self.server.receiver.authorize(authorization)
        except AuthError:
            self.send_json(401, b'{"error":"unauthorized"}')
            return
        if self.headers.get_all("Transfer-Encoding"):
            self.send_json(400, b'{"error":"content_length_required"}')
            return
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,7}", lengths[0]):
            self.send_json(411, b'{"error":"content_length_required"}')
            return
        length = int(lengths[0])
        if length > 256 * 1024:
            self.send_json(413, b'{"error":"brief_too_large"}')
            return
        if length == 0:
            self.send_json(422, b'{"error":"invalid_brief"}')
            return
        types = self.headers.get_all("Content-Type", [])
        if len(types) != 1 or types[0].split(";", 1)[0].strip().lower() != "application/json":
            self.send_json(415, b'{"error":"json_required"}')
            return
        payload, _ = self.server.cache.read()
        if payload is None:
            self.send_json(503, b'{"error":"snapshot_not_ready"}')
            return
        try:
            known = {project["id"] for project in json.loads(payload)["projects"]}
            data = self.rfile.read(length)
            if len(data) != length:
                self.send_json(400, b'{"error":"incomplete_body"}')
                return
            accepted = self.server.receiver.accept(data, authorization, known)
        except AuthError:
            self.send_json(401, b'{"error":"unauthorized"}')
            return
        except BriefRejected:
            self.send_json(422, b'{"error":"invalid_brief"}')
            return
        except BriefConflict:
            self.send_json(409, b'{"error":"brief_conflict"}')
            return
        except BriefUnavailable:
            self.send_json(503, b'{"error":"brief_storage_unavailable"}')
            return
        except (OSError, ValueError, KeyError, TypeError):
            self.send_json(503, b'{"error":"brief_unavailable"}')
            return
        if accepted.changed and self.server.wake_event is not None:
            self.server.wake_event.set()
        self.send_json(202, json.dumps({"accepted": True, "changed": accepted.changed}).encode("utf-8"))

    do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = do_POST


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Снимки облачных задач Codex для iPhone-приложения Orbit.")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Получить все страницы задач и сохранить JSON-снимок.")
    export.add_argument("--output", required=True, type=Path)
    export.add_argument("--env", dest="environment_id")
    export.add_argument("--briefs-dir", type=Path, help="Каталог сводок, явно переданных агентами.")
    serve = commands.add_parser("serve", help="Обновлять снимок и отдавать его локальному HTTPS-прокси.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--snapshot", type=Path, required=True)
    serve.add_argument("--refresh-seconds", type=int, default=60)
    serve.add_argument("--env", dest="environment_id")
    serve.add_argument("--briefs-dir", type=Path, help="Каталог сводок, явно переданных агентами.")
    serve.add_argument("--writer-config", type=Path, help="JSON с отдельными токенами отправки по projectId.")
    validate = commands.add_parser("validate-briefs", help="Проверить сводки по готовому снимку без входа в Codex.")
    validate.add_argument("--snapshot", type=Path, required=True)
    validate.add_argument("--briefs-dir", type=Path, required=True)
    validate.add_argument("--output", type=Path, help="Сохранить объединённый снимок для просмотра на iPhone.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        if args.command == "export":
            snapshot = build_snapshot(fetch_cloud_tasks(environment_id=args.environment_id))
            payload = encode_snapshot(enrich_snapshot(snapshot, args.briefs_dir))
            write_snapshot(args.output, payload)
            LOG.info("Снимок сохранён. Импортируйте файл в Orbit через «Источник данных».")
            return 0
        if args.command == "validate-briefs":
            try:
                with args.snapshot.open("rb") as source:
                    raw = source.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise BridgeError("Исходный снимок превышает 2 МБ.")
                snapshot = json.loads(raw)
            except (OSError, ValueError, UnicodeError) as error:
                raise BridgeError("Не удалось прочитать исходный JSON-снимок Orbit.") from error
            if (not isinstance(snapshot, dict) or iso_date(snapshot.get("generatedAt")) is None
                    or not isinstance(snapshot.get("projects"), list)
                    or len(snapshot["projects"]) > MAX_PROJECTS
                    or any(not isinstance(project, dict) or not isinstance(project.get("id"), str)
                           or not project["id"].strip() for project in snapshot["projects"])):
                raise BridgeError("Исходный файл должен быть JSON-снимком Orbit с датой и списком проектов.")
            payload = encode_snapshot(enrich_snapshot(snapshot, args.briefs_dir))
            if args.output:
                write_snapshot(args.output, payload)
            LOG.info("Сводки проверены по указанному снимку. Доступ к аккаунту не выполнялся.")
            return 0
        if not 10 <= args.refresh_seconds <= 86400:
            raise BridgeError("--refresh-seconds должен быть от 10 до 86400 секунд.")
        if not 1 <= args.port <= 65535:
            raise BridgeError("--port должен быть от 1 до 65535.")
        token = validate_token(os.environ.get("ORBIT_BRIDGE_TOKEN"))
        receiver = None
        cache = SnapshotCache()
        if args.writer_config is not None:
            if args.briefs_dir is None:
                raise BridgeError("Для приёма сводок укажите --briefs-dir вместе с --writer-config.")
            receiver = BriefReceiver(args.briefs_dir, load_writer_config(args.writer_config, token),
                                     preflight=brief_budget_guard(cache))
        wake = threading.Event()
        server = OrbitHTTPServer((args.host, args.port), token, cache, receiver, wake)
        stop = threading.Event()
        worker = threading.Thread(target=refresh_loop, args=(stop, server.cache, args.snapshot),
                                  kwargs={"interval": args.refresh_seconds, "environment_id": args.environment_id,
                                          "briefs_directory": args.briefs_dir, "wake_event": wake},
                                  name="orbit-refresh", daemon=True)

        def interrupted(_signum, _frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, interrupted)
        worker.start()
        LOG.info("Локальный Orbit bridge запущен. Для подключения iPhone настройте HTTPS reverse proxy.")
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            wake.set()
            server.server_close()
            worker.join(timeout=CLI_TIMEOUT + 2)
        return 0
    except (BridgeError, BriefError, BriefRejected, BriefConflict, BriefUnavailable, AuthError, OSError) as error:
        # OSError may contain host details; use a fixed listener error instead.
        LOG.error("%s", error if isinstance(error, (BridgeError, BriefError, BriefRejected, BriefConflict, BriefUnavailable, AuthError)) else "Не удалось открыть локальный HTTP listener. Проверьте порт и права запуска.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
