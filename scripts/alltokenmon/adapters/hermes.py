"""Privacy-safe Hermes Agent SQLite usage adapter."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence, Tuple

from ..discovery import discover
from ..normalize import parse_timestamp, safe_int
from ..schema import (
    AdapterResult,
    AdapterStatus,
    Diagnostic,
    TokenBreakdown,
    UsageRecord,
)
from .base import DiscoveryContext, SourceSpec
from .sqliteio import (
    SqliteReadError,
    open_sqlite_readonly,
    quote_identifier,
    sqlite_schema,
)


_RUNTIME = "hermes"
_MAX_ROWS = 100_000
_SESSION_COLUMNS = {
    "id",
    "model",
    "started_at",
    "message_count",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "billing_provider",
    "estimated_cost_usd",
    "actual_cost_usd",
}
_MODEL_COLUMNS = {
    "session_id",
    "model",
    "billing_provider",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "estimated_cost_usd",
    "actual_cost_usd",
}


def _text(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _canonical_provider(value: Optional[str], model: str) -> str:
    if value:
        normalized = value.rstrip("/").split("/")[0].lower().replace("-", "_")
        aliases = {
            "openai_codex": "openai",
            "vertex": "anthropic",
            "vertex_ai": "anthropic",
            "gemini": "google",
        }
        if normalized != "unknown":
            return aliases.get(normalized, normalized)
    lower = model.lower()
    if "claude" in lower or "anthropic" in lower:
        return "anthropic"
    if "gpt" in lower or "openai" in lower:
        return "openai"
    if "gemini" in lower or "google" in lower:
        return "google"
    if "grok" in lower:
        return "xai"
    if "kimi" in lower:
        return "moonshotai"
    if "glm" in lower:
        return "zai"
    return "hermes"


def _timestamp(value: object) -> datetime:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = value if value > 1_000_000_000_000 else value * 1000
        try:
            return datetime.fromtimestamp(numeric / 1000, timezone.utc)
        except (OSError, OverflowError, ValueError):
            return datetime.fromtimestamp(0, timezone.utc)
    try:
        return parse_timestamp(value)
    except ValueError:
        return datetime.fromtimestamp(0, timezone.utc)


def _number(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return result if 0 < result < float("inf") else 0.0


def _cost(actual: object, estimated: object) -> Tuple[Optional[float], Optional[str]]:
    actual_value = _number(actual)
    if actual_value > 0:
        return actual_value, "provider_reported"
    estimated_value = _number(estimated)
    if estimated_value > 0:
        return estimated_value, "provider_reported"
    return None, None


def _record(
    path: Path,
    session_id: str,
    model: str,
    provider_value: object,
    started_at: object,
    message_count: object,
    input_tokens: object,
    output_tokens: object,
    cache_read: object,
    cache_write: object,
    reasoning: object,
    estimated_cost: object,
    actual_cost: object,
    dedup_key: str,
) -> Optional[UsageRecord]:
    tokens = TokenBreakdown(
        safe_int(input_tokens),
        safe_int(output_tokens),
        safe_int(cache_read),
        safe_int(cache_write),
        safe_int(reasoning),
    )
    cost, source = _cost(actual_cost, estimated_cost)
    if tokens.total == 0 and tokens.reasoning == 0 and cost is None:
        return None
    return UsageRecord(
        runtime=_RUNTIME,
        provider=_canonical_provider(_text(provider_value), model),
        model=model,
        session_id=session_id,
        timestamp=_timestamp(started_at),
        tokens=tokens,
        message_count=safe_int(message_count),
        source_kind="sqlite",
        source_path=str(path),
        dedup_key=dedup_key,
        confidence="exact",
        cost=cost,
        cost_source=source,
    )


def _parse_db(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool]:
    connection = open_sqlite_readonly(path)
    try:
        schema = sqlite_schema(connection)
        session_columns = set(schema.get("sessions", ()))
        if not _SESSION_COLUMNS.issubset(session_columns):
            return (), False, False
        model_columns = set(schema.get("session_model_usage", ()))
        has_models = _MODEL_COLUMNS.issubset(model_columns)

        sessions_query = (
            "SELECT "
            + ", ".join(quote_identifier(name) for name in (
                "id", "model", "started_at", "message_count",
                "input_tokens", "output_tokens", "cache_read_tokens",
                "cache_write_tokens", "reasoning_tokens", "billing_provider",
                "estimated_cost_usd", "actual_cost_usd",
            ))
            + " FROM "
            + quote_identifier("sessions")
            + " ORDER BY "
            + quote_identifier("id")
            + " LIMIT ?"
        )
        sessions = {}
        partial = False
        for index, row in enumerate(connection.execute(sessions_query, (_MAX_ROWS + 1,))):
            if index >= _MAX_ROWS:
                partial = True
                break
            session_id = _text(row[0])
            if session_id is not None:
                sessions[session_id] = row

        records = []
        covered = set()
        if has_models:
            model_query = (
                "SELECT "
                + ", ".join(quote_identifier(name) for name in (
                    "session_id", "model", "billing_provider",
                    "input_tokens", "output_tokens", "cache_read_tokens",
                    "cache_write_tokens", "reasoning_tokens",
                    "estimated_cost_usd", "actual_cost_usd",
                ))
                + " FROM "
                + quote_identifier("session_model_usage")
                + " ORDER BY "
                + quote_identifier("session_id")
                + ", "
                + quote_identifier("model")
                + ", "
                + quote_identifier("billing_provider")
                + " LIMIT ?"
            )
            grouped: Dict[Tuple[str, str, Optional[str]], list] = {}
            for index, row in enumerate(connection.execute(model_query, (_MAX_ROWS + 1,))):
                if index >= _MAX_ROWS:
                    partial = True
                    break
                session_id = _text(row[0])
                model = _text(row[1])
                if session_id is None or model is None or session_id not in sessions:
                    continue
                key = (session_id, model, _text(row[2]))
                values = grouped.setdefault(key, [0, 0, 0, 0, 0, 0.0, 0.0])
                for offset in range(5):
                    values[offset] += safe_int(row[3 + offset])
                estimated = _number(row[8])
                actual = _number(row[9])
                values[6] += actual if actual > 0 else estimated
            counted = set()
            for (session_id, model, provider), values in sorted(grouped.items()):
                session = sessions[session_id]
                record = _record(
                    path,
                    session_id,
                    model,
                    provider,
                    session[2],
                    session[3] if session_id not in counted else 0,
                    *values[:5],
                    0,
                    values[6],
                    "hermes:{}:{}:{}".format(
                        session_id, model, provider if provider is not None else "<null>"
                    ),
                )
                if record is not None:
                    records.append(record)
                    covered.add(session_id)
                    counted.add(session_id)

        for session_id, row in sorted(sessions.items()):
            if session_id in covered:
                continue
            model = _text(row[1])
            if model is None:
                continue
            actual = row[11]
            estimated = row[10] if actual is None else None
            record = _record(
                path,
                session_id,
                model,
                row[9],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
                row[7],
                row[8],
                estimated,
                actual,
                session_id,
            )
            if record is not None:
                records.append(record)
        return tuple(records), True, partial
    finally:
        connection.close()


def _diagnostic(status, code, source_count, record_count):
    return Diagnostic(
        _RUNTIME,
        status,
        code,
        "Hermes adapter completed",
        source_count,
        record_count,
    )


def parse_hermes(paths: Sequence[Path]) -> AdapterResult:
    existing = tuple(
        sorted(
            {Path(path) for path in paths if Path(path).is_file()},
            key=lambda path: (str(path).casefold(), str(path)),
        )
    )
    records = []
    recognized = False
    partial = False
    read_error = False
    unsupported_source = False
    for path in existing:
        try:
            values, known, incomplete = _parse_db(path)
        except (SqliteReadError, OSError):
            read_error = True
            continue
        records.extend(values)
        recognized = recognized or known
        unsupported_source = unsupported_source or not known
        partial = partial or incomplete

    unique = {}
    for record in records:
        unique.setdefault(record.dedup_key, record)
    result_records = tuple(unique.values())
    if read_error and not result_records and not recognized:
        status, code = AdapterStatus.ERROR, "read_error"
    elif partial or (read_error and result_records) or (
        unsupported_source and result_records
    ):
        status, code = AdapterStatus.PARTIAL, "partial_source"
    elif result_records:
        status, code = AdapterStatus.OK, "ok"
    elif not existing:
        status, code = AdapterStatus.NO_DATA, "no_data"
    elif recognized:
        status, code = AdapterStatus.NO_DATA, "no_data"
    else:
        status, code = AdapterStatus.UNSUPPORTED_FORMAT, "unsupported_format"
    return AdapterResult(
        _RUNTIME,
        status,
        result_records,
        (_diagnostic(status, code, len(existing), len(result_records)),),
    )


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]) -> AdapterResult:
    paths = []
    for spec in specs:
        paths.extend(discover(spec, context))
    return parse_hermes(tuple(dict.fromkeys(paths)))
