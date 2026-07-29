"""Privacy-safe Codex JSONL token usage adapter."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional, Sequence, Set, Tuple

from ..discovery import discover
from ..normalize import (
    CumulativeCounter,
    deduplicate,
    parse_timestamp,
    safe_int,
    stable_key,
)
from ..schema import (
    AdapterResult,
    AdapterStatus,
    Diagnostic,
    TokenBreakdown,
    UsageRecord,
)
from .base import DiscoveryContext, SourceSpec
from .jsonio import read_json_lines


_RUNTIME = "codex"


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


def _timestamp(
    value: object, metadata_value: object, path: Path
) -> datetime:
    for candidate in (value, metadata_value):
        try:
            return parse_timestamp(candidate)
        except ValueError:
            continue
    return _fallback_timestamp(path)


def _usage_counter(
    value: object,
) -> Optional[CumulativeCounter]:
    usage = _mapping(value)
    if usage is None:
        return None
    known_fields = (
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "cache_read_input_tokens",
        "reasoning_output_tokens",
    )
    if not any(field in usage for field in known_fields):
        return None
    cached = max(
        safe_int(usage.get("cached_input_tokens")),
        safe_int(usage.get("cache_read_input_tokens")),
    )
    return CumulativeCounter(
        input=safe_int(usage.get("input_tokens")),
        output=safe_int(usage.get("output_tokens")),
        cache_read=cached,
        cache_write=0,
        reasoning=safe_int(usage.get("reasoning_output_tokens")),
    )


def _tokens(counter: CumulativeCounter) -> TokenBreakdown:
    input_tokens = safe_int(counter.input)
    cached = min(safe_int(counter.cache_read), input_tokens)
    return TokenBreakdown(
        input=input_tokens - cached,
        output=safe_int(counter.output),
        cache_read=cached,
        cache_write=0,
        reasoning=safe_int(counter.reasoning),
    )


def _counter_values(
    counter: CumulativeCounter,
) -> Tuple[int, int, int, int]:
    return (
        safe_int(counter.input),
        safe_int(counter.output),
        safe_int(counter.cache_read),
        safe_int(counter.reasoning),
    )


def _counter_regressed(
    current: CumulativeCounter, previous: CumulativeCounter
) -> bool:
    return any(
        now < before
        for now, before in zip(
            _counter_values(current),
            _counter_values(previous),
        )
    )


def _counter_within(
    current: CumulativeCounter, baseline: CumulativeCounter
) -> bool:
    return all(
        now <= inherited
        for now, inherited in zip(
            _counter_values(current),
            _counter_values(baseline),
        )
    )


def _looks_like_stale_regression(
    current: CumulativeCounter,
    previous: CumulativeCounter,
    last: CumulativeCounter,
) -> bool:
    previous_total = sum(_counter_values(previous))
    current_total = sum(_counter_values(current))
    last_total = sum(_counter_values(last))
    if previous_total <= 0 or current_total <= 0 or last_total <= 0:
        return False
    return (
        current_total * 100 >= previous_total * 98
        or current_total + last_total * 2 >= previous_total
    )


def _fork_parent_from_source(value: object) -> Optional[str]:
    source = _mapping(value)
    subagent = _mapping(source.get("subagent")) if source else None
    thread_spawn = (
        _mapping(subagent.get("thread_spawn")) if subagent else None
    )
    return (
        _text(thread_spawn.get("parent_thread_id"))
        if thread_spawn
        else None
    )


def _uuid_v7_time_prefix(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    parts = value.split("-")
    if (
        len(parts) != 5
        or len(parts[0]) != 8
        or len(parts[1]) != 4
        or len(parts[2]) != 4
        or not parts[2].startswith("7")
    ):
        return None
    prefix = parts[0] + parts[1]
    try:
        int(prefix, 16)
    except ValueError:
        return None
    return prefix.lower()


def _task_starts_child(
    child_session_id: Optional[str], turn_id: Optional[str]
) -> bool:
    if turn_id is None or child_session_id is None:
        return False
    child_prefix = _uuid_v7_time_prefix(child_session_id)
    if child_prefix is None:
        return True
    turn_prefix = _uuid_v7_time_prefix(turn_id)
    return turn_prefix is not None and turn_prefix >= child_prefix


def _turn_starts_child(
    child_session_id: Optional[str],
    replay_session_id: Optional[str],
    turn_id: Optional[str],
    task_started_turn_ids: Set[str],
    is_user_fork: bool,
) -> bool:
    if replay_session_id is None or child_session_id is None:
        return True
    child_prefix = _uuid_v7_time_prefix(child_session_id)
    turn_prefix = _uuid_v7_time_prefix(turn_id)
    if turn_id is not None and child_prefix is not None:
        if turn_prefix is None:
            return is_user_fork or turn_id in task_started_turn_ids
        if turn_prefix > child_prefix:
            return True
        if turn_prefix < child_prefix:
            return False
        return is_user_fork or turn_id in task_started_turn_ids
    return True


def _model_from_payload(payload: Mapping[str, object]) -> Optional[str]:
    model_info = _mapping(payload.get("model_info"))
    info = _mapping(payload.get("info"))
    return (
        _text(payload.get("model"))
        or _text(payload.get("model_name"))
        or (_text(model_info.get("slug")) if model_info else None)
        or (_text(info.get("model")) if info else None)
        or (_text(info.get("model_name")) if info else None)
    )


def _record(
    path: Path,
    provider: str,
    model: str,
    session_id: str,
    timestamp: datetime,
    counter: CumulativeCounter,
    dedup_key: str,
) -> Optional[UsageRecord]:
    tokens = _tokens(counter)
    if tokens.total == 0 and tokens.reasoning == 0:
        return None
    return UsageRecord(
        runtime=_RUNTIME,
        provider=provider,
        model=model,
        session_id=session_id,
        timestamp=timestamp,
        tokens=tokens,
        message_count=1,
        source_kind="jsonl",
        source_path=str(path),
        dedup_key=dedup_key,
        confidence="exact",
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
        message="Codex adapter completed",
        source_count=source_count,
        record_count=record_count,
    )


def parse_codex(paths: Sequence[Path]) -> AdapterResult:
    """Parse bounded Codex JSONL sources into exact usage records."""
    records = []
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

        session_id = path.stem or "unknown"
        dedup_scope = session_id
        provider = "openai"
        model = "unknown"
        metadata_timestamp = None
        previous_total = None
        fork_child_session_id = None
        fork_replay_session_id = None
        fork_waiting = False
        fork_task_started_turn_ids = set()
        fork_is_user = False
        fork_inherited_baseline = None

        for row_index, row in enumerate(result.values):
            entry_type = _text(row.get("type"))
            payload = _mapping(row.get("payload"))

            if fork_waiting and payload is not None:
                turn_id = _text(payload.get("turn_id"))
                if (
                    entry_type == "turn_context"
                    and _turn_starts_child(
                        fork_child_session_id,
                        fork_replay_session_id,
                        turn_id,
                        fork_task_started_turn_ids,
                        fork_is_user,
                    )
                ):
                    fork_waiting = False
                    fork_replay_session_id = None
                    fork_task_started_turn_ids = set()
                    fork_is_user = False
                    session_id = fork_child_session_id or session_id
                    model = _model_from_payload(payload) or model
                    continue
                if (
                    entry_type == "event_msg"
                    and _text(payload.get("type")) == "task_started"
                    and _task_starts_child(
                        fork_child_session_id, turn_id
                    )
                    and turn_id is not None
                ):
                    fork_task_started_turn_ids.add(turn_id)
                if entry_type == "session_meta":
                    replay_id = _text(payload.get("id"))
                    if (
                        replay_id is not None
                        and replay_id != fork_child_session_id
                    ):
                        fork_replay_session_id = replay_id
                if (
                    entry_type == "event_msg"
                    and _text(payload.get("type")) == "token_count"
                ):
                    info = _mapping(payload.get("info"))
                    inherited = (
                        _usage_counter(info.get("total_token_usage"))
                        if info
                        else None
                    )
                    if inherited is not None:
                        previous_total = inherited
                        fork_inherited_baseline = inherited
                continue

            if entry_type == "session_meta" and payload is not None:
                recognized = True
                metadata_session_id = _text(payload.get("id"))
                session_id = metadata_session_id or session_id
                forked_from = (
                    _text(payload.get("forked_from_id"))
                    or _fork_parent_from_source(payload.get("source"))
                )
                repeated_active_child_meta = (
                    not fork_waiting
                    and metadata_session_id is not None
                    and metadata_session_id == fork_child_session_id
                )
                if forked_from and not repeated_active_child_meta:
                    fork_child_session_id = session_id
                    fork_replay_session_id = None
                    fork_waiting = True
                    fork_task_started_turn_ids = set()
                    fork_is_user = (
                        _text(payload.get("thread_source")) == "user"
                    )
                    fork_inherited_baseline = None
                dedup_scope = forked_from or session_id
                provider = _text(payload.get("model_provider")) or provider
                model = _model_from_payload(payload) or model
                metadata_timestamp = row.get("timestamp")
                continue

            if entry_type == "turn_context" and payload is not None:
                recognized = True
                model = _model_from_payload(payload) or model
                continue

            is_token_count = (
                entry_type == "event_msg"
                and payload is not None
                and _text(payload.get("type")) == "token_count"
            )
            if is_token_count:
                info = _mapping(payload.get("info"))
                if info is None:
                    recognized = True
                    continue
                recognized = True
                model = _model_from_payload(payload) or model
                last = _usage_counter(info.get("last_token_usage"))
                total = _usage_counter(info.get("total_token_usage"))
                if (
                    fork_inherited_baseline is not None
                    and total is not None
                    and _counter_within(
                        total, fork_inherited_baseline
                    )
                ):
                    continue
                if fork_inherited_baseline is not None and total is not None:
                    fork_inherited_baseline = None
                if total is not None and previous_total == total:
                    continue
                if last is not None:
                    if (
                        total is not None
                        and previous_total is not None
                        and _counter_regressed(total, previous_total)
                        and _looks_like_stale_regression(
                            total, previous_total, last
                        )
                    ):
                        continue
                    increment = last
                elif total is not None:
                    if (
                        previous_total is not None
                        and _counter_regressed(total, previous_total)
                    ):
                        previous_total = total
                        continue
                    increment = total if previous_total is None else (
                        total.delta_from(previous_total)
                    )
                else:
                    continue
                if total is not None:
                    previous_total = total

                event_id = _text(payload.get("id"))
                if event_id:
                    key = stable_key(
                        _RUNTIME, dedup_scope, "event", event_id
                    )
                elif total is not None:
                    key = stable_key(
                        _RUNTIME,
                        dedup_scope,
                        "total",
                        total.input,
                        total.output,
                        total.cache_read,
                        total.reasoning,
                    )
                else:
                    key = stable_key(
                        _RUNTIME,
                        dedup_scope,
                        row.get("timestamp"),
                        increment.input,
                        increment.output,
                        increment.cache_read,
                        increment.reasoning,
                    )
                usage_record = _record(
                    path,
                    provider,
                    model,
                    session_id,
                    _timestamp(
                        row.get("timestamp"), metadata_timestamp, path
                    ),
                    increment,
                    key,
                )
                if usage_record is not None:
                    records.append(usage_record)
                continue

            if entry_type == "turn.completed":
                recognized = True
                usage = _usage_counter(row.get("usage"))
                if usage is None:
                    data = _mapping(row.get("data"))
                    usage = _usage_counter(data.get("usage")) if data else None
                if usage is None:
                    continue
                event_model = _text(row.get("model"))
                if event_model:
                    model = event_model
                event_provider = _text(row.get("provider"))
                if event_provider:
                    provider = event_provider
                event_id = _text(row.get("id"))
                key = stable_key(
                    _RUNTIME,
                    dedup_scope,
                    "turn.completed",
                    event_id or row_index,
                    model,
                )
                usage_record = _record(
                    path,
                    provider,
                    model,
                    session_id,
                    _timestamp(
                        row.get("timestamp"), metadata_timestamp, path
                    ),
                    usage,
                    key,
                )
                if usage_record is not None:
                    records.append(usage_record)
                continue

            if (
                entry_type == "event_msg"
                and payload is not None
                and _text(payload.get("type")) == "user_message"
            ):
                recognized = True

    unique_records = tuple(deduplicate(records))
    if read_error and not unique_records and not recognized:
        status = AdapterStatus.ERROR
        code = "read_error"
    elif partial:
        status = AdapterStatus.PARTIAL
        code = "partial_source"
    elif unique_records:
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
    diagnostic = _diagnostic(
        status, code, existing_count, len(unique_records)
    )
    return AdapterResult(
        runtime=_RUNTIME,
        status=status,
        records=unique_records,
        diagnostics=(diagnostic,),
    )


def scan(
    context: DiscoveryContext, specs: Sequence[SourceSpec]
) -> AdapterResult:
    """Discover registered Codex paths and parse them."""
    paths = []
    for spec in specs:
        paths.extend(discover(spec, context))
    return parse_codex(tuple(dict.fromkeys(paths)))
