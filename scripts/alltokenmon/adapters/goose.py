"""Privacy-safe Goose SQLite session usage adapter."""

import json
from pathlib import Path
import sqlite3
from typing import Mapping, Sequence, Tuple

from ..normalize import safe_int
from ..schema import TokenBreakdown, UsageRecord
from .amp import _provider, _record, _result, _scan, _text, _timestamp
from .base import DiscoveryContext, SourceSpec
from .jsonio import MAX_JSON_BYTES
from .sqliteio import SqliteReadError, open_sqlite_readonly, quote_identifier, sqlite_schema

_RUNTIME = "goose"
_MAX_ROWS = 100_000
_COLUMNS = {
    "id", "model_config_json", "provider_name", "created_at", "total_tokens",
    "input_tokens", "output_tokens", "accumulated_total_tokens",
    "accumulated_input_tokens", "accumulated_output_tokens",
}
_PROVIDER_ALIASES = {
    "openai_codex": "openai", "vertex": "anthropic", "vertex_ai": "anthropic",
    "gemini": "google", "x_ai": "xai", "z_ai": "zai",
}


def _canonical_provider(value: str) -> str:
    normalized = value.strip().rstrip("/").split("/")[0].lower().replace("-", "_")
    return _PROVIDER_ALIASES.get(normalized, normalized or value)


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    connection = open_sqlite_readonly(path)
    try:
        if not _COLUMNS.issubset(set(sqlite_schema(connection).get("sessions", ()))):
            return (), False, False, False
        columns = tuple(sorted(_COLUMNS - {"model_config_json"}))
        config = quote_identifier("model_config_json")
        query = (
            "SELECT "
            + ", ".join(quote_identifier(name) for name in columns)
            + ", length(CAST(" + config + " AS BLOB)) FROM "
            + quote_identifier("sessions")
            + " ORDER BY " + quote_identifier("id") + " LIMIT ?"
        )
        records = []
        partial = False
        total_bytes = 0
        for index, row in enumerate(connection.execute(query, (_MAX_ROWS + 1,))):
            if index >= _MAX_ROWS:
                partial = True
                break
            values = dict(zip(columns, row[:-1]))
            byte_count = row[-1]
            if type(byte_count) is not int or byte_count < 0 or byte_count > MAX_JSON_BYTES:
                partial = True
                continue
            total_bytes += byte_count
            if total_bytes > MAX_JSON_BYTES:
                partial = True
                break
            config_row = connection.execute(
                "SELECT {config} FROM {table} WHERE {id} IS ? "
                "AND {length} = ? AND {length} <= ? LIMIT 1".format(
                    config=config,
                    table=quote_identifier("sessions"),
                    id=quote_identifier("id"),
                    length="length(CAST({} AS BLOB))".format(config),
                ),
                (values["id"], byte_count, MAX_JSON_BYTES),
            ).fetchone()
            if config_row is None or not isinstance(config_row[0], str):
                partial = True
                continue
            try:
                model_config = json.loads(config_row[0])
            except (TypeError, ValueError):
                partial = True
                continue
            if not isinstance(model_config, Mapping):
                partial = True
                continue
            model = _text(model_config.get("model_name"))
            session = _text(values["id"])
            if model is None or session is None:
                continue
            input_tokens = safe_int(
                values["accumulated_input_tokens"]
                if values["accumulated_input_tokens"] is not None
                else values["input_tokens"]
            )
            output_tokens = safe_int(
                values["accumulated_output_tokens"]
                if values["accumulated_output_tokens"] is not None
                else values["output_tokens"]
            )
            total = safe_int(
                values["accumulated_total_tokens"]
                if values["accumulated_total_tokens"] is not None
                else values["total_tokens"]
            )
            if input_tokens == output_tokens == total == 0:
                continue
            provider_value = _text(values["provider_name"])
            provider = (
                _canonical_provider(provider_value)
                if provider_value is not None
                else _provider(model, "goose")
            )
            records.append(_record(
                _RUNTIME, path, provider, model, session,
                _timestamp(values["created_at"], path),
                TokenBreakdown(
                    input_tokens, output_tokens, 0, 0,
                    max(total - input_tokens - output_tokens, 0),
                ),
                session, source_kind="sqlite",
            ))
        return tuple(records), True, partial, False
    except sqlite3.DatabaseError:
        return (), True, False, True
    finally:
        connection.close()


def parse_goose(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _safe_path)


def _safe_path(path: Path):
    try:
        return _path(path)
    except (OSError, SqliteReadError):
        return (), False, False, True


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_goose)
