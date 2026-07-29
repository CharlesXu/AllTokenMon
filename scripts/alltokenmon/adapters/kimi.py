"""Privacy-safe Kimi CLI and Kimi Code wire adapter."""

from pathlib import Path
from typing import Sequence, Tuple

from ..normalize import safe_int, stable_key
from ..schema import TokenBreakdown, UsageRecord
from .amp import (
    _jsonl,
    _mapping,
    _mtime,
    _parsed_timestamp,
    _record,
    _result,
    _scan,
    _text,
)
from .base import DiscoveryContext, SourceSpec

_RUNTIME = "kimi"


def _numeric_timestamp(value, path, divisor):
    try:
        seconds = float(value) / divisor
    except (TypeError, ValueError, OverflowError):
        return _mtime(path)
    return _parsed_timestamp(seconds) or _mtime(path)


def _tokens(value):
    usage = _mapping(value)
    if usage is None:
        return None
    tokens = TokenBreakdown(
        safe_int(usage.get("input_other", usage.get("inputOther"))),
        safe_int(usage.get("output")),
        safe_int(usage.get("input_cache_read", usage.get("inputCacheRead"))),
        safe_int(usage.get("input_cache_creation", usage.get("inputCacheCreation"))),
    )
    return tokens if tokens.total else None


def _is_code(path: Path) -> bool:
    return path.parent.parent.name == "agents" if len(path.parents) >= 2 else False


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    values, partial, failed = _jsonl(path)
    records = []
    recognized = False
    if _is_code(path):
        session = path.parent.parent.parent.name or "unknown"
        latest = None
        for index, value in enumerate(values):
            kind = value.get("type")
            if kind == "llm.request":
                raw = (_text(value.get("model")) or "").strip()
                model = raw[len("kimi-code/"):] if raw.startswith("kimi-code/") else raw
                if model and not (model.startswith("__") and model.endswith("__")):
                    latest = model
                recognized = True
                continue
            if kind != "usage.record":
                continue
            recognized = True
            if value.get("usageScope") != "turn":
                continue
            tokens = _tokens(value.get("usage"))
            if tokens is None:
                continue
            raw = (_text(value.get("model")) or "").strip()
            model = raw[len("kimi-code/"):] if raw.startswith("kimi-code/") else raw
            if not model or (model.startswith("__") and model.endswith("__")):
                model = latest or "kimi-for-coding"
            time = value.get("time")
            timestamp = (
                _numeric_timestamp(time, path, 1000)
                if isinstance(time, (int, float)) and not isinstance(time, bool)
                else _mtime(path)
            )
            records.append(_record(
                _RUNTIME, path, "moonshot", model, session, timestamp, tokens,
                stable_key(_RUNTIME, session, index, time, model, tokens),
            ))
        return tuple(records), recognized, partial, failed

    session = path.parent.name or "unknown"
    config = path.parent.parent.parent.parent / "config.json"
    model = "kimi-for-coding"
    try:
        import json
        if config.stat().st_size <= 1024 * 1024:
            value = json.loads(config.read_text(encoding="utf-8"))
            model = _text(value.get("model")) if isinstance(value, dict) else model
            model = model or "kimi-for-coding"
    except (OSError, UnicodeError, ValueError):
        pass
    keyed = {}
    for index, value in enumerate(values):
        if value.get("type") == "metadata":
            recognized = True
            continue
        message = _mapping(value.get("message"))
        payload = _mapping(message.get("payload")) if message else None
        if not message or message.get("type") != "StatusUpdate" or not payload:
            continue
        recognized = True
        tokens = _tokens(payload.get("token_usage"))
        if tokens is None:
            continue
        raw_time = value.get("timestamp")
        timestamp = (
            _numeric_timestamp(raw_time, path, 1)
            if isinstance(raw_time, (int, float)) and not isinstance(raw_time, bool)
            else _mtime(path)
        )
        message_id = _text(payload.get("message_id"))
        key = message_id or stable_key(_RUNTIME, session, index, raw_time, tokens)
        record = _record(
            _RUNTIME, path, "moonshot", model, session, timestamp, tokens,
            "kimi:{}:{}".format(session, key),
        )
        existing = keyed.get(key)
        if existing is None or (
            sum(vars(record.tokens).values()), record.timestamp
        ) >= (
            sum(vars(existing.tokens).values()), existing.timestamp
        ):
            keyed[key] = record
    return tuple(keyed.values()), recognized, partial, failed


def parse_kimi(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _path)


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_kimi)
