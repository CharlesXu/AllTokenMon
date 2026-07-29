"""Privacy-safe Devin Desktop ACP NDJSON adapter."""

from pathlib import Path
from typing import Sequence, Tuple

from ..normalize import safe_int
from ..schema import TokenBreakdown, UsageRecord
from .amp import _jsonl, _mapping, _provider, _record, _result, _scan, _text, _timestamp
from .base import DiscoveryContext, SourceSpec

_RUNTIME = "devin-desktop"


def _nested(notification, *paths):
    for path in paths:
        current = notification
        for key in path:
            current = current.get(key) if isinstance(current, dict) else None
        if current is not None:
            return current
    return None


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    values, partial, failed = _jsonl(path)
    session = path.stem or "unknown"
    legacy = []
    aggregate = None
    recognized = False
    for index, event in enumerate(values):
        notification = _mapping(event.get("notification"))
        if not notification:
            continue
        recognized = True
        if notification.get("sessionUpdate") == "session_info_update":
            continue
        if notification.get("sessionUpdate") == "usage_update":
            meta = _mapping(notification.get("_meta"))
            if meta and any(
                key in meta for key in (
                    "cognition.ai/inputTokens", "cognition.ai/outputTokens",
                    "cognition.ai/cachedReadTokens", "cognition.ai/cachedWriteTokens",
                )
            ):
                if aggregate is None:
                    aggregate = {"input": 0, "output": 0, "read": 0, "write": 0, "model": None, "timestamp": None}
                if "cognition.ai/inputTokens" in meta:
                    aggregate["input"] = safe_int(meta.get("cognition.ai/inputTokens"))
                if "cognition.ai/cachedReadTokens" in meta:
                    aggregate["read"] = safe_int(meta.get("cognition.ai/cachedReadTokens"))
                if "cognition.ai/cachedWriteTokens" in meta:
                    aggregate["write"] = safe_int(meta.get("cognition.ai/cachedWriteTokens"))
                aggregate["output"] += safe_int(meta.get("cognition.ai/outputTokens"))
                aggregate["model"] = aggregate["model"] or _nested(
                    notification, ("content", "metadata", "generation_model"),
                    ("metadata", "generation_model"), ("_meta", "cognition.ai/model"),
                )
                aggregate["timestamp"] = _nested(
                    notification, ("content", "metadata", "created_at"),
                    ("metadata", "created_at"), ("created_at",), ("timestamp",),
                ) or aggregate["timestamp"]
                continue
        usage = _nested(
            notification, ("content", "metadata", "metrics"),
            ("metadata", "metrics"), ("metrics",),
            ("content", "metadata"), ("metadata",),
        )
        usage = _mapping(usage)
        if not usage:
            continue
        tokens = TokenBreakdown(
            safe_int(usage.get("input_tokens")), safe_int(usage.get("output_tokens")),
            safe_int(usage.get("cache_read_tokens")), safe_int(usage.get("cache_creation_tokens")),
        )
        if tokens.total == 0:
            continue
        model = _nested(
            notification, ("content", "metadata", "generation_model"),
            ("metadata", "generation_model"), ("_meta", "cognition.ai/model"),
        )
        model = _text(model) or "devin"
        timestamp_value = _nested(
            notification, ("content", "metadata", "created_at"),
            ("metadata", "created_at"), ("created_at",), ("timestamp",),
        )
        legacy.append(_record(
            _RUNTIME, path, _provider(model, "devin"), model, session,
            _timestamp(timestamp_value, path), tokens,
            "devin-desktop:{}:{}".format(path, index), source_kind="ndjson",
        ))
    if aggregate is not None:
        tokens = TokenBreakdown(
            max(aggregate["input"] - aggregate["read"], 0),
            aggregate["output"], aggregate["read"], aggregate["write"],
        )
        if tokens.total:
            model = _text(aggregate["model"]) or "devin"
            record = _record(
                _RUNTIME, path, _provider(model, "devin"), model, session,
                _timestamp(aggregate["timestamp"], path), tokens,
                "devin-desktop:{}:usage".format(path), source_kind="ndjson",
            )
            return (record,), recognized, partial, failed
        return (), recognized, partial, failed
    return tuple(legacy), recognized, partial, failed


def parse_devin_desktop(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _path)


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_devin_desktop)
