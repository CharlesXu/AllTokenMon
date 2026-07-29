"""Privacy-safe JetBrains Junie usage event adapter."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence, Tuple

from ..normalize import safe_int, stable_key
from ..schema import TokenBreakdown, UsageRecord
from .amp import (
    _back_anchor,
    _finite_cost,
    _jsonl,
    _mapping,
    _parsed_timestamp,
    _provider,
    _record,
    _result,
    _scan,
    _text,
    _timestamp,
)
from .base import DiscoveryContext, SourceSpec

_RUNTIME = "junie"


def _first(usage, names):
    for name in names:
        if name in usage:
            return safe_int(usage.get(name))
    return 0


def _session_timestamp(session):
    parts = session.split("-")
    if (
        len(parts) < 3
        or parts[0] != "session"
        or len(parts[1]) != 6
        or len(parts[2]) != 6
        or not parts[1].isdigit()
        or not parts[2].isdigit()
    ):
        return None
    try:
        naive = datetime.strptime(parts[1] + parts[2], "%y%m%d%H%M%S")
        local = naive.astimezone()
    except ValueError:
        return None
    roundtrip = datetime.fromtimestamp(local.timestamp()).replace(tzinfo=None)
    if roundtrip != naive:
        return None
    return local.astimezone(timezone.utc)


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    values, partial, failed = _jsonl(path)
    session = path.parent.name or "unknown"
    default_timestamp = _session_timestamp(session) or _timestamp(None, path)
    records = []
    seen = set()
    recognized = False
    for value in values:
        event = _mapping(value.get("event"))
        agent_event = _mapping(event.get("agentEvent")) if event else None
        if not agent_event or agent_event.get("kind") != "LlmResponseMetadataEvent":
            continue
        usages = agent_event.get("modelUsage")
        if not isinstance(usages, list):
            continue
        recognized = True
        for index, raw in enumerate(usages):
            usage = _mapping(raw)
            model = _text(usage.get("model")) if usage else None
            if not usage or not model:
                continue
            tokens = TokenBreakdown(
                _first(usage, ("inputTokens", "input")),
                _first(usage, ("outputTokens", "output")),
                _first(usage, ("cacheInputTokens", "cacheReadInputTokens", "cacheRead")),
                _first(usage, ("cacheCreateTokens", "cacheCreationInputTokens", "cacheWrite")),
                _first(usage, ("reasoningTokens", "reasoningOutputTokens", "thinkingTokens")),
            )
            cost = _finite_cost(usage.get("cost")) if "cost" in usage else None
            if tokens.total == 0 and tokens.reasoning == 0 and cost is None:
                continue
            raw_timestamp = safe_int(value.get("timestampMs"))
            explicit_timestamp = (
                _parsed_timestamp(raw_timestamp) if raw_timestamp > 0 else None
            )
            timestamp = explicit_timestamp or default_timestamp
            if explicit_timestamp is not None:
                timestamp = _back_anchor(explicit_timestamp, usage.get("time"))
            dedup_timestamp = (
                raw_timestamp
                if explicit_timestamp is not None
                else int(default_timestamp.timestamp() * 1000)
            )
            dedup = stable_key(
                _RUNTIME, session, dedup_timestamp, model, tokens,
                cost if cost is not None else "", index,
            )
            if dedup in seen:
                continue
            seen.add(dedup)
            provider = _text(usage.get("provider")) or _provider(model, _RUNTIME)
            records.append(_record(
                _RUNTIME, path, provider, model, session, timestamp, tokens,
                dedup, cost=cost,
            ))
    return tuple(records), recognized, partial, failed


def parse_junie(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _path)


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_junie)
