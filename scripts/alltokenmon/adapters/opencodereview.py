"""Privacy-safe OpenCodeReview response event adapter."""

from pathlib import Path
from typing import Sequence, Tuple

from ..normalize import safe_int, stable_key
from ..schema import TokenBreakdown, UsageRecord
from .amp import (
    _back_anchor,
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

_RUNTIME = "opencodereview"


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    values, partial, failed = _jsonl(path)
    session = path.stem or "unknown"
    records = []
    seen = set()
    recognized = False
    for value in values:
        if value.get("type") == "session_start":
            recognized = True
            continue
        if value.get("type") != "llm_response":
            continue
        usage = _mapping(value.get("usage"))
        if usage is None:
            continue
        recognized = True
        tokens = TokenBreakdown(
            safe_int(usage.get("prompt_tokens")),
            safe_int(usage.get("completion_tokens")),
            safe_int(usage.get("cache_read_tokens")),
            safe_int(usage.get("cache_write_tokens")),
        )
        if tokens.total == 0:
            continue
        model = _text(value.get("model")) or "unknown"
        explicit_timestamp = _parsed_timestamp(value.get("timestamp"))
        recorded = explicit_timestamp or _timestamp(None, path)
        timestamp = (
            _back_anchor(explicit_timestamp, value.get("duration_ms"))
            if explicit_timestamp is not None
            else recorded
        )
        dedup = stable_key(_RUNTIME, session, recorded, model, tokens)
        if dedup in seen:
            continue
        seen.add(dedup)
        records.append(_record(
            _RUNTIME, path, _provider(model, _RUNTIME), model, session,
            timestamp, tokens, dedup,
        ))
    return tuple(records), recognized, partial, failed


def parse_opencodereview(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _path)


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_opencodereview)
