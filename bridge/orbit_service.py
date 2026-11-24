#!/usr/bin/env python3
"""Create a private Orbit service state and launch the existing loopback bridge.

The service owns only Orbit tokens. Codex authentication remains with the
official CLI on this computer; it is never copied into profiles or this state.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
import http.client
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys
import tempfile
import unicodedata
from typing import Any
from urllib.error import URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

try:
    import fcntl
except ImportError:  # The service host is macOS/Linux; keep the error explicit.
    fcntl = None

try:
    from . import orbit_bridge
except ImportError:
    import orbit_bridge


MAX_CONFIG_BYTES = 32 * 1024
MAX_PROFILE_BYTES = 16 * 1024
DEFAULT_STATE = Path(".orbit/service")


class ServiceError(Exception):
    """A public message that does not include credentials or account content."""


@dataclass(frozen=True)
class ServiceState:
    directory: Path
    read_token: str = field(repr=False)
    writers: dict[str, str] = field(repr=False)


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _invalid_constant(_value: str) -> None:
    raise ValueError("non-finite number")


def _json(data: bytes) -> Any:
    return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_keys,
                      parse_constant=_invalid_constant)


def _valid_token(value: Any) -> bool:
    return (isinstance(value, str) and 32 <= len(value) <= 512
            and all(33 <= ord(character) <= 126 for character in value))


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 120


def _owned(info: os.stat_result) -> bool:
    return not hasattr(os, "getuid") or info.st_uid == os.getuid()


def _state_path(directory: Path) -> Path:
    path = Path(directory).expanduser().absolute()
    # Resolve the parent, not the leaf: the state itself must never be a symlink.
    return path.parent.resolve() / path.name


def _check_directory(directory: Path) -> None:
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or not _owned(info)
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise ServiceError("Каталог сервиса должен принадлежать текущему пользователю и быть доступен только ему (0700).")


def _read_file(path: Path, maximum: int, *, private: bool) -> bytes:
    descriptor: int | None = None
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ServiceError("Нужен обычный файл; символьные ссылки не поддерживаются.")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ServiceError("Нужен обычный файл.")
        if private and (not _owned(info) or stat.S_IMODE(info.st_mode) & 0o077):
            raise ServiceError("Файл настроек должен принадлежать текущему пользователю и быть доступен только ему (0600).")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            data = source.read(maximum + 1)
        if len(data) > maximum:
            raise ServiceError("Файл превышает допустимый размер.")
        return data
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_private(path: Path, data: bytes) -> None:
    temporary: str | None = None
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or not _owned(info):
            raise ServiceError("Выходной файл должен быть обычным файлом текущего пользователя.")
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".orbit-", suffix=".tmp", dir=path.parent)
        with os.fdopen(descriptor, "wb") as target:
            os.fchmod(target.fileno(), 0o600)
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _encode(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def load_state(directory: Path) -> ServiceState:
    directory = _state_path(directory)
    try:
        _check_directory(directory)
        config = _json(_read_file(directory / "config.json", MAX_CONFIG_BYTES, private=True))
        writers = _json(_read_file(directory / "writers.json", MAX_CONFIG_BYTES, private=True))
        if (not isinstance(config, dict) or set(config) != {"version", "readToken"}
                or type(config["version"]) is not int or config["version"] != 1
                or not _valid_token(config["readToken"])):
            raise ServiceError("Неизвестный или некорректный формат настроек сервиса.")
        if not isinstance(writers, dict) or len(writers) > orbit_bridge.MAX_PROJECTS:
            raise ServiceError("Некорректный список проектов сервиса.")
        used = {config["readToken"]}
        for identifier, token in writers.items():
            if not _valid_id(identifier) or not _valid_token(token) or token in used:
                raise ServiceError("Некорректные токены проектов сервиса.")
            used.add(token)
        _check_directory(directory / "briefs")
        return ServiceState(directory, config["readToken"], writers)
    except ServiceError:
        raise
    except (OSError, ValueError, UnicodeError, RecursionError, TypeError):
        raise ServiceError("Не удалось прочитать приватные настройки сервиса. Проверьте каталог и права доступа.") from None


def _source_snapshot(snapshot_path: Path | None) -> dict[str, Any]:
    try:
        if snapshot_path is None:
            snapshot = orbit_bridge.build_snapshot(orbit_bridge.fetch_cloud_tasks())
        else:
            snapshot = _json(_read_file(Path(snapshot_path), orbit_bridge.MAX_BYTES, private=False))
        if (not isinstance(snapshot, dict) or orbit_bridge.iso_date(snapshot.get("generatedAt")) is None
                or not isinstance(snapshot.get("projects"), list)
                or len(snapshot["projects"]) > orbit_bridge.MAX_PROJECTS):
            raise ServiceError("Для настройки нужен корректный JSON-снимок Orbit.")
        identifiers = []
        for project in snapshot["projects"]:
            if not isinstance(project, dict) or not _valid_id(project.get("id")):
                raise ServiceError("В снимке отсутствует допустимый ID проекта.")
            identifiers.append(project["id"])
        if len(identifiers) != len(set(identifiers)):
            raise ServiceError("В снимке повторяются ID проектов.")
        orbit_bridge.encode_snapshot(snapshot)
        return snapshot
    except ServiceError:
        raise
    except orbit_bridge.BridgeError:
        raise ServiceError("Не удалось получить снимок Codex. Проверьте официальный CLI, сеть и вход через codex login на этом компьютере.") from None
    except (OSError, ValueError, UnicodeError, RecursionError, TypeError):
        raise ServiceError("Не удалось прочитать исходный JSON-снимок Orbit.") from None


def _new_token(used: set[str]) -> str:
    for _ in range(16):
        token = secrets.token_urlsafe(32)
        if _valid_token(token) and token not in used:
            used.add(token)
            return token
    raise ServiceError("Не удалось создать отдельный токен. Повторите настройку.")


def initialize_state(directory: Path, snapshot_path: Path | None = None) -> tuple[ServiceState, bool]:
    directory = _state_path(directory)
    if directory.exists() or directory.is_symlink():
        _check_directory(directory)
        if any(directory.iterdir()):
            return load_state(directory), False
    snapshot = _source_snapshot(snapshot_path)
    directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging: Path | None = Path(tempfile.mkdtemp(prefix=".orbit-setup-", dir=directory.parent))
    try:
        used: set[str] = set()
        read_token = _new_token(used)
        writers = {project["id"]: _new_token(used) for project in snapshot["projects"]}
        _write_private(staging / "config.json", _encode({"version": 1, "readToken": read_token}))
        _write_private(staging / "writers.json", _encode(writers))
        _write_private(staging / "snapshot.json", orbit_bridge.encode_snapshot(snapshot))
        (staging / "briefs").mkdir(mode=0o700)
        try:
            os.replace(staging, directory)
        except OSError:
            # A simultaneous initializer may already have committed valid state.
            if directory.exists():
                return load_state(directory), False
            raise
        staging = None
        return load_state(directory), True
    except ServiceError:
        raise
    except OSError:
        raise ServiceError("Не удалось сохранить настройки сервиса. Незавершённая настройка не опубликована.") from None
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


@contextmanager
def _state_lock(directory: Path):
    if fcntl is None:
        raise ServiceError("Для изменения проектов требуется хост macOS или Linux с файловой блокировкой.")
    descriptor: int | None = None
    try:
        _check_directory(directory)
        descriptor = os.open(directory / "state.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not _owned(info) or stat.S_IMODE(info.st_mode) & 0o077:
            raise ServiceError("Недоступна приватная блокировка настроек.")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        if descriptor is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def add_project(directory: Path, project_id: str, snapshot_path: Path | None = None) -> bool:
    if not _valid_id(project_id):
        raise ServiceError("Нужен непустой ID проекта длиной до 120 символов.")
    state = load_state(directory)
    if project_id in state.writers:
        return False
    snapshot = _source_snapshot(snapshot_path)
    if project_id not in {project["id"] for project in snapshot["projects"]}:
        raise ServiceError("Проект не найден в указанном снимке. Проверьте ID; доступ не добавлен.")
    with _state_lock(state.directory):
        current = load_state(state.directory)
        if project_id in current.writers:
            return False
        if len(current.writers) >= orbit_bridge.MAX_PROJECTS:
            raise ServiceError("Поддерживается не более 12 проектов записи.")
        writers = dict(current.writers)
        writers[project_id] = _new_token({current.read_token, *writers.values()})
        _write_private(current.directory / "writers.json", _encode(writers))
    return True


def validate_endpoint(endpoint: str) -> str:
    def hidden_character(character: str) -> bool:
        return character.isspace() or unicodedata.category(character) in {"Cc", "Cf"}

    if not isinstance(endpoint, str) or not endpoint or any(hidden_character(char) for char in endpoint):
        raise ServiceError("Укажите прямой HTTPS-адрес снимка без пробелов.")
    try:
        parts = urlsplit(endpoint)
        if (parts.scheme != "https" or not parts.hostname or parts.username is not None
                or parts.password is not None or "?" in endpoint or "#" in endpoint
                or parts.port is not None and not 1 <= parts.port <= 65535
                or any(hidden_character(char) for char in unquote(parts.hostname, errors="strict"))):
            raise ValueError
    except (TypeError, ValueError):
        raise ServiceError("Нужен HTTPS-адрес без логина, пароля, параметров и фрагмента.") from None
    return endpoint


def export_connection(directory: Path, endpoint: str, output: Path) -> None:
    state = load_state(directory)
    endpoint = validate_endpoint(endpoint)
    output = Path(output).expanduser().absolute()
    resolved_output = output.resolve(strict=False)
    resolved_state = state.directory.resolve()
    if resolved_output == resolved_state or resolved_state in resolved_output.parents:
        raise ServiceError("Сохраните профиль подключения вне приватного каталога сервиса.")
    payload = _encode({"format": "orbit-connection", "version": 1, "endpoint": endpoint, "token": state.read_token})
    if len(payload) > MAX_PROFILE_BYTES:
        raise ServiceError("Профиль подключения превышает 16 КиБ.")
    _write_private(output, payload)


def run_service(directory: Path, *, port: int = 8765) -> int:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ServiceError("Порт должен быть числом от 1 до 65535.")
    state, _ = initialize_state(directory)
    arguments = ["serve", "--host", "127.0.0.1", "--port", str(port),
                 "--snapshot", str(state.directory / "snapshot.json"),
                 "--briefs-dir", str(state.directory / "briefs")]
    if state.writers:
        arguments.extend(["--writer-config", str(state.directory / "writers.json")])
    key = "ORBIT_BRIDGE_TOKEN"
    previous = os.environ.get(key)
    os.environ[key] = state.read_token
    try:
        return orbit_bridge.main(arguments)
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def service_ready(directory: Path, *, port: int = 8765, opener=None) -> bool:
    load_state(directory)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ServiceError("Порт должен быть числом от 1 до 65535.")
    request = Request(f"http://127.0.0.1:{port}/health", headers={"Accept": "application/json"})
    try:
        client = opener if opener is not None else build_opener(ProxyHandler({}), _NoRedirects())
        with client.open(request, timeout=3) as response:
            if response.status != 200:
                raise ServiceError("Локальный сервис не ответил корректно.")
            data = response.read(4097)
        if len(data) > 4096:
            raise ServiceError("Некорректный ответ локального сервиса.")
        result = _json(data)
        if not isinstance(result, dict) or set(result) != {"ready"} or type(result["ready"]) is not bool:
            raise ServiceError("Некорректный ответ локального сервиса.")
        return result["ready"]
    except ServiceError:
        raise
    except (URLError, OSError, http.client.HTTPException, ValueError, UnicodeError, RecursionError):
        raise ServiceError("Локальный сервис недоступен. Проверьте запуск и выбранный порт.") from None


class _SafeParser(argparse.ArgumentParser):
    def error(self, _message):
        self.print_usage(sys.stderr)
        self.exit(2, "Ошибка аргументов. Используйте --help; токены не передаются в командной строке.\n")


def main(argv: list[str] | None = None) -> int:
    parser = _SafeParser(description="Приватная настройка и запуск Orbit на компьютере с Codex CLI.")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_SafeParser)
    for name, help_text in [("init", "Создать или проверить приватные настройки."),
                            ("run", "Настроить при первом запуске и запустить сервис."),
                            ("connection", "Создать приватный профиль подключения iPhone."),
                            ("status", "Проверить готовность локального сервиса."),
                            ("add-project", "Добавить запись агента для существующего проекта.")]:
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--state", type=Path, default=DEFAULT_STATE)
        if name in ("init", "add-project"):
            command.add_argument("--snapshot", type=Path)
        if name in ("run", "status"):
            command.add_argument("--port", type=int, default=8765)
        if name == "connection":
            command.add_argument("--endpoint", required=True)
            command.add_argument("--output", type=Path, required=True)
        if name == "add-project":
            command.add_argument("--project-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            _, created = initialize_state(args.state, args.snapshot)
            print("Приватные настройки сервиса созданы." if created else "Существующие настройки проверены; токены сохранены.")
        elif args.command == "run":
            return run_service(args.state, port=args.port)
        elif args.command == "connection":
            export_connection(args.state, args.endpoint, args.output)
            print("Приватный профиль подключения сохранён. Передайте файл на свой iPhone.")
        elif args.command == "add-project":
            changed = add_project(args.state, args.project_id, args.snapshot)
            print("Доступ проекта добавлен. Перезапустите сервис для загрузки нового токена." if changed
                  else "Доступ проекта уже настроен; токены сохранены.")
        else:
            ready = service_ready(args.state, port=args.port)
            print("Сервис готов: успешный снимок получен." if ready
                  else "Сервис запущен, но ещё не получил успешный снимок Codex.")
            return 0 if ready else 2
        return 0
    except ServiceError as error:
        print(f"Orbit: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError):
        print("Orbit: операция не выполнена. Проверьте параметры, файлы и права доступа.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Orbit: операция остановлена.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
