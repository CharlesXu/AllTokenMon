"""Privacy-safe Kiro multi-source usage reconciliation.

Exact execution IDs are authoritative over IDE snapshots. For the same stable
session ID and turn index, CLI metadata outranks IDE estimates, which outrank
SQLite estimates. No timestamp/token-count similarity is used for deduplication.
"""

import json
import math
from datetime import timedelta
from pathlib import Path
import sqlite3
from typing import Mapping, Optional, Sequence

from ..normalize import safe_int, stable_key
from ..schema import (
    AdapterResult,
    AdapterStatus,
    Diagnostic,
    TokenBreakdown,
    UsageRecord,
)
from .amp import _mapping, _record, _scan, _text, _timestamp
from .base import DiscoveryContext, SourceSpec
from .jsonio import MAX_JSON_BYTES, read_json, read_json_lines
from .sqliteio import SqliteReadError, open_sqlite_readonly, quote_identifier, sqlite_schema

_RUNTIME = "kiro"
_PROVIDER = "amazon-bedrock"
_MAX_ROWS = 100_000
_MAX_TREE_NODES = 100_000
_PRIORITY = {
    "kiro_sqlite": 1,
    "kiro_snapshot": 2,
    "kiro_ide": 3,
    "kiro_cli": 4,
    "kiro_execution": 5,
}


def _workspace_scope(path: Path) -> str:
    parts = tuple(part.casefold() for part in path.parts)
    try:
        index = parts.index("kiro.kiroagent")
        workspace = path.parts[index + 1]
    except (ValueError, IndexError):
        return "global"
    return stable_key("kiro-workspace", workspace)


def _estimate(characters: int) -> int:
    return max(0, characters + 3) // 4


def _percentage_tokens(context_window: object, percentage: object) -> int:
    window = safe_int(context_window)
    if isinstance(percentage, bool):
        return 0
    try:
        value = float(percentage)
    except (TypeError, ValueError, OverflowError):
        return 0
    if not math.isfinite(value) or value <= 0 or window <= 0:
        return 0
    return max(0, int(window * value / 100.0))


def _chars(value: object) -> int:
    return len(value) if isinstance(value, str) else 0


def _content_chars(value: object) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        total = 0
        for item in value:
            if isinstance(item, str):
                total += len(item)
                continue
            part = _mapping(item)
            if part is None:
                continue
            kind = _text(part.get("kind")) or _text(part.get("type"))
            if kind in (None, "text"):
                total += _chars(part.get("data")) + _chars(part.get("text"))
        return total
    return 0


def _model(
    value: Mapping[str, object],
    capped: Optional[list] = None,
    budget: Optional[list] = None,
) -> str:
    if budget is None:
        budget = [_MAX_TREE_NODES]
    containers = (
        "messages", "conversation", "chat", "transcript", "entries", "events",
        "history", "prompt", "response", "content", "text", "message", "parts",
        "items", "nodes", "promptLogs", "completionOptions", "context",
    )

    def visit(node: object, depth: int = 0) -> Optional[str]:
        if depth >= 64:
            if capped is not None:
                capped[0] = True
            return None
        if budget[0] <= 0:
            if capped is not None:
                capped[0] = True
            return None
        budget[0] -= 1
        mapping = _mapping(node)
        if mapping is not None:
            for key in ("model_id", "modelId", "model"):
                candidate = _text(mapping.get(key))
                if (
                    candidate
                    and candidate.casefold() not in ("auto", "agent", "qdev")
                ):
                    return candidate
            for key in containers:
                if key in mapping:
                    found = visit(mapping[key], depth + 1)
                    if found:
                        return found
                    if budget[0] <= 0:
                        if capped is not None:
                            capped[0] = True
                        break
        elif isinstance(node, list):
            for child in node:
                found = visit(child, depth + 1)
                if found:
                    return found
                if budget[0] <= 0:
                    if capped is not None:
                        capped[0] = True
                    break
        return None

    return visit(value) or "auto"


