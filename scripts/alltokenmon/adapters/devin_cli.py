"""Privacy-safe Devin CLI SQLite message usage adapter."""

import json
from datetime import timedelta
from pathlib import Path
import sqlite3
from typing import Mapping, Optional, Sequence, Tuple

from ..normalize import safe_int
from ..schema import TokenBreakdown, UsageRecord
from .amp import _mapping, _provider, _record, _result, _scan, _text, _timestamp
from .base import DiscoveryContext, SourceSpec
from .jsonio import MAX_JSON_BYTES
from .sqliteio import SqliteReadError, open_sqlite_readonly, quote_identifier, sqlite_schema

_RUNTIME = "devin-cli"
_MAX_ROWS = 100_000
_MESSAGE_COLUMNS = {"row_id", "session_id", "chat_message", "created_at"}
_SESSION_COLUMNS = {"id", "model"}


def _back_anchor(timestamp, duration: int):
    if duration <= 0:
        return timestamp
    try:
        candidate = timestamp - timedelta(milliseconds=duration)
        return candidate if candidate.timestamp() > 0 else timestamp
    except (OverflowError, OSError, ValueError):
        return timestamp


def _record_from_payload(
    path: Path,
    row_id: object,
    row_session: object,
    created_at: object,
    session_model: object,
    payload: Mapping[str, object],
) -> Optional[UsageRecord]:
    if payload.get("role") != "assistant":
        return None
    metadata = _mapping(payload.get("metadata")) or {}
    metrics = _mapping(metadata.get("metrics")) or {}
    model = _text(metadata.get("generation_model")) or _text(session_model)
    if model is None or model == "adaptive":
        return None
    tokens = TokenBreakdown(
        safe_int(metrics.get("input_tokens")),
        safe_int(metrics.get("output_tokens")),
        safe_int(metrics.get("cache_read_tokens")),
        safe_int(metrics.get("cache_creation_tokens")),
    )
    if tokens.total == 0:
        fallback = safe_int(metadata.get("num_tokens"))
        tokens = TokenBreakdown(output=fallback)
    if tokens.total == 0:
        return None
    session = _text(row_session)
    row = _text(row_id)
    if session is None or row is None:
        return None
    recorded = _timestamp(
        safe_int(created_at) * 1000 if safe_int(created_at) else None, path
    )
    timestamp = _back_anchor(recorded, safe_int(metrics.get("total_time_ms")))
    return _record(
        _RUNTIME, path, _provider(model, "devin"), model, session, timestamp,
        tokens, "devin-cli:{}:{}".format(session, row), source_kind="sqlite",
    )


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    connection = open_sqlite_readonly(path)
    try:
        schema = sqlite_schema(connection)
        if (
            not _MESSAGE_COLUMNS.issubset(set(schema.get("message_nodes", ())))
            or not _SESSION_COLUMNS.issubset(set(schema.get("sessions", ())))
        ):
            return (), False, False, False
        chat = "m." + quote_identifier("chat_message")
        length = "length(CAST({} AS BLOB))".format(chat)
        query = (
            "SELECT m.{row}, m.{session}, {length}, m.{created}, s.{model} "
            "FROM {messages} AS m JOIN {sessions} AS s "
            "ON m.{session} = s.{id} ORDER BY m.{row} LIMIT ?"
        ).format(
            row=quote_identifier("row_id"),
            session=quote_identifier("session_id"),
            length=length,
            created=quote_identifier("created_at"),
            model=quote_identifier("model"),
            messages=quote_identifier("message_nodes"),
            sessions=quote_identifier("sessions"),
            id=quote_identifier("id"),
        )
        records = []
        partial = False
        total_bytes = 0
        for index, (row_id, session_id, byte_count, created_at, model) in enumerate(
            connection.execute(query, (_MAX_ROWS + 1,))
        ):
            if index >= _MAX_ROWS:
                partial = True
                break
            if type(byte_count) is not int or byte_count < 0 or byte_count > MAX_JSON_BYTES:
                partial = True
                continue
            total_bytes += byte_count
            if total_bytes > MAX_JSON_BYTES:
                partial = True
                break
            data = connection.execute(
                "SELECT {chat} FROM {messages} WHERE {row} IS ? AND {session} IS ? "
                "AND {length} = ? AND {length} <= ? LIMIT 1".format(
                    chat=quote_identifier("chat_message"),
                    messages=quote_identifier("message_nodes"),
                    row=quote_identifier("row_id"),
                    session=quote_identifier("session_id"),
                    length="length(CAST({} AS BLOB))".format(
                        quote_identifier("chat_message")
                    ),
                ),
                (row_id, session_id, byte_count, MAX_JSON_BYTES),
            ).fetchone()
            if data is None or not isinstance(data[0], str):
                partial = True
                continue
            try:
                payload = json.loads(data[0])
            except (TypeError, ValueError):
                partial = True
                continue
            if not isinstance(payload, Mapping):
                partial = True
                continue
            record = _record_from_payload(
                path, row_id, session_id, created_at, model, payload
            )
            if record is not None:
                records.append(record)
        return tuple(records), True, partial, False
    except sqlite3.DatabaseError:
        return (), True, False, True
    finally:
        connection.close()


def parse_devin_cli(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _safe_path)


def _safe_path(path: Path):
    try:
        return _path(path)
    except (OSError, SqliteReadError):
        return (), False, False, True


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_devin_cli)
