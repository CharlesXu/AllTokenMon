"""Privacy-safe Kilo CLI SQLite usage adapter."""

import json
from pathlib import Path
import sqlite3
from typing import Mapping, Optional, Sequence, Tuple

from ..normalize import safe_int
from ..schema import TokenBreakdown, UsageRecord
from .amp import _finite_cost, _mapping, _provider, _record, _result, _scan, _text, _timestamp
from .base import DiscoveryContext, SourceSpec
from .jsonio import MAX_JSON_BYTES
from .sqliteio import SqliteReadError, open_sqlite_readonly, quote_identifier, sqlite_schema

_RUNTIME = "kilo"
_MAX_ROWS = 100_000
_COLUMNS = {"id", "session_id", "data"}
_PROVIDER_ALIASES = {
    "openai_codex": "openai", "vertex": "anthropic", "vertex_ai": "anthropic",
    "gemini": "google", "x_ai": "xai", "z_ai": "zai",
}


def _canonical_provider(value: str) -> str:
    normalized = value.strip().rstrip("/").split("/")[0].lower().replace("-", "_")
    return _PROVIDER_ALIASES.get(normalized, normalized or value)


def _payload_record(
    path: Path, row_id: object, row_session: object, payload: Mapping[str, object]
) -> Optional[UsageRecord]:
    if payload.get("role") != "assistant":
        return None
    model = _text(payload.get("modelID"))
    tokens_value = _mapping(payload.get("tokens"))
    cache = _mapping(tokens_value.get("cache")) if tokens_value else None
    if model is None or tokens_value is None or cache is None:
        return None
    time_value = _mapping(payload.get("time")) or {}
    provider_value = _text(payload.get("providerID"))
    provider = (
        _canonical_provider(provider_value)
        if provider_value is not None
        else _provider(model, "kilo")
    )
    cost = _finite_cost(payload.get("cost"))
    message_id = _text(payload.get("id")) or _text(row_id)
    if message_id is None:
        return None
    session_id = _text(payload.get("session_id")) or _text(row_session) or "unknown"
    return _record(
        _RUNTIME,
        path,
        provider,
        model,
        session_id,
        _timestamp(time_value.get("created"), path),
        TokenBreakdown(
            safe_int(tokens_value.get("input")),
            safe_int(tokens_value.get("output")),
            safe_int(cache.get("read")),
            safe_int(cache.get("write")),
            safe_int(tokens_value.get("reasoning")),
        ),
        message_id,
        source_kind="sqlite",
        cost=cost,
    )


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    connection = open_sqlite_readonly(path)
    try:
        schema = sqlite_schema(connection)
        if not _COLUMNS.issubset(set(schema.get("message", ()))):
            return (), False, False, False
        identifiers = {name: quote_identifier(name) for name in _COLUMNS}
        byte_expr = "length(CAST({} AS BLOB))".format(identifiers["data"])
        query = (
            "SELECT {id}, {session}, {length} FROM {table} "
            "ORDER BY {id} LIMIT ?"
        ).format(
            id=identifiers["id"],
            session=identifiers["session_id"],
            length=byte_expr,
            table=quote_identifier("message"),
        )
        records = []
        partial = False
        total_bytes = 0
        for index, (row_id, session_id, byte_count) in enumerate(
            connection.execute(query, (_MAX_ROWS + 1,))
        ):
            if index >= _MAX_ROWS:
                partial = True
                break
            if type(byte_count) is not int or byte_count < 0 or byte_count > MAX_JSON_BYTES:
                partial = True
                continue
            if total_bytes + byte_count > MAX_JSON_BYTES:
                partial = True
                break
            total_bytes += byte_count
            data_row = connection.execute(
                "SELECT {data} FROM {table} WHERE {id} IS ? AND {session} IS ? "
                "AND {length} = ? AND {length} <= ? LIMIT 1".format(
                    data=identifiers["data"],
                    table=quote_identifier("message"),
                    id=identifiers["id"],
                    session=identifiers["session_id"],
                    length=byte_expr,
                ),
                (row_id, session_id, byte_count, MAX_JSON_BYTES),
            ).fetchone()
            if data_row is None or not isinstance(data_row[0], str):
                partial = True
                continue
            try:
                payload = json.loads(data_row[0])
            except (TypeError, ValueError):
                partial = True
                continue
            if not isinstance(payload, Mapping):
                partial = True
                continue
            record = _payload_record(path, row_id, session_id, payload)
            if record is not None:
                records.append(record)
        return tuple(records), True, partial, False
    except sqlite3.DatabaseError:
        return (), True, False, True
    finally:
        connection.close()


def parse_kilo(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _safe_path)


def _safe_path(path: Path):
    try:
        return _path(path)
    except (OSError, SqliteReadError):
        return (), False, False, True


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_kilo)
