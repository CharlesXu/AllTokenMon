"""Privacy-safe WorkBuddy detailed JSONL and aggregate SQLite adapter."""

from pathlib import Path
import sqlite3
from typing import Sequence, Tuple

from ..normalize import safe_int, stable_key
from ..schema import (
    AdapterResult,
    AdapterStatus,
    Diagnostic,
    TokenBreakdown,
    UsageRecord,
)
from .amp import _mapping, _provider, _record, _result, _scan, _text, _timestamp
from .base import DiscoveryContext, SourceSpec
from .jsonio import read_json_lines
from .sqliteio import SqliteReadError, open_sqlite_readonly, quote_identifier, sqlite_schema

_RUNTIME = "workbuddy"
_MAX_ROWS = 100_000
_USAGE_COLUMNS = {"session_id", "used", "updated_at"}
_SESSION_COLUMNS = {"id", "model"}


def _first(usage, names, positive=False):
    found = None
    for name in names:
        if name in usage:
            value = safe_int(usage.get(name))
            if found is None:
                found = value
            if not positive or value > 0:
                return value
    return found or 0


def _tokens(value):
    usage = _mapping(value)
    if not usage:
        return None
    tokens = TokenBreakdown(
        _first(usage, (
            "cachedMissTokens", "cacheMissTokens", "input_tokens",
            "inputTokens", "prompt_tokens",
        )),
        _first(usage, (
            "output_tokens", "outputTokens", "completion_tokens",
        )),
        _first(usage, (
            "cache_read_input_tokens", "cacheReadInputTokens", "cacheTokens",
            "prompt_cache_hit_tokens", "cached_tokens",
        ), True),
        _first(usage, (
            "cache_creation_input_tokens", "cacheCreationInputTokens",
            "cachedWriteTokens", "prompt_cache_write_tokens",
        ), True),
        _first(usage, (
            "completion_thinking_tokens", "completionThinkingTokens",
            "reasoningTokens",
        )),
    )
    return tokens if tokens.total or tokens.reasoning else None


def _jsonl_path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    result = read_json_lines(path)
    records = {}
    recognized = False
    for index, value in enumerate(result.values):
        is_message = value.get("type") == "message" and value.get("role") == "assistant"
        is_call = value.get("type") == "function_call"
        if not is_message and not is_call:
            continue
        recognized = True
        if value.get("status") not in (None, "completed"):
            continue
        message = _mapping(value.get("message")) or {}
        provider_data = _mapping(value.get("providerData")) or {}
        usage = None
        for candidate in (
            message.get("usage"),
            provider_data.get("usage"),
            provider_data.get("rawUsage"),
        ):
            if _mapping(candidate) is not None:
                usage = candidate
                break
        tokens = _tokens(usage)
        if tokens is None:
            continue
        model = (
            _text(provider_data.get("model"))
            or _text(provider_data.get("requestModelId"))
            or _text(message.get("model"))
            or _RUNTIME
        )
        session = _text(value.get("sessionId")) or path.stem or "unknown"
        upstream = (
            _text(provider_data.get("messageId"))
            or _text(provider_data.get("traceId"))
            or _text(value.get("id"))
        )
        dedup = (
            "workbuddy:jsonl:{}:{}".format(session, upstream)
            if upstream
            else stable_key(_RUNTIME, "jsonl", path, index)
        )
        record = _record(
            _RUNTIME, path, _provider(model, "tencent"), model, session,
            _timestamp(value.get("timestamp"), path), tokens, dedup,
            source_kind="jsonl",
        )
        existing = records.get(dedup)
        if existing is None or record.tokens.total >= existing.tokens.total:
            records[dedup] = record
    failed = bool(
        result.error_code and result.error_code.startswith("io_error:")
    )
    return tuple(records.values()), recognized, result.partial, failed


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    if path.suffix.lower() == ".jsonl":
        return _jsonl_path(path)
    if path.suffix.lower() != ".db":
        return (), False, False, False
    connection = open_sqlite_readonly(path)
    try:
        schema = sqlite_schema(connection)
        if (
            not _USAGE_COLUMNS.issubset(set(schema.get("session_usage", ())))
            or not _SESSION_COLUMNS.issubset(set(schema.get("sessions", ())))
        ):
            return (), False, False, False
        query = (
            "SELECT su.{session}, su.{used}, su.{updated}, s.{model} "
            "FROM {usage} AS su LEFT JOIN {sessions} AS s "
            "ON s.{id} = su.{session} ORDER BY su.{session}, su.{updated} LIMIT ?"
        ).format(
            session=quote_identifier("session_id"),
            used=quote_identifier("used"),
            updated=quote_identifier("updated_at"),
            model=quote_identifier("model"),
            usage=quote_identifier("session_usage"),
            sessions=quote_identifier("sessions"),
            id=quote_identifier("id"),
        )
        records = []
        partial = False
        for index, (session_value, used, updated_at, model_value) in enumerate(
            connection.execute(query, (_MAX_ROWS + 1,))
        ):
            if index >= _MAX_ROWS:
                partial = True
                break
            session = _text(session_value)
            tokens = safe_int(used)
            timestamp_value = safe_int(updated_at)
            if session is None or tokens == 0 or timestamp_value == 0:
                continue
            model = _text(model_value) or "auto"
            records.append(_record(
                _RUNTIME, path, _provider(model, "workbuddy"), model, session,
                _timestamp(timestamp_value, path), TokenBreakdown(input=tokens),
                "workbuddy:sqlite:{}:{}".format(session, updated_at),
                source_kind="sqlite",
            ))
        return tuple(records), True, partial, False
    except sqlite3.DatabaseError:
        return (), True, False, True
    finally:
        connection.close()


