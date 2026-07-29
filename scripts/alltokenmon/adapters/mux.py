"""Privacy-safe Mux cumulative session-usage snapshot adapter."""

from pathlib import Path
from typing import Sequence, Tuple

from ..normalize import safe_int
from ..schema import TokenBreakdown, UsageRecord
from .amp import _finite_cost, _mapping, _record, _result, _scan, _timestamp
from .base import DiscoveryContext, SourceSpec
from .jsonio import read_json

_RUNTIME = "mux"


def _bucket(value, name):
    bucket = _mapping(value.get(name)) if value else None
    return safe_int(bucket.get("tokens")) if bucket else 0


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    result = read_json(path)
    if result.error_code:
        return (), False, False, result.error_code.startswith("io_error:")
    root = _mapping(result.value)
    models = _mapping(root.get("byModel")) if root else None
    if root is None or models is None:
        return (), False, False, False
    session = path.parent.name or "unknown"
    last = _mapping(root.get("lastRequest")) or {}
    timestamp = _timestamp(last.get("timestamp"), path)
    records = []
    for model_key, raw in sorted(models.items()):
        usage = _mapping(raw)
        if usage is None:
            continue
        tokens = TokenBreakdown(
            _bucket(usage, "input"), _bucket(usage, "output"),
            _bucket(usage, "cached"), _bucket(usage, "cacheCreate"),
            _bucket(usage, "reasoning"),
        )
        if tokens.total == 0 and tokens.reasoning == 0:
            continue
        provider, separator, model = model_key.partition(":")
        if not separator:
            provider, model = "unknown", model_key
        costs = []
        cost_valid = False
        for bucket_name in ("input", "cached", "cacheCreate", "output", "reasoning"):
            bucket = _mapping(usage.get(bucket_name))
            if bucket and "cost_usd" in bucket:
                cost = _finite_cost(bucket.get("cost_usd"))
                if cost is not None:
                    costs.append(cost)
                    cost_valid = True
        records.append(_record(
            _RUNTIME, path, provider, model, session, timestamp, tokens,
            "mux:{}:{}".format(session, model_key),
            cost=sum(costs) if cost_valid else None,
        ))
    return tuple(records), True, False, False


def parse_mux(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _path)


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_mux)
