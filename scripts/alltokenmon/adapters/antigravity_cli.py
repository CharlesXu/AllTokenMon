"""Bounded Antigravity CLI SQLite/protobuf usage adapter."""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Optional, Sequence, Set, Tuple

from ..normalize import MAX_TOKEN_VALUE
from ..schema import TokenBreakdown, UsageRecord
from .amp import _provider, _record, _result, _scan
from .base import DiscoveryContext, SourceSpec
from .sqliteio import SqliteReadError, open_sqlite_readonly, quote_identifier, sqlite_schema

_RUNTIME = "antigravity-cli"
_MAX_ROWS = 100_000
_MAX_PROTO_BYTES = 32 * 1024 * 1024
_MAX_PROTO_DEPTH = 4
_MAX_VARINT_BYTES = 10


class _ProtoError(ValueError):
    pass


class _Reader:
    def __init__(self, value: bytes):
        if len(value) > _MAX_PROTO_BYTES:
            raise _ProtoError("oversize")
        self.value = value
        self.position = 0

    def varint(self) -> int:
        result = 0
        for index in range(_MAX_VARINT_BYTES):
            if self.position >= len(self.value):
                raise _ProtoError("truncated")
            byte = self.value[self.position]
            self.position += 1
            if index == 9 and byte > 1:
                raise _ProtoError("overflow")
            result |= (byte & 0x7F) << (7 * index)
            if byte & 0x80 == 0:
                return result
        raise _ProtoError("overlong")

    def fields(self):
        while self.position < len(self.value):
            tag = self.varint()
            number, wire = tag >> 3, tag & 7
            if number == 0:
                raise _ProtoError("field_zero")
            if wire == 0:
                yield number, wire, self.varint()
            elif wire == 1:
                end = self.position + 8
                if end > len(self.value):
                    raise _ProtoError("truncated")
                self.position = end
                yield number, wire, None
            elif wire == 2:
                length = self.varint()
                end = self.position + length
                if end > len(self.value) or length > _MAX_PROTO_BYTES:
                    raise _ProtoError("truncated")
                payload = self.value[self.position:end]
                self.position = end
                yield number, wire, payload
            elif wire == 5:
                end = self.position + 4
                if end > len(self.value):
                    raise _ProtoError("truncated")
                self.position = end
                yield number, wire, None
            else:
                raise _ProtoError("unsupported_wire")


def _field(value: bytes, number: int, wire: int, depth: int = 0):
    if depth > _MAX_PROTO_DEPTH:
        raise _ProtoError("depth")
    for found, found_wire, payload in _Reader(value).fields():
        if found == number and found_wire == wire:
            return payload
    return None


def _message(value: bytes, number: int, depth: int = 0) -> Optional[bytes]:
    payload = _field(value, number, 2, depth)
    return payload if isinstance(payload, bytes) else None


def _varint(value: bytes, number: int, depth: int = 0) -> Optional[int]:
    payload = _field(value, number, 0, depth)
    return payload if isinstance(payload, int) else None


def _string(value: bytes, number: int, depth: int = 0) -> Optional[str]:
    payload = _message(value, number, depth)
    if payload is None:
        return None
    try:
        return payload.decode("utf-8")
    except UnicodeError:
        return None


def _timestamp(value: Optional[bytes]) -> Optional[datetime]:
    if value is None:
        return None
    seconds = _varint(value, 1, 1)
    nanos = _varint(value, 2, 1) or 0
    if seconds is None or nanos > 999_999_999:
        return None
    try:
        return datetime.fromtimestamp(
            seconds + nanos / 1_000_000_000, timezone.utc
        )
    except (OSError, OverflowError, ValueError):
        return None


