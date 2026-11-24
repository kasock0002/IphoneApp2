#!/usr/bin/env python3
"""Send an explicit project brief to Orbit without exposing account credentials.

This standalone client uses only Python's standard library. The write token is
project-scoped and is separate from the iPhone's snapshot read token.
"""

from __future__ import annotations

import argparse
from http.client import HTTPException
import json
import os
from pathlib import Path
import ssl
import stat
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener


MAX_BRIEF_BYTES = 256 * 1024
REQUEST_TIMEOUT = 20


class PublishError(Exception):
    """A safe, user-facing message containing no token, URL or report content."""


class NoRedirects(HTTPRedirectHandler):
    """Never resend a write credential to a redirected endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_endpoint(endpoint: str) -> str:
    if not isinstance(endpoint, str) or not endpoint:
        raise PublishError("Укажите прямой HTTPS-адрес приёма сводок Orbit.")
    if any(ord(char) <= 32 or ord(char) == 127 for char in endpoint):
        raise PublishError("HTTPS-адрес не должен содержать пробелы или управляющие символы.")
    try:
        parts = urlsplit(endpoint)
        port = parts.port
        if (parts.scheme != "https" or not parts.hostname
                or parts.username is not None or parts.password is not None
                or "?" in endpoint or "#" in endpoint
                or port is not None and not 1 <= port <= 65535):
            raise ValueError
    except (TypeError, ValueError):
        raise PublishError("Нужен прямой HTTPS-адрес без логина, пароля, параметров и фрагмента.") from None
    return endpoint


def validate_token(token: str | None) -> str:
    if (not isinstance(token, str) or not 32 <= len(token) <= 512
            or any(not 33 <= ord(char) <= 126 for char in token)):
        raise PublishError("Задайте ORBIT_PROJECT_WRITE_TOKEN: от 32 до 512 печатных ASCII-символов без пробелов. Это отдельный токен проекта Orbit.")
    return token


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PublishError("В JSON-сводке повторяются ключи. Исправьте документ перед отправкой.")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise PublishError("JSON-сводка содержит недопустимое числовое значение.")


def read_brief(path: Path) -> bytes:
    descriptor: int | None = None
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise PublishError("Для отправки нужен обычный JSON-файл сводки.")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PublishError("Для отправки нужен обычный JSON-файл сводки.")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            data = source.read(MAX_BRIEF_BYTES + 1)
        if len(data) > MAX_BRIEF_BYTES:
            raise PublishError("Максимальный размер сводки — 256 КиБ.")
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
        if (not isinstance(value, dict) or not isinstance(value.get("projectId"), str)
                or not value["projectId"].strip() or len(value["projectId"]) > 120):
            raise PublishError("JSON-сводка должна содержать непустой projectId длиной до 120 символов.")
        return data
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise PublishError("Не удалось прочитать JSON-сводку. Проверьте формат файла и права доступа.") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _status_error(status: int) -> PublishError:
    if status in (401, 403):
        return PublishError("Сервис отклонил доступ. Проверьте токен записи и projectId: токен действует только для своего проекта.")
    if status == 409:
        return PublishError("Сервис уже получил более новую или отличающуюся сводку с тем же временем. Проверьте observedAt и содержание перед повторной отправкой.")
    if status == 413:
        return PublishError("Сервис отклонил размер сводки. Максимальный размер — 256 КиБ.")
    if status == 422:
        return PublishError("Сервис отклонил сводку. Проверьте формат и размер итогового снимка по инструкции BRIEFS.md.")
    if status == 503:
        return PublishError("Сервис пока не готов принимать сводки. Нужен успешный опрос Codex и известный ID проекта.")
    if 300 <= status < 400:
        return PublishError("Сервис перенаправляет запрос. Укажите конечный HTTPS-адрес приёма сводок; токен не был перенаправлен.")
    if status == 429:
        return PublishError("Сервис временно ограничил запросы. Повторите отправку позже.")
    return PublishError(f"Сервис не принял сводку: HTTP {status}. Проверьте адрес приёма и состояние сервиса.")


def publish_brief(endpoint: str, path: Path, token: str | None, *, opener=None) -> None:
    """PUT a bounded brief over HTTPS; return only when the service accepts it.

    ``opener`` is an injectable urllib-compatible opener for tests. The default
    uses system certificate verification and refuses every HTTP redirect.
    """
    endpoint = validate_endpoint(endpoint)
    token = validate_token(token)
    payload = read_brief(Path(path))
    request = Request(endpoint, data=payload, method="PUT", headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        client = opener if opener is not None else build_opener(
            HTTPSHandler(context=ssl.create_default_context()), NoRedirects()
        )
        with client.open(request, timeout=REQUEST_TIMEOUT) as response:
            status = response.status
            if status != 202:
                raise _status_error(status)
    except HTTPError as error:
        status = error.code
        error.close()
        raise _status_error(status) from None
    except (URLError, OSError, ValueError, HTTPException):
        raise PublishError("Не удалось отправить сводку по HTTPS. Проверьте сеть, сертификат и доступность сервиса.") from None


class _SafeParser(argparse.ArgumentParser):
    def error(self, _message):
        # Unknown CLI arguments could accidentally include a secret. Do not echo them.
        self.print_usage(sys.stderr)
        self.exit(2, "Ошибка аргументов. Используйте --url и --file; токен передаётся только через ORBIT_PROJECT_WRITE_TOKEN.\n")


def main(argv: list[str] | None = None) -> int:
    parser = _SafeParser(description="Отправить явную сводку проекта в сервис Orbit по HTTPS.")
    parser.add_argument("--url", required=True, help="Прямой HTTPS-адрес приёма, обычно с путём /briefs.")
    parser.add_argument("--file", required=True, type=Path, help="JSON-файл сводки проекта.")
    args = parser.parse_args(argv)
    try:
        publish_brief(args.url, args.file, os.environ.get("ORBIT_PROJECT_WRITE_TOKEN"))
    except PublishError as error:
        print(f"Сводка не отправлена. {error}", file=sys.stderr)
        return 1
    print("Сводка принята сервисом Orbit. Она появится после обновления снимка и приложения.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
