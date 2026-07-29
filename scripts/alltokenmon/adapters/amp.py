"""Privacy-safe Amp thread usage adapter and file-adapter primitives."""

from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence, Tuple

from ..discovery import discover
from ..normalize import parse_timestamp, safe_int, stable_key
from ..schema import (
    AdapterResult,
    AdapterStatus,
    Diagnostic,
    TokenBreakdown,
    UsageRecord,
)
from .base import DiscoveryContext, SourceSpec
from .jsonio import (
    MAX_JSON_BYTES,
    MAX_JSONL_LINE_BYTES,
    MAX_JSONL_RECORDS,
    _exceeds_nesting,
    read_json,
)


_RUNTIME = "amp"
_PathParser = Callable[
    [Path], Tuple[Tuple[UsageRecord, ...], bool, bool, bool]
]


def _mapping(value: object) -> Optional[Mapping[str, object]]:
    return value if isinstance(value, Mapping) else None


def _text(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _mtime(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except (OSError, OverflowError, ValueError):
        return datetime.fromtimestamp(0, timezone.utc)


def _timestamp(value: object, path: Path) -> datetime:
    parsed = _parsed_timestamp(value)
    return parsed if parsed is not None else _mtime(path)


def _parsed_timestamp(value: object) -> Optional[datetime]:
    try:
        return parse_timestamp(value)
    except ValueError:
        return None


def _back_anchor(end: datetime, duration_value: object) -> datetime:
    duration = safe_int(duration_value)
    if duration == 0:
        return end
    try:
        candidate = end - timedelta(milliseconds=duration)
        return candidate if candidate.timestamp() > 0 else end
    except (OverflowError, OSError, ValueError):
        return end


def _offset_timestamp(base: datetime, seconds_value: object) -> datetime:
    seconds = safe_int(seconds_value)
    if seconds == 0:
        return base
    try:
        return base + timedelta(seconds=seconds)
    except OverflowError:
        return base


def _finite_cost(value: object, *, allow_zero: bool = True) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result) or result < 0 or (not allow_zero and result == 0):
        return None
    return result


def _provider(model: str, fallback: str = "unknown") -> str:
    lower = model.lower()
    rules = (
        (("claude", "anthropic", "opus", "sonnet", "haiku", "fable"), "anthropic"),
        (("gpt", "openai", "o1", "o3", "o4"), "openai"),
        (("gemini", "google"), "google"),
        (("grok",), "xai"),
        (("deepseek",), "deepseek"),
        (("minimax",), "minimax"),
        (("mistral", "mixtral"), "mistral"),
        (("llama",), "meta"),
        (("qwen",), "qwen"),
        (("kimi",), "moonshotai"),
        (("glm",), "zai"),
    )
    for needles, provider in rules:
        if any(needle in lower for needle in needles):
            return provider
    return fallback


def _jsonl(path: Path) -> Tuple[Tuple[Mapping[str, object], ...], bool, bool]:
    """Read bounded object lines, skipping malformed records like Tokscale."""
    values = []
    partial = False
    try:
        with path.open("rb") as source:
            total = 0
            while len(values) < MAX_JSONL_RECORDS:
                remaining = MAX_JSON_BYTES - total
                if remaining <= 0:
                    return tuple(values), True, False
                line = source.readline(min(MAX_JSONL_LINE_BYTES, remaining) + 1)
                if not line:
                    break
                total += len(line)
                if len(line) > MAX_JSONL_LINE_BYTES or total > MAX_JSON_BYTES:
                    return tuple(values), True, False
                if not line.strip():
                    continue
                try:
                    text = line.decode("utf-8")
                    if _exceeds_nesting(text):
                        partial = True
                        continue
                    value = json.loads(text)
                except (RecursionError, UnicodeError, ValueError):
                    partial = True
                    continue
                if isinstance(value, Mapping):
                    values.append(value)
                else:
                    partial = True
            if source.read(1):
                partial = True
    except OSError:
        return tuple(values), partial, True
    return tuple(values), partial, False


def _result(
    runtime: str,
    paths: Sequence[Path],
    parser: _PathParser,
) -> AdapterResult:
    existing = tuple(sorted(
        {Path(path) for path in paths if Path(path).is_file()},
        key=lambda path: (str(path).casefold(), str(path)),
    ))
    records = []
    recognized = False
    partial = False
    unsupported = False
    read_error = False
    for path in existing:
        parsed, known, incomplete, failed = parser(path)
        records.extend(parsed)
        recognized = recognized or known
        partial = partial or incomplete
        unsupported = unsupported or not known
        read_error = read_error or failed

    unique = {}
    for record in records:
        unique[record.dedup_key] = record
    result_records = tuple(unique.values())
    if read_error and not result_records and not recognized:
        status, code = AdapterStatus.ERROR, "read_error"
    elif partial or (read_error and result_records) or (unsupported and result_records):
        status, code = AdapterStatus.PARTIAL, "partial_source"
    elif result_records:
        status, code = AdapterStatus.OK, "ok"
    elif not existing or recognized:
        status, code = AdapterStatus.NO_DATA, "no_data"
    else:
        status, code = AdapterStatus.UNSUPPORTED_FORMAT, "unsupported_format"
    diagnostic = Diagnostic(
        runtime,
        status,
        code,
        "{} adapter completed".format(runtime),
        len(existing),
        len(result_records),
    )
    return AdapterResult(runtime, status, result_records, (diagnostic,))


def _scan(
    context: DiscoveryContext,
    specs: Sequence[SourceSpec],
    parser: Callable[[Sequence[Path]], AdapterResult],
) -> AdapterResult:
    paths = []
    for spec in specs:
        paths.extend(discover(spec, context))
    return parser(tuple(dict.fromkeys(paths)))


def _record(
    runtime: str,
    path: Path,
    provider: str,
    model: str,
    session_id: str,
    timestamp: datetime,
    tokens: TokenBreakdown,
    dedup_key: str,
    *,
    source_kind: Optional[str] = None,
    confidence: str = "exact",
    cost: Optional[float] = None,
) -> UsageRecord:
    return UsageRecord(
        runtime=runtime,
        provider=provider or "unknown",
        model=model or "unknown",
        session_id=session_id or "unknown",
        timestamp=timestamp,
        tokens=tokens,
        message_count=1,
        source_kind=source_kind or ("jsonl" if path.suffix in (".jsonl", ".ndjson") else "json"),
        source_path=str(path),
        dedup_key=dedup_key,
        confidence=confidence,
        cost=cost,
        cost_source="provider_reported" if cost is not None else None,
    )


def _amp_path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    result = read_json(path)
    if result.error_code:
        return (), False, False, result.error_code.startswith("io_error:")
    root = _mapping(result.value)
    if root is None:
        return (), False, False, False
    ledger = _mapping(root.get("usageLedger"))
    events = ledger.get("events") if ledger else None
    messages = root.get("messages")
    if not isinstance(events, list) and not isinstance(messages, list):
        return (), False, False, False
    session = _text(root.get("id")) or path.stem or "unknown"
    created = _timestamp(root.get("created"), path)
    ledger_rows = []
    for index, raw in enumerate(events if isinstance(events, list) else ()):
        event = _mapping(raw)
        if event is None:
            continue
        model = _text(event.get("model"))
        if model is None:
            continue
        token_value = _mapping(event.get("tokens")) or {}
        tokens = TokenBreakdown(
            safe_int(token_value.get("input")),
            safe_int(token_value.get("output")),
            safe_int(token_value.get("cacheReadInputTokens")),
            safe_int(token_value.get("cacheCreationInputTokens")),
        )
        explicit = event.get("timestamp") is not None
        timestamp = _timestamp(event.get("timestamp"), path) if explicit else created
        ledger_rows.append({
            "model": model,
            "tokens": tokens,
            "timestamp": timestamp,
            "explicit": explicit,
            "to_id": safe_int(event.get("toMessageId")) or None,
            "cost": _finite_cost(event.get("credits"), allow_zero=False),
            "index": index,
        })
    message_rows = []
    for index, raw in enumerate(messages if isinstance(messages, list) else ()):
        message = _mapping(raw)
        usage = _mapping(message.get("usage")) if message else None
        if not message or message.get("role") != "assistant" or not usage:
            continue
        model = _text(usage.get("model"))
        if model is None:
            continue
        message_id = safe_int(message.get("messageId"))
        message_rows.append({
            "model": model,
            "tokens": TokenBreakdown(
                safe_int(usage.get("inputTokens")),
                safe_int(usage.get("outputTokens")),
                safe_int(usage.get("cacheReadInputTokens")),
                safe_int(usage.get("cacheCreationInputTokens")),
            ),
            "timestamp": _offset_timestamp(created, message_id),
            "id": message_id or None,
            "cost": _finite_cost(usage.get("credits"), allow_zero=False),
            "index": index,
        })
    consumed = set()
    combined = []
    for message in message_rows:
        match = None
        for index, ledger_row in enumerate(ledger_rows):
            if index in consumed:
                continue
            if message["id"] and ledger_row["to_id"] == message["id"]:
                match = index
                break
        if match is None:
            for index, ledger_row in enumerate(ledger_rows):
                if (
                    index not in consumed
                    and ledger_row["model"] == message["model"]
                    and ledger_row["tokens"] == message["tokens"]
                ):
                    match = index
                    break
        if match is None:
            combined.append(message)
            continue
        consumed.add(match)
        merged = dict(ledger_rows[match])
        if not merged["explicit"]:
            merged["timestamp"] = message["timestamp"]
        if merged["cost"] is None:
            merged["cost"] = message["cost"]
        merged["id"] = message["id"]
        combined.append(merged)
    combined.extend(row for index, row in enumerate(ledger_rows) if index not in consumed)
    combined.sort(key=lambda row: row["timestamp"])
    records = []
    for index, row in enumerate(combined):
        identity = row.get("id") or row.get("to_id") or row.get("index", index)
        records.append(_record(
            _RUNTIME, path, _provider(row["model"], "anthropic"), row["model"],
            session, row["timestamp"], row["tokens"],
            "amp:{}:{}".format(session, identity), cost=row["cost"],
        ))
    return tuple(records), True, False, False


def parse_amp(paths: Sequence[Path]) -> AdapterResult:
    return _result(_RUNTIME, paths, _amp_path)


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]) -> AdapterResult:
    return _scan(context, specs, parse_amp)
