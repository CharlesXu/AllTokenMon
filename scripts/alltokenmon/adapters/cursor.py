"""Bounded parser for existing Tokscale Cursor CSV caches."""

import csv
from datetime import datetime, time, timezone
from io import StringIO
import math
from pathlib import Path
from typing import Optional, Sequence, Tuple

from ..normalize import MAX_TOKEN_VALUE, stable_key
from ..schema import TokenBreakdown, UsageRecord
from .amp import _provider, _record, _result, _scan, _text
from .base import DiscoveryContext, SourceSpec
from .jsonio import MAX_JSON_BYTES


_RUNTIME = "cursor"
_MAX_ROWS = 100_000


def _date(value: str) -> Optional[datetime]:
    text = value.strip()
    if not text:
        return None
    if len(text) == 10:
        try:
            return datetime.combine(
                datetime.strptime(text, "%Y-%m-%d").date(),
                time(12, tzinfo=timezone.utc),
            )
        except ValueError:
            return None
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _cost(value: object) -> Optional[float]:
    if not isinstance(value, str):
        return None
    cleaned = value.replace("$", "").replace(",", "").strip()
    if not cleaned or not any(character.isdigit() for character in cleaned):
        return None
    try:
        result = float(cleaned)
    except (ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _integer(value: str) -> int:
    try:
        result = int(value.strip())
    except (TypeError, ValueError, OverflowError):
        return 0
    return min(max(result, 0), MAX_TOKEN_VALUE)


def _account(path: Path) -> str:
    name = path.name
    if name == "usage.csv":
        return "active"
    if name.startswith("usage.") and name.endswith(".csv"):
        raw = name[len("usage."):-len(".csv")]
        cleaned = "".join(
            character
            if character.isascii()
            and (character.isalnum() or character in "-_.")
            else "-"
            for character in raw
        )
        return cleaned or "unknown"
    return "unknown"


def _path(path: Path) -> Tuple[Tuple[UsageRecord, ...], bool, bool, bool]:
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            return (), False, False, False
        body = path.read_bytes()
    except OSError:
        return (), False, False, True
    if len(body) > MAX_JSON_BYTES:
        return (), False, False, False
    try:
        text = body.decode("utf-8-sig")
        rows = csv.reader(StringIO(text))
        header = next(rows)
    except (UnicodeError, StopIteration, csv.Error):
        return (), False, False, False
    names = tuple(value.strip() for value in header)
    if "Date" not in names or "Model" not in names:
        return (), False, False, False
    has_kind = "Kind" in names
    if has_kind and len(names) >= 11:
        indices = (4, 6, 7, 8, 9, 11)
    elif has_kind:
        indices = (2, 4, 5, 6, 7, 9)
    else:
        indices = (1, 2, 3, 4, 5, 7)
    model_i, with_cache_i, input_i, read_i, output_i, cost_i = indices
    records = []
    partial = False
    try:
        for index, fields in enumerate(rows):
            if index >= _MAX_ROWS:
                partial = True
                break
            if not fields or all(not field.strip() for field in fields):
                continue
            if len(fields) <= cost_i:
                partial = True
                continue
            model = _text(fields[model_i])
            timestamp = _date(fields[0])
            if model is None or timestamp is None:
                partial = True
                continue
            input_tokens = _integer(fields[input_i])
            with_cache = _integer(fields[with_cache_i])
            tokens = TokenBreakdown(
                input_tokens,
                _integer(fields[output_i]),
                _integer(fields[read_i]),
                max(0, with_cache - input_tokens),
            )
            cost = _cost(fields[cost_i])
            account = _account(path)
            records.append(
                _record(
                    _RUNTIME,
                    path,
                    _provider(model, "cursor"),
                    model,
                    "cursor-{}-{}".format(account, fields[0].strip()),
                    timestamp,
                    tokens,
                    stable_key(
                        "cursor",
                        account,
                        index,
                        fields[0],
                        model,
                    ),
                    source_kind="csv-cache",
                    cost=cost,
                )
            )
    except csv.Error:
        partial = True
    return tuple(records), True, partial, False


def parse_cursor(paths: Sequence[Path]):
    return _result(_RUNTIME, paths, _path)


def scan(context: DiscoveryContext, specs: Sequence[SourceSpec]):
    return _scan(context, specs, parse_cursor)
