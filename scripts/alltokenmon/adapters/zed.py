"""Privacy-safe Zed hosted-agent SQLite usage adapter."""

from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from typing import Mapping, Sequence, Tuple

from ..normalize import safe_int
from ..schema import TokenBreakdown, UsageRecord
from .amp import _mapping, _record, _result, _scan, _text, _timestamp
from .base import DiscoveryContext, SourceSpec
from .sqliteio import SqliteReadError, open_sqlite_readonly, quote_identifier, sqlite_schema
from .zstdlite import ZstdDecodeError, decompress_zstd

_RUNTIME = "zed"
_PROVIDER = "zed.dev"
_MAX_ROWS = 100_000
_MAX_BLOB_BYTES = 32 * 1024 * 1024
_REQUIRED = {"id", "updated_at", "data_type", "data"}


def _usage(value: object):
    mapping = _mapping(value)
    if mapping is None:
        return None
    return TokenBreakdown(
        safe_int(mapping.get("input_tokens")),
        safe_int(mapping.get("output_tokens")),
        safe_int(mapping.get("cache_read_input_tokens")),
        safe_int(mapping.get("cache_creation_input_tokens")),
    )


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    connection = open_sqlite_readonly(path)
    try:
        columns = set(sqlite_schema(connection).get("threads", ()))
        if not _REQUIRED.issubset(columns):
            return (), False, False, False
        optional = [
            quote_identifier("created_at") if "created_at" in columns else "NULL"
        ]
        data = quote_identifier("data")
        length = "length(CAST({} AS BLOB))".format(data)
        query = (
            "SELECT {id}, {updated}, {created}, {kind}, {length} FROM {table} "
            "ORDER BY {id} LIMIT ?"
        ).format(
            id=quote_identifier("id"), updated=quote_identifier("updated_at"),
            created=optional[0], kind=quote_identifier("data_type"),
            length=length, table=quote_identifier("threads"),
        )
        records = []
        partial = False
        saw_supported_payload = False
        saw_unsupported_payload = False
        total_bytes = 0
        total_decoded_bytes = 0
        for index, (row_id, updated, created, data_type, byte_count) in enumerate(
            connection.execute(query, (_MAX_ROWS + 1,))
        ):
            if index >= _MAX_ROWS:
                partial = True
                break
            payload_type = (_text(data_type) or "").lower()
            if payload_type not in ("json", "zstd"):
                saw_unsupported_payload = True
                continue
            saw_supported_payload = True
            if (
                type(byte_count) is not int or byte_count < 0
                or byte_count > _MAX_BLOB_BYTES
                or total_bytes + byte_count > _MAX_BLOB_BYTES
            ):
                partial = True
                continue
            total_bytes += byte_count
            blob_row = connection.execute(
                "SELECT {data} FROM {table} WHERE {id} IS ? AND {length} = ? "
                "AND {length} <= ? LIMIT 1".format(
                    data=data, table=quote_identifier("threads"),
                    id=quote_identifier("id"), length=length,
                ),
                (row_id, byte_count, _MAX_BLOB_BYTES),
            ).fetchone()
            if blob_row is None or not isinstance(blob_row[0], (str, bytes)):
                partial = True
                continue
            try:
                encoded = blob_row[0]
                if payload_type == "zstd":
                    if not isinstance(encoded, bytes):
                        raise ZstdDecodeError()
                    encoded = decompress_zstd(
                        encoded,
                        _MAX_BLOB_BYTES - total_decoded_bytes,
                    )
                decoded_bytes = (
                    len(encoded.encode("utf-8"))
                    if isinstance(encoded, str)
                    else len(encoded)
                )
                if total_decoded_bytes + decoded_bytes > _MAX_BLOB_BYTES:
                    raise ZstdDecodeError()
                total_decoded_bytes += decoded_bytes
                payload = json.loads(encoded)
            except (TypeError, UnicodeError, ValueError, ZstdDecodeError):
                partial = True
                continue
            if not isinstance(payload, Mapping) or payload.get("imported") is True:
                continue
            model_value = _mapping(payload.get("model"))
            provider = _text(model_value.get("provider")) if model_value else None
            model = _text(model_value.get("model")) if model_value else None
            if provider is None or provider.lower() != _PROVIDER or model is None:
                continue
            usages = payload.get("request_token_usage")
            values = usages.values() if isinstance(usages, Mapping) else (
                usages if isinstance(usages, list) else ()
            )
            total = TokenBreakdown()
            count = 0
            for value in values:
                usage = _usage(value)
                if usage is None or usage.total == 0:
                    continue
                total = TokenBreakdown(
                    safe_int(total.input + usage.input),
                    safe_int(total.output + usage.output),
                    safe_int(total.cache_read + usage.cache_read),
                    safe_int(total.cache_write + usage.cache_write),
                )
                count += 1
            if total.total == 0:
                cumulative = _usage(payload.get("cumulative_token_usage"))
                if cumulative is None or cumulative.total == 0:
                    continue
                total, count = cumulative, 1
            session = _text(row_id)
            if session is None:
                continue
            records.append(_record(
                _RUNTIME, path, _PROVIDER, model, session,
                _timestamp(created or updated or payload.get("updated_at"), path),
                total, "zed:" + session, source_kind="sqlite",
            ))
            records[-1] = replace(records[-1], message_count=count)
        if saw_unsupported_payload and not saw_supported_payload:
            return (), False, False, False
        return tuple(records), True, partial or saw_unsupported_payload, False
    except sqlite3.DatabaseError:
        return (), True, False, True
    finally:
        connection.close()


def parse_zed(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _safe_path)


def _safe_path(path: Path):
    try:
        return _path(path)
    except (OSError, SqliteReadError):
        return (), False, False, True


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_zed)
