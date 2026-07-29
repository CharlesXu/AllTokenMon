"""Privacy-safe Qwen CLI JSONL adapter."""

from pathlib import Path
from typing import Sequence, Tuple

from ..normalize import safe_int
from ..schema import TokenBreakdown, UsageRecord
from .amp import _jsonl, _mapping, _record, _result, _scan, _text, _timestamp
from .base import DiscoveryContext, SourceSpec

_RUNTIME = "qwen"


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    values, partial, failed = _jsonl(path)
    records = []
    recognized = False
    ordinal = 0
    project = path.parent.parent.name if path.parent.name == "chats" else "unknown"
    fallback_session = "{}-{}".format(project, path.stem)
    for value in values:
        if value.get("type") != "assistant":
            continue
        usage = _mapping(value.get("usageMetadata"))
        if usage is None:
            continue
        recognized = True
        tokens = TokenBreakdown(
            safe_int(usage.get("promptTokenCount")),
            safe_int(usage.get("candidatesTokenCount")),
            safe_int(usage.get("cachedContentTokenCount")),
            0,
            safe_int(usage.get("thoughtsTokenCount")),
        )
        if tokens.total == 0 and tokens.reasoning == 0:
            continue
        session = _text(value.get("sessionId")) or fallback_session
        model = _text(value.get("model")) or "unknown"
        records.append(_record(
            _RUNTIME, path, "qwen", model, session,
            _timestamp(value.get("timestamp"), path), tokens,
            "qwen:{}:{}".format(session, ordinal),
        ))
        ordinal += 1
    return tuple(records), recognized, partial, failed


def parse_qwen(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _path)


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_qwen)
