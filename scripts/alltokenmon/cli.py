import argparse
from datetime import datetime, tzinfo
import fnmatch
import importlib
import os
from pathlib import Path
import platform
import sys
from typing import Iterable, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .adapters.base import DiscoveryContext
from .adapters.registry import RUNTIME_IDS, SOURCE_SPECS
from .aggregate import aggregate
from .normalize import parse_timestamp
from .report import render_json, render_markdown
from .schema import AdapterResult, AdapterStatus, Diagnostic, UsageRecord


_DIAGNOSTIC_CODES = frozenset({
    "adapter_diagnostic",
    "adapter_unavailable",
    "adapter_valueerror",
    "adapter_oserror",
    "adapter_typeerror",
    "adapter_runtimeerror",
    "adapter_importerror",
    "adapter_modulenotfounderror",
    "adapter_memoryerror",
    "adapter_recursionerror",
    "adapter_attributeerror",
    "adapter_keyerror",
    "adapter_error",
    "ok",
    "no_data",
    "unsupported_format",
    "partial",
    "error",
    "read_error",
    "partial_source",
    "resource_limit",
    "malformed_source",
})
_EXCEPTION_CODES = (
    (ValueError, "adapter_valueerror"),
    (OSError, "adapter_oserror"),
    (TypeError, "adapter_typeerror"),
    (RuntimeError, "adapter_runtimeerror"),
    (ImportError, "adapter_importerror"),
    (ModuleNotFoundError, "adapter_modulenotfounderror"),
    (MemoryError, "adapter_memoryerror"),
    (RecursionError, "adapter_recursionerror"),
    (AttributeError, "adapter_attributeerror"),
    (KeyError, "adapter_keyerror"),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze local token usage")
    parser.add_argument("--version", action="version", version="all-token-monitor 0.1.0")
    parser.add_argument("--runtime", default="")
    parser.add_argument("--model", default="")
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        dest="output_format",
    )
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--home", type=Path)
    parser.add_argument("--now")
    return parser


def _csv_values(
    parser: argparse.ArgumentParser, value: str, label: str
) -> Tuple[str, ...]:
    if not value:
        return ()
    values = tuple(part.strip() for part in value.split(","))
    if any(not part for part in values):
        parser.error(
            "{} must be a comma-separated list of non-empty values".format(label)
        )
    return values


def _selected_runtimes(
    parser: argparse.ArgumentParser, value: str
) -> Tuple[str, ...]:
    requested = _csv_values(parser, value, "--runtime")
    if not requested:
        return RUNTIME_IDS
    unknown = set(requested).difference(RUNTIME_IDS)
    if unknown:
        parser.error("--runtime contains an unsupported runtime")
    requested_set = set(requested)
    return tuple(runtime for runtime in RUNTIME_IDS if runtime in requested_set)


def _explicit_timezone(env: Mapping[str, str]) -> Optional[ZoneInfo]:
    key = env.get("TZ", "").strip()
    if key.startswith(":"):
        key = key[1:]
    if not key:
        return None
    try:
        return ZoneInfo(key)
    except (ValueError, ZoneInfoNotFoundError):
        return None


def _timezone_from_path(path: Path) -> Optional[ZoneInfo]:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    parts = resolved.parts
    for marker in ("zoneinfo", "zoneinfo.default"):
        if marker not in parts:
            continue
        index = parts.index(marker)
        key = "/".join(parts[index + 1:])
        if not key:
            continue
        try:
            return ZoneInfo(key)
        except (ValueError, ZoneInfoNotFoundError):
            continue
    return None


def _local_timezone(env: Mapping[str, str]) -> tzinfo:
    explicit = _explicit_timezone(env)
    if explicit is not None:
        return explicit
    if not env.get("TZ", "").strip():
        for localtime in (
            Path("/etc/localtime"),
            Path("/var/db/timezone/localtime"),
        ):
            discovered = _timezone_from_path(localtime)
            if discovered is not None:
                return discovered
    fallback = datetime.now().astimezone().tzinfo
    assert fallback is not None
    return fallback


def _local_now(
    parser: argparse.ArgumentParser,
    value: Optional[str],
    env: Mapping[str, str],
    local_timezone: tzinfo,
) -> datetime:
    if value is None:
        return datetime.now(local_timezone)
    try:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        parse_timestamp(parsed)
    except (AttributeError, ValueError):
        parser.error("--now must be an ISO-8601 timestamp with a UTC offset")
    explicit_timezone = _explicit_timezone(env)
    if explicit_timezone is not None:
        return parsed.astimezone(explicit_timezone)
    return parsed


