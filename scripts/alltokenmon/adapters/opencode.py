"""Privacy-safe OpenCode SQLite and legacy JSON token adapter."""

import json
import sqlite3
import struct
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence, Tuple

from ..discovery import discover
from ..normalize import parse_timestamp, safe_int, stable_key
from ..schema import (
    AdapterResult,
    AdapterStatus,
    Diagnostic,
    TokenBreakdown,
    UsageRecord,
)
from .base import DiscoveryContext, SourceSpec
from .jsonio import MAX_JSON_BYTES, read_json
from .sqliteio import (
    SqliteReadError,
    open_sqlite_readonly,
    quote_identifier,
    sqlite_schema,
)


_RUNTIME = "opencode"
_MAX_ROWS = 100_000
_FingerprintContext = Tuple[str, str, Optional[str]]
_PROVIDER_ALIASES = {
    "openai_codex": "openai",
    "vertex": "anthropic",
    "vertex_ai": "anthropic",
    "gemini": "google",
    "x_ai": "xai",
    "z_ai": "zai",
}


def _mapping(value: object) -> Optional[Mapping[str, object]]:
    return value if isinstance(value, Mapping) else None


def _text(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _provider(value: object) -> str:
    raw = _text(value)
    if raw is None:
        return "unknown"
    first = raw.rstrip("/").split("/")[0].strip().lower().replace("-", "_")
    if not first or first == "unknown" or any(character.isdigit() for character in first):
        return raw
    return _PROVIDER_ALIASES.get(first, first)


def _fallback_timestamp(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except (OSError, OverflowError, ValueError):
        return datetime.fromtimestamp(0, timezone.utc)


def _timestamp(value: object, path: Path) -> datetime:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value / 1000, timezone.utc)
        except (OSError, OverflowError, ValueError):
            return _fallback_timestamp(path)
    try:
        return parse_timestamp(value)
    except ValueError:
        return _fallback_timestamp(path)


def _cost(value: object) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result > 0 and result < float("inf") else None


def _float_bits(value: object) -> Optional[str]:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return struct.pack("!d", float(value)).hex()
    except (OverflowError, struct.error):
        return None


def _payload_record(
    path: Path,
    payload: Mapping[str, object],
    *,
    row_id: Optional[str],
    row_session_id: Optional[str],
    source_kind: str,
    sqlite_assistant: bool,
) -> Optional[Tuple[UsageRecord, _FingerprintContext]]:
    role = _text(payload.get("role"))
    if not sqlite_assistant and role != "assistant":
        return None
    if sqlite_assistant and role not in (None, "assistant"):
        return None

    nested_model = _mapping(payload.get("model"))
    model = _text(payload.get("modelID")) or (
        _text(nested_model.get("id")) if nested_model else None
    )
    if model is None:
        return None
    provider_raw = _text(payload.get("providerID")) or (
        _text(nested_model.get("providerID")) if nested_model else None
    )
    tokens_value = _mapping(payload.get("tokens"))
    cache = _mapping(tokens_value.get("cache")) if tokens_value else None
    time_value = _mapping(payload.get("time"))
    if tokens_value is None or cache is None or time_value is None:
        return None
    if not all(name in tokens_value for name in ("input", "output")):
        return None
    if not all(name in cache for name in ("read", "write")):
        return None
    created_bits = _float_bits(time_value.get("created"))
    if created_bits is None:
        return None
    completed_value = time_value.get("completed")
    completed_bits = (
        None
        if completed_value is None
        else _float_bits(completed_value)
    )
    if completed_value is not None and completed_bits is None:
        return None

    session_id = (
        row_session_id
        or _text(payload.get("sessionID"))
        or "unknown"
    )
    message_id = _text(payload.get("id"))
    agent = _text(payload.get("mode")) or _text(payload.get("agent")) or ""
    normalized_agent = agent.strip().lower()
    if message_id:
        dedup_key = "opencode:message:" + message_id
    elif row_id:
        dedup_key = "opencode:row:" + row_id + ":agent:" + normalized_agent
    else:
        dedup_key = (
            "opencode:file:"
            + (path.stem or "unknown")
            + ":agent:"
            + normalized_agent
        )
    tokens = TokenBreakdown(
        input=safe_int(tokens_value.get("input")),
        output=safe_int(tokens_value.get("output")),
        cache_read=safe_int(cache.get("read")),
        cache_write=safe_int(cache.get("write")),
        reasoning=safe_int(tokens_value.get("reasoning")),
    )
    provider_cost = _cost(payload.get("cost"))
    return (
        UsageRecord(
            runtime=_RUNTIME,
            provider=_provider(provider_raw),
            model=model,
            session_id=session_id,
            timestamp=_timestamp(time_value.get("created"), path),
            tokens=tokens,
            message_count=1,
            source_kind=source_kind,
            source_path=str(path),
            dedup_key=dedup_key,
            confidence="exact",
            cost=provider_cost,
            cost_source="provider_reported" if provider_cost is not None else None,
        ),
        (normalized_agent, created_bits, completed_bits),
    )


def _fingerprint(
    record: UsageRecord,
    context: _FingerprintContext,
) -> str:
    agent, created_bits, completed_bits = context
    return stable_key(
        _RUNTIME,
        created_bits,
        completed_bits,
        record.provider,
        record.model,
        record.tokens.input,
        record.tokens.output,
        record.tokens.cache_read,
        record.tokens.cache_write,
        record.tokens.reasoning,
        record.cost or 0,
        agent,
    )


def _sqlite_records(
    path: Path,
) -> Tuple[
    Tuple[Tuple[UsageRecord, _FingerprintContext], ...],
    bool,
    bool,
    bool,
]:
    """Return records, recognized, partial, and resource-limit flags."""
    records = []
    partial = False
    resource_limited = False
    stop_scanning = False
    payload_bytes = 0
    row_count = 0
    connection = open_sqlite_readonly(path)
    try:
        schema = sqlite_schema(connection)
        variants = []
        for table, assistant_column in (
            ("session_message", "type"),
            ("message", None),
        ):
            columns = set(schema.get(table, ()))
            required = {"id", "session_id", "data"}
            if not required.issubset(columns):
                continue
            if assistant_column and assistant_column not in columns:
                continue
            variants.append((table, assistant_column))
        if not variants:
            return (), False, False, False

        for table, assistant_column in variants:
            id_column = quote_identifier("id")
            session_column = quote_identifier("session_id")
            data_column = quote_identifier("data")
            byte_length = (
                "length(CAST(" + data_column + " AS BLOB))"
            )
            query = (
                "SELECT "
                + id_column
                + ", "
                + session_column
                + ", "
                + byte_length
                + " FROM "
                + quote_identifier(table)
            )
            parameters = ()
            if assistant_column:
                query += " WHERE " + quote_identifier(assistant_column) + " = ?"
                parameters = ("assistant",)
            query += " ORDER BY " + id_column + " LIMIT ?"
            parameters += (max(_MAX_ROWS - row_count, 0) + 1,)
            try:
                rows = connection.execute(query, parameters)
                for row_id, session_id, data_bytes in rows:
                    if row_count >= _MAX_ROWS:
                        partial = True
                        resource_limited = True
                        stop_scanning = True
                        break
                    row_count += 1
                    if (
                        type(data_bytes) is not int
                        or data_bytes < 0
                    ):
                        partial = True
                        continue
                    if data_bytes > MAX_JSON_BYTES:
                        partial = True
                        resource_limited = True
                        continue
                    if payload_bytes + data_bytes > MAX_JSON_BYTES:
                        partial = True
                        resource_limited = True
                        stop_scanning = True
                        break
                    payload_bytes += data_bytes

                    data_query = (
                        "SELECT "
                        + data_column
                        + " FROM "
                        + quote_identifier(table)
                        + " WHERE "
                        + id_column
                        + " IS ? AND "
                        + session_column
                        + " IS ?"
                    )
                    data_parameters = (row_id, session_id)
                    if assistant_column:
                        data_query += (
                            " AND "
                            + quote_identifier(assistant_column)
                            + " = ?"
                        )
                        data_parameters += ("assistant",)
                    data_query += (
                        " AND "
                        + byte_length
                        + " = ? AND "
                        + byte_length
                        + " <= ? LIMIT 1"
                    )
                    data_parameters += (data_bytes, MAX_JSON_BYTES)
                    data_row = connection.execute(
                        data_query, data_parameters
                    ).fetchone()
                    if data_row is None or not isinstance(data_row[0], str):
                        partial = True
                        continue
                    data = data_row[0]
                    try:
                        payload = json.loads(data)
                    except (TypeError, ValueError):
                        partial = True
                        continue
                    if not isinstance(payload, Mapping):
                        partial = True
                        continue
                    candidate = _payload_record(
                        path,
                        payload,
                        row_id=_text(row_id),
                        row_session_id=_text(session_id),
                        source_kind="sqlite",
                        sqlite_assistant=assistant_column is not None,
                    )
                    if candidate is not None:
                        records.append(candidate)
            except sqlite3.DatabaseError:
                partial = True
                continue
            if stop_scanning:
                break
    finally:
        connection.close()
    return tuple(records), True, partial, resource_limited


def _diagnostic(
    status: AdapterStatus, code: str, source_count: int, record_count: int
) -> Diagnostic:
    return Diagnostic(
        runtime=_RUNTIME,
        status=status,
        code=code,
        message="OpenCode adapter completed",
        source_count=source_count,
        record_count=record_count,
    )


def parse_opencode(paths: Sequence[Path]) -> AdapterResult:
    """Parse OpenCode databases before legacy files with logical deduplication."""
    existing = []
    for value in paths:
        path = Path(value)
        if path.is_file():
            existing.append(path)
    existing.sort(key=lambda path: (path.suffix != ".db", str(path).casefold(), str(path)))

    candidates = []
    recognized = False
    partial = False
    resource_limited = False
    read_error = False
    unsupported_source = False
    for path in existing:
        if path.suffix == ".db":
            try:
                (
                    records,
                    known,
                    incomplete,
                    source_resource_limited,
                ) = _sqlite_records(path)
            except (SqliteReadError, OSError):
                read_error = True
                continue
            recognized = recognized or known
            unsupported_source = unsupported_source or not known
            partial = partial or incomplete
            resource_limited = (
                resource_limited or source_resource_limited
            )
            candidates.extend(records)
            continue

        result = read_json(path)
        if result.error_code is not None:
            read_error = read_error or result.error_code.startswith("io_error:")
            unsupported_source = True
            continue
        if not isinstance(result.value, Mapping):
            unsupported_source = True
            continue
        if "role" in result.value:
            recognized = True
        candidate = _payload_record(
            path,
            result.value,
            row_id=None,
            row_session_id=None,
            source_kind="json",
            sqlite_assistant=False,
        )
        if candidate is not None:
            candidates.append(candidate)
        elif "role" not in result.value:
            unsupported_source = True

    accumulated = []
    explicit_ids = []
    fingerprint_indices: Dict[str, list] = {}
    for record, context in candidates:
        fingerprint = _fingerprint(record, context)
        incoming_id = (
            record.dedup_key.removeprefix("opencode:message:")
            if record.dedup_key.startswith("opencode:message:")
            else None
        )
        match_index = None
        for index in fingerprint_indices.get(fingerprint, ()):
            existing_id = explicit_ids[index]
            if (
                existing_id is None
                or incoming_id is None
                or existing_id == incoming_id
            ):
                match_index = index
                break
        if match_index is not None:
            if incoming_id is not None and explicit_ids[match_index] is None:
                accumulated[match_index] = replace(
                    accumulated[match_index],
                    dedup_key=record.dedup_key,
                )
                explicit_ids[match_index] = incoming_id
            continue
        fingerprint_indices.setdefault(fingerprint, []).append(
            len(accumulated)
        )
        accumulated.append(record)
        explicit_ids.append(incoming_id)

    by_key: Dict[str, UsageRecord] = {}
    for record in accumulated:
        by_key.setdefault(record.dedup_key, record)
    records = tuple(by_key.values())
    if read_error and not records and not recognized:
        status, code = AdapterStatus.ERROR, "read_error"
    elif partial or (read_error and records) or (unsupported_source and records):
        status = AdapterStatus.PARTIAL
        code = "resource_limit" if resource_limited else "partial_source"
    elif records:
        status, code = AdapterStatus.OK, "ok"
    elif not existing:
        status, code = AdapterStatus.NO_DATA, "no_data"
    elif recognized:
        status, code = AdapterStatus.NO_DATA, "no_data"
    else:
        status, code = AdapterStatus.UNSUPPORTED_FORMAT, "unsupported_format"
    return AdapterResult(
        _RUNTIME,
        status,
        records,
        (_diagnostic(status, code, len(existing), len(records)),),
    )


def scan(
    context: DiscoveryContext, specs: Sequence[SourceSpec]
) -> AdapterResult:
    paths = []
    for spec in specs:
        paths.extend(discover(spec, context))
    return parse_opencode(tuple(dict.fromkeys(paths)))
