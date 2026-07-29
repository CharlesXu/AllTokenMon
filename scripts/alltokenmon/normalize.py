from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from typing import Iterable, List

from .schema import TokenBreakdown, UsageRecord


MAX_TOKEN_VALUE = (1 << 63) - 1


def safe_int(value: object) -> int:
    if isinstance(value, bool):
        return 0

    try:
        if isinstance(value, int):
            converted = value
        else:
            text = str(value).strip()
            try:
                converted = int(text)
            except ValueError:
                decimal_value = Decimal(text)
                if not decimal_value.is_finite():
                    return 0
                if decimal_value <= 0:
                    return 0
                if decimal_value >= MAX_TOKEN_VALUE:
                    return MAX_TOKEN_VALUE
                converted = int(decimal_value)
    except (TypeError, ValueError, OverflowError, InvalidOperation):
        return 0

    return min(max(converted, 0), MAX_TOKEN_VALUE)


def parse_timestamp(value: object) -> datetime:
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, bool):
            raise ValueError("timestamp must not be a boolean")
        elif isinstance(value, (int, float)):
            numeric_value = value / 1000 if abs(value) >= 100_000_000_000 else value
            parsed = datetime.fromtimestamp(numeric_value, timezone.utc)
        elif isinstance(value, str):
            timestamp = value.strip()
            if timestamp.endswith("Z"):
                timestamp = f"{timestamp[:-1]}+00:00"
            parsed = datetime.fromisoformat(timestamp)
        else:
            raise ValueError("unsupported timestamp value")

        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timestamp must include a UTC offset")
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError) as exc:
        raise ValueError("invalid timestamp") from exc


def stable_key(*parts: object) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def deduplicate(records: Iterable[UsageRecord]) -> List[UsageRecord]:
    seen = set()
    unique = []
    for record in records:
        if record.dedup_key in seen:
            continue
        seen.add(record.dedup_key)
        unique.append(record)
    return unique


@dataclass(frozen=True)
class CumulativeCounter:
    input: int
    output: int
    cache_read: int
    cache_write: int
    reasoning: int

    def to_tokens(self) -> TokenBreakdown:
        return TokenBreakdown(
            input=safe_int(self.input),
            output=safe_int(self.output),
            cache_read=safe_int(self.cache_read),
            cache_write=safe_int(self.cache_write),
            reasoning=safe_int(self.reasoning),
        )

    def delta_from(self, previous: "CumulativeCounter") -> "CumulativeCounter":
        current = tuple(
            safe_int(value)
            for value in (
                self.input,
                self.output,
                self.cache_read,
                self.cache_write,
                self.reasoning,
            )
        )
        before = tuple(
            safe_int(value)
            for value in (
                previous.input,
                previous.output,
                previous.cache_read,
                previous.cache_write,
                previous.reasoning,
            )
        )
        if any(now < old for now, old in zip(current, before)):
            return CumulativeCounter(*current)
        return CumulativeCounter(
            *(now - old for now, old in zip(current, before))
        )
