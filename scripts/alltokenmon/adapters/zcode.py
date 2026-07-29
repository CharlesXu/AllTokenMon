"""Privacy-safe ZCode legacy JSONL and v2 SQLite adapter."""

import json
from pathlib import Path
from typing import Sequence, Tuple

from ..normalize import safe_int
from ..schema import TokenBreakdown, UsageRecord
from .amp import _jsonl, _mapping, _record, _result, _scan, _text, _timestamp
from .base import DiscoveryContext, SourceSpec
from .sqliteio import SqliteReadError, open_sqlite_readonly, sqlite_schema

_RUNTIME = "zcode"
_MAX_ROWS = 100_000


def _chars(value):
    if value is None or value == "" or value == [] or value == {}:
        return 0
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _field(value, names):
    for name in names:
        if name in value:
            return safe_int(value.get(name))
    return 0


def _tokens(value):
    usage = _mapping(value)
    if not usage:
        return None
    raw_input = _field(usage, ("input", "input_tokens", "prompt_tokens", "inputTokens"))
    raw_output = _field(usage, ("output", "output_tokens", "completion_tokens", "outputTokens"))
    cache_read = _field(usage, ("cache_read", "input_cache_read", "cache_read_tokens", "cacheReadTokens"))
    cache_write = _field(usage, ("cache_write", "input_cache_creation", "cache_write_tokens", "cacheCreationTokens"))
    reasoning = _field(usage, ("reasoning", "reasoningTokens"))
    if not any((raw_input, raw_output, cache_read, cache_write, reasoning)):
        return None
    total = None
    for name in ("total", "totalTokens"):
        if name in usage:
            total = safe_int(usage.get(name))
            break
    inclusive = raw_input + raw_output
    exclusive = inclusive + cache_read + cache_write + reasoning
    if total == inclusive and total != exclusive and (cache_read or cache_write or reasoning):
        raw_input = max(raw_input - cache_read - cache_write, 0)
        raw_output = max(raw_output - reasoning, 0)
    return TokenBreakdown(raw_input, raw_output, cache_read, cache_write, reasoning)


def _jsonl_path(path):
    values, partial, failed = _jsonl(path)
    session = None
    model = None
    context_chars = 0
    assistant_index = 0
    records = []
    recognized = False
    for value in values:
        role = value.get("role")
        if role is None:
            continue
        recognized = True
        session = session or _text(value.get("sessionId"))
        if _text(value.get("model")):
            model = _text(value.get("model")).lower()
        chars = _chars(value.get("content"))
        if role != "assistant":
            context_chars += chars
            continue
        tokens = _tokens(value.get("usage")) or _tokens(value.get("token_usage"))
        if tokens is None:
            tokens = TokenBreakdown(
                input=(context_chars + 3) // 4,
                output=(chars + 3) // 4,
            )
            if tokens.total == 0:
                context_chars += chars
                continue
        context_chars += chars
        active_session = session or path.stem or "unknown"
        records.append(_record(
            _RUNTIME, path, "zhipu", model or "glm-5.2", active_session,
            _timestamp(value.get("timestamp"), path), tokens,
            "{}:{}".format(active_session, assistant_index),
        ))
        assistant_index += 1
    return tuple(records), recognized, partial, failed


def _sqlite_path(path):
    try:
        connection = open_sqlite_readonly(path)
    except SqliteReadError:
        return (), False, False, True
    try:
        schema = sqlite_schema(connection)
        columns = set(schema.get("model_usage", ()))
        required = {"id", "input_tokens", "output_tokens"}
        if not required.issubset(columns):
            return (), False, False, False
        names = (
            "id", "session_id", "turn_id", "model_id", "started_at",
            "completed_at", "duration_ms", "input_tokens", "output_tokens",
            "reasoning_tokens", "cache_read_input_tokens",
            "cache_creation_input_tokens", "computed_total_tokens",
        )
        selections = [
            name if name in columns else "NULL AS " + name for name in names
        ]
        rows = connection.execute(
            "SELECT " + ", ".join(selections)
            + " FROM model_usage ORDER BY COALESCE(completed_at, started_at, 0), id LIMIT ?",
            (_MAX_ROWS + 1,),
        )
        legacy = "computed_total_tokens" not in columns
        records = []
        partial = False
        for index, row in enumerate(rows):
            if index >= _MAX_ROWS:
                partial = True
                break
            (
                row_id, session, _turn, model, started, completed, duration,
                raw_input, raw_output, reasoning, cache_read, cache_write,
                computed_total,
            ) = row
            raw_input = safe_int(raw_input)
            raw_output = safe_int(raw_output)
            reasoning = safe_int(reasoning)
            cache_read = safe_int(cache_read)
            cache_write = safe_int(cache_write)
            if not any((raw_input, raw_output, reasoning, cache_read, cache_write)):
                continue
            if legacy:
                net_input = max(raw_input - cache_read - cache_write, 0)
                net_output = max(raw_output - reasoning, 0)
            else:
                net_input, net_output = raw_input, raw_output
                if computed_total is not None:
                    inclusive = raw_input + raw_output
                    exclusive = inclusive + reasoning + cache_read + cache_write
                    if safe_int(computed_total) == inclusive and inclusive != exclusive:
                        net_input = max(raw_input - cache_read - cache_write, 0)
                        net_output = max(raw_output - reasoning, 0)
            timestamp_value = started if safe_int(started) > 0 else completed
            records.append(_record(
                _RUNTIME, path, "zhipu", (_text(model) or "glm-5.2").lower(),
                _text(session) or "unknown", _timestamp(timestamp_value, path),
                TokenBreakdown(net_input, net_output, cache_read, cache_write, reasoning),
                "zcode-sqlite:{}".format(row_id if row_id is not None else index),
                source_kind="sqlite",
            ))
        return tuple(records), True, partial, False
    except Exception:
        return (), False, False, True
    finally:
        connection.close()


def _path(path):
    return _sqlite_path(path) if path.suffix in (".sqlite", ".db") else _jsonl_path(path)


def parse_zcode(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _path)


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_zcode)
