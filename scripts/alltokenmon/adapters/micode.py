"""Privacy-safe MiMo Code SQLite usage adapter."""

import json
from pathlib import Path
import sqlite3
from typing import Dict, Mapping, Optional, Sequence, Tuple

from ..normalize import safe_int, stable_key
from ..schema import TokenBreakdown, UsageRecord
from .amp import _finite_cost, _mapping, _record, _result, _scan, _text, _timestamp
from .base import DiscoveryContext, SourceSpec
from .jsonio import MAX_JSON_BYTES
from .sqliteio import SqliteReadError, open_sqlite_readonly, quote_identifier, sqlite_schema

_RUNTIME = "micode"
_MAX_ROWS = 100_000
_COLUMNS = {"id", "session_id", "data"}
_PROVIDER_ALIASES = {
    "openai_codex": "openai", "vertex": "anthropic", "vertex_ai": "anthropic",
    "gemini": "google", "x_ai": "xai", "z_ai": "zai",
}


def _canonical_provider(value: str) -> str:
    normalized = value.strip().rstrip("/").split("/")[0].lower().replace("-", "_")
    return _PROVIDER_ALIASES.get(normalized, normalized or value)


def _normalized_time(value: object) -> object:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value if value > 1_000_000_000_000 else value * 1000
    return value


def _candidate(
    path: Path, row_id: object, row_session: object, payload: Mapping[str, object]
) -> Optional[Tuple[UsageRecord, str, bool]]:
    if payload.get("role") != "assistant":
        return None
    model = _text(payload.get("modelID"))
    tokens_value = _mapping(payload.get("tokens"))
    time_value = _mapping(payload.get("time"))
    if model is None or tokens_value is None or time_value is None:
        return None
    cache = _mapping(tokens_value.get("cache")) or {}
    session = _text(row_session) or "unknown"
    message_id = _text(payload.get("id"))
    row = _text(row_id)
    if message_id is None and row is None:
        return None
    provider_value = _text(payload.get("providerID"))
    provider = _canonical_provider(provider_value) if provider_value else "unknown"
    created = _normalized_time(time_value.get("created"))
    tokens = TokenBreakdown(
        safe_int(tokens_value.get("input")),
        safe_int(tokens_value.get("output")),
        safe_int(cache.get("read")),
        safe_int(cache.get("write")),
        safe_int(tokens_value.get("reasoning")),
    )
    cost = _finite_cost(payload.get("cost"))
    dedup = message_id or "{}:{}".format(path, row)
    fingerprint = stable_key(
        created, _normalized_time(time_value.get("completed")), model, provider,
        tokens.input, tokens.output, tokens.cache_read, tokens.cache_write,
        tokens.reasoning, cost or 0, _text(payload.get("mode")) or _text(payload.get("agent")),
    )
    return (
        _record(
            _RUNTIME, path, provider, model, session, _timestamp(created, path),
            tokens, dedup, source_kind="sqlite", cost=cost,
        ),
        fingerprint,
        message_id is not None,
    )


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    connection = open_sqlite_readonly(path)
    try:
        if not _COLUMNS.issubset(set(sqlite_schema(connection).get("message", ()))):
            return (), False, False, False
        data = quote_identifier("data")
        length = "length(CAST({} AS BLOB))".format(data)
        query = (
            "SELECT {id}, {session}, {length} FROM {table} "
            "ORDER BY {id}, {session} LIMIT ?"
        ).format(
            id=quote_identifier("id"), session=quote_identifier("session_id"),
            length=length, table=quote_identifier("message"),
        )
        records = []
        fingerprints: Dict[str, int] = {}
        embedded = []
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
            total_bytes += byte_count
            if total_bytes > MAX_JSON_BYTES:
                partial = True
                break
            value = connection.execute(
                "SELECT {data} FROM {table} WHERE {id} IS ? AND {session} IS ? "
                "AND {length} = ? AND {length} <= ? LIMIT 1".format(
                    data=data, table=quote_identifier("message"),
                    id=quote_identifier("id"), session=quote_identifier("session_id"),
                    length=length,
                ),
                (row_id, session_id, byte_count, MAX_JSON_BYTES),
            ).fetchone()
            if value is None or not isinstance(value[0], str):
                partial = True
                continue
            try:
                payload = json.loads(value[0])
            except (TypeError, ValueError):
                partial = True
                continue
            if not isinstance(payload, Mapping):
                partial = True
                continue
            candidate = _candidate(path, row_id, session_id, payload)
            if candidate is None:
                continue
            record, fingerprint, has_embedded = candidate
            existing = fingerprints.get(fingerprint)
            if existing is not None:
                if has_embedded and not embedded[existing]:
                    records[existing] = record
                    embedded[existing] = True
                continue
            fingerprints[fingerprint] = len(records)
            records.append(record)
            embedded.append(has_embedded)
        return tuple(records), True, partial, False
    except sqlite3.DatabaseError:
        return (), True, False, True
    finally:
        connection.close()


def parse_micode(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _safe_path)


def _safe_path(path: Path):
    try:
        return _path(path)
    except (OSError, SqliteReadError):
        return (), False, False, True


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_micode)
