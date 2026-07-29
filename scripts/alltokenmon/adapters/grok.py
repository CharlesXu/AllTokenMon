"""Privacy-safe Grok Build cumulative update adapter."""

from pathlib import Path
from typing import Mapping, Sequence, Tuple

from ..normalize import safe_int
from ..schema import TokenBreakdown, UsageRecord
from .amp import _jsonl, _mapping, _record, _result, _scan, _text, _timestamp
from .base import DiscoveryContext, SourceSpec
from .jsonio import read_json

_RUNTIME = "grok"


def _at(value: Mapping[str, object], *keys):
    current = value
    for key in keys:
        current = _mapping(current.get(key))
        if current is None:
            return None
    return current


def _first(value, paths):
    for path in paths:
        current = value
        for key in path:
            current = current.get(key) if isinstance(current, Mapping) else None
        if current is not None:
            return current
    return None


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    if path.name != "updates.jsonl":
        return (), False, False, False
    values, partial, failed = _jsonl(path)
    session = path.parent.name or "unknown"
    model = "grok-unknown"
    fallback_value = None
    summary = read_json(path.with_name("summary.json"))
    summary_value = _mapping(summary.value)
    if summary_value:
        model = (
            _text(summary_value.get("current_model_id"))
            or _text(summary_value.get("model_id"))
            or model
        )
        fallback_value = (
            summary_value.get("updated_at")
            or summary_value.get("created_at")
        )
    events_path = path.with_name("events.jsonl")
    if events_path.is_file():
        event_values, _, _ = _jsonl(events_path)
        for event in event_values[:500]:
            model = _text(event.get("model_id")) or model
            fallback_value = event.get("ts") or fallback_value
            if session == "unknown":
                session = _text(event.get("session_id")) or session
    recognized = bool(values)
    last_total = None
    active = None
    turn_index = 0
    rows = []
    last_timestamp = _timestamp(fallback_value, path)
    for value in values:
        raw_model = _first(value, (
            ("params", "update", "_meta", "modelId"),
            ("params", "_meta", "modelId"), ("params", "modelId"),
            ("model_id",), ("modelId",), ("model",),
        ))
        if _text(raw_model):
            model = _text(raw_model)
        raw_time = _first(value, (
            ("params", "_meta", "agentTimestampMs"),
            ("params", "update", "_meta", "agentTimestampMs"),
            ("params", "timestamp"), ("timestamp",), ("ts",),
        ))
        timestamp = _timestamp(raw_time, path)
        update = _at(value, "params", "update")
        if update and update.get("sessionUpdate") == "user_message_chunk":
            if active and active["max"] > active["base"]:
                rows.append(active)
            active = {
                "base": last_total or 0, "max": last_total or 0,
                "timestamp": timestamp, "model": model, "index": turn_index,
            }
            turn_index += 1
        raw_total = _first(value, (
            ("params", "_meta", "totalTokens"),
            ("params", "update", "_meta", "totalTokens"),
            ("params", "update", "totalTokens"), ("params", "totalTokens"),
            ("usage", "totalTokens"), ("totalTokens",),
        ))
        if raw_total is None:
            continue
        total = safe_int(raw_total)
        if last_total is not None and total < last_total:
            continue
        if last_total is not None and total > last_total and active is None:
            active = {
                "base": last_total, "max": last_total, "timestamp": timestamp,
                "model": model, "index": turn_index,
            }
            turn_index += 1
        if active and total > active["max"]:
            active["max"] = total
            active["timestamp"] = timestamp
        last_total = total
        last_timestamp = timestamp
    if active and active["max"] > active["base"]:
        rows.append(active)
    if not rows and last_total:
        rows.append({
            "base": 0, "max": last_total, "timestamp": last_timestamp,
            "model": model, "index": 0,
        })
    records = [
        _record(
            _RUNTIME, path, "xai", row["model"], session, row["timestamp"],
            TokenBreakdown(input=row["max"] - row["base"]),
            "grok:{}:{}".format(session, row["index"]),
        )
        for row in rows
    ]
    signals = read_json(path.with_name("signals.json"))
    signal = _mapping(signals.value)
    if signal:
        before = safe_int(signal.get("totalTokensBeforeCompaction"))
        total = safe_int(signal.get("totalTokens"))
        if "contextTokensUsed" in signal:
            signal_total = max(total, before + safe_int(signal.get("contextTokensUsed")))
        else:
            signal_total = before + total
        extra = max(signal_total - sum(record.tokens.input for record in records), 0)
        if extra:
            signal_model = _text(signal.get("primaryModelId"))
            models = signal.get("modelsUsed")
            if not signal_model and isinstance(models, list) and models:
                signal_model = _text(models[0])
            records.append(_record(
                _RUNTIME, path, "xai", signal_model or model, session,
                max((record.timestamp for record in records), default=_timestamp(None, path)),
                TokenBreakdown(input=extra), "grok:{}:signals".format(session),
            ))
    return tuple(records), recognized, partial, failed


def parse_grok(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _path)


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_grok)
