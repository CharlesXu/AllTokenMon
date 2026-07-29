"""Bounded parser for existing Tokscale Trae usage caches."""

from dataclasses import replace
from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple

from ..normalize import parse_timestamp, safe_int, stable_key
from ..schema import TokenBreakdown, UsageRecord
from .amp import (
    _finite_cost,
    _mapping,
    _record,
    _result,
    _scan,
    _text,
)
from .base import DiscoveryContext, SourceSpec
from .claude import _canonical_provider_hint
from .jsonio import read_json


_RUNTIME = "trae"
_MAX_ROWS = 100_000


def _session(
    path: Path, value: Mapping[str, object]
) -> Optional[UsageRecord]:
    session = _text(value.get("session_id"))
    usage_time = _integer(value.get("usage_time"))
    if session is None or usage_time <= 0:
        return None
    model_raw = _text(value.get("model_name"))
    mode = _text(value.get("mode"))
    model = (
        model_raw
        if model_raw is not None
        else "trae-{}".format(mode.lower()) if mode else "trae-unknown"
    )
    provider_raw = (
        _text(value.get("provider_id"))
        or _text(value.get("provider_name"))
        or _text(value.get("provider"))
    )
    provider = (
        _canonical_provider_hint(provider_raw)
        if provider_raw is not None
        else None
    ) or "trae"
    extra = _mapping(value.get("extra_info")) or {}
    tokens = TokenBreakdown(
        _integer(extra.get("input_token")),
        _integer(extra.get("output_token")),
        _integer(extra.get("cache_read_token")),
        _integer(extra.get("cache_write_token")),
    )
    if tokens.total == 0:
        return None
    try:
        timestamp = parse_timestamp(usage_time)
        if timestamp.timestamp() <= 0:
            return None
    except (OSError, OverflowError, ValueError):
        return None
    cost = _reported_cost(value.get("dollar_float"))
    return _record(
        _RUNTIME,
        path,
        provider,
        model,
        session,
        timestamp,
        tokens,
        stable_key("trae", session, usage_time),
        source_kind="json-cache",
        cost=cost,
    )


def _integer(value: object) -> int:
    return safe_int(value) if type(value) is int else 0


def _reported_cost(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return _finite_cost(value)


def _latest(records: Sequence[UsageRecord]) -> Tuple[UsageRecord, ...]:
    latest = {}
    for record in records:
        existing = latest.get(record.session_id)
        if existing is None or (
            record.timestamp,
            record.dedup_key,
        ) > (
            existing.timestamp,
            existing.dedup_key,
        ):
            latest[record.session_id] = record
    return tuple(
        sorted(
            latest.values(),
            key=lambda record: (record.session_id, record.timestamp),
        )
    )


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    result = read_json(path)
    if result.error_code:
        return (), False, False, result.error_code.startswith("io_error:")
    if not isinstance(result.value, list):
        return (), False, False, False
    partial = len(result.value) > _MAX_ROWS
    records = []
    for raw in result.value[:_MAX_ROWS]:
        value = _mapping(raw)
        if value is None:
            partial = True
            continue
        record = _session(path, value)
        if record is not None:
            records.append(record)
    return tuple(records), True, partial, False


def parse_trae(paths: Sequence[Path]):
    result = _result(_RUNTIME, paths, _path)
    records = _latest(result.records)
    diagnostics = tuple(
        replace(diagnostic, record_count=len(records))
        for diagnostic in result.diagnostics
    )
    return replace(result, records=records, diagnostics=diagnostics)


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_trae)