def _platform_name() -> str:
    current = platform.system().strip().lower()
    return {
        "darwin": "darwin",
        "linux": "linux",
        "windows": "windows",
    }.get(current, current)


def _unavailable(runtime: str) -> AdapterResult:
    diagnostic = Diagnostic(
        runtime=runtime,
        status=AdapterStatus.NO_DATA,
        code="adapter_unavailable",
        message="Adapter is unavailable",
    )
    return AdapterResult(
        runtime=runtime,
        status=AdapterStatus.NO_DATA,
        diagnostics=(diagnostic,),
    )


def _scan_runtime(runtime: str, context: DiscoveryContext) -> AdapterResult:
    package = __package__ or "alltokenmon"
    module_name = "{}.adapters.{}".format(
        package,
        runtime.replace("-", "_"),
    )
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return _unavailable(runtime)
        raise
    scan = getattr(module, "scan", None)
    if scan is None:
        return _unavailable(runtime)
    result = scan(context, SOURCE_SPECS[runtime])
    if not isinstance(result, AdapterResult):
        raise TypeError("adapter scan must return AdapterResult")
    return result


def _safe_code(code: object) -> str:
    return (
        code
        if type(code) is str and code in _DIAGNOSTIC_CODES
        else "adapter_diagnostic"
    )


def _safe_diagnostic(runtime: str, diagnostic: Diagnostic) -> Diagnostic:
    return Diagnostic(
        runtime=runtime,
        status=diagnostic.status,
        code=_safe_code(diagnostic.code),
        message="Adapter diagnostic",
        source_count=diagnostic.source_count,
        record_count=diagnostic.record_count,
    )


def _exception_diagnostic(runtime: str, exc: Exception) -> Diagnostic:
    error_type = type(exc)
    code = "adapter_error"
    for known_type, known_code in _EXCEPTION_CODES:
        if error_type is known_type:
            code = known_code
            break
    return Diagnostic(
        runtime=runtime,
        status=AdapterStatus.ERROR,
        code=code,
        message="Adapter failed",
    )


def _result_diagnostics(
    runtime: str, result: AdapterResult
) -> Tuple[Diagnostic, ...]:
    if result.diagnostics:
        return tuple(
            _safe_diagnostic(runtime, diagnostic)
            for diagnostic in result.diagnostics
        )
    return (
        Diagnostic(
            runtime=runtime,
            status=result.status,
            code=result.status.value,
            message="Adapter completed",
            record_count=len(result.records),
        ),
    )


def _model_matches(record: UsageRecord, patterns: Tuple[str, ...]) -> bool:
    return not patterns or any(
        fnmatch.fnmatchcase(record.model, pattern) for pattern in patterns
    )


def _write_diagnostics(diagnostics: Iterable[Diagnostic]) -> None:
    for diagnostic in diagnostics:
        sys.stderr.write(
            "{}:{}:{}\n".format(
                diagnostic.runtime,
                diagnostic.status.value,
                diagnostic.code,
            )
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    runtimes = _selected_runtimes(parser, arguments.runtime)
    model_patterns = _csv_values(parser, arguments.model, "--model")
    environment = dict(os.environ)
    local_timezone = _local_timezone(environment)
    now = _local_now(
        parser,
        arguments.now,
        environment,
        local_timezone,
    )
    context = DiscoveryContext(
        os_name=_platform_name(),
        home=arguments.home if arguments.home is not None else Path.home(),
        env=environment,
    )

    records = []
    diagnostics = []
    statuses = []
    for runtime in runtimes:
        try:
            result = _scan_runtime(runtime, context)
        except Exception as exc:
            statuses.append(AdapterStatus.ERROR)
            diagnostics.append(_exception_diagnostic(runtime, exc))
            continue
        statuses.append(result.status)
        records.extend(
            record
            for record in result.records
            if _model_matches(record, model_patterns)
        )
        diagnostics.extend(_result_diagnostics(runtime, result))

    if statuses and all(status is AdapterStatus.ERROR for status in statuses):
        if arguments.diagnostics:
            _write_diagnostics(diagnostics)
        sys.stderr.write("all-token-monitor:error:no_scan\n")
        return 1

    report = aggregate(records, diagnostics, now)
    if arguments.output_format == "json":
        rendered = render_json(report)
    else:
        rendered = render_markdown(report)
    sys.stdout.write(rendered)
    if arguments.diagnostics:
        _write_diagnostics(diagnostics)
    return 0
