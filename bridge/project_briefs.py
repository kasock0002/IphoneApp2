"""Merge explicit, timestamped project briefs into a Codex cloud snapshot.

This module does not read a Codex account or infer agent activity. A brief is
provided by a separate producer that has actually observed the project.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any


MAX_BRIEF_BYTES = 256 * 1024
MAX_BRIEFS = 12
MAX_CLOCK_SKEW = timedelta(minutes=5)
ISO_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
REQUIRED = {"projectId", "observedAt", "producer"}
EDITABLE = {"name", "subtitle", "symbol", "stage", "progress", "agents", "reports", "steps"}
STAGES = {"discovery", "development", "testing", "delivery"}
STATUSES = {"working", "waiting", "completed"}


class BriefError(Exception):
    """A public error with no report content, account details or path values."""


def _fail(field: str) -> None:
    raise BriefError(f"Некорректное поле сводки: {field}. Предыдущий снимок не заменён.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not ISO_PATTERN.fullmatch(value):
        _fail(field)
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            _fail(field)
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        _fail(field)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: Any, field: str, maximum: int, *, nonblank: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (nonblank and not value.strip()):
        _fail(field)
    return value


def _object(value: Any, field: str, required: set[str], allowed: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or not required.issubset(value):
        _fail(field)
    if not set(value).issubset(required if allowed is None else allowed):
        _fail(field)
    return value


def _records(value: Any, field: str, maximum: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        _fail(field)
    return value


def _record_ids(records: list[Any], field: str) -> None:
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            _fail(field)
        identifier = _text(record.get("id"), f"{field}.id", 120, nonblank=True)
        if identifier in seen:
            _fail(f"{field}.id — повтор идентификатора")
        seen.add(identifier)


def _agents(value: Any) -> list[dict[str, Any]]:
    records = _records(value, "agents", 12)
    _record_ids(records, "agents")
    for record in records:
        _object(record, "agents", {"id", "name", "role", "status", "currentTask"})
        _text(record["name"], "agents.name", 80, nonblank=True)
        _text(record["role"], "agents.role", 160)
        _text(record["currentTask"], "agents.currentTask", 4000)
        if not isinstance(record["status"], str) or record["status"] not in STATUSES:
            _fail("agents.status")
    return deepcopy(records)


def _reports(value: Any, observed_at: datetime) -> list[dict[str, Any]]:
    records = _records(value, "reports", 50)
    _record_ids(records, "reports")
    output = []
    for record in records:
        _object(record, "reports", {"id", "title", "summary", "createdAt", "highlights"})
        _text(record["title"], "reports.title", 300, nonblank=True)
        _text(record["summary"], "reports.summary", 20000)
        created_at = _time(record["createdAt"], "reports.createdAt")
        if created_at > observed_at + MAX_CLOCK_SKEW:
            _fail("reports.createdAt — позже времени наблюдения")
        highlights = _records(record["highlights"], "reports.highlights", 50)
        for highlight in highlights:
            _text(highlight, "reports.highlights", 2000)
        normalized = deepcopy(record)
        normalized["createdAt"] = _iso(created_at)
        output.append(normalized)
    return output


def _steps(value: Any) -> list[dict[str, Any]]:
    records = _records(value, "steps", 50)
    _record_ids(records, "steps")
    for record in records:
        _object(record, "steps", {"id", "title", "detail", "isComplete"})
        _text(record["title"], "steps.title", 300, nonblank=True)
        _text(record["detail"], "steps.detail", 4000)
        if not isinstance(record["isComplete"], bool):
            _fail("steps.isComplete")
    return deepcopy(records)


def _validated_brief(value: Any, now: datetime) -> dict[str, Any]:
    value = _object(value, "brief", REQUIRED, REQUIRED | EDITABLE)
    if not EDITABLE.intersection(value):
        _fail("brief — нет сведений о проекте")
    project_id = _text(value["projectId"], "projectId", 120, nonblank=True)
    producer = _text(value["producer"], "producer", 80, nonblank=True)
    observed_at = _time(value["observedAt"], "observedAt")
    if observed_at > now + MAX_CLOCK_SKEW:
        _fail("observedAt — время в будущем")
    result: dict[str, Any] = {
        "projectId": project_id,
        "briefUpdatedAt": _iso(observed_at),
        "briefSource": producer,
    }
    for name, limit, nonblank in [("name", 80, True), ("subtitle", 300, False), ("symbol", 80, True)]:
        if name in value:
            result[name] = _text(value[name], name, limit, nonblank=nonblank)
    if "stage" in value:
        stage = value["stage"]
        if stage is not None and (not isinstance(stage, str) or stage not in STAGES):
            _fail("stage")
        result["stage"] = stage
    if "progress" in value:
        progress = value["progress"]
        if progress is not None and (
            isinstance(progress, bool) or not isinstance(progress, (float, int))
            or not 0 <= progress <= 1 or (isinstance(progress, float) and not math.isfinite(progress))
        ):
            _fail("progress")
        result["progress"] = progress
    if "agents" in value:
        result["agents"] = _agents(value["agents"])
    if "reports" in value:
        result["reports"] = _reports(value["reports"], observed_at)
    if "steps" in value:
        result["steps"] = _steps(value["steps"])
    return result


def validate_brief(value: Any) -> dict[str, Any]:
    """Validate one received brief with the same rules as local brief files."""
    return _validated_brief(value, _now())


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("JSON — повтор ключа")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    _fail("JSON — недопустимое число")


def _read_brief(path: Path, now: datetime) -> dict[str, Any]:
    descriptor: int | None = None
    try:
        # O_NOFOLLOW closes the stat/open symlink race on supported host platforms.
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            _fail("файл — символьная ссылка")
        if not stat.S_ISREG(mode):
            _fail("файл — требуется обычный JSON-файл")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            _fail("файл — требуется обычный JSON-файл")
        with os.fdopen(descriptor, "rb") as file:
            descriptor = None
            data = file.read(MAX_BRIEF_BYTES + 1)
        if len(data) > MAX_BRIEF_BYTES:
            _fail("файл — больше 256 КиБ")
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys,
                           parse_constant=_reject_constant)
        return _validated_brief(value, now)
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise BriefError("Не удалось прочитать JSON-сводку проекта. Проверьте формат и права доступа; предыдущий снимок не заменён.") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def enrich_snapshot(snapshot: dict[str, Any], briefs_directory: Path | None) -> dict[str, Any]:
    """Return a deep copy enriched only by explicit facts in matching briefs.

    The caller should handle BriefError using its normal stale-snapshot path.
    Unknown project IDs are errors: configured facts must never disappear silently.
    """
    result = deepcopy(snapshot)
    if briefs_directory is None:
        return result
    directory = Path(briefs_directory)
    try:
        if directory.is_symlink() or not directory.is_dir():
            raise BriefError("Каталог сводок отсутствует или недоступен. Предыдущий снимок не заменён.")
        paths = []
        for path in directory.iterdir():
            if not path.name.startswith(".") and path.name.endswith(".json"):
                paths.append(path)
                if len(paths) > MAX_BRIEFS:
                    _fail("каталог — больше 12 сводок")
        paths.sort()
    except OSError:
        raise BriefError("Не удалось прочитать каталог сводок. Предыдущий снимок не заменён.") from None
    now = _now()
    return _merge_validated_briefs(result, [_read_brief(path, now) for path in paths])


def merge_brief_documents(snapshot: dict[str, Any], documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply in-memory documents using the same merge rules as local files."""
    if not isinstance(documents, list) or len(documents) > MAX_BRIEFS:
        _fail("сводки — больше 12 документов")
    now = _now()
    return _merge_validated_briefs(deepcopy(snapshot), [_validated_brief(value, now) for value in documents])


def _merge_validated_briefs(result: dict[str, Any], briefs: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(result, dict) or not isinstance(result.get("projects"), list):
        _fail("снимок — отсутствует список проектов")
    projects: dict[str, dict[str, Any]] = {}
    for project in result["projects"]:
        if not isinstance(project, dict):
            _fail("снимок — некорректный проект")
        identifier = _text(project.get("id"), "снимок.id", 120, nonblank=True)
        if identifier in projects:
            _fail("снимок.id — повтор идентификатора")
        projects[identifier] = project
    seen: set[str] = set()
    for brief in briefs:
        identifier = brief.pop("projectId")
        if identifier in seen:
            _fail("projectId — несколько сводок для одного проекта")
        seen.add(identifier)
        if identifier not in projects:
            raise BriefError("Сводка ссылается на неизвестный проект. Сверьте projectId с экспортом Orbit; предыдущий снимок не заменён.")
        project = projects[identifier]
        observed_at = _time(brief["briefUpdatedAt"], "observedAt")
        previous_time = project.get("updatedAt")
        updated_at = observed_at if previous_time is None else max(_time(previous_time, "снимок.updatedAt"), observed_at)
        project.update(brief)
        project["updatedAt"] = _iso(updated_at)
    return result
