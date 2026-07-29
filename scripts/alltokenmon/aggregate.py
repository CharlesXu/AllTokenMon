from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import math
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from .normalize import MAX_TOKEN_VALUE
from .schema import AdapterStatus, Diagnostic, UsageRecord


TOKEN_FIELDS = ("input", "output", "cache_read", "cache_write", "reasoning")


@dataclass(frozen=True)
class PeriodWindow:
    name: str
    start: Optional[datetime]
    end: datetime


def period_windows(now: datetime) -> Tuple[PeriodWindow, ...]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = midnight.replace(day=1)
    return (
        PeriodWindow("today", midnight, now),
        PeriodWindow("week", midnight - timedelta(days=6), now),
        PeriodWindow("month", month_start, now),
        PeriodWindow("all_time", None, now),
    )


def _bounded_add(left: int, right: int) -> int:
    return min(left + right, MAX_TOKEN_VALUE)


def _new_bucket() -> Dict[str, object]:
    return {
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_write": 0,
        "reasoning": 0,
        "message_count": 0,
        "costs": [],
        "invalid_cost": False,
        "record_count": 0,
        "confidence_counts": {},
    }


def _add_record(bucket: Dict[str, object], record: UsageRecord) -> None:
    for field in TOKEN_FIELDS:
        bucket[field] = _bounded_add(
            int(bucket[field]),
            getattr(record.tokens, field),
        )
    bucket["message_count"] = _bounded_add(
        int(bucket["message_count"]),
        record.message_count,
    )
    bucket["record_count"] = _bounded_add(int(bucket["record_count"]), 1)

    if record.cost is not None:
        valid_cost = (
            type(record.cost) is int
            and record.cost >= 0
        ) or (
            type(record.cost) is float
            and math.isfinite(record.cost)
            and record.cost >= 0
        )
        if valid_cost:
            costs = bucket["costs"]
            assert isinstance(costs, list)
            costs.append(record.cost)
        else:
            bucket["invalid_cost"] = True

    confidence_counts = bucket["confidence_counts"]
    assert isinstance(confidence_counts, dict)
    confidence_counts[record.confidence] = _bounded_add(
        confidence_counts.get(record.confidence, 0),
        1,
    )


def _sum_costs(bucket: Mapping[str, object]) -> Tuple[Optional[float], bool]:
    costs = bucket["costs"]
    assert isinstance(costs, list)
    if not costs:
        return None, False
    try:
        normalized = [float(value) for value in costs]
        if any(not math.isfinite(value) for value in normalized):
            return None, True
        with localcontext() as context:
            context.prec = 1200
            total = float(
                sum(
                    (Decimal(format(value, ".15g")) for value in normalized),
                    Decimal(),
                )
            )
    except (OverflowError, ValueError):
        return None, True
    if not math.isfinite(total):
        return None, True
    return total, False


def _finalize_totals(bucket: Mapping[str, object]) -> Dict[str, object]:
    values = {field: int(bucket[field]) for field in TOKEN_FIELDS}
    normalized_total = 0
    for field in ("input", "output", "cache_read", "cache_write"):
        normalized_total = _bounded_add(normalized_total, values[field])

    cost, _ = _sum_costs(bucket)
    return {
        **values,
        "total": normalized_total,
        "message_count": int(bucket["message_count"]),
        "cost": cost,
    }


def _row(
    identity: Mapping[str, str],
    bucket: Mapping[str, object],
    period_total: int,
) -> Dict[str, object]:
    totals = _finalize_totals(bucket)
    return {
        **identity,
        **totals,
        "share": totals["total"] / period_total if period_total else 0.0,
    }


def _name_key(value: str) -> Tuple[str, str]:
    return value.casefold(), value


def _coverage(diagnostics: Tuple[Diagnostic, ...]) -> Dict[str, object]:
    status_counts: Dict[str, int] = {}
    source_count = 0
    record_count = 0
    runtimes = set()
    for diagnostic in diagnostics:
        status = diagnostic.status.value
        status_counts[status] = _bounded_add(status_counts.get(status, 0), 1)
        source_count = _bounded_add(source_count, diagnostic.source_count)
        record_count = _bounded_add(record_count, diagnostic.record_count)
        runtimes.add(diagnostic.runtime)

    statuses = {diagnostic.status for diagnostic in diagnostics}
    incomplete = {
        AdapterStatus.ERROR,
        AdapterStatus.PARTIAL,
        AdapterStatus.UNSUPPORTED_FORMAT,
    }
    if not diagnostics:
        coverage_status = "unknown"
    elif statuses and statuses <= {AdapterStatus.NO_DATA}:
        coverage_status = "no_data"
    elif statuses & incomplete:
        coverage_status = "partial"
    else:
        coverage_status = "complete"

    return {
        "status": coverage_status,
        "runtime_count": len(runtimes),
        "diagnostic_count": len(diagnostics),
        "source_count": source_count,
        "record_count": record_count,
        "status_counts": {
            status: status_counts[status] for status in sorted(status_counts)
        },
    }


