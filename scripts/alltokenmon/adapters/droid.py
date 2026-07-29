"""Privacy-safe Factory Droid settings snapshot adapter."""

from pathlib import Path
import re
from typing import Sequence, Tuple

from ..normalize import safe_int, stable_key
from ..schema import TokenBreakdown, UsageRecord
from .amp import (
    _mapping, _provider, _record, _result, _scan, _text, _timestamp,
)
from .base import DiscoveryContext, SourceSpec
from .jsonio import read_json

_RUNTIME = "droid"


def _model_name(value: str) -> str:
    value = value[len("custom:"):] if value.startswith("custom:") else value
    value = re.sub(r"\[.*?\]", "", value).rstrip("-").lower().replace(".", "-")
    return re.sub(r"-+", "-", value)


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    result = read_json(path)
    if result.error_code:
        return (), False, False, result.error_code.startswith("io_error:")
    root = _mapping(result.value)
    if root is None or "tokenUsage" not in root:
        return (), False, False, False
    usage = _mapping(root.get("tokenUsage"))
    if usage is None:
        return (), True, False, False
    tokens = TokenBreakdown(
        safe_int(usage.get("inputTokens")),
        safe_int(usage.get("outputTokens")),
        safe_int(usage.get("cacheReadTokens")),
        safe_int(usage.get("cacheCreationTokens")),
        safe_int(usage.get("thinkingTokens")),
    )
    if tokens.total == 0 and tokens.reasoning == 0:
        return (), True, False, False
    raw_model = _text(root.get("model"))
    provider = _text(root.get("providerLock")) or _provider(raw_model or "")
    if raw_model:
        model = _model_name(raw_model)
    else:
        model = None
        transcript = path.with_name(
            path.name[:-len(".settings.json")] + ".jsonl"
        ) if path.name.endswith(".settings.json") else None
        if transcript is not None:
            try:
                with transcript.open("rb") as source:
                    for _ in range(500):
                        line = source.readline(8 * 1024 * 1024 + 1)
                        if not line or len(line) > 8 * 1024 * 1024:
                            break
                        marker = line.find(b"Model:")
                        if marker < 0:
                            continue
                        raw = line[marker + 6:].split(b"[", 1)[0]
                        raw = raw.split(b"\\", 1)[0].split(b'"', 1)[0]
                        candidate = raw.decode("utf-8", errors="ignore").strip()
                        if candidate:
                            model = _model_name(candidate)
                            break
            except OSError:
                pass
        model = model or {
            "anthropic": "claude-unknown", "openai": "gpt-unknown",
            "google": "gemini-unknown", "xai": "grok-unknown",
        }.get(provider, "{}-unknown".format(provider))
    session = path.name[:-len(".settings.json")] if path.name.endswith(".settings.json") else path.stem
    record = _record(
        _RUNTIME, path, provider, model, session,
        _timestamp(root.get("providerLockTimestamp"), path), tokens,
        stable_key(_RUNTIME, session, model, tokens),
    )
    return (record,), True, False, False


def parse_droid(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _path)


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_droid)
