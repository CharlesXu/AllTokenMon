"""Privacy-safe parser shared by Cline-family VS Code task logs."""

from collections.abc import Mapping as MappingABC
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple

from ..discovery import discover
from ..normalize import MAX_TOKEN_VALUE, parse_timestamp, stable_key
from ..schema import AdapterResult, TokenBreakdown, UsageRecord
from .amp import _record, _result
from .base import DiscoveryContext, SourceSpec
from .jsonio import read_json


MAX_EMBEDDED_JSON_BYTES = 1024 * 1024
MAX_EMBEDDED_DEPTH = 32
MAX_EMBEDDED_NODES = 10_000
MAX_UI_MESSAGES = 100_000
MAX_UI_ENTRY_DEPTH = 16
MAX_UI_ENTRY_NODES = 10_000
MAX_MODEL_CHARS = 256
MAX_API_PROTOCOL_CHARS = 64
_USAGE_KEYS = frozenset(
    ("cost", "tokensIn", "tokensOut", "cacheReads", "cacheWrites")
)
_PROTOCOL_PROVIDERS = {
    "anthropic": "anthropic",
    "openai": "openai",
    "openai-native": "openai",
    "openai_native": "openai",
    "openai-codex": "openai",
    "openai_codex": "openai",
    "google": "google",
    "gemini": "google",
    "vertex": "anthropic",
    "vertex-ai": "anthropic",
    "vertex_ai": "anthropic",
    "openrouter": "openrouter",
    "bedrock": "bedrock",
    "bedrock/anthropic": "bedrock/anthropic",
    "azure": "azure_ai",
    "azure-ai": "azure_ai",
    "azure_ai": "azure_ai",
    "azure/openai": "azure/openai",
    "x-ai": "xai",
    "x_ai": "xai",
    "xai": "xai",
    "z-ai": "zai",
    "z_ai": "zai",
    "zai": "zai",
    "moonshot": "moonshotai",
    "moonshotai": "moonshotai",
    "meta": "meta_llama",
    "meta-llama": "meta_llama",
    "meta_llama": "meta_llama",
    "together": "together_ai",
    "together-ai": "together_ai",
    "together_ai": "together_ai",
    "fireworks": "fireworks_ai",
    "fireworks-ai": "fireworks_ai",
    "fireworks_ai": "fireworks_ai",
    "minimax": "minimax",
    "minimaxai": "minimax",
    "minimax-ai": "minimax",
    "minimax_ai": "minimax",
    "mistral": "mistralai",
    "mistralai": "mistralai",
    "ai21": "ai21",
    "deepseek": "deepseek",
    "groq": "groq",
    "ollama": "ollama",
    "lmstudio": "lmstudio",
    "lm-studio": "lmstudio",
    "lm_studio": "lmstudio",
    "requesty": "requesty",
    "litellm": "litellm",
}
_PathResult = Tuple[Tuple[UsageRecord, ...], bool, bool, bool]


def _text(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _identifier(value: object) -> Optional[str]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (str, int)):
        text = str(value).strip()
        return text or None
    return None


def _model(value: object) -> str:
    text = _text(value)
    if text is None or len(text) > MAX_MODEL_CHARS:
        return "unknown"
    return text


def _provider_from_protocol(value: object) -> str:
    protocol = _text(value)
    if protocol is None or len(protocol) > MAX_API_PROTOCOL_CHARS:
        return "unknown"
    return _PROTOCOL_PROVIDERS.get(protocol.casefold(), "unknown")


