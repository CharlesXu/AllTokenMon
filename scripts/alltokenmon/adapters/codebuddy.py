"""Privacy-safe Tencent CodeBuddy transcript and extension-log adapter."""

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Sequence, Tuple

from ..normalize import safe_int, stable_key
from ..schema import TokenBreakdown, UsageRecord
from .amp import _jsonl, _mapping, _provider, _record, _result, _scan, _text, _timestamp
from .base import DiscoveryContext, SourceSpec
from .jsonio import MAX_JSON_BYTES, MAX_JSONL_LINE_BYTES, MAX_JSONL_RECORDS

_RUNTIME = "codebuddy"


def _first(usage, names, positive=False):
    found = None
    for name in names:
        if name in usage:
            value = safe_int(usage.get(name))
            if found is None:
                found = value
            if not positive or value > 0:
                return value
    return found or 0


def _tokens(value):
    usage = _mapping(value)
    if not usage:
        return None
    tokens = TokenBreakdown(
        _first(usage, ("cachedMissTokens", "cacheMissTokens", "input_tokens", "inputTokens", "prompt_tokens")),
        _first(usage, ("output_tokens", "outputTokens", "completion_tokens")),
        _first(usage, ("cache_read_input_tokens", "cacheReadInputTokens", "cacheTokens", "prompt_cache_hit_tokens", "cached_tokens"), True),
        _first(usage, ("cache_creation_input_tokens", "cacheCreationInputTokens", "cachedWriteTokens", "prompt_cache_write_tokens"), True),
        _first(usage, ("completion_thinking_tokens", "completionThinkingTokens", "reasoningTokens")),
    )
    return tokens if tokens.total or tokens.reasoning else None


def _jsonl_path(path):
    values, partial, failed = _jsonl(path)
    records = []
    recognized = False
    keyed = {}
    for index, value in enumerate(values):
        is_message = value.get("type") == "message" and value.get("role") == "assistant"
        is_call = value.get("type") == "function_call"
        if not is_message and not is_call:
            continue
        recognized = True
        if value.get("status") not in (None, "completed"):
            continue
        message = _mapping(value.get("message")) or {}
        provider_data = _mapping(value.get("providerData")) or {}
        usage = message.get("usage") or provider_data.get("usage") or provider_data.get("rawUsage")
        tokens = _tokens(usage)
        if tokens is None:
            continue
        model = (
            _text(provider_data.get("model"))
            or _text(provider_data.get("requestModelId"))
            or _text(message.get("model"))
            or _RUNTIME
        )
        session = _text(value.get("sessionId")) or path.stem or "unknown"
        upstream = (
            _text(provider_data.get("messageId"))
            or _text(provider_data.get("traceId"))
            or _text(value.get("id"))
        )
        dedup = "codebuddy:{}:{}".format(session, upstream) if upstream else stable_key(_RUNTIME, path, index)
        record = _record(
            _RUNTIME, path, _provider(model, "tencent"), model, session,
            _timestamp(value.get("timestamp"), path), tokens, dedup,
        )
        if dedup not in keyed or record.tokens.total >= keyed[dedup].tokens.total:
            keyed[dedup] = record
    return tuple(keyed.values()), recognized, partial, failed


_MODEL = re.compile(r"\[CraftInvokableAgent\]\s*\[([^]]+)\].*Model prepared:\s*.*\(([^()]+)\)")
_USAGE = re.compile(r"\[AgentReporter\]\s*\[([^]]+)\].*Agent execution successful with usage:\s*(\{.*\})")
_LOG_TIMESTAMP = re.compile(
    r"^\s*(?:\[(?P<extension>\d{4}/\d{1,2}/\d{1,2} "
    r"\d{1,2}:\d{2}:\d{2}\.\d+)\]|"
    r"(?P<vscode>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+))"
)


def _log_timestamp(text, path):
    match = _LOG_TIMESTAMP.match(text)
    if match:
        raw = match.group("extension") or match.group("vscode")
        pattern = (
            "%Y/%m/%d %H:%M:%S.%f"
            if match.group("extension")
            else "%Y-%m-%d %H:%M:%S.%f"
        )
        try:
            return datetime.strptime(raw, pattern).astimezone()
        except ValueError:
            pass
    return _timestamp(None, path)


def _log_path(path):
    records = []
    models = {}
    partial = False
    try:
        with path.open("rb") as source:
            total = 0
            for index in range(MAX_JSONL_RECORDS):
                line = source.readline(MAX_JSONL_LINE_BYTES + 1)
                if not line:
                    break
                total += len(line)
                if len(line) > MAX_JSONL_LINE_BYTES or total > MAX_JSON_BYTES:
                    partial = True
                    break
                try:
                    text = line.decode("utf-8")
                except UnicodeError:
                    partial = True
                    continue
                model_match = _MODEL.search(text)
                if model_match:
                    models[model_match.group(1).strip()] = model_match.group(2).strip()
                    continue
                usage_match = _USAGE.search(text)
                if not usage_match:
                    continue
                agent = usage_match.group(1).strip()
                try:
                    tokens = _tokens(json.loads(usage_match.group(2)))
                except ValueError:
                    partial = True
                    continue
                if tokens is None:
                    continue
                model = models.get(agent, _RUNTIME)
                parsed = _log_timestamp(text, path)
                dedup = "codebuddy:extension-log:{}:{}:{}:{}:{}:{}:{}".format(
                    agent, int(parsed.timestamp()), tokens.input, tokens.output,
                    tokens.cache_read, tokens.cache_write, tokens.reasoning,
                )
                records.append(_record(
                    _RUNTIME, path, _provider(model, "tencent"), model, agent,
                    parsed, tokens, dedup, source_kind="log",
                ))
    except OSError:
        return (), False, partial, True
    return tuple(records), bool(records or models), partial, False


def _path(path):
    return _log_path(path) if path.suffix.lower() == ".log" else _jsonl_path(path)


def parse_codebuddy(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _path)


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_codebuddy)