def _execution_record(
    path: Path, value: Mapping[str, object], capped: Optional[list] = None
) -> Optional[UsageRecord]:
    execution_id = _text(value.get("executionId"))
    actions = value.get("actions")
    if execution_id is None or not isinstance(actions, list):
        return None
    if _text(value.get("status")) != "succeed":
        return None
    output_chars = 0
    for raw in actions:
        action = _mapping(raw)
        if action is None or _text(action.get("actionType")) not in ("say", "reasoning"):
            continue
        output = action.get("output")
        output_map = _mapping(output)
        output_chars += _chars(output)
        if output_map:
            output_chars += _chars(output_map.get("message"))
    input_chars = 0
    context = _mapping(value.get("context")) or {}
    messages = context.get("messages")
    if isinstance(messages, list):
        for raw in messages:
            message = _mapping(raw) or {}
            entries = message.get("entries")
            if not isinstance(entries, list):
                continue
            for raw_entry in entries:
                entry = _mapping(raw_entry) or {}
                if _text(entry.get("type")) == "text":
                    input_chars += _chars(entry.get("text"))
    input_value = _mapping(value.get("input")) or {}
    data = _mapping(input_value.get("data")) or {}
    messages = data.get("messages")
    if isinstance(messages, list):
        for raw in messages:
            message = _mapping(raw) or {}
            input_chars += _content_chars(message.get("content"))
    tokens = TokenBreakdown(_estimate(input_chars), _estimate(output_chars))
    if not tokens.total:
        return None
    session = _text(value.get("chatSessionId")) or execution_id
    return _record(
        _RUNTIME,
        path,
        _PROVIDER,
        _model(value, capped),
        session,
        _timestamp(value.get("startTime"), path),
        tokens,
        "kiro:execution:{}:{}".format(_workspace_scope(path), execution_id),
        source_kind="kiro_execution",
        confidence="estimated",
    )


def _collect_role_text(
    value: object,
    counts: Mapping[str, int],
    *,
    depth: int = 0,
    role: Optional[str] = None,
    budget: Optional[list] = None,
    capped: Optional[list] = None,
) -> Mapping[str, int]:
    if budget is None:
        budget = [_MAX_TREE_NODES]
    prompt = counts["prompt"]
    assistant = counts["assistant"]
    if depth >= 64 or budget[0] <= 0:
        if capped is not None:
            capped[0] = True
        return {"prompt": prompt, "assistant": assistant}
    budget[0] -= 1
    if isinstance(value, list):
        for item in value:
            updated = _collect_role_text(
                item,
                {"prompt": prompt, "assistant": assistant},
                depth=depth + 1,
                role=role,
                budget=budget,
                capped=capped,
            )
            prompt, assistant = updated["prompt"], updated["assistant"]
            if budget[0] <= 0:
                if capped is not None:
                    capped[0] = True
                break
        return {"prompt": prompt, "assistant": assistant}
    if isinstance(value, str):
        if role == "prompt":
            prompt += len(value)
        elif role == "assistant":
            assistant += len(value)
        return {"prompt": prompt, "assistant": assistant}
    node = _mapping(value)
    if node is None:
        return {"prompt": prompt, "assistant": assistant}
    raw_role = (
        _text(node.get("role")) or _text(node.get("type")) or ""
    ).lower()
    if raw_role in ("user", "prompt", "human"):
        role = "prompt"
    elif raw_role in ("assistant", "response", "bot"):
        role = "assistant"
    groups = (
        ("prompt", "response", "content", "text", "data", "message"),
        (
            "messages", "conversation", "chat", "transcript", "entries",
            "events", "history",
        ),
        ("parts", "items", "nodes"),
    )
    for group in groups:
        visited = []
        for key in group:
            if key not in node:
                continue
            item = node[key]
            try:
                duplicate = any(item == prior for prior in visited)
            except RecursionError:
                duplicate = False
            if duplicate:
                continue
            visited.append(item)
            updated = _collect_role_text(
                item,
                {"prompt": prompt, "assistant": assistant},
                depth=depth + 1,
                role=role,
                budget=budget,
                capped=capped,
            )
            prompt, assistant = updated["prompt"], updated["assistant"]
            if budget[0] <= 0:
                if capped is not None:
                    capped[0] = True
                break
        if budget[0] <= 0:
            if capped is not None:
                capped[0] = True
            break
    return {"prompt": prompt, "assistant": assistant}


