"""Privacy-safe GitHub Copilot local usage reconciliation.

Authoritative-source order is OTEL, VS Code chat, then Desktop session totals.
OTEL and VS Code records reconcile only through explicit response/request IDs.
Desktop aggregates remain distinct because a session ID does not identify one
event; timestamps and token counts are never dedup keys.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import sqlite3
from typing import Mapping, Optional, Sequence, Tuple

from ..normalize import safe_int, stable_key
from ..schema import (
    AdapterResult,
    AdapterStatus,
    Diagnostic,
    TokenBreakdown,
    UsageRecord,
)
from .amp import _mapping, _provider, _record, _scan, _text, _timestamp
from .base import DiscoveryContext, SourceSpec
from .jsonio import read_json_lines
from .sqliteio import SqliteReadError, open_sqlite_readonly, quote_identifier, sqlite_schema

_RUNTIME = "copilot"
_MAX_ROWS = 100_000
_DESKTOP_COLUMNS = {
    "id",
    "model",
    "total_input_tokens",
    "total_output_tokens",
    "total_cached_tokens",
    "total_reasoning_tokens",
    "created_at",
}
_PRIORITY = {"desktop_sqlite": 1, "vscode_chat": 2, "otel_jsonl": 3}


def _normalized_tokens(
    input_tokens: object,
    output_tokens: object,
    cache_read: object = 0,
    cache_write: object = 0,
    reasoning: object = 0,
) -> TokenBreakdown:
    inclusive_input = safe_int(input_tokens)
    cached = min(inclusive_input, safe_int(cache_read))
    return TokenBreakdown(
        inclusive_input - cached,
        safe_int(output_tokens),
        safe_int(cache_read),
        safe_int(cache_write),
        safe_int(reasoning),
    )


def _nonzero_id(value: object) -> Optional[str]:
    text = _text(value)
    if text is None or all(character == "0" for character in text):
        return None
    return text


def _otel_kind(row: Mapping[str, object], attrs: Mapping[str, object]) -> int:
    name = _text(row.get("name")) or ""
    body = _text(row.get("body")) or _text(row.get("_body")) or ""
    event = _text(attrs.get("event.name"))
    operation = _text(attrs.get("gen_ai.operation.name"))
    is_span = row.get("type") == "span" or bool(
        name
        and (
            row.get("traceId")
            or row.get("spanId")
            or row.get("startTime")
            or row.get("endTime")
        )
    )
    if is_span and (operation == "chat" or name.startswith("chat ")):
        return 4
    if not is_span and (
        event == "gen_ai.client.inference.operation.details"
        or body.startswith("GenAI inference:")
    ):
        return 3
    if not is_span and (
        event == "copilot_chat.agent.turn"
        or body.startswith("copilot_chat.agent.turn")
    ):
        return 2
    if is_span and (operation == "invoke_agent" or name.startswith("invoke_agent ")):
        return 1
    return 0


def _first(attrs: Mapping[str, object], names: Sequence[str]) -> object:
    for name in names:
        if name in attrs:
            return attrs[name]
    return 0


def _time_parts(value: object) -> Optional[datetime]:
    if not isinstance(value, list) or not value:
        return None
    seconds = safe_int(value[0])
    nanos = safe_int(value[1]) if len(value) > 1 else 0
    try:
        return datetime.fromtimestamp(
            seconds + nanos / 1_000_000_000.0, timezone.utc
        )
    except (OSError, OverflowError, ValueError):
        return None


def _duration_ms(value: object) -> int:
    if isinstance(value, list) and value:
        return safe_int(value[0]) * 1000 + (
            safe_int(value[1]) // 1_000_000 if len(value) > 1 else 0
        )
    if isinstance(value, bool):
        return 0
    try:
        duration = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    if not math.isfinite(duration) or duration <= 0:
        return 0
    return int(duration / 1_000_000.0) if duration >= 1_000_000 else int(duration)


def _otel_timestamp(row: Mapping[str, object], path: Path) -> datetime:
    start = _time_parts(row.get("startTime"))
    if start is not None:
        return start
    end = _time_parts(row.get("endTime"))
    if end is not None:
        duration = _duration_ms(row.get("duration"))
        if duration:
            try:
                return end - timedelta(milliseconds=duration)
            except OverflowError:
                return end
        return end
    for name in ("hrTime", "_hrTime", "time"):
        parsed = _time_parts(row.get(name))
        if parsed is not None:
            return parsed
    for name in ("timestamp", "observedTimestamp"):
        if row.get(name) is not None:
            value = row.get(name)
            if isinstance(value, bool):
                continue
            try:
                raw = int(value)
            except (TypeError, ValueError, OverflowError):
                continue
            magnitude = abs(raw)
            if magnitude >= 100_000_000_000_000_000:
                millis = raw // 1_000_000
            elif magnitude >= 100_000_000_000_000:
                millis = raw // 1_000
            elif magnitude >= 100_000_000_000:
                millis = raw
            else:
                millis = raw * 1000
            return _timestamp(millis, path)
    nanos = safe_int(row.get("timeUnixNano"))
    if nanos:
        return _timestamp(nanos // 1_000_000, path)
    return _timestamp(None, path)


def _otel_rows(
    path: Path, rows: Sequence[Mapping[str, object]]
) -> Tuple[Tuple[UsageRecord, ...], bool]:
    contexts = {}
    session_names = (
        "gen_ai.response.id",
        "github.copilot.interaction_id",
        "gen_ai.conversation.id",
        "copilot_chat.session_id",
        "copilot_chat.chat_session_id",
        "session.id",
    )
    for row in rows:
        attrs = _mapping(row.get("attributes"))
        context = _mapping(row.get("spanContext")) or {}
        trace_id = _nonzero_id(row.get("traceId")) or _nonzero_id(
            context.get("traceId")
        )
        if attrs is None or trace_id is None:
            continue
        model = (
            _text(attrs.get("gen_ai.response.model"))
            or _text(attrs.get("gen_ai.request.model"))
        )
        session = None
        session_priority = -1
        for priority, name in enumerate(session_names):
            candidate = _text(attrs.get(name))
            if candidate is not None and priority > session_priority:
                session, session_priority = candidate, priority
        current = contexts.setdefault(trace_id, [None, None, -1])
        if current[0] is None and model is not None:
            current[0] = model
        if session is not None and session_priority > current[2]:
            current[1], current[2] = session, session_priority

    candidates = []
    recognized = False
    for index, row in enumerate(rows):
        attrs = _mapping(row.get("attributes"))
        if attrs is None:
            continue
        lane = _otel_kind(row, attrs)
        if lane == 0:
            continue
        recognized = True
        tokens = _normalized_tokens(
            attrs.get("gen_ai.usage.input_tokens"),
            attrs.get("gen_ai.usage.output_tokens"),
            _first(
                attrs,
                (
                    "gen_ai.usage.cache_read.input_tokens",
                    "gen_ai.usage.cache_read_input_tokens",
                ),
            ),
            _first(
                attrs,
                (
                    "gen_ai.usage.cache_write.input_tokens",
                    "gen_ai.usage.cache_creation.input_tokens",
                    "gen_ai.usage.cache_write_input_tokens",
                    "gen_ai.usage.cache_creation_input_tokens",
                ),
            ),
            _first(
                attrs,
                (
                    "gen_ai.usage.reasoning.output_tokens",
                    "gen_ai.usage.reasoning_tokens",
                ),
            ),
        )
        if not tokens.total:
            continue
        trace_id = _nonzero_id(row.get("traceId"))
        context = _mapping(row.get("spanContext")) or {}
        trace_id = trace_id or _nonzero_id(context.get("traceId"))
        span_id = _nonzero_id(row.get("spanId")) or _nonzero_id(context.get("spanId"))
        response_id = _text(attrs.get("gen_ai.response.id"))
        trace_context = contexts.get(trace_id, (None, None, -1))
        session = (
            _text(attrs.get("gen_ai.conversation.id"))
            or _text(attrs.get("copilot_chat.session_id"))
            or _text(attrs.get("copilot_chat.chat_session_id"))
            or _text(attrs.get("session.id"))
            or _text(attrs.get("github.copilot.interaction_id"))
            or response_id
            or trace_context[1]
            or trace_id
            or "unknown-session"
        )
        if response_id:
            identity = "response:" + response_id
        elif trace_id and span_id:
            identity = "span:{}:{}".format(trace_id, span_id)
        elif span_id:
            identity = "span:{}:{}".format(session, span_id)
        else:
            identity = "local:" + stable_key(path, index)
        model = (
            _text(attrs.get("gen_ai.response.model"))
            or _text(attrs.get("gen_ai.request.model"))
            or trace_context[0]
            or "unknown"
        )
        candidate = _record(
            _RUNTIME,
            path,
            _provider(model, "github-copilot"),
            model,
            session,
            _otel_timestamp(row, path),
            tokens,
            "copilot:" + identity,
            source_kind="otel_jsonl",
        )
        candidates.append((lane, trace_id, response_id, identity, candidate))

    traces_by_lane = {
        lane: {item[1] for item in candidates if item[0] == lane and item[1]}
        for lane in range(1, 5)
    }
    responses_by_lane = {
        lane: {item[2] for item in candidates if item[0] == lane and item[2]}
        for lane in range(1, 5)
    }
    winners = {}
    for lane, trace_id, response_id, identity, record in candidates:
        if any(
            (trace_id and trace_id in traces_by_lane[higher])
            or (response_id and response_id in responses_by_lane[higher])
            for higher in range(lane + 1, 5)
        ):
            continue
        current = winners.get(identity)
        if current is None or lane > current[0]:
            winners[identity] = (lane, record)
        elif lane == current[0]:
            prior = current[1]
            winners[identity] = (
                lane,
                replace(
                    prior,
                    timestamp=min(prior.timestamp, record.timestamp),
                    tokens=_normalized_tokens(
                        max(
                            prior.tokens.input + prior.tokens.cache_read,
                            record.tokens.input + record.tokens.cache_read,
                        ),
                        max(prior.tokens.output, record.tokens.output),
                        max(prior.tokens.cache_read, record.tokens.cache_read),
                        max(prior.tokens.cache_write, record.tokens.cache_write),
                        max(prior.tokens.reasoning, record.tokens.reasoning),
                    ),
                ),
            )
    return tuple(value[1] for value in winners.values()), recognized


def _vscode_rows(
    path: Path, rows: Sequence[Mapping[str, object]]
) -> Tuple[Tuple[UsageRecord, ...], bool, bool]:
    requests = []
    recognized = False
    partial = False
    for row in rows:
        kind = safe_int(row.get("kind"))
        values = None
        if kind == 0:
            root = _mapping(row.get("v"))
            values = root.get("requests") if root else None
        elif kind == 2:
            key = row.get("k")
            if isinstance(key, list) and key and key[0] == "requests":
                values = row.get("v")
        if isinstance(values, list):
            recognized = True
            remaining = _MAX_ROWS - len(requests)
            requests.extend(values[:remaining])
            if len(values) > remaining:
                partial = True
                break
    records = []
    session = path.stem or "unknown"
    for index, raw in enumerate(requests):
        request = _mapping(raw)
        if request is None:
            continue
        metadata = _mapping((_mapping(request.get("result")) or {}).get("metadata")) or {}
        model_raw = _text(request.get("modelId"))
        resolved = _text(metadata.get("resolvedModel"))
        if resolved is None and not (model_raw and model_raw.startswith("copilot/")):
            continue
        model = resolved or model_raw.removeprefix("copilot/")  # type: ignore[union-attr]
        tokens = TokenBreakdown(
            safe_int(request.get("promptTokens") or metadata.get("promptTokens")),
            safe_int(request.get("completionTokens") or metadata.get("outputTokens")),
            reasoning=sum(
                safe_int((_mapping((_mapping(item) or {}).get("thinking")) or {}).get("tokens"))
                for item in (
                    metadata.get("toolCallRounds")
                    if isinstance(metadata.get("toolCallRounds"), list)
                    else ()
                )
            ),
        )
        if not tokens.total:
            continue
        request_id = _text(request.get("requestId"))
        identity = (
            "response:" + request_id
            if request_id
            else "vscode:{}:{}".format(session, index)
        )
        records.append(
            _record(
                _RUNTIME,
                path,
                _provider(model, "github-copilot"),
                model,
                session,
                _timestamp(request.get("timestamp"), path),
                tokens,
                "copilot:" + identity,
                source_kind="vscode_chat",
            )
        )
    return tuple(records), recognized, partial


def _jsonl_path(path: Path):
    result = read_json_lines(path)
    otel, otel_known = _otel_rows(path, result.values)
    vscode, vscode_known, vscode_partial = _vscode_rows(path, result.values)
    failed = bool(result.error_code and result.error_code.startswith("io_error:"))
    return (
        (*otel, *vscode),
        otel_known or vscode_known,
        result.partial or vscode_partial,
        failed,
    )


def _desktop_path(path: Path):
    connection = open_sqlite_readonly(path)
    try:
        schema = sqlite_schema(connection)
        if not _DESKTOP_COLUMNS.issubset(set(schema.get("sessions", ()))):
            return (), False, False, False
        columns = {name: quote_identifier(name) for name in _DESKTOP_COLUMNS}
        query = (
            "SELECT {id}, {model}, {input}, {output}, {cached}, {reasoning}, {created} "
            "FROM {table} ORDER BY rowid LIMIT ?"
        ).format(
            id=columns["id"],
            model=columns["model"],
            input=columns["total_input_tokens"],
            output=columns["total_output_tokens"],
            cached=columns["total_cached_tokens"],
            reasoning=columns["total_reasoning_tokens"],
            created=columns["created_at"],
            table=quote_identifier("sessions"),
        )
        records = []
        partial = False
        metadata_failed = False
        for index, row in enumerate(connection.execute(query, (_MAX_ROWS + 1,))):
            if index >= _MAX_ROWS:
                partial = True
                break
            session = _text(row[0])
            if session is None:
                continue
            model = _text(row[1]) or "auto"
            state_root = path.parent / "session-state"
            events = state_root / session / "events.jsonl"
            safe_component = (
                session not in (".", "..")
                and Path(session).name == session
                and "/" not in session
                and "\\" not in session
            )
            safe_events = False
            if safe_component and events.is_file():
                try:
                    parent_root = path.parent.resolve()
                    state_resolved = state_root.resolve()
                    event_resolved = events.resolve()
                    state_resolved.relative_to(parent_root)
                    event_resolved.relative_to(state_resolved)
                    safe_events = not (
                        state_root.is_symlink()
                        or events.parent.is_symlink()
                        or events.is_symlink()
                    )
                except (OSError, ValueError):
                    safe_events = False
            if safe_events:
                event_result = read_json_lines(events)
                partial |= event_result.partial
                metadata_failed |= bool(
                    event_result.error_code
                    and event_result.error_code.startswith("io_error:")
                )
                for event in event_result.values:
                    if _text(event.get("type")) != "session.model_change":
                        continue
                    data = _mapping(event.get("data")) or {}
                    changed = _text(data.get("newModel"))
                    if changed and changed != "auto":
                        model = changed
            tokens = _normalized_tokens(row[2], row[3], row[4], 0, row[5])
            if not tokens.total:
                continue
            records.append(
                _record(
                    _RUNTIME,
                    path,
                    _provider(model, "github-copilot"),
                    model,
                    session,
                    _timestamp(row[6], path),
                    tokens,
                    "copilot:desktop:" + session,
                    source_kind="desktop_sqlite",
                )
            )
        return tuple(records), True, partial, metadata_failed
    except sqlite3.DatabaseError:
        return (), True, False, True
    finally:
        connection.close()


def _safe_path(path: Path):
    try:
        if path.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
            return _desktop_path(path)
        return _jsonl_path(path)
    except (OSError, SqliteReadError):
        return (), False, False, True


def parse_copilot(paths: Sequence[Path]) -> AdapterResult:
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
    for record in parsed:
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
                "copilot adapter completed",
                len(existing),
                len(records),
            ),
        ),
    )


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_copilot)
