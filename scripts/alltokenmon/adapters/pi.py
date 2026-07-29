"""Privacy-safe Pi and Oh My Pi JSONL adapter."""

from pathlib import Path
from typing import Sequence, Tuple

from ..normalize import safe_int
from ..schema import TokenBreakdown, UsageRecord
from .amp import _jsonl, _mapping, _provider, _record, _result, _scan, _text, _timestamp
from .base import DiscoveryContext, SourceSpec

_RUNTIME = "pi"


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    values, partial, failed = _jsonl(path)
    session = None
    recognized = False
    records = []
    ordinal = 0
    for value in values:
        kind = value.get("type")
        if session is None:
            if kind == "title":
                continue
            if kind != "session":
                return (), False, partial, failed
            session = _text(value.get("id"))
            if session is None:
                return (), False, partial, failed
            recognized = True
            continue
        if kind != "message":
            continue
        message = _mapping(value.get("message"))
        usage = _mapping(message.get("usage")) if message else None
        if not message or message.get("role") != "assistant" or not usage:
            continue
        model = _text(message.get("model"))
        if model is None:
            continue
        tokens = TokenBreakdown(
            safe_int(usage.get("input")), safe_int(usage.get("output")),
            safe_int(usage.get("cacheRead")), safe_int(usage.get("cacheWrite")),
        )
        if tokens.total == 0:
            continue
        provider = _text(message.get("provider")) or _provider(model, _RUNTIME)
        message_id = _text(value.get("id")) or str(ordinal)
        records.append(_record(
            _RUNTIME, path, provider, model, session,
            _timestamp(value.get("timestamp"), path), tokens,
            "pi:{}:{}".format(session, message_id),
        ))
        ordinal += 1
    return tuple(records), recognized, partial, failed


def parse_pi(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _path)


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_pi)
