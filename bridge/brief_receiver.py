"""Project-scoped, write-only storage for explicitly produced agent briefs.

The HTTP layer supplies the current set of known projects. This module never
queries Codex, publishes a snapshot, or grants access to other projects.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import threading
from typing import Any, Callable

try:
    from . import project_briefs
except ImportError:
    import project_briefs


MAX_CONFIG_BYTES = 32 * 1024
MAX_INCOMING_BYTES = project_briefs.MAX_BRIEF_BYTES
MAX_WRITERS = project_briefs.MAX_BRIEFS


class AuthError(Exception):
    """A generic authentication/scope failure safe for an HTTP response."""


class BriefRejected(Exception):
    """The submitted document is invalid; no file was written."""


class BriefConflict(Exception):
    """An older or conflicting observation must not replace the current brief."""


class BriefUnavailable(Exception):
    """The receiver's local storage/configuration is unavailable."""


@dataclass(frozen=True)
class Acceptance:
    project_id: str
    observed_at: str
    changed: bool


def _duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _invalid_number(_value: str) -> None:
    raise ValueError("non-finite number")


def _json(data: bytes) -> Any:
    return json.loads(data.decode("utf-8"), object_pairs_hook=_duplicate_keys,
                      parse_constant=_invalid_number)


def _valid_project_id(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 120


def _valid_token(value: Any) -> bool:
    return (isinstance(value, str) and 32 <= len(value) <= 512
            and all(33 <= ord(char) <= 126 for char in value))


def _tokens(value: Any, read_token: str | None = None) -> dict[str, str]:
    if not isinstance(value, dict) or not 1 <= len(value) <= MAX_WRITERS:
        raise BriefUnavailable("Некорректная конфигурация доступа к сводкам.")
    output: dict[str, str] = {}
    used: set[str] = set()
    for identifier, token in value.items():
        if (not _valid_project_id(identifier) or not _valid_token(token)
                or token in used or (read_token is not None and secrets.compare_digest(token, read_token))):
            raise BriefUnavailable("Некорректная конфигурация доступа к сводкам.")
        output[identifier] = token
        used.add(token)
    return output


def _read_regular(path: Path, maximum: int) -> bytes:
    descriptor: int | None = None
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise OSError("regular file required")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0))
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("regular file required")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            data = source.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError("file exceeds cap")
        return data
    finally:
        if descriptor is not None:
            os.close(descriptor)


def load_writer_config(path: Path, read_token: str) -> dict[str, str]:
    """Load an immutable startup map of project ID to a separate writer token."""
    try:
        return _tokens(_json(_read_regular(Path(path), MAX_CONFIG_BYTES)), read_token)
    except BriefUnavailable:
        raise
    except (OSError, ValueError, UnicodeError, RecursionError, TypeError):
        raise BriefUnavailable("Не удалось прочитать конфигурацию доступа к сводкам.") from None


def _validated(value: Any) -> dict[str, Any]:
    return project_briefs.validate_brief(value)


def _timestamp(value: str) -> tuple[str, tuple[datetime, str]]:
    """Keep all source fractional digits while normalizing the UTC whole second.

    datetime stores only microseconds, while ISO input may contain nanoseconds.
    A canonical fraction with trailing zeroes removed compares lexically within
    the same whole second, including fractions shorter than their successors.
    """
    second = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(microsecond=0)
    match = re.search(r"\.(\d+)(?:Z|[+-]\d{2}:\d{2})$", value)
    fraction = match.group(1).rstrip("0") if match else ""
    base = second.isoformat(timespec="seconds").removesuffix("+00:00")
    canonical = base + ("." + fraction if fraction else "") + "Z"
    return canonical, (second, fraction)


def _canonical_brief(value: Any) -> dict[str, Any]:
    normalized = _validated(value)
    # Retain subsecond observation order even though display metadata uses whole
    # seconds. Semantically equivalent timezone spellings become idempotent.
    observed_at = _timestamp(value["observedAt"])[0]
    result = {
        "projectId": normalized["projectId"],
        "observedAt": observed_at,
        "producer": normalized["briefSource"],
    }
    for key in project_briefs.EDITABLE:
        if key in normalized:
            result[key] = normalized[key]
    if "reports" in result:
        for original, canonical in zip(value["reports"], result["reports"]):
            canonical["createdAt"] = _timestamp(original["createdAt"])[0]
    return result


