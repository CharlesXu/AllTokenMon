"""Privacy-safe Crush cost-only SQLite adapter."""

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Dict, Mapping, Sequence, Tuple

from ..normalize import safe_int
from ..schema import TokenBreakdown, UsageRecord
from .amp import _finite_cost, _result, _scan, _text
from .base import DiscoveryContext, SourceSpec
from .jsonio import read_json
from .sqliteio import SqliteReadError, open_sqlite_readonly, quote_identifier, sqlite_schema

_RUNTIME = "crush"
_MAX_ROWS = 100_000
_MAX_TREE_DEPTH = 128
_SESSION_COLUMNS = {
    "id", "parent_session_id", "message_count", "cost", "updated_at", "created_at",
}
_MESSAGE_COLUMNS = {"session_id", "role", "created_at"}


def _timestamp_ms(value: object) -> int:
    numeric = safe_int(value)
    if numeric == 0:
        return 0
    return numeric if numeric >= 100_000_000_000 else numeric * 1000


def _datetime(value: int):
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc)
    except (OSError, OverflowError, ValueError):
        return datetime.fromtimestamp(0, timezone.utc)


def _local_day(value: int):
    try:
        return datetime.fromtimestamp(value / 1000).date().isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _db_records(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    connection = open_sqlite_readonly(path)
    try:
        schema = sqlite_schema(connection)
        if (
            not _SESSION_COLUMNS.issubset(set(schema.get("sessions", ())))
            or not _MESSAGE_COLUMNS.issubset(set(schema.get("messages", ())))
        ):
            return (), False, False, False
        session_query = (
            "SELECT "
            + ", ".join(quote_identifier(name) for name in (
                "id", "cost", "created_at", "updated_at"
            ))
            + " FROM " + quote_identifier("sessions")
            + " WHERE " + quote_identifier("parent_session_id") + " IS NULL"
            + " AND (COALESCE(" + quote_identifier("message_count") + ", 0) > ?"
            + " OR COALESCE(" + quote_identifier("cost") + ", 0) > ?)"
            + " ORDER BY " + quote_identifier("created_at") + " LIMIT ?"
        )
        sessions = []
        partial = False
        for index, row in enumerate(connection.execute(
            session_query, (0, 0, _MAX_ROWS + 1)
        )):
            if index >= _MAX_ROWS:
                partial = True
                break
            session = _text(row[0])
            if session is not None:
                sessions.append((session, _finite_cost(row[1]), row[2], row[3]))

        tree_query = (
            "WITH RECURSIVE raw_tree("
            "root_session_id, session_id, depth, visited"
            ") AS ("
            "SELECT s.{id}, s.{id}, 0, "
            "',' || hex(CAST(s.{id} AS BLOB)) || ',' "
            "FROM {sessions} AS s WHERE s.{parent} IS NULL "
            "UNION ALL "
            "SELECT rt.root_session_id, s.{id}, rt.depth + 1, "
            "rt.visited || hex(CAST(s.{id} AS BLOB)) || ',' "
            "FROM {sessions} AS s JOIN raw_tree AS rt "
            "ON s.{parent} = rt.session_id "
            "WHERE rt.depth < ? "
            "AND instr("
            "rt.visited, ',' || hex(CAST(s.{id} AS BLOB)) || ','"
            ") = 0 LIMIT ?"
            "), session_tree(root_session_id, session_id) AS ("
            "SELECT root_session_id, session_id FROM raw_tree "
            "GROUP BY root_session_id, session_id"
            "), issues(issue) AS ("
            "SELECT 1 FROM raw_tree AS rt JOIN {sessions} AS s "
            "ON s.{parent} = rt.session_id "
            "WHERE instr("
            "rt.visited, ',' || hex(CAST(s.{id} AS BLOB)) || ','"
            ") > 0 "
            "OR (rt.depth >= ? "
            "AND instr("
            "rt.visited, ',' || hex(CAST(s.{id} AS BLOB)) || ','"
            ") = 0) LIMIT 1"
            "), traversal_state(issue) AS ("
            "SELECT (EXISTS(SELECT 1 FROM issues) "
            "OR (SELECT COUNT(*) FROM raw_tree) > ?)"
            ") "
            "SELECT st.root_session_id, m.{created}, "
            "(SELECT issue FROM traversal_state) "
            "FROM session_tree AS st "
            "JOIN {messages} AS m ON m.{session} = st.session_id "
            "WHERE m.{role} = ? "
            "UNION ALL SELECT NULL, NULL, issue FROM traversal_state WHERE issue "
            "ORDER BY 1, 2 LIMIT ?"
        ).format(
            id=quote_identifier("id"),
            sessions=quote_identifier("sessions"),
            parent=quote_identifier("parent_session_id"),
            created=quote_identifier("created_at"),
            messages=quote_identifier("messages"),
            session=quote_identifier("session_id"),
            role=quote_identifier("role"),
        )
        buckets: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for index, (session, created, traversal_issue) in enumerate(
            connection.execute(
                tree_query,
                (
                    _MAX_TREE_DEPTH,
                    _MAX_ROWS + 1,
                    _MAX_TREE_DEPTH,
                    _MAX_ROWS,
                    "assistant",
                    _MAX_ROWS + 1,
                ),
            )
        ):
            if index >= _MAX_ROWS:
                partial = True
                break
            if traversal_issue:
                partial = True
            timestamp = _timestamp_ms(created)
            day = _local_day(timestamp) if timestamp else None
            session_text = _text(session)
            if day is not None and session_text is not None:
                buckets[session_text][day].append(timestamp)

        records = []
        for session, cost, created_at, updated_at in sessions:
            day_map = buckets.get(session, {})
            session_id = "{}:{}".format(path, session)
            if day_map:
                total_messages = sum(len(values) for values in day_map.values())
                total_cost = cost or 0.0
                allocated = 0.0
                ordered = sorted(day_map.items())
                for index, (_, timestamps) in enumerate(ordered):
                    if index + 1 == len(ordered):
                        bucket_cost = max(total_cost - allocated, 0.0)
                    else:
                        bucket_cost = total_cost * len(timestamps) / total_messages
                    allocated += bucket_cost
                    timestamp = min(timestamps)
                    records.append(UsageRecord(
                        runtime=_RUNTIME, provider="crush", model="session-total",
                        session_id=session_id, timestamp=_datetime(timestamp),
                        tokens=TokenBreakdown(), message_count=len(timestamps),
                        source_kind="sqlite", source_path=str(path),
                        dedup_key="crush:{}:{}".format(session_id, timestamp),
                        confidence="exact", cost=bucket_cost,
                        cost_source="provider_reported",
                    ))
                continue
            if cost is None or cost <= 0:
                continue
            timestamp = _timestamp_ms(updated_at) or _timestamp_ms(created_at)
            if timestamp == 0:
                continue
            records.append(UsageRecord(
                runtime=_RUNTIME, provider="crush", model="session-total",
                session_id=session_id, timestamp=_datetime(timestamp),
                tokens=TokenBreakdown(), message_count=0,
                source_kind="sqlite", source_path=str(path),
                dedup_key="crush:{}:fallback".format(session_id),
                confidence="exact", cost=cost, cost_source="provider_reported",
            ))
        return tuple(records), True, partial, False
    except sqlite3.DatabaseError:
        return (), True, False, True
    finally:
        connection.close()


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    if path.suffix == ".db":
        return _db_records(path)
    result = read_json(path)
    if result.error_code is not None:
        return (), False, result.partial, result.error_code.startswith("io_error:")
    root = result.value
    if not isinstance(root, Mapping) or not isinstance(root.get("projects"), list):
        return (), False, result.partial, False
    db_paths = []
    for value in root["projects"]:
        if not isinstance(value, Mapping):
            continue
        project = _text(value.get("path"))
        data_dir = _text(value.get("data_dir"))
        if project is None or data_dir is None:
            continue
        data_path = Path(data_dir)
        if not data_path.is_absolute():
            data_path = Path(project) / data_path
        candidate = data_path / "crush.db"
        if candidate.is_file() and not candidate.is_symlink():
            db_paths.append(candidate)
    records = []
    recognized = False
    partial = result.partial
    failed = False
    for db_path in sorted(set(db_paths), key=lambda value: str(value)):
        parsed, known, incomplete, read_failed = _db_records(db_path)
        records.extend(parsed)
        recognized = recognized or known
        partial = partial or incomplete
        failed = failed or read_failed
    if not db_paths:
        recognized = True
    return tuple(records), recognized, partial, failed


def parse_crush(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _safe_path)


def _safe_path(path: Path):
    try:
        return _path(path)
    except (OSError, SqliteReadError):
        return (), False, False, True


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_crush)