def _snapshot_record(
    path: Path, value: Mapping[str, object], capped: Optional[list] = None
) -> Optional[UsageRecord]:
    execution_id = _text(value.get("executionId"))
    budget = [_MAX_TREE_NODES]
    counts = _collect_role_text(
        value,
        {"prompt": 0, "assistant": 0},
        budget=budget,
        capped=capped,
    )
    tokens = TokenBreakdown(
        _estimate(counts["prompt"]), _estimate(counts["assistant"])
    )
    if not tokens.total:
        return None
    session = (
        _text(value.get("sessionId"))
        or _text(value.get("chatSessionId"))
        or path.stem
        or "unknown"
    )
    identity = (
        "execution:{}:{}".format(_workspace_scope(path), execution_id)
        if execution_id
        else "snapshot:{}:{}:{}".format(
            _workspace_scope(path), session, path.stem
        )
    )
    return _record(
        _RUNTIME,
        path,
        _PROVIDER,
        _model(value, capped, budget),
        session,
        _timestamp(value.get("startTime") or value.get("createdAt"), path),
        tokens,
        "kiro:" + identity,
        source_kind="kiro_snapshot",
        confidence="estimated",
    )


def _cli_records(path: Path, value: Mapping[str, object]):
    state = _mapping(value.get("session_state"))
    if state is None:
        return None
    model_state = _mapping(state.get("rts_model_state")) or {}
    model_info = _mapping(model_state.get("model_info")) or {}
    metadata = _mapping(state.get("conversation_metadata")) or {}
    turns = metadata.get("user_turn_metadatas")
    if not isinstance(turns, list):
        return (), False
    session = _text(value.get("session_id")) or path.stem or "unknown"
    model = _text(model_info.get("model_id")) or "auto"
    context_window = model_info.get("context_window_tokens")
    content = {}
    sidecar = read_json_lines(path.with_suffix(".jsonl"))
    pending = (0, None)
    for row in sidecar.values:
        data = _mapping(row.get("data")) or {}
        kind = _text(row.get("kind"))
        message_id = _text(data.get("message_id"))
        if message_id is None:
            continue
        size = _content_chars(data.get("content"))
        if kind == "Prompt":
            meta = _mapping(data.get("meta")) or {}
            pending = (size, meta.get("timestamp"))
        elif kind == "AssistantMessage":
            content[message_id] = (pending[0], size, pending[1])
            pending = (0, None)
    records = []
    for index, raw in enumerate(turns[:_MAX_ROWS]):
        turn = _mapping(raw)
        if turn is None:
            continue
        prompt_chars = output_chars = 0
        timestamp_value = None
        message_ids = turn.get("message_ids")
        if isinstance(message_ids, list):
            for message_id in message_ids:
                if not isinstance(message_id, str) or message_id not in content:
                    continue
                prompt, output, prompt_timestamp = content[message_id]
                prompt_chars += prompt
                output_chars += output
                timestamp_value = timestamp_value or prompt_timestamp
        explicit_input = safe_int(turn.get("input_token_count"))
        explicit_output = safe_int(turn.get("output_token_count"))
        input_tokens = explicit_input or _percentage_tokens(
            context_window, turn.get("context_usage_percentage")
        ) or _estimate(prompt_chars)
        output_tokens = explicit_output or _estimate(output_chars)
        tokens = TokenBreakdown(input_tokens, output_tokens)
        if not tokens.total:
            continue
        exact = explicit_input > 0 and explicit_output > 0
        records.append(
            _record(
                _RUNTIME,
                path,
                _PROVIDER,
                model,
                session,
                _timestamp(timestamp_value or turn.get("end_timestamp"), path),
                tokens,
                "kiro:turn:{}:{}".format(session, index),
                source_kind="kiro_cli",
                confidence="exact" if exact else "estimated",
            )
        )
    return tuple(records), len(turns) > _MAX_ROWS


