"""Privacy-safe gajae-code JSONL adapter."""

from pathlib import Path
from typing import Sequence, Tuple

from ..normalize import safe_int, stable_key
from ..schema import TokenBreakdown, UsageRecord
from .amp import _finite_cost, _jsonl, _mapping, _provider, _record, _result, _scan, _text, _timestamp
from .base import DiscoveryContext, SourceSpec

_RUNTIME = "gjc"


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    values, partial, failed = _jsonl(path)
    session = None
    records = []
    recognized = False
    for index, value in enumerate(values):
        kind = value.get("type")
        if kind == "session":
            session = _text(value.get("id"))
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
        recognized = True
        active_session = session or path.stem or "unknown"
        provider = _text(message.get("provider")) or _provider(model, _RUNTIME)
        tokens = TokenBreakdown(
            safe_int(usage.get("input")), safe_int(usage.get("output")),
            safe_int(usage.get("cacheRead")), safe_int(usage.get("cacheWrite")),
        )
        cost_value = (_mapping(usage.get("cost")) or {}).get("total")
        cost = _finite_cost(cost_value) if cost_value is not None else None
        if tokens.total == 0 and (cost is None or cost <= 0):
            continue
        raw_timestamp = message.get("timestamp") or value.get("timestamp")
        timestamp = _timestamp(raw_timestamp, path)
        message_id = _text(value.get("id"))
        dedup = "{}:{}".format(active_session, message_id) if message_id else stable_key(
            _RUNTIME, active_session, raw_timestamp, model, provider, tokens, index
        )
        records.append(_record(
            _RUNTIME, path, provider, model, active_session, timestamp, tokens,
            dedup, cost=cost,
        ))
    return tuple(records), recognized, partial, failed


def parse_gjc(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _path)


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_gjc)
