"""Privacy-safe Jcode snapshot and journal adapter."""

from pathlib import Path
from typing import Mapping, Sequence, Tuple

from ..normalize import safe_int
from ..schema import TokenBreakdown, UsageRecord
from .amp import (
    _back_anchor,
    _jsonl,
    _mapping,
    _parsed_timestamp,
    _record,
    _result,
    _scan,
    _text,
    _timestamp,
)
from .base import DiscoveryContext, SourceSpec
from .jsonio import read_json

_RUNTIME = "jcode"


def _provider_name(value):
    raw = (_text(value) or _RUNTIME).lower().replace("-", "_")
    return {
        "openai_codex": "openai", "gemini": "google",
        "vertex": "anthropic", "vertex_ai": "anthropic",
        "x_ai": "xai", "z_ai": "zai",
    }.get(raw, raw)


def _tokens(usage: Mapping[str, object]) -> TokenBreakdown:
    raw_input = safe_int(usage.get("input_tokens"))
    cache_read = safe_int(usage.get("cache_read_input_tokens"))
    cache_write = safe_int(usage.get("cache_creation_input_tokens"))
    split = "cache_creation_input_tokens" in usage or cache_read > raw_input
    return TokenBreakdown(
        raw_input if split else max(raw_input - min(raw_input, cache_read), 0),
        safe_int(usage.get("output_tokens")), cache_read, cache_write,
        safe_int(usage.get("reasoning_output_tokens")),
    )


def _first_wins(records):
    unique = {}
    for record in records:
        if record.dedup_key not in unique:
            unique[record.dedup_key] = record
    return tuple(unique.values())


def _messages(
    path, values, session, provider, model, scope
):
    records = []
    for index, raw in enumerate(values if isinstance(values, list) else ()):
        message = _mapping(raw)
        usage = _mapping(message.get("token_usage")) if message else None
        if not message or not usage:
            continue
        tokens = _tokens(usage)
        if tokens.total == 0 and tokens.reasoning == 0:
            continue
        message_id = _text(message.get("id")) or "{}:{}".format(scope, index)
        explicit_timestamp = _parsed_timestamp(message.get("timestamp"))
        timestamp = explicit_timestamp or _timestamp(None, path)
        if explicit_timestamp is not None:
            timestamp = _back_anchor(
                explicit_timestamp, message.get("tool_duration_ms")
            )
        records.append(_record(
            _RUNTIME, path, provider, model, session, timestamp, tokens,
            "jcode:{}:{}".format(session, message_id),
        ))
    return records


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    result = read_json(path)
    if result.error_code:
        return (), False, False, result.error_code.startswith("io_error:")
    root = _mapping(result.value)
    if root is None or not isinstance(root.get("messages"), list):
        return (), False, False, False
    session = _text(root.get("id")) or path.stem
    provider = _provider_name(root.get("provider_key"))
    model = _text(root.get("model")) or "unknown"
    records = _messages(path, root.get("messages"), session, provider, model, "snapshot")
    indices = {}
    for index, record in enumerate(records):
        if record.dedup_key not in indices:
            indices[record.dedup_key] = index
    journal = path.with_name(path.name[:-5] + ".journal.jsonl") if path.name.endswith(".json") else path.with_name(path.name + ".journal.jsonl")
    if journal.is_file():
        values, partial, failed = _jsonl(journal)
        for line_index, entry in enumerate(values):
            meta = _mapping(entry.get("meta"))
            if meta:
                provider = (
                    _provider_name(meta.get("provider_key"))
                    if _text(meta.get("provider_key"))
                    else provider
                )
                model = _text(meta.get("model")) or model
            for record in _messages(
                journal, entry.get("append_messages"), session, provider, model,
                "journal:{}".format(line_index),
            ):
                existing = indices.get(record.dedup_key)
                if existing is None:
                    indices[record.dedup_key] = len(records)
                    records.append(record)
                else:
                    records[existing] = record
        return _first_wins(records), True, partial, failed
    return _first_wins(records), True, False, False


def parse_jcode(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _path)


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_jcode)