def _normal_diagnostics(
    diagnostics: Tuple[Diagnostic, ...],
) -> List[Dict[str, object]]:
    ordered = sorted(
        diagnostics,
        key=lambda diagnostic: (
            *_name_key(diagnostic.runtime),
            diagnostic.status.value,
            diagnostic.code.casefold(),
            diagnostic.code,
            diagnostic.source_count,
            diagnostic.record_count,
        ),
    )
    return [
        {
            "runtime": diagnostic.runtime,
            "status": diagnostic.status.value,
            "code": diagnostic.code,
            "source_count": diagnostic.source_count,
            "record_count": diagnostic.record_count,
        }
        for diagnostic in ordered
    ]


def _period_report(
    records: Iterable[UsageRecord],
    coverage: Mapping[str, object],
) -> Dict[str, object]:
    total_bucket = _new_bucket()
    runtime_buckets: Dict[str, Dict[str, object]] = {}
    model_buckets: Dict[str, Dict[str, object]] = {}
    runtime_model_buckets: Dict[Tuple[str, str], Dict[str, object]] = {}

    for record in records:
        _add_record(total_bucket, record)

        runtime_bucket = runtime_buckets.setdefault(record.runtime, _new_bucket())
        _add_record(runtime_bucket, record)

        model_bucket = model_buckets.setdefault(record.model, _new_bucket())
        _add_record(model_bucket, record)

        runtime_model_bucket = runtime_model_buckets.setdefault(
            (record.runtime, record.model),
            _new_bucket(),
        )
        _add_record(runtime_model_bucket, record)

    totals = _finalize_totals(total_bucket)
    period_total = int(totals["total"])
    runtimes = [
        _row({"runtime": runtime}, bucket, period_total)
        for runtime, bucket in runtime_buckets.items()
    ]
    runtimes.sort(
        key=lambda row: (
            -int(row["total"]),
            *_name_key(str(row["runtime"])),
        )
    )
    models = [
        _row({"model": model}, bucket, period_total)
        for model, bucket in model_buckets.items()
    ]
    models.sort(
        key=lambda row: (
            -int(row["total"]),
            *_name_key(str(row["model"])),
        )
    )
    runtime_models = [
        _row({"runtime": runtime, "model": model}, bucket, period_total)
        for (runtime, model), bucket in runtime_model_buckets.items()
    ]
    runtime_models.sort(
        key=lambda row: (
            -int(row["total"]),
            *_name_key(str(row["runtime"])),
            *_name_key(str(row["model"])),
        )
    )

    input_side = (
        int(totals["input"])
        + int(totals["cache_read"])
        + int(totals["cache_write"])
    )
    input_tokens = int(totals["input"])
    confidence_counts = total_bucket["confidence_counts"]
    assert isinstance(confidence_counts, dict)
    flags = []
    if any(confidence != "exact" for confidence in confidence_counts):
        flags.append("non_exact_records")
    if bool(total_bucket["invalid_cost"]):
        flags.append("invalid_cost")
    _, cost_overflow = _sum_costs(total_bucket)
    if cost_overflow:
        flags.append("cost_overflow")
    if coverage["status"] == "partial":
        flags.append("partial_coverage")
    elif coverage["status"] == "unknown":
        flags.append("coverage_unknown")
    if int(total_bucket["record_count"]) == 0:
        flags.append("no_usage")

    return {
        "totals": totals,
        "runtimes": runtimes,
        "models": models,
        "runtime_models": runtime_models,
        "cache_share_input_side": (
            (int(totals["cache_read"]) + int(totals["cache_write"])) / input_side
            if input_side
            else None
        ),
        "output_input_ratio": (
            int(totals["output"]) / input_tokens if input_tokens else None
        ),
        "data_quality": {
            "record_count": int(total_bucket["record_count"]),
            "confidence_counts": {
                confidence: confidence_counts[confidence]
                for confidence in sorted(confidence_counts)
            },
            "flags": flags,
        },
    }


def aggregate(
    records: Iterable[UsageRecord],
    diagnostics: Iterable[Diagnostic],
    now: datetime,
) -> Dict[str, object]:
    windows = period_windows(now)
    record_values = tuple(records)
    diagnostic_values = tuple(diagnostics)
    coverage = _coverage(diagnostic_values)

    periods = {}
    for window in windows:
        selected = []
        start_instant = (
            window.start.astimezone(timezone.utc)
            if window.start is not None
            else None
        )
        end_instant = window.end.astimezone(timezone.utc)
        for record in record_values:
            record_instant = record.timestamp.astimezone(timezone.utc)
            if record_instant > end_instant:
                continue
            if start_instant is not None and record_instant < start_instant:
                continue
            selected.append(record)
        periods[window.name] = _period_report(selected, coverage)

    return {
        "schema_version": "1",
        "generated_at": now.isoformat(),
        "timezone": str(now.tzinfo),
        "coverage": coverage,
        "periods": periods,
        "diagnostics": _normal_diagnostics(diagnostic_values),
    }