def _workspace_session_record(
    path: Path, value: Mapping[str, object]
) -> Optional[UsageRecord]:
    history = value.get("history")
    if not isinstance(history, list) or (
        value.get("sessionId") is None and value.get("selectedModel") is None
    ):
        return None
    prompt_chars = response_chars = 0
    for raw in history[:_MAX_ROWS]:
        entry = _mapping(raw) or {}
        logs = entry.get("promptLogs")
        if isinstance(logs, list):
            for raw_log in logs[:_MAX_ROWS]:
                log = _mapping(raw_log) or {}
                prompt_chars += _chars(log.get("prompt"))
        message = _mapping(entry.get("message")) or {}
        if _text(message.get("role")) == "assistant":
            response_chars += _content_chars(message.get("content"))
    tokens = TokenBreakdown(_estimate(prompt_chars), _estimate(response_chars))
    if not tokens.total:
        return None
    session = _text(value.get("sessionId")) or path.stem or "unknown"
    return _record(
        _RUNTIME,
        path,
        _PROVIDER,
        _text(value.get("selectedModel")) or "auto",
        session,
        _timestamp(value.get("createdAt"), path),
        tokens,
        "kiro:workspace:{}:{}".format(_workspace_scope(path), session),
        source_kind="kiro_snapshot",
        confidence="estimated",
    )


def _ide_records(path: Path, value: Mapping[str, object]):
    if path.name != "session.json" or not path.parent.name.startswith("sess_"):
        return None
    session = _text(value.get("id")) or path.parent.name
    model = _text(value.get("modelId"))
    sidecar = read_json_lines(path.with_name("messages.jsonl"))
    turns = []
    current = {
        "prompt": 0,
        "assistant": 0,
        "timestamp": None,
        "end": None,
        "percentage": 0,
        "elapsed": 0,
    }
    tree_capped = [False]
    for row in sidecar.values:
        payload = _mapping(row.get("payload")) or row
        if model is None:
            candidate_model = _model(payload, tree_capped)
            if candidate_model != "auto":
                model = candidate_model
        role = (
            _text(payload.get("type"))
            or _text(payload.get("role"))
            or ""
        ).lower()
        size = _content_chars(payload.get("content"))
        if role in ("user", "human", "prompt"):
            if current["assistant"]:
                turns.append(current)
                current = {
                    "prompt": 0, "assistant": 0, "timestamp": None,
                    "end": None, "percentage": 0, "elapsed": 0,
                }
            current["prompt"] += size
            current["timestamp"] = current["timestamp"] or row.get("timestamp")
        elif role in ("assistant", "bot", "response"):
            current["assistant"] += size
        elif role == "tool_call":
            args = payload.get("args")
            current["assistant"] += (
                len(args)
                if isinstance(args, str)
                else len(json.dumps(args, sort_keys=True, separators=(",", ":")))
                if args is not None
                else 0
            )
        elif (
            role == "session_metadata"
            and _text(payload.get("key")) == "contextUsage"
        ):
            usage = _mapping(payload.get("value")) or {}
            current["percentage"] = usage.get("usagePercentage") or 0
        elif role == "usage_summary":
            current["elapsed"] = safe_int(payload.get("elapsedTime"))
        elif role == "turn_end":
            current["end"] = row.get("timestamp")
            if current["prompt"] or current["assistant"]:
                turns.append(current)
            current = {
                "prompt": 0, "assistant": 0, "timestamp": None,
                "end": None, "percentage": 0, "elapsed": 0,
            }
    if current["prompt"] or current["assistant"]:
        turns.append(current)
    records = []
    for index, turn in enumerate(turns[:_MAX_ROWS]):
        timestamp = _timestamp(
            turn["timestamp"] or turn["end"] or value.get("createdAt"), path
        )
        if not turn["timestamp"] and turn["end"] and turn["elapsed"]:
            try:
                timestamp -= timedelta(milliseconds=turn["elapsed"])
            except (OverflowError, TypeError):
                pass
        records.append(_record(
            _RUNTIME,
            path,
            _PROVIDER,
            model or "auto",
            session,
            timestamp,
            TokenBreakdown(
                _percentage_tokens(200_000, turn["percentage"])
                or _estimate(turn["prompt"]),
                _estimate(turn["assistant"]),
            ),
            "kiro:turn:{}:{}".format(session, index),
            source_kind="kiro_ide",
            confidence="estimated",
        ))
    return tuple(records), len(turns) > _MAX_ROWS or tree_capped[0]