def _token(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return min(max(value, 0), MAX_TOKEN_VALUE)
    if isinstance(value, str):
        try:
            parsed = int(value.strip())
        except ValueError:
            return 0
        return min(max(parsed, 0), MAX_TOKEN_VALUE)
    return 0


def _cost(value: object) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _timestamp(value: object) -> Optional[datetime]:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        try:
            numeric = int(text)
        except ValueError:
            numeric = None
        if numeric is not None:
            value = numeric
        elif text and not text.endswith(("Z", "z")):
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                return None
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
    if isinstance(value, float):
        return None
    if isinstance(value, int) and value <= 0:
        return None
    try:
        return parse_timestamp(value)
    except ValueError:
        return None


def _within_structure_limit(
    value: object, max_depth: int, max_nodes: int
) -> bool:
    stack = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if depth > max_depth or nodes > max_nodes:
            return False
        children = (
            current.values()
            if isinstance(current, MappingABC)
            else current
            if isinstance(current, list)
            else ()
        )
        for child in children:
            if isinstance(child, (MappingABC, list)):
                stack.append((child, depth + 1))
    return True


def _bounded_object(text: str) -> Optional[Mapping[str, object]]:
    if len(text) > MAX_EMBEDDED_JSON_BYTES:
        return None
    try:
        encoded = text.encode("utf-8")
    except UnicodeError:
        return None
    if len(encoded) > MAX_EMBEDDED_JSON_BYTES:
        return None
    try:
        value = json.loads(text)
    except (RecursionError, ValueError):
        return None
    if not isinstance(value, MappingABC) or not _within_structure_limit(
        value, MAX_EMBEDDED_DEPTH, MAX_EMBEDDED_NODES
    ):
        return None
    return value


def _parse_path(runtime: str, path: Path) -> _PathResult:
    try:
        result = read_json(path)
    except RecursionError:
        return (), False, True, False
    if result.error_code:
        failed = result.error_code.startswith("io_error:")
        return (), False, not failed, failed
    if not isinstance(result.value, list):
        return (), False, False, False

    records = []
    partial = len(result.value) > MAX_UI_MESSAGES
    task_id = path.parent.name or "unknown"
    for index, raw in enumerate(result.value[:MAX_UI_MESSAGES]):
        if not isinstance(raw, MappingABC):
            continue
        if not _within_structure_limit(
            raw, MAX_UI_ENTRY_DEPTH, MAX_UI_ENTRY_NODES
        ):
            partial = True
            continue
        if raw.get("type") != "say" or raw.get("say") != "api_req_started":
            continue
        embedded_text = raw.get("text")
        if not isinstance(embedded_text, str):
            continue
        usage = _bounded_object(embedded_text)
        if usage is None:
            partial = True
            continue
        if not any(key in usage for key in _USAGE_KEYS):
            continue
        timestamp = _timestamp(raw.get("ts"))
        if timestamp is None:
            continue

        message_id = (
            _identifier(raw.get("id"))
            or _identifier(raw.get("messageId"))
            or _identifier(raw.get("requestId"))
            or str(index)
        )
        provider = _provider_from_protocol(usage.get("apiProtocol"))
        model = _model(usage.get("model"))
        tokens = TokenBreakdown(
            input=_token(usage.get("tokensIn")),
            output=_token(usage.get("tokensOut")),
            cache_read=_token(usage.get("cacheReads")),
            cache_write=_token(usage.get("cacheWrites")),
            reasoning=0,
        )
        cost = _cost(usage.get("cost")) if "cost" in usage else None
        dedup_key = stable_key(runtime, task_id, message_id)
        records.append(
            _record(
                runtime,
                path,
                provider,
                model,
                task_id,
                timestamp,
                tokens,
                dedup_key,
                source_kind="json",
                cost=cost,
            )
        )
    return tuple(records), True, partial, False


def parse_cline_family(
    runtime: str, paths: Sequence[Path]
) -> AdapterResult:
    """Parse bounded usage metadata without retaining surrounding UI text."""
    return _result(runtime, paths, lambda path: _parse_path(runtime, path))


def scan_cline_family(
    runtime: str,
    context: DiscoveryContext,
    specs: Sequence[SourceSpec],
) -> AdapterResult:
    paths = []
    for spec in specs:
        paths.extend(discover(spec, context))
    return parse_cline_family(runtime, tuple(dict.fromkeys(paths)))
