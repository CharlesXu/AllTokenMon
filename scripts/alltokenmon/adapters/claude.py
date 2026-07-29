"""Privacy-safe Claude Code JSONL token usage adapter."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
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
from .jsonio import read_json, read_json_lines


_RUNTIME = "claude"
_MAX_SETTINGS_BYTES = 1024 * 1024
_ROUTE_KEYS = (
    "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)
_ROUTE_PROVIDERS = {
    "CLAUDE_CODE_USE_BEDROCK": "amazon-bedrock",
    "CLAUDE_CODE_USE_VERTEX": "google-vertex",
    "CLAUDE_CODE_USE_FOUNDRY": "microsoft-foundry",
}


@dataclass(frozen=True)
class _ClaudeConfig:
    model: Optional[str] = None
    provider: Optional[str] = None
    model_overrides: Tuple[Tuple[str, str], ...] = ()


_EMPTY_CONFIG = _ClaudeConfig()


@dataclass(frozen=True)
class _Candidate:
    record: UsageRecord
    provider_confidence: int
    model_confidence: int
    provider_from_config: bool = False
    model_from_config: bool = False


def _mapping(value: object) -> Optional[Mapping[str, object]]:
    return value if isinstance(value, Mapping) else None


def _text(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _safe_model(value: object) -> Optional[str]:
    model = _text(value)
    encoded_length = _utf8_length(model)
    if model is None or encoded_length is None or encoded_length > 256:
        return None
    if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in model):
        return None
    if any(marker in model for marker in ("://", "@", "?", "#")):
        return None
    lowered = model.lower()
    if any(
        marker in lowered
        for marker in (
            "api_key",
            "apikey",
            "authorization",
            "bearer ",
            "private_key",
            "secret",
            "token",
            "-----begin",
        )
    ) or lowered.startswith(("sk-", "sk_", "key-")):
        return None
    return model


def _utf8_length(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def _secret_shaped(value: str) -> bool:
    lowered = value.lower()
    return (
        any(
            marker in lowered
            for marker in (
                "api-key",
                "apikey",
                "authorization",
                "bearer",
                "private-key",
                "secret",
                "token",
                "-----begin",
            )
        )
        or lowered.startswith(("sk-", "sk.", "key-"))
    )


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
    normalized = value.strip().rstrip("/").lower().replace("_", "-")
    if normalized.startswith("<") and normalized.endswith(">"):
        return None
    if normalized in ("", "unknown"):
        return None
    if len(normalized) > 128 or _secret_shaped(normalized):
        return None
    if not all(
        character.isascii()
        and (character.isalnum() or character in ".-")
        for character in normalized
    ):
        return None
    return normalized


def _canonical_provider_hint(value: str) -> Optional[str]:
    first = value.strip().rstrip("/").split("/", 1)[0]
    return _canonical_provider_segment(first)


def _model_digest(model: str) -> Optional[str]:
    try:
        return hashlib.sha256(model.encode("utf-8")).hexdigest()
    except UnicodeEncodeError:
        return None


def _canonical_model(
    model: str, config: _ClaudeConfig = _EMPTY_CONFIG
) -> str:
    digest = _model_digest(model)
    if digest is None:
        return model
    for configured_digest, canonical in config.model_overrides:
        if digest == configured_digest:
            return canonical
    return model


def _resolved_model(
    value: object, config: _ClaudeConfig
) -> Optional[str]:
    raw = _text(value)
    encoded_length = _utf8_length(raw)
    if raw is None or encoded_length is None or encoded_length > 1024:
        return None
    canonical = _canonical_model(raw, config)
    return _safe_model(canonical)


def _provider(
    row: Mapping[str, object],
    message: Mapping[str, object],
    model: str,
    configured: Optional[str] = None,
) -> Tuple[str, int]:
    explicit = (
        _text(message.get("providerId"))
        or _text(message.get("provider_id"))
        or _text(message.get("provider"))
        or _text(row.get("providerId"))
        or _text(row.get("provider_id"))
        or _text(row.get("provider"))
    )
    canonical = _canonical_provider_hint(explicit) if explicit else None
    if canonical is not None:
        return canonical, 4
    if "/" in model:
        model_provider = _canonical_provider_hint(model)
        if model_provider is not None:
            return model_provider, 3
    if configured is not None:
        return configured, 2
    if model.lower().startswith("claude-"):
        return "anthropic", 1
    return "unknown", 0


def _truthy(value: object) -> bool:
    text = _text(value)
    return text is not None and text.lower() in ("1", "true", "yes", "on")


def _settings_path(context: DiscoveryContext) -> Optional[Path]:
    root = context.home / ".claude"
    path = root / "settings.json"
    if root.is_symlink() or path.is_symlink():
        return None
    try:
        if not path.is_file() or path.stat().st_size > _MAX_SETTINGS_BYTES:
            return None
    except OSError:
        return None
    return path


def _config(context: DiscoveryContext) -> _ClaudeConfig:
    settings: Mapping[str, object] = {}
    path = _settings_path(context)
    if path is not None:
        value = read_json(path).value
        settings = _mapping(value) or {}
    settings_env = _mapping(settings.get("env")) or {}

    route_values = {
        key: settings_env.get(key)
        for key in _ROUTE_KEYS
        if key in settings_env
    }
    for key in _ROUTE_KEYS:
        if key in context.env:
            route_values[key] = context.env[key]

    if _truthy(route_values.get("CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST")):
        provider = "host-managed"
    else:
        active = tuple(
            provider_name
            for key, provider_name in _ROUTE_PROVIDERS.items()
            if _truthy(route_values.get(key))
        )
        provider = active[0] if len(active) == 1 else None

    configured_model = (
        _safe_model(context.env.get("ANTHROPIC_MODEL"))
        or _safe_model(settings_env.get("ANTHROPIC_MODEL"))
        or _safe_model(settings.get("model"))
    )
    overrides = _mapping(settings.get("modelOverrides")) or {}
    model_overrides = []
    for canonical_value, deployment_value in tuple(overrides.items())[:128]:
        canonical = _safe_model(canonical_value)
        deployment = _text(deployment_value)
        if (
            canonical is not None
            and deployment is not None
            and _utf8_length(deployment) is not None
            and _utf8_length(deployment) <= 1024
        ):
            digest = _model_digest(deployment)
            if digest is not None:
                model_overrides.append((digest, canonical))
    return _ClaudeConfig(
        model=configured_model,
        provider=provider,
        model_overrides=tuple(model_overrides),
    )


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
    use_new_model = new.model_confidence > existing.model_confidence
    provider_from_config = (
        new.provider_from_config
        if use_new_provider
        else existing.provider_from_config
    )
    model_from_config = (
        new.model_from_config
        if use_new_model
        else existing.model_from_config
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
            if use_new_model
            else existing.record.model
        ),
        tokens=tokens,
        confidence=(
            "estimated"
            if provider_from_config or model_from_config
            else "exact"
        ),
    )
    return _Candidate(
        record=updated,
        provider_confidence=max(
            existing.provider_confidence,
            new.provider_confidence,
        ),
        model_confidence=max(
            existing.model_confidence,
            new.model_confidence,
        ),
        provider_from_config=provider_from_config,
        model_from_config=model_from_config,
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


def parse_claude(
    paths: Sequence[Path], config: _ClaudeConfig = _EMPTY_CONFIG
) -> AdapterResult:
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
            raw_message_model = _text(message.get("model"))
            model = _resolved_model(raw_message_model, config)
            if model is not None:
                resolved_raw_model = model
                model_confidence = 4
                candidate_model_from_config = (
                    raw_message_model is not None
                    and raw_message_model != model
                )
            elif previous is not None:
                resolved_raw_model = previous.record.model
                model_confidence = previous.model_confidence
                candidate_model_from_config = previous.model_from_config
            elif config.model is not None:
                resolved_raw_model = config.model
                model_confidence = 1
                candidate_model_from_config = True
            else:
                continue
            resolved_model = resolved_raw_model
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
                row, message, resolved_model, config.provider
            )
            provider_from_config = provider_confidence == 2
            model_from_config = candidate_model_from_config
            record = UsageRecord(
                runtime=_RUNTIME,
                provider=provider,
                model=resolved_model,
                session_id=session_id,
                timestamp=_timestamp(row.get("timestamp"), path),
                tokens=usage,
                message_count=1,
                source_kind="jsonl",
                source_path=str(path),
                dedup_key=dedup_key,
                confidence=(
                    "estimated"
                    if provider_from_config or model_from_config
                    else "exact"
                ),
            )
            candidate = _Candidate(
                record,
                provider_confidence,
                model_confidence,
                provider_from_config,
                model_from_config,
            )
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
    return parse_claude(tuple(dict.fromkeys(paths)), _config(context))