def _json_path(path: Path):
    result = read_json(path)
    if result.error_code:
        return (), False, False, result.error_code.startswith("io_error:")
    value = _mapping(result.value)
    if value is None:
        return (), False, False, False
    tree_capped = [False]
    execution = _execution_record(path, value, tree_capped)
    if execution is not None or (
        _text(value.get("executionId")) is not None
        and isinstance(value.get("actions"), list)
    ):
        return (
            ((execution,) if execution else ()),
            True,
            tree_capped[0],
            False,
        )
    if value.get("version") is not None and value.get("executions") is not None:
        return (), True, False, False
    cli_result = _cli_records(path, value)
    if cli_result is not None:
        cli, capped = cli_result
        sidecar_path = path.with_suffix(".jsonl")
        sidecar = read_json_lines(sidecar_path) if sidecar_path.is_file() else None
        return (
            cli,
            True,
            capped or bool(sidecar and sidecar.partial),
            bool(
                sidecar
                and sidecar.error_code
                and sidecar.error_code.startswith("io_error:")
            ),
        )
    ide_result = _ide_records(path, value)
    if ide_result is not None:
        ide, capped = ide_result
        sidecar_path = path.with_name("messages.jsonl")
        sidecar = read_json_lines(sidecar_path) if sidecar_path.is_file() else None
        return (
            ide,
            True,
            capped or bool(sidecar and sidecar.partial),
            bool(
                sidecar
                and sidecar.error_code
                and sidecar.error_code.startswith("io_error:")
            ),
        )
    workspace = _workspace_session_record(path, value)
    if workspace is not None:
        history = value.get("history")
        nested_capped = any(
            isinstance((_mapping(entry) or {}).get("promptLogs"), list)
            and len((_mapping(entry) or {}).get("promptLogs")) > _MAX_ROWS
            for entry in history[:_MAX_ROWS]
        ) if isinstance(history, list) else False
        return (
            (workspace,),
            True,
            (
                isinstance(history, list)
                and (len(history) > _MAX_ROWS or nested_capped)
            ),
            False,
        )
    snapshot = _snapshot_record(path, value, tree_capped)
    return (
        ((snapshot,) if snapshot else ()),
        snapshot is not None,
        tree_capped[0],
        False,
    )


def _sqlite_path(path: Path):
    connection = open_sqlite_readonly(path)
    try:
        schema = sqlite_schema(connection)
        columns = set(schema.get("conversations_v2", ()))
        if not {"key", "conversation_id", "value"}.issubset(columns):
            return (), False, False, False
        query = (
            "SELECT {key}, {conversation}, length(CAST({value} AS BLOB)) "
            "FROM {table} ORDER BY rowid LIMIT ?"
        ).format(
            key=quote_identifier("key"),
            conversation=quote_identifier("conversation_id"),
            value=quote_identifier("value"),
            table=quote_identifier("conversations_v2"),
        )
        records = []
        total_bytes = 0
        partial = False
        for row_index, (key_raw, conversation_raw, byte_count) in enumerate(
            connection.execute(query, (_MAX_ROWS + 1,))
        ):
            if row_index >= _MAX_ROWS:
                partial = True
                break
            if len(records) >= _MAX_ROWS:
                partial = True
                break
            if type(byte_count) is not int or not (0 <= byte_count <= MAX_JSON_BYTES):
                partial = True
                continue
            if total_bytes + byte_count > MAX_JSON_BYTES:
                partial = True
                break
            total_bytes += byte_count
            row = connection.execute(
                "SELECT {value} FROM {table} WHERE {key} IS ? AND {conversation} IS ? "
                "AND length(CAST({value} AS BLOB)) = ? LIMIT 1".format(
                    value=quote_identifier("value"),
                    table=quote_identifier("conversations_v2"),
                    key=quote_identifier("key"),
                    conversation=quote_identifier("conversation_id"),
                ),
                (key_raw, conversation_raw, byte_count),
            ).fetchone()
            if row is None or not isinstance(row[0], str):
                partial = True
                continue
            try:
                value = json.loads(row[0])
            except (ValueError, RecursionError):
                partial = True
                continue
            conversation = _text(conversation_raw)
            root = _mapping(value)
            if conversation is None or root is None:
                partial = True
                continue
            model_info = _mapping(root.get("model_info")) or {}
            model = _text(model_info.get("model_id")) or "auto"
            context_window = model_info.get("context_window_tokens")
            history = root.get("history")
            if not isinstance(history, list):
                continue
            remaining = _MAX_ROWS - len(records)
            if len(history) > remaining:
                partial = True
            for index, raw_turn in enumerate(history[:remaining]):
                turn = _mapping(raw_turn) or {}
                metadata = _mapping(turn.get("request_metadata"))
                if metadata is None:
                    continue
                tokens = TokenBreakdown(
                    _percentage_tokens(
                        context_window,
                        metadata.get("context_usage_percentage"),
                    ),
                    _estimate(safe_int(metadata.get("response_size"))),
                )
                if not tokens.total:
                    continue
                records.append(
                    _record(
                        _RUNTIME,
                        path,
                        _PROVIDER,
                        model,
                        conversation,
                        _timestamp(
                            metadata.get("request_start_timestamp_ms")
                            or metadata.get("stream_end_timestamp_ms"),
                            path,
                        ),
                        tokens,
                        "kiro:turn:{}:{}".format(conversation, index),
                        source_kind="kiro_sqlite",
                        confidence="estimated",
                    )
                )
        return tuple(records), True, partial, False
    except sqlite3.DatabaseError:
        return (), True, False, True
    finally:
        connection.close()