def _detailed_result(paths: Sequence[Path]) -> AdapterResult:
    results = tuple(
        _result(_RUNTIME, (path,), _safe_path)
        for path in sorted(
            {Path(path) for path in paths if Path(path).is_file()},
            key=lambda path: (str(path).casefold(), str(path)),
        )
    )
    records = {}
    for result in results:
        for record in result.records:
            existing = records.get(record.dedup_key)
            if existing is None or record.tokens.total >= existing.tokens.total:
                records[record.dedup_key] = record
    values = tuple(records.values())
    statuses = tuple(result.status for result in results)
    if values:
        status = (
            AdapterStatus.OK
            if all(item is AdapterStatus.OK for item in statuses)
            else AdapterStatus.PARTIAL
        )
    elif AdapterStatus.ERROR in statuses:
        status = AdapterStatus.ERROR
    elif AdapterStatus.PARTIAL in statuses:
        status = AdapterStatus.PARTIAL
    elif AdapterStatus.UNSUPPORTED_FORMAT in statuses:
        status = AdapterStatus.UNSUPPORTED_FORMAT
    else:
        status = AdapterStatus.NO_DATA
    code = {
        AdapterStatus.OK: "ok",
        AdapterStatus.NO_DATA: "no_data",
        AdapterStatus.UNSUPPORTED_FORMAT: "unsupported_format",
        AdapterStatus.PARTIAL: "partial_source",
        AdapterStatus.ERROR: "read_error",
    }[status]
    diagnostic = Diagnostic(
        _RUNTIME,
        status,
        code,
        "workbuddy adapter completed",
        sum(
            item.source_count
            for result in results
            for item in result.diagnostics
        ),
        len(values),
    )
    return AdapterResult(_RUNTIME, status, values, (diagnostic,))


def parse_workbuddy(paths: Sequence[Path]):
    detailed = tuple(path for path in paths if Path(path).suffix.lower() == ".jsonl")
    databases = tuple(path for path in paths if Path(path).suffix.lower() == ".db")
    detailed_result = _detailed_result(detailed)
    if (
        detailed_result.records
        and detailed_result.status is AdapterStatus.OK
    ):
        return detailed_result
    database_result = _result(_RUNTIME, databases, _safe_path)
    if (
        detailed_result.status in (AdapterStatus.PARTIAL, AdapterStatus.ERROR)
        and (detailed_result.records or database_result.records)
    ):
        detailed_sessions = {
            record.session_id for record in detailed_result.records
        }
        unique = {}
        fallback_records = tuple(
            record
            for record in database_result.records
            if record.session_id not in detailed_sessions
        )
        for record in (*detailed_result.records, *fallback_records):
            unique.setdefault(record.dedup_key, record)
        records = tuple(unique.values())
        diagnostic = Diagnostic(
            _RUNTIME,
            AdapterStatus.PARTIAL,
            "partial_source",
            "workbuddy adapter completed",
            sum(item.source_count for item in (
                *detailed_result.diagnostics,
                *database_result.diagnostics,
            )),
            len(records),
        )
        return AdapterResult(
            _RUNTIME,
            AdapterStatus.PARTIAL,
            records,
            (diagnostic,),
        )
    return database_result if databases else detailed_result


def _safe_path(path: Path):
    try:
        return _path(path)
    except (OSError, SqliteReadError):
        return (), False, False, True


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_workbuddy)
