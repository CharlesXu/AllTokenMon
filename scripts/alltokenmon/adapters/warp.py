"""Bounded parser for existing Tokscale Warp aggregate usage caches."""

from dataclasses import replace
from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple

from ..normalize import parse_timestamp, safe_int, stable_key
from ..schema import TokenBreakdown, UsageRecord
from .amp import _mapping, _record, _result, _scan, _text
from .base import DiscoveryContext, SourceSpec
from .jsonio import read_json


_RUNTIME = "warp"
_MAX_ROWS = 100_000


def _cents(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return safe_int(value) / 100.0


def _integer(value: object) -> int:
    return min(safe_int(value), 2**31 - 1) if type(value) is int else 0


def _account(path: Path) -> str:
    name = path.name
    if name == "usage.json":
        label = "active"
    elif name.startswith("usage.") and name.endswith(".json"):
        name = name[len("usage."):-len(".json")]
        cleaned = "".join(
            character
            if character.isascii()
            and (character.isalnum() or character in "-_.")
            else "-"
            for character in name
        )
        label = cleaned or "unknown"
    else:
        label = "unknown"
    parts = path.parts
    cache_indices = tuple(
        index
        for index, part in enumerate(parts)
        if part.casefold() == "warp-cache"
    )
    if cache_indices:
        source_identity = "/".join(parts[cache_indices[-1] + 1:])
    else:
        try:
            source_identity = path.resolve().as_posix()
        except OSError:
            source_identity = path.absolute().as_posix()
    return "{}-{}".format(
        label,
        stable_key("warp-cache", source_identity)[7:],
    )


def _aggregate(
    path: Path,
    value: Mapping[str, object],
    timestamp,
    identity: str,
) -> Optional[UsageRecord]:
    identity = "{}:{}".format(_account(path), identity)
    requests = _integer(value.get("requestsUsed"))
    cost = _cents(value.get("spendCents")) if "spendCents" in value else None
    if requests == 0 and cost in (None, 0.0):
        return None
    record = _record(
        _RUNTIME,
        path,
        "warp",
        "aggregate-requests",
        "warp-aggregate-{}".format(stable_key(identity)[7:23]),
        timestamp,
        TokenBreakdown(),
        stable_key("warp", identity, timestamp.isoformat()),
        source_kind="json-cache",
        cost=cost,
    )
    return replace(record, message_count=requests)


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    result = read_json(path)
    if result.error_code:
        return (), False, False, result.error_code.startswith("io_error:")
    root = _mapping(result.value)
    if root is None:
        return (), False, False, False
    if "syncedAt" not in root:
        return (), True, False, False
    try:
        timestamp = parse_timestamp(root.get("syncedAt"))
    except (OSError, OverflowError, ValueError):
        return (), True, False, False
    workspaces = root.get("workspaces")
    records = []
    partial = False
    if isinstance(workspaces, list):
        partial = len(workspaces) > _MAX_ROWS
        for index, raw in enumerate(workspaces[:_MAX_ROWS]):
            workspace = _mapping(raw)
            if workspace is None:
                partial = True
                continue
            identity = _text(workspace.get("id")) or "workspace-{}".format(index)
            record = _aggregate(path, workspace, timestamp, identity)
            if record is not None:
                records.append(record)
    if not records:
        aggregate = _mapping(root.get("usage"))
        if aggregate is not None:
            record = _aggregate(path, aggregate, timestamp, "account")
            if record is not None:
                records.append(record)
    return tuple(records), True, partial, False


def parse_warp(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _path)


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_warp)
