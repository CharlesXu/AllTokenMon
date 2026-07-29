"""Privacy-safe Codebuff/Manicode chat history adapter."""

from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple

from ..normalize import safe_int
from ..schema import TokenBreakdown, UsageRecord
from .amp import _finite_cost, _mapping, _provider, _record, _result, _scan, _text, _timestamp
from .base import DiscoveryContext, SourceSpec
from .jsonio import read_json

_RUNTIME = "codebuff"


def _number(value: Mapping[str, object], keys) -> int:
    for key in keys:
        if key in value and safe_int(value.get(key)) > 0:
            return safe_int(value.get(key))
    return 0


def _usage(value: object) -> Optional[dict]:
    raw = _mapping(value)
    if raw is None:
        return None
    details = _mapping(raw.get("promptTokensDetails")) or _mapping(raw.get("prompt_tokens_details"))
    return {
        "input": _number(raw, ("inputTokens", "input_tokens", "promptTokens", "prompt_tokens")),
        "output": _number(raw, ("outputTokens", "output_tokens", "completionTokens", "completion_tokens")),
        "cache_read": _number(raw, ("cacheReadInputTokens", "cache_read_input_tokens", "cachedTokensCreated", "cached_tokens_created"))
            or (_number(details, ("cachedTokens", "cached_tokens")) if details else 0),
        "cache_write": _number(raw, ("cacheCreationInputTokens", "cache_creation_input_tokens", "cacheCreationTokens", "cache_creation_tokens")),
        "model": _text(raw.get("model")),
        "credits": _finite_cost(raw.get("credits"), allow_zero=False),
    }


def _merge(primary: dict, fallback: Optional[dict]) -> dict:
    if fallback is None:
        return primary
    return {
        key: primary.get(key) or fallback.get(key)
        for key in primary
    }


def _extract(message: Mapping[str, object]) -> dict:
    metadata = _mapping(message.get("metadata")) or {}
    base = {
        "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
        "model": _text(metadata.get("model")), "credits": None,
    }
    base = _merge(base, _usage(metadata.get("usage")))
    codebuff = _mapping(metadata.get("codebuff"))
    base = _merge(base, _usage(codebuff.get("usage")) if codebuff else None)
    history = _mapping(_mapping(_mapping(metadata.get("runState") or {}).get("sessionState") or {}).get("mainAgentState") or {}).get("messageHistory")
    if isinstance(history, list):
        for raw in reversed(history):
            entry = _mapping(raw)
            options = _mapping(entry.get("providerOptions")) if entry and entry.get("role") == "assistant" else None
            if not options:
                continue
            entry_usage = _usage(options.get("usage"))
            nested = _mapping(options.get("codebuff"))
            entry_usage = _merge(entry_usage or {
                "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                "model": None, "credits": None,
            }, _usage(nested.get("usage")) if nested else None)
            if nested and not entry_usage.get("model"):
                entry_usage["model"] = _text(nested.get("model"))
            base = _merge(base, entry_usage)
    base["credits"] = base.get("credits") or _finite_cost(message.get("credits"), allow_zero=False)
    return base


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    result = read_json(path)
    if result.error_code:
        return (), False, False, result.error_code.startswith("io_error:")
    if not isinstance(result.value, list):
        return (), False, False, False
    chat = path.parent.name or "unknown"
    project = path.parent.parent.parent.name if len(path.parents) >= 3 else "unknown"
    channel = path.parent.parent.parent.parent.parent.name if len(path.parents) >= 5 else "manicode"
    session = "{}/{}/{}".format(channel, project, chat)
    records = []
    for ordinal, raw in enumerate(result.value):
        message = _mapping(raw)
        if not message or (message.get("variant") or message.get("role")) not in ("ai", "agent", "assistant"):
            continue
        usage = _extract(message)
        tokens = TokenBreakdown(
            safe_int(usage["input"]), safe_int(usage["output"]),
            safe_int(usage["cache_read"]), safe_int(usage["cache_write"]),
        )
        if tokens.total == 0 and usage.get("credits") is None:
            continue
        model = usage.get("model") or "codebuff-unknown"
        timestamp_value = message.get("timestamp") or message.get("createdAt") or (_mapping(message.get("metadata")) or {}).get("timestamp")
        timestamp = _timestamp(timestamp_value, path)
        message_id = _text(message.get("id"))
        dedup = message_id or "codebuff:{}:{}:{}:{}:{}:{}:{}:{}".format(
            session, int(timestamp.timestamp() * 1000), model, ordinal,
            tokens.input, tokens.output, tokens.cache_read, tokens.cache_write,
        )
        records.append(_record(
            _RUNTIME, path, _provider(model), model, session, timestamp, tokens,
            dedup, cost=usage.get("credits"),
        ))
    return tuple(records), True, False, False


def parse_codebuff(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _path)


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_codebuff)
