"""Bounded parser for existing Tokscale Antigravity JSONL caches."""

from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple

from ..normalize import parse_timestamp, safe_int, stable_key
from ..schema import TokenBreakdown, UsageRecord
from .amp import _jsonl, _provider, _record, _result, _scan, _text
from .base import DiscoveryContext, SourceSpec
from .claude import _canonical_model, _canonical_provider_hint


_RUNTIME = "antigravity"
_MAX_ROWS = 100_000
def _provider_name(value: object, model: str) -> str:
    explicit = _text(value)
    if explicit:
        return _canonical_provider_hint(explicit) or explicit.lower()
    return _provider(model, "antigravity")


def _usage(
    path: Path,
    value: Mapping[str, object],
    fallback_model: Optional[str],
    index: int,
) -> Optional[UsageRecord]:
    session = _text(value.get("sessionId"))
    if session is None or safe_int(value.get("timestamp")) <= 0:
        return None
    model = _canonical_model(
        _text(value.get("modelId")) or fallback_model or "unknown"
    )
    tokens = TokenBreakdown(
        safe_int(value.get("input")),
        safe_int(value.get("output")),
        safe_int(value.get("cacheRead")),
        safe_int(value.get("cacheWrite")),
        safe_int(value.get("reasoning")),
    )
    if tokens.total == 0 and tokens.reasoning == 0:
        return None
    response = _text(value.get("responseId"))
    return _record(
        _RUNTIME,
        path,
        _provider_name(value.get("providerId"), model),
        model,
        session,
        parse_timestamp(value.get("timestamp")),
        tokens,
        response or stable_key("antigravity", path.name, session, index),
        source_kind="jsonl-cache",
    )


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    values, partial, failed = _jsonl(path)
    if failed and not values:
        return (), False, partial, True
    model = None
    records = []
    recognized = False
    for index, value in enumerate(values):
        if index >= _MAX_ROWS:
            partial = True
            break
        row_type = _text(value.get("type"))
        if row_type == "session_meta":
            recognized = True
            model = _text(value.get("modelId")) or model
        elif row_type == "usage":
            recognized = True
            try:
                record = _usage(path, value, model, index)
            except ValueError:
                partial = True
                continue
            if record is not None:
                records.append(record)
    return tuple(records), recognized, partial, False


def parse_antigravity(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _path)


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_antigravity)
