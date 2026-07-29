"""Privacy-safe Claude Code JSONL token usage adapter."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence, Tuple

from ..discovery import discover
from ..normalize import deduplicate, parse_timestamp, safe_int, stable_key
from ..schema import (
    AdapterResult,
    AdapterStatus,
    Diagnostic,
    TokenBreakdown,
    UsageRecord,
)
from .base import DiscoveryContext, SourceSpec
from .jsonio import read_json_lines


_RUNTIME = "claude"
_PROVIDER_ALIASES = {
    "x_ai": "xai",
    "xai": "xai",
    "z_ai": "zai",
    "zai": "zai",
    "moonshot": "moonshotai",
    "moonshotai": "moonshotai",
    "meta": "meta_llama",
    "meta_llama": "meta_llama",
    "azure": "azure_ai",
    "azure_ai": "azure_ai",
    "anthropic": "anthropic",
    "vertex": "anthropic",
    "vertex_ai": "anthropic",
    "together": "together_ai",
    "together_ai": "together_ai",
    "fireworks": "fireworks_ai",
    "fireworks_ai": "fireworks_ai",
    "google": "google",
    "gemini": "google",
    "openai": "openai",
    "openai_codex": "openai",
    "minimax": "minimax",
    "minimaxai": "minimax",
    "minimax_ai": "minimax",
    "mistral": "mistralai",
    "mistralai": "mistralai",
    "ai21": "ai21",
}
_MODEL_ALIASES = {
    "big-pickle": "glm-4.7",
    "big pickle": "glm-4.7",
    "bigpickle": "glm-4.7",
    "k2p5": "kimi-k2-thinking",
    "k2-p5": "kimi-k2-thinking",
    "k2p6": "kimi-k2.6",
    "k2-p6": "kimi-k2.6",
    "kimi-k2p6": "kimi-k2.6",
    "kimi-k2.5-thinking": "kimi-k2-thinking",
    "kimi-for-coding": "kimi-k2.5",
    "kimi-for-coding-highspeed": "kimi-k2.7-code-highspeed",
    "k3": "kimi-k3",
    "model_placeholder_m26": "claude-opus-4-6",
    "model_placeholder_m35": "claude-sonnet-4-6",
    "model_placeholder_m36": "gemini-3.1-pro",
    "model_placeholder_m37": "gemini-3.1-pro",
    "model_placeholder_m16": "gemini-3.1-pro",
    "model_placeholder_m18": "gemini-3-flash-preview",
    "model_placeholder_m84": "gemini-3-flash-preview",
    "model_placeholder_m132": "gemini-3.5-flash-high",
    "model_placeholder_m133": "gemini-3.5-flash-high",
    "model_placeholder_m187": "gemini-3.5-flash-extra-low",
    "model_placeholder_m20": "gemini-3.5-flash-medium",
    "gemini-pro-default": "gemini-3.1-pro",
    "gemini-pro-agent": "gemini-3.1-pro",
    "gemini-3-flash-agent": "gemini-3.5-flash-high",
    "gemini-3-flash-b": "gemini-3.5-flash-high",
    "gemini-3.5-flash-low": "gemini-3.5-flash-medium",
    "model_placeholder_m47": "gemini-3-flash-preview",
    "model_openai_gpt_oss_120b_medium": "gpt-oss-120b-medium",
    "claude-opus-4-6-thinking": "claude-opus-4-6",
    "claude-sonnet-4-6-thinking": "claude-sonnet-4-6",
    "claude-opus-4.6-thinking": "claude-opus-4-6",
    "claude-sonnet-4.6-thinking": "claude-sonnet-4-6",
    "claude-opus-4-6": "claude-opus-4-6",
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "claude-haiku-4-6": "claude-haiku-4-6",
    "claude-opus-4.6": "claude-opus-4-6",
    "claude-sonnet-4.6": "claude-sonnet-4-6",
    "claude-haiku-4.6": "claude-haiku-4-6",
    "anthropic/claude-4-5-opus": "claude-opus-4-5",
    "anthropic/claude-4-5-sonnet": "claude-sonnet-4-5",
    "anthropic/claude-4-5-haiku": "claude-haiku-4-5",
    "anthropic/claude-4-6-opus": "claude-opus-4-6",
    "anthropic/claude-4-6-sonnet": "claude-sonnet-4-6",
    "anthropic/claude-4-6-haiku": "claude-haiku-4-6",
    "gemini-3.1-pro-high": "gemini-3.1-pro",
    "gemini-3.1-pro-low": "gemini-3.1-pro",
    "gemini-3-pro-high": "gemini-3-pro",
    "gemini-3-pro-low": "gemini-3-pro",
    "gemini-3-flash": "gemini-3-flash-preview",
    "gemini-3-flash-c": "gemini-3-flash-preview",
    "gemini-3-flash-a": "gemini-3.5-flash-high",
    "grok-composer-2.5": "composer-2.5",
    "grok-composer-2.5-fast": "composer-2.5-fast",
    "kimi-k2.5-nvfp4": "kimi-k2.5",
    "kimi-k2-instruct-0905": "kimi-k2.5",
}


@dataclass(frozen=True)
class _Candidate:
    record: UsageRecord
    provider_confidence: int


def _mapping(value: object) -> Optional[Mapping[str, object]]:
    return value if isinstance(value, Mapping) else None


def _text(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _fallback_timestamp(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except (OSError, OverflowError, ValueError):
        return datetime.fromtimestamp(0, timezone.utc)


def _timestamp(value: object, path: Path) -> datetime:
    try:
        return parse_timestamp(value)
    except ValueError:
        return _fallback_timestamp(path)


def _canonical_provider_segment(value: str) -> Optional[str]:
    normalized = value.strip().rstrip("/").lower().replace("-", "_")
    if normalized.startswith("<") and normalized.endswith(">"):
        return None
    if normalized in ("", "unknown"):
        return None
    if normalized in _PROVIDER_ALIASES:
        return _PROVIDER_ALIASES[normalized]
    if any(character.isdigit() for character in normalized):
        return None
    return normalized


def _canonical_provider_hint(value: str) -> Optional[str]:
    for segment in value.strip().rstrip("/").split("/"):
        candidates = (segment,) + (
            tuple(segment.split(".")) if "." in segment else ()
        )
        for candidate in candidates:
            canonical = _canonical_provider_segment(candidate)
            if canonical is not None:
                return canonical
    return None


def _contains_delimited(value: str, needle: str) -> bool:
    start = 0
    while True:
        position = value.find(needle, start)
        if position < 0:
            return False
        before = value[position - 1] if position > 0 else ""
        before_ok = (
            position == 0
            or not (before.isascii() and before.isalnum())
        )
        after = position + len(needle)
        following = value[after] if after < len(value) else ""
        after_ok = (
            after == len(value)
            or not (following.isascii() and following.isalnum())
        )
        if before_ok and after_ok:
            return True
        start = position + 1


def _inferred_provider(model: str) -> Optional[str]:
    lower = model.lower()
    if (
        "claude" in lower
        or "anthropic" in lower
        or any(
            _contains_delimited(lower, family)
            for family in ("opus", "sonnet", "haiku", "fable")
        )
    ):
        return "anthropic"
    if (
        "gpt" in lower
        or "openai" in lower
        or any(
            _contains_delimited(lower, family)
            for family in ("o1", "o3", "o4")
        )
    ):
        return "openai"
    if "gemini" in lower or "google" in lower:
        return "google"
    if "grok" in lower:
        return "xai"
    if "deepseek" in lower:
        return "deepseek"
    if "minimax" in lower:
        return "minimax"
    if "mistral" in lower or "mixtral" in lower:
        return "mistralai"
    if "llama" in lower or _contains_delimited(lower, "meta"):
        return "meta_llama"
    if "qwen" in lower:
        return "qwen"
    if "fugu" in lower:
        return "sakana"
    if _contains_delimited(lower, "kimi"):
        return "moonshotai"
    if _contains_delimited(lower, "mimo"):
        return "xiaomi"
    if _contains_delimited(lower, "glm"):
        return "zai"
    return None


def _canonical_model(model: str) -> str:
    return _MODEL_ALIASES.get(model.lower(), model)


def _provider(
    row: Mapping[str, object],
    message: Mapping[str, object],
    model: str,
) -> Tuple[str, int]:
    explicit = (
        _text(message.get("providerId"))
        or _text(message.get("provider_id"))
        or _text(message.get("provider"))
        or _text(row.get("providerId"))
        or _text(row.get("provider_id"))
        or _text(row.get("provider"))
    )
    canonical = (
        _canonical_provider_hint(explicit) if explicit else None
    )
    inferred = _inferred_provider(model)
    if canonical == "anthropic":
        if inferred is not None and inferred != "anthropic":
            return inferred, 2
        return "anthropic", 1
    if canonical is not None:
        return canonical, 3
    if inferred is not None:
        return inferred, 2
    if "/" in model:
        model_provider = _canonical_provider_hint(model)
        if model_provider is not None:
            return model_provider, 3
    return "unknown", 0


def _usage_tokens(value: object) -> Optional[TokenBreakdown]:
    usage = _mapping(value)
    if usage is None:
        return None
    fields = (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    )
    if not any(field in usage for field in fields):
        return None
    return TokenBreakdown(
        input=safe_int(usage.get("input_tokens")),
        output=safe_int(usage.get("output_tokens")),
        cache_read=safe_int(usage.get("cache_read_input_tokens")),
        cache_write=safe_int(usage.get("cache_creation_input_tokens")),
        reasoning=0,
    )


def _merged_candidate(
    existing: _Candidate, new: _Candidate
) -> _Candidate:
    old_tokens = existing.record.tokens
    new_tokens = new.record.tokens
    tokens = TokenBreakdown(
        input=max(old_tokens.input, new_tokens.input),
        output=max(old_tokens.output, new_tokens.output),
        cache_read=max(old_tokens.cache_read, new_tokens.cache_read),
        cache_write=max(old_tokens.cache_write, new_tokens.cache_write),
        reasoning=0,
    )
    use_new_provider = (
        new.provider_confidence > existing.provider_confidence
    )
    updated = replace(
        existing.record,
        provider=(
            new.record.provider
            if use_new_provider
            else existing.record.provider
        ),
        model=(
            new.record.model
            if existing.record.model == "unknown"
            else existing.record.model
        ),
        tokens=tokens,
    )
    return _Candidate(
        record=updated,
        provider_confidence=max(
            existing.provider_confidence,
            new.provider_confidence,
        ),
    )


def _diagnostic(
    status: AdapterStatus,
    code: str,
    source_count: int,
    record_count: int,
) -> Diagnostic:
    return Diagnostic(
        runtime=_RUNTIME,
        status=status,
        code=code,
        message="Claude adapter completed",
        source_count=source_count,
        record_count=record_count,
    )


def parse_claude(paths: Sequence[Path]) -> AdapterResult:
    """Parse bounded Claude Code assistant usage without retaining content."""
    candidates: Dict[str, _Candidate] = {}
    existing_count = 0
    recognized = False
    partial = False
    read_error = False

    for path_value in paths:
        path = Path(path_value)
        if not path.is_file():
            continue
        existing_count += 1
        result = read_json_lines(path)
        if result.partial:
            partial = True
            if result.error_code and result.error_code.startswith("io_error:"):
                read_error = True

        for row_index, row in enumerate(result.values):
            entry_type = _text(row.get("type"))
            if entry_type in ("user", "tool_result"):
                recognized = True
                continue
            if entry_type != "assistant":
                continue
            recognized = True
            message = _mapping(row.get("message"))
            if message is None:
                continue
            usage = _usage_tokens(message.get("usage"))
            if usage is None:
                continue
            if usage.total == 0:
                continue

            message_id = _text(message.get("id"))
            request_id = _text(row.get("requestId"))
            if message_id and request_id:
                identity = "message-request:{}:{}".format(
                    message_id, request_id
                )
            elif message_id:
                identity = "message:{}".format(message_id)
            else:
                identity = "row:{}".format(
                    stable_key(path, row_index, row.get("timestamp"))
                )
            dedup_key = stable_key(_RUNTIME, identity)
            previous = candidates.get(dedup_key)
            model = _text(message.get("model"))
            if model is None and previous is None:
                continue
            resolved_raw_model = (
                model if model is not None else previous.record.model
            )
            session_id = (
                _text(row.get("sessionId"))
                or _text(row.get("session_id"))
                or (
                    previous.record.session_id
                    if previous is not None
                    else None
                )
                or path.stem
                or "unknown"
            )
            provider, provider_confidence = _provider(
                row, message, resolved_raw_model
            )
            record = UsageRecord(
                runtime=_RUNTIME,
                provider=provider,
                model=_canonical_model(resolved_raw_model),
                session_id=session_id,
                timestamp=_timestamp(row.get("timestamp"), path),
                tokens=usage,
                message_count=1,
                source_kind="jsonl",
                source_path=str(path),
                dedup_key=dedup_key,
                confidence="exact",
            )
            candidate = _Candidate(record, provider_confidence)
            candidates[dedup_key] = (
                candidate
                if previous is None
                else _merged_candidate(previous, candidate)
            )

    records = tuple(
        deduplicate(candidate.record for candidate in candidates.values())
    )
    if read_error and not records and not recognized:
        status = AdapterStatus.ERROR
        code = "read_error"
    elif partial:
        status = AdapterStatus.PARTIAL
        code = "partial_source"
    elif records:
        status = AdapterStatus.OK
        code = "ok"
    elif existing_count == 0:
        status = AdapterStatus.NO_DATA
        code = "no_data"
    elif recognized:
        status = AdapterStatus.NO_DATA
        code = "no_data"
    else:
        status = AdapterStatus.UNSUPPORTED_FORMAT
        code = "unsupported_format"
    diagnostic = _diagnostic(status, code, existing_count, len(records))
    return AdapterResult(
        runtime=_RUNTIME,
        status=status,
        records=records,
        diagnostics=(diagnostic,),
    )


def scan(
    context: DiscoveryContext, specs: Sequence[SourceSpec]
) -> AdapterResult:
    """Discover registered Claude paths and parse them."""
    paths = []
    for spec in specs:
        paths.extend(discover(spec, context))
    return parse_claude(tuple(dict.fromkeys(paths)))
