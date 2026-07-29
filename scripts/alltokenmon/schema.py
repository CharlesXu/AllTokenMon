from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Tuple


class AdapterStatus(str, Enum):
    OK = "ok"
    NO_DATA = "no_data"
    UNSUPPORTED_FORMAT = "unsupported_format"
    PARTIAL = "partial"
    ERROR = "error"


@dataclass(frozen=True)
class TokenBreakdown:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    reasoning: int = 0

    def __post_init__(self) -> None:
        token_values = (
            self.input,
            self.output,
            self.cache_read,
            self.cache_write,
            self.reasoning,
        )
        if any(type(value) is not int for value in token_values):
            raise ValueError("token values must be integers")
        if any(value < 0 for value in token_values):
            raise ValueError("token values must not be negative")

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_write


@dataclass(frozen=True)
class UsageRecord:
    runtime: str
    provider: str
    model: str
    session_id: str
    timestamp: datetime
    tokens: TokenBreakdown
    message_count: int
    source_kind: str
    source_path: str
    dedup_key: str
    confidence: str
    cost: Optional[float] = None
    cost_source: Optional[str] = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")

        required_values = (
            self.runtime,
            self.provider,
            self.model,
            self.session_id,
            self.dedup_key,
        )
        if any(not value for value in required_values):
            raise ValueError("runtime, provider, model, session_id, and dedup_key must not be empty")

        if not isinstance(self.tokens, TokenBreakdown):
            raise ValueError("tokens must be a TokenBreakdown")
        token_values = (
            self.tokens.input,
            self.tokens.output,
            self.tokens.cache_read,
            self.tokens.cache_write,
            self.tokens.reasoning,
        )
        if type(self.message_count) is not int:
            raise ValueError("message_count must be an integer")
        if any(value < 0 for value in token_values) or self.message_count < 0:
            raise ValueError("token values and message_count must not be negative")


@dataclass(frozen=True)
class Diagnostic:
    runtime: str
    status: AdapterStatus
    code: str
    message: str
    source_count: int = 0
    record_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.status, AdapterStatus):
            raise ValueError("status must be an AdapterStatus")
        if not self.runtime or not self.code or not self.message:
            raise ValueError("runtime, code, and message must not be empty")

        counts = (self.source_count, self.record_count)
        if any(type(value) is not int for value in counts):
            raise ValueError("source_count and record_count must be integers")
        if any(value < 0 for value in counts):
            raise ValueError("source_count and record_count must not be negative")


@dataclass(frozen=True)
class AdapterResult:
    runtime: str
    status: AdapterStatus
    records: Tuple[UsageRecord, ...] = ()
    diagnostics: Tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", tuple(self.records))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

        if not self.runtime:
            raise ValueError("runtime must not be empty")
        if not isinstance(self.status, AdapterStatus):
            raise ValueError("status must be an AdapterStatus")
        if any(not isinstance(record, UsageRecord) for record in self.records):
            raise ValueError("records must contain only UsageRecord values")
        if any(not isinstance(diagnostic, Diagnostic) for diagnostic in self.diagnostics):
            raise ValueError("diagnostics must contain only Diagnostic values")