class BriefReceiver:
    def __init__(self, directory: Path, tokens: dict[str, str],
                 preflight: Callable[[dict[str, dict[str, Any]]], None] | None = None) -> None:
        self.directory = Path(directory)
        self._tokens = _tokens(tokens)
        self._preflight = preflight
        self._lock = threading.Lock()
        try:
            if self.directory.is_symlink():
                raise OSError("symlink directory")
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not self.directory.is_dir():
                raise OSError("directory required")
        except OSError:
            raise BriefUnavailable("Каталог приёма сводок недоступен.") from None

    def _authenticate(self, authorization: str) -> str:
        if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
            raise AuthError("Доступ к отправке сводки отклонён.")
        token = authorization[len("Bearer "):]
        if not _valid_token(token):
            raise AuthError("Доступ к отправке сводки отклонён.")
        match: str | None = None
        # Tokens are unique; scan every configured writer without early exit.
        for identifier, expected in self._tokens.items():
            if secrets.compare_digest(token, expected):
                match = identifier
        if match is None:
            raise AuthError("Доступ к отправке сводки отклонён.")
        return match

    def authorize(self, authorization: str) -> str:
        """Authenticate the header before HTTP reads a body or cached snapshot.

        This grants no snapshot read access. accept() still repeats authentication
        and verifies that the document targets this exact known project.
        """
        return self._authenticate(authorization)

    def accept(self, data: bytes, authorization: str, known_project_ids: set[str]) -> Acceptance:
        """Accept an authorized observation; never run CLI or return report data.

        An exact retry is idempotent. Older observations and changed content with
        the same observation time are conflicts. The next bridge refresh merges
        the atomically stored document through the existing overlay validator.
        """
        permitted_project = self.authorize(authorization)
        if not isinstance(data, bytes) or len(data) > MAX_INCOMING_BYTES:
            raise BriefRejected("Сводка должна быть JSON-документом размером до 256 КиБ.")
        try:
            value = _json(data)
        except (ValueError, UnicodeError, RecursionError):
            raise BriefRejected("Некорректный JSON сводки.") from None
        identifier = value.get("projectId") if isinstance(value, dict) else None
        if identifier != permitted_project or permitted_project not in known_project_ids:
            raise AuthError("Доступ к отправке сводки отклонён.")
        try:
            incoming = _canonical_brief(value)
            payload = (json.dumps(incoming, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
            if len(payload) > MAX_INCOMING_BYTES:
                raise ValueError("normalized brief exceeds cap")
        except (project_briefs.BriefError, ValueError, UnicodeError, OverflowError, RecursionError):
            raise BriefRejected("Сводка не соответствует формату Orbit.") from None

        with self._lock:
            path = self.directory / ("incoming-" + hashlib.sha256(identifier.encode("utf-8")).hexdigest() + ".json")
            try:
                if self.directory.is_symlink() or not self.directory.is_dir():
                    raise BriefUnavailable("Каталог приёма сводок недоступен.")
                existing = self._existing_briefs()
                previous = existing.get(identifier)
                if previous is not None:
                    previous_path, previous_brief = previous
                    if previous_path != path:
                        raise BriefConflict("Для проекта уже настроена локальная сводка. Проверьте источник обновлений.")
                    before = _timestamp(previous_brief["observedAt"])[1]
                    after = _timestamp(incoming["observedAt"])[1]
                    if after < before:
                        raise BriefConflict("Более старая сводка не заменяет текущую.")
                    if after == before:
                        if incoming == previous_brief:
                            return Acceptance(identifier, incoming["observedAt"], False)
                        raise BriefConflict("Для этого времени наблюдения уже сохранена другая сводка.")
                elif len(existing) >= project_briefs.MAX_BRIEFS:
                    raise BriefUnavailable("Достигнут лимит сводок проектов.")
                if self._preflight is not None:
                    # The callback sees the complete proposed directory state,
                    # never tokens or report paths. Its mutation cannot change
                    # the already validated bytes that will be persisted.
                    proposed = {key: deepcopy(item[1]) for key, item in existing.items()}
                    proposed[identifier] = deepcopy(incoming)
                    self._preflight(proposed)
                self._write(path, payload)
                return Acceptance(identifier, incoming["observedAt"], True)
            except (BriefConflict, BriefUnavailable, BriefRejected):
                raise
            except (OSError, ValueError, UnicodeError, project_briefs.BriefError, RecursionError):
                raise BriefUnavailable("Не удалось сохранить сводку; проверьте локальный каталог приёма.") from None

    def _existing_briefs(self) -> dict[str, tuple[Path, dict[str, Any]]]:
        output: dict[str, tuple[Path, dict[str, Any]]] = {}
        paths = []
        for path in self.directory.iterdir():
            if not path.name.startswith(".") and path.name.endswith(".json"):
                paths.append(path)
                if len(paths) > project_briefs.MAX_BRIEFS:
                    raise BriefUnavailable("Некорректный каталог приёма сводок.")
        for path in sorted(paths):
            brief = _canonical_brief(_json(_read_regular(path, MAX_INCOMING_BYTES)))
            identifier = brief["projectId"]
            if identifier in output:
                raise BriefUnavailable("Для одного проекта настроено несколько источников сводок.")
            output[identifier] = (path, brief)
        return output

    @staticmethod
    def _write(path: Path, payload: bytes) -> None:
        temporary: str | None = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".incoming-", suffix=".tmp", dir=path.parent)
            with os.fdopen(descriptor, "wb") as target:
                os.fchmod(target.fileno(), 0o600)
                target.write(payload)
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