def _mtime(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except (OSError, OverflowError, ValueError):
        return datetime.fromtimestamp(0, timezone.utc)


def _generation(
    path: Path,
    session: str,
    row_id: object,
    blob: bytes,
    fallback: datetime,
    seen: Set[str],
) -> Optional[UsageRecord]:
    chat = _message(blob, 1, 1)
    if chat is None:
        return None
    usage = _message(chat, 4, 2)
    if usage is None:
        return None
    fixed = min(_varint(usage, 1, 3) or 0, MAX_TOKEN_VALUE)
    new_input = min(_varint(usage, 2, 3) or 0, MAX_TOKEN_VALUE)
    tokens = TokenBreakdown(
        min(fixed + new_input, MAX_TOKEN_VALUE),
        min(_varint(usage, 9, 3) or 0, MAX_TOKEN_VALUE),
        min(_varint(usage, 5, 3) or 0, MAX_TOKEN_VALUE),
        0,
        min(_varint(usage, 10, 3) or 0, MAX_TOKEN_VALUE),
    )
    if tokens.total == 0 and tokens.reasoning == 0:
        return None
    response = _string(usage, 11, 3)
    if response:
        if response in seen:
            return None
        seen.add(response)
    model_raw = (_string(chat, 19, 2) or "unknown").strip() or "unknown"
    model = model_raw
    generation = _message(chat, 9, 2)
    generated_at = _timestamp(_message(generation, 4, 3)) if generation else None
    return _record(
        _RUNTIME, path, _provider(model, "antigravity"), model, session,
        generated_at or fallback, tokens,
        response or "antigravity-cli:{}:{}".format(session, row_id),
        source_kind="sqlite",
    )


def _trajectory_timestamp(connection, schema, path: Path) -> datetime:
    columns = set(schema.get("trajectory_metadata_blob", ()))
    if "data" not in columns:
        return _mtime(path)
    data = quote_identifier("data")
    length = "length(CAST({} AS BLOB))".format(data)
    row = connection.execute(
        "SELECT {length} FROM {table} LIMIT 1".format(
            length=length, table=quote_identifier("trajectory_metadata_blob")
        )
    ).fetchone()
    if (
        row is None or type(row[0]) is not int or row[0] < 0
        or row[0] > _MAX_PROTO_BYTES
    ):
        return _mtime(path)
    blob = connection.execute(
        "SELECT {data} FROM {table} WHERE {length} = ? AND {length} <= ? LIMIT 1".format(
            data=data, table=quote_identifier("trajectory_metadata_blob"),
            length=length,
        ),
        (row[0], _MAX_PROTO_BYTES),
    ).fetchone()
    if blob is None or not isinstance(blob[0], bytes):
        return _mtime(path)
    try:
        return _timestamp(_message(blob[0], 2, 1)) or _mtime(path)
    except _ProtoError:
        return _mtime(path)


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    connection = open_sqlite_readonly(path)
    try:
        schema = sqlite_schema(connection)
        if not {"idx", "data"}.issubset(set(schema.get("gen_metadata", ()))):
            return (), False, False, False
        session = path.stem or "unknown"
        fallback = _trajectory_timestamp(connection, schema, path)
        data = quote_identifier("data")
        length = "length(CAST({} AS BLOB))".format(data)
        query = (
            "SELECT {idx}, {length} FROM {table} ORDER BY {idx} LIMIT ?"
        ).format(
            idx=quote_identifier("idx"), length=length,
            table=quote_identifier("gen_metadata"),
        )
        records = []
        seen: Set[str] = set()
        partial = False
        total_bytes = 0
        for index, (row_id, byte_count) in enumerate(
            connection.execute(query, (_MAX_ROWS + 1,))
        ):
            if index >= _MAX_ROWS:
                partial = True
                break
            if (
                type(byte_count) is not int or byte_count < 0
                or byte_count > _MAX_PROTO_BYTES
                or total_bytes + byte_count > _MAX_PROTO_BYTES
            ):
                partial = True
                continue
            total_bytes += byte_count
            row = connection.execute(
                "SELECT {data} FROM {table} WHERE {idx} IS ? AND {length} = ? "
                "AND {length} <= ? LIMIT 1".format(
                    data=data, table=quote_identifier("gen_metadata"),
                    idx=quote_identifier("idx"), length=length,
                ),
                (row_id, byte_count, _MAX_PROTO_BYTES),
            ).fetchone()
            if row is None or not isinstance(row[0], bytes):
                partial = True
                continue
            try:
                record = _generation(
                    path, session, row_id, row[0], fallback, seen
                )
            except _ProtoError:
                partial = True
                continue
            if record is not None:
                records.append(record)
        return tuple(records), True, partial, False
    except sqlite3.DatabaseError:
        return (), True, False, True
    finally:
        connection.close()


def parse_antigravity_cli(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _safe_path)


def _safe_path(path: Path):
    try:
        return _path(path)
    except (OSError, SqliteReadError):
        return (), False, False, True


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_antigravity_cli)
