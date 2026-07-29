"""Bounded, privacy-safe readers for local JSON sources."""

from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping, Optional, Tuple


MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_JSONL_LINE_BYTES = 8 * 1024 * 1024
MAX_JSONL_RECORDS = 100_000
MAX_JSON_NESTING = 256


@dataclass(frozen=True)
class JsonReadResult:
    value: Optional[object]
    partial: bool = False
    error_code: Optional[str] = None


@dataclass(frozen=True)
class JsonLinesResult:
    values: Tuple[Mapping[str, object], ...]
    partial: bool = False
    error_code: Optional[str] = None


def _io_error(error: OSError) -> str:
    return "io_error:" + type(error).__name__


def _exceeds_nesting(text: str) -> bool:
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_NESTING:
                return True
        elif character in "]}":
            depth = max(0, depth - 1)
    return False


def read_json(path: Path) -> JsonReadResult:
    """Read one bounded UTF-8 JSON document without exposing source content."""
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            return JsonReadResult(None, error_code="unsupported_format")
        with path.open("rb") as source:
            body = source.read(MAX_JSON_BYTES + 1)
    except OSError as error:
        return JsonReadResult(None, error_code=_io_error(error))

    if len(body) > MAX_JSON_BYTES:
        return JsonReadResult(None, error_code="unsupported_format")

    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as error:
        return JsonReadResult(
            None,
            error_code="decode_error:" + type(error).__name__,
        )

    if _exceeds_nesting(text):
        return JsonReadResult(None, error_code="malformed_json:nesting_limit")

    try:
        return JsonReadResult(json.loads(text))
    except (RecursionError, ValueError) as error:
        return JsonReadResult(
            None,
            error_code="malformed_json:" + type(error).__name__,
        )


def read_json_lines(path: Path) -> JsonLinesResult:
    """Read bounded UTF-8 JSON object lines, retaining rows before corruption."""
    values = []
    total_bytes = 0
    try:
        with path.open("rb") as source:
            while True:
                remaining_bytes = MAX_JSON_BYTES - total_bytes
                read_limit = min(
                    MAX_JSONL_LINE_BYTES,
                    remaining_bytes,
                ) + 1
                line = source.readline(read_limit)
                if not line:
                    break
                if len(line) > remaining_bytes:
                    return JsonLinesResult(
                        tuple(values),
                        partial=True,
                        error_code="file_too_large",
                    )
                total_bytes += len(line)
                if len(line) > MAX_JSONL_LINE_BYTES:
                    return JsonLinesResult(
                        tuple(values),
                        partial=True,
                        error_code="line_too_long",
                    )

                try:
                    text = line.decode("utf-8")
                except UnicodeDecodeError as error:
                    return JsonLinesResult(
                        tuple(values),
                        partial=True,
                        error_code="decode_error:" + type(error).__name__,
                    )

                if not text.strip():
                    continue

                if _exceeds_nesting(text):
                    return JsonLinesResult(
                        tuple(values),
                        partial=True,
                        error_code="malformed_json:nesting_limit",
                    )

                try:
                    value = json.loads(text)
                except (RecursionError, ValueError) as error:
                    return JsonLinesResult(
                        tuple(values),
                        partial=True,
                        error_code="malformed_json:" + type(error).__name__,
                    )

                if not isinstance(value, MappingABC):
                    return JsonLinesResult(
                        tuple(values),
                        partial=True,
                        error_code="non_object",
                    )
                if len(values) >= MAX_JSONL_RECORDS:
                    return JsonLinesResult(
                        tuple(values),
                        partial=True,
                        error_code="record_limit",
                    )
                values.append(value)
    except OSError as error:
        return JsonLinesResult(
            tuple(values),
            partial=True,
            error_code=_io_error(error),
        )

    return JsonLinesResult(tuple(values))