def _safe_path(path: Path):
    try:
        if path.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
            return _sqlite_path(path)
        if path.suffix.lower() in (".jsonl", ".ndjson"):
            # CLI/IDE sidecars are consumed with their bounded JSON header.
            return (), True, False, False
        return _json_path(path)
    except (OSError, SqliteReadError):
        return (), False, False, True


def parse_kiro(paths: Sequence[Path]) -> AdapterResult:
    existing = tuple(
        sorted(
            {Path(path) for path in paths if Path(path).is_file()},
            key=lambda path: (str(path).casefold(), str(path)),
        )
    )
    parsed = []
    recognized = partial = failed = unsupported = False
    for path_index, path in enumerate(existing):
        records, known, incomplete, error = _safe_path(path)
        remaining = _MAX_ROWS - len(parsed)
        parsed.extend(records[:remaining])
        if len(records) > remaining:
            partial = True
        recognized |= known
        partial |= incomplete
        failed |= error
        unsupported |= not known
        if len(parsed) >= _MAX_ROWS:
            partial |= path_index + 1 < len(existing)
            break
    winners = {}
    executed_sessions = {
        record.session_id
        for record in parsed
        if record.source_kind == "kiro_execution"
    }
    for record in parsed:
        if (
            record.source_kind == "kiro_snapshot"
            and record.dedup_key.startswith("kiro:workspace:")
            and record.session_id in executed_sessions
        ):
            continue
        current = winners.get(record.dedup_key)
        if current is None or _PRIORITY[record.source_kind] > _PRIORITY[current.source_kind]:
            winners[record.dedup_key] = record
    records = tuple(
        sorted(
            winners.values(),
            key=lambda record: (record.timestamp, record.dedup_key),
        )
    )
    if failed and not records and not recognized:
        status, code = AdapterStatus.ERROR, "read_error"
    elif partial or (failed and records) or (unsupported and records):
        status, code = AdapterStatus.PARTIAL, "partial_source"
    elif records:
        status, code = AdapterStatus.OK, "ok"
    elif not existing or recognized:
        status, code = AdapterStatus.NO_DATA, "no_data"
    else:
        status, code = AdapterStatus.UNSUPPORTED_FORMAT, "unsupported_format"
    return AdapterResult(
        _RUNTIME,
        status,
        records,
        (
            Diagnostic(
                _RUNTIME,
                status,
                code,
                "kiro adapter completed",
                len(existing),
                len(records),
            ),
        ),
    )


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_kiro)
