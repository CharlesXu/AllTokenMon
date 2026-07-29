"""Privacy-safe Gemini CLI JSON and streaming JSONL usage adapter."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence, Tuple

from ..discovery import discover
from ..normalize import parse_timestamp, safe_int, stable_key
from ..schema import (
    AdapterResult,
    AdapterStatus,
    Diagnostic,
    TokenBreakdown,
    UsageRecord,
)
from .base import DiscoveryContext, SourceSpec
from .jsonio import read_json, read_json_lines


_RUNTIME = "gemini"
_INPUT_KEYS = ("input", "prompt", "input_tokens", "prompt_tokens", "promptTokenCount")
_OUTPUT_KEYS = (
    "output",
    "candidates",
    "output_tokens",
    "completion_tokens",
    "candidates_tokens",
    "candidatesTokenCount",
)
_CACHE_KEYS = ("cached", "cached_tokens", "cachedContentTokenCount")
_REASONING_KEYS = ("thoughts", "reasoning", "thoughts_tokens", "reasoning_tokens")
_TOOL_KEYS = ("tool", "tool_tokens")
_TOTAL_KEYS = ("total", "totalTokenCount", "total_tokens")


def _mapping(value: object) -> Optional[Mapping[str, object]]:
    return value if isinstance(value, Mapping) else None


def _text(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _first(value: Mapping[str, object], keys: Tuple[str, ...]) -> Tuple[int, bool]:
    for key in keys:
        if key in value:
            return safe_int(value.get(key)), True
    return 0, False


def _fallback_timestamp(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except (OSError, OverflowError, ValueError):
        return datetime.fromtimestamp(0, timezone.utc)


def _timestamp(value: object, path: Path) -> datetime:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = value if value >= 1_000_000_000_000 else value * 1000
        try:
            return datetime.fromtimestamp(numeric / 1000, timezone.utc)
        except (OSError, OverflowError, ValueError):
            return _fallback_timestamp(path)
    if isinstance(value, str):
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    try:
        return parse_timestamp(value)
    except ValueError:
        return _fallback_timestamp(path)


def _tokens(
    value: object,
    *,
    headless: bool,
) -> Optional[TokenBreakdown]:
    raw = _mapping(value)
    if raw is None:
        return None
    input_tokens, has_input = _first(raw, _INPUT_KEYS)
    output, has_output = _first(raw, _OUTPUT_KEYS)
    cached, has_cache = _first(raw, _CACHE_KEYS)
    reasoning, has_reasoning = _first(raw, _REASONING_KEYS)
    tool, has_tool = _first(raw, _TOOL_KEYS)
    total, has_total = _first(raw, _TOTAL_KEYS)
    if not any((has_input, has_output, has_cache, has_reasoning, has_tool, has_total)):
        return None

    cache_inclusive = headless
    if not headless and has_total and cached > 0:
        inclusive_total = input_tokens + output + reasoning + tool
        exclusive_total = inclusive_total + cached
        cache_inclusive = total == inclusive_total and total != exclusive_total
    if cache_inclusive:
        input_tokens = max(input_tokens - min(input_tokens, cached), 0)
    return TokenBreakdown(
        input=input_tokens + tool,
        output=output,
        cache_read=cached,
        cache_write=0,
        reasoning=reasoning,
    )


def _headless_tokens(value: object) -> Optional[TokenBreakdown]:
    raw = _mapping(value)
    if raw is None:
        return None
    wrapped = _mapping(raw.get("tokens"))
    tokens = wrapped or raw
    prompt, has_prompt = _first(
        tokens, ("prompt", "input_tokens", "prompt_tokens")
    )
    net_input, has_net_input = _first(tokens, ("input",))
    input_tokens = prompt if has_prompt else net_input
    output, has_output = _first(tokens, _OUTPUT_KEYS)
    cached, has_cache = _first(tokens, _CACHE_KEYS)
    reasoning, has_reasoning = _first(tokens, _REASONING_KEYS)
    if not any(
        (has_prompt, has_net_input, has_output, has_cache, has_reasoning)
    ):
        return None
    input_includes_cache = has_prompt or wrapped is not None or not has_net_input
    if input_includes_cache:
        input_tokens = max(input_tokens - min(input_tokens, cached), 0)
    return TokenBreakdown(
        input=input_tokens,
        output=output,
        cache_read=cached,
        cache_write=0,
        reasoning=reasoning,
    )


def _record(
    path: Path,
    session_id: str,
    model: str,
    timestamp_value: object,
    tokens: TokenBreakdown,
    identity: str,
) -> Optional[UsageRecord]:
    if tokens.total == 0 and tokens.reasoning == 0:
        return None
    return UsageRecord(
        runtime=_RUNTIME,
        provider="google",
        model=model,
        session_id=session_id,
        timestamp=_timestamp(timestamp_value, path),
        tokens=tokens,
        message_count=1,
        source_kind="jsonl" if path.suffix == ".jsonl" else "json",
        source_path=str(path),
        dedup_key=identity,
        confidence="exact",
    )


def _stats_records(
    path: Path,
    stats_value: object,
    session_id: str,
    model_hint: Optional[str],
    timestamp_value: object,
    identity_prefix: str,
) -> Tuple[UsageRecord, ...]:
    stats = _mapping(stats_value)
    if stats is None:
        return ()
    models = _mapping(stats.get("models"))
    values = []
    if models:
        for model, data_value in sorted(models.items()):
            data = _mapping(data_value)
            if data is None:
                continue
            tokens = _headless_tokens(data)
            if tokens is None:
                continue
            record = _record(
                path,
                session_id,
                model,
                timestamp_value,
                tokens,
                identity_prefix + ":" + model,
            )
            if record is not None:
                values.append(record)
        if values:
            return tuple(values)
    tokens = _headless_tokens(stats)
    if tokens is None:
        return ()
    model = model_hint or "unknown"
    record = _record(
        path,
        session_id,
        model,
        timestamp_value,
        tokens,
        identity_prefix + ":" + model,
    )
    return (record,) if record is not None else ()


def _parse_conversation(
    path: Path, value: Mapping[str, object]
) -> Tuple[Tuple[UsageRecord, ...], bool]:
    messages = value.get("messages")
    session_id = _text(value.get("sessionId")) or _text(value.get("session_id"))
    if not isinstance(messages, list) or session_id is None:
        return (), False
    records = []
    for index, raw_message in enumerate(messages):
        message = _mapping(raw_message)
        if message is None:
            continue
        model = _text(message.get("model"))
        token_value = message.get("tokens")
        if model is None or token_value is None:
            continue
        tokens = _tokens(token_value, headless=False)
        if tokens is None:
            continue
        message_id = _text(message.get("id")) or str(index)
        record = _record(
            path,
            session_id,
            model,
            message.get("timestamp"),
            tokens,
            "gemini:{}:{}".format(session_id, message_id),
        )
        if record is not None:
            records.append(record)
    return tuple(records), True


def _parse_json(
    path: Path, value: Mapping[str, object]
) -> Tuple[Tuple[UsageRecord, ...], bool]:
    conversation, recognized = _parse_conversation(path, value)
    if recognized:
        return conversation, True

    session_id = (
        _text(value.get("sessionId"))
        or _text(value.get("session_id"))
        or path.stem
        or "unknown"
    )
    model = _text(value.get("model"))
    if value.get("tokens") is not None and model is not None:
        tokens = _tokens(value.get("tokens"), headless=False)
        if tokens is not None:
            identity = _text(value.get("id")) or stable_key(
                _RUNTIME, session_id, model, value.get("timestamp"), tokens
            )
            record = _record(
                path,
                session_id,
                model,
                value.get("timestamp") or value.get("created_at"),
                tokens,
                "gemini:{}:{}".format(session_id, identity),
            )
            return ((record,) if record else ()), True

    stats = value.get("stats")
    result = _mapping(value.get("result"))
    if stats is None and result is not None:
        stats = result.get("stats")
    if stats is not None:
        return (
            _stats_records(
                path,
                stats,
                session_id,
                model,
                value.get("timestamp") or value.get("created_at"),
                stable_key(_RUNTIME, session_id, "stats", path.name),
            ),
            True,
        )
    return (), False


def _parse_jsonl(path: Path):
    result = read_json_lines(path)
    session_id = path.stem or "unknown"
    current_model = None
    records = []
    direct_indices: Dict[Tuple[str, str], int] = {}
    recognized = False
    for index, value in enumerate(result.values):
        event_type = _text(value.get("type"))
        session_id = (
            _text(value.get("session_id"))
            or _text(value.get("sessionId"))
            or session_id
        )
        if event_type == "init":
            recognized = True
            current_model = _text(value.get("model")) or current_model
            continue

        if value.get("tokens") is not None or event_type == "gemini":
            recognized = True
            current_model = _text(value.get("model")) or current_model
            if current_model is None:
                continue
            tokens = _tokens(value.get("tokens"), headless=False)
            if tokens is None:
                continue
            message_id = _text(value.get("id"))
            identity = message_id or stable_key(
                _RUNTIME, session_id, index, current_model, value.get("timestamp"), tokens
            )
            record = _record(
                path,
                session_id,
                current_model,
                value.get("timestamp") or value.get("created_at"),
                tokens,
                "gemini:{}:{}".format(session_id, identity),
            )
            if record is None:
                continue
            replacement_key = (
                (session_id, message_id)
                if message_id is not None
                else None
            )
            if (
                replacement_key is not None
                and replacement_key in direct_indices
            ):
                records[direct_indices[replacement_key]] = record
            else:
                if replacement_key is not None:
                    direct_indices[replacement_key] = len(records)
                records.append(record)
            continue

        result_value = _mapping(value.get("result"))
        stats = value.get("stats")
        if stats is None and result_value is not None:
            stats = result_value.get("stats")
        if stats is not None:
            recognized = True
            records.extend(
                _stats_records(
                    path,
                    stats,
                    session_id,
                    current_model,
                    value.get("timestamp") or value.get("created_at"),
                    stable_key(_RUNTIME, session_id, "stats", path.name, index),
                )
            )
    return tuple(records), recognized, result.partial


def _diagnostic(status, code, source_count, record_count):
    return Diagnostic(
        _RUNTIME,
        status,
        code,
        "Gemini adapter completed",
        source_count,
        record_count,
    )


def parse_gemini(paths: Sequence[Path]) -> AdapterResult:
    existing = tuple(
        sorted(
            {Path(path) for path in paths if Path(path).is_file()},
            key=lambda path: (str(path).casefold(), str(path)),
        )
    )
    records = []
    recognized = False
    partial = False
    read_error = False
    unsupported_source = False
    for path in existing:
        if path.suffix == ".jsonl":
            parsed, known, incomplete = _parse_jsonl(path)
            records.extend(parsed)
            recognized = recognized or known
            partial = partial or incomplete
            continue
        result = read_json(path)
        if result.error_code:
            read_error = read_error or result.error_code.startswith("io_error:")
            unsupported_source = True
            continue
        if not isinstance(result.value, Mapping):
            unsupported_source = True
            continue
        parsed, known = _parse_json(path, result.value)
        records.extend(parsed)
        recognized = recognized or known
        unsupported_source = unsupported_source or not known

    unique = {}
    for record in records:
        unique[record.dedup_key] = record
    result_records = tuple(unique.values())
    if read_error and not result_records and not recognized:
        status, code = AdapterStatus.ERROR, "read_error"
    elif partial or (read_error and result_records) or (
        unsupported_source and result_records
    ):
        status, code = AdapterStatus.PARTIAL, "partial_source"
    elif result_records:
        status, code = AdapterStatus.OK, "ok"
    elif not existing:
        status, code = AdapterStatus.NO_DATA, "no_data"
    elif recognized:
        status, code = AdapterStatus.NO_DATA, "no_data"
    else:
        status, code = AdapterStatus.UNSUPPORTED_FORMAT, "unsupported_format"
    return AdapterResult(
        _RUNTIME,
        status,
        result_records,
        (_diagnostic(status, code, len(existing), len(result_records)),),
    )


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]) -> AdapterResult:
    paths = []
    for spec in specs:
        paths.extend(discover(spec, context))
    return parse_gemini(tuple(dict.fromkeys(paths)))
