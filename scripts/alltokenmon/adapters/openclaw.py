"""Privacy-safe OpenClaw assistant usage adapter."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional, Sequence

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
from .jsonio import read_json_lines


_RUNTIME = "openclaw"


def _mapping(value: object) -> Optional[Mapping[str, object]]:
    return value if isinstance(value, Mapping) else None


def _text(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _session_id(path: Path) -> str:
    name = path.name
    marker = ".jsonl"
    if marker in name:
        prefix = name.split(marker, 1)[0]
        if prefix:
            return prefix
    return path.stem or "unknown"


def _fallback_timestamp(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except (OSError, OverflowError, ValueError):
        return datetime.fromtimestamp(0, timezone.utc)


def _timestamp(value: object, path: Path) -> datetime:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value / 1000, timezone.utc)
        except (OSError, OverflowError, ValueError):
            return _fallback_timestamp(path)
    try:
        return parse_timestamp(value)
    except ValueError:
        return _fallback_timestamp(path)


def _cost(value: object) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if 0 < number < float("inf") else None


def _diagnostic(status, code, source_count, record_count):
    return Diagnostic(
        _RUNTIME,
        status,
        code,
        "OpenClaw adapter completed",
        source_count,
        record_count,
    )


def parse_openclaw(paths: Sequence[Path]) -> AdapterResult:
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
    for path in existing:
        result = read_json_lines(path)
        partial = partial or result.partial
        read_error = read_error or bool(
            result.error_code and result.error_code.startswith("io_error:")
        )
        session_id = _session_id(path)
        current_model = None
        current_provider = None
        for index, entry in enumerate(result.values):
            entry_type = _text(entry.get("type"))
            if entry_type == "model_change":
                recognized = True
                current_model = _text(entry.get("modelId")) or current_model
                current_provider = _text(entry.get("provider")) or current_provider
                continue
            if entry_type == "custom":
                recognized = True
                if _text(entry.get("customType")) != "model-snapshot":
                    continue
                data = _mapping(entry.get("data"))
                if data is not None:
                    current_model = _text(data.get("modelId")) or current_model
                    current_provider = _text(data.get("provider")) or current_provider
                continue
            if entry_type != "message":
                continue
            recognized = True
            message = _mapping(entry.get("message"))
            if message is None or _text(message.get("role")) != "assistant":
                continue
            usage = _mapping(message.get("usage"))
            if usage is None:
                continue
            model = _text(message.get("model")) or current_model
            if model is None:
                continue
            provider = _text(message.get("provider")) or current_provider or "unknown"
            current_model = model
            current_provider = provider
            tokens = TokenBreakdown(
                safe_int(usage.get("input")),
                safe_int(usage.get("output")),
                safe_int(usage.get("cacheRead")),
                safe_int(usage.get("cacheWrite")),
                0,
            )
            cost_value = _mapping(usage.get("cost"))
            provider_cost = _cost(cost_value.get("total")) if cost_value else None
            message_id = _text(entry.get("id"))
            identity = message_id or stable_key(
                _RUNTIME,
                session_id,
                index,
                model,
                message.get("timestamp"),
                tokens,
            )
            if tokens.total == 0 and provider_cost is None:
                continue
            records.append(
                UsageRecord(
                    runtime=_RUNTIME,
                    provider=provider,
                    model=model,
                    session_id=session_id,
                    timestamp=_timestamp(message.get("timestamp"), path),
                    tokens=tokens,
                    message_count=1,
                    source_kind="jsonl",
                    source_path=str(path),
                    dedup_key="openclaw:{}:message:{}".format(
                        session_id, identity
                    ),
                    confidence="exact",
                    cost=provider_cost,
                    cost_source=(
                        "provider_reported" if provider_cost is not None else None
                    ),
                )
            )

    unique = {}
    for record in records:
        unique.setdefault(record.dedup_key, record)
    result_records = tuple(unique.values())
    if read_error and not result_records and not recognized:
        status, code = AdapterStatus.ERROR, "read_error"
    elif partial or (read_error and result_records):
        status, code = AdapterStatus.PARTIAL, "partial_source"
    elif result_records:
        status, code = AdapterStatus.OK, "ok"
    elif not existing or recognized:
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
    return parse_openclaw(tuple(dict.fromkeys(paths)))
