"""Privacy-safe estimated Command Code transcript adapter."""

import json
from pathlib import Path
from typing import Sequence, Tuple

from ..schema import TokenBreakdown, UsageRecord
from .amp import _jsonl, _provider, _record, _result, _scan, _text, _timestamp
from .base import DiscoveryContext, SourceSpec
from .jsonio import read_json

_RUNTIME = "commandcode"


def _chars(value):
    if value is None or value == [] or value == {}:
        return 0
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _estimate(chars):
    return (chars + 3) // 4


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    if path.name.endswith(".checkpoints.jsonl"):
        return (), False, False, False
    values, partial, failed = _jsonl(path)
    root = path.parent.parent.parent if len(path.parents) >= 3 else path.parent
    config = read_json(root / "config.json")
    config_value = config.value if isinstance(config.value, dict) else {}
    raw_model = _text(config_value.get("model"))
    model = raw_model.rsplit("/", 1)[-1] if raw_model else "unknown"
    if model.lower().endswith("-free") and len(model) > 5:
        model = model[:-5]
    provider = _provider(raw_model or "", "command-code")
    session = None
    input_chars = 0
    assistant_index = 0
    records = []
    recognized = False
    for value in values:
        role = value.get("role")
        if role is None:
            continue
        recognized = True
        session = session or _text(value.get("sessionId"))
        chars = _chars(value.get("content"))
        if role != "assistant":
            input_chars += chars
            continue
        tokens = TokenBreakdown(input=_estimate(input_chars), output=_estimate(chars))
        input_chars = 0
        if tokens.total == 0:
            continue
        active_session = session or path.stem or "unknown"
        records.append(_record(
            _RUNTIME, path, provider, model, active_session,
            _timestamp(value.get("timestamp"), path), tokens,
            "{}:{}".format(active_session, assistant_index),
            confidence="estimated",
        ))
        assistant_index += 1
    return tuple(records), recognized, partial, failed


def parse_commandcode(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _path)


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_commandcode)
