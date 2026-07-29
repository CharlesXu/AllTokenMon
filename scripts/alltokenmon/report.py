import json
from decimal import Decimal
import unicodedata
from typing import Mapping, Optional


PERIODS = (
    ("today", "Today"),
    ("week", "Week"),
    ("month", "Month"),
    ("all_time", "All time"),
)


def render_json(report: Mapping[str, object]) -> str:
    return json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ) + "\n"


def _cost(value: object) -> str:
    if value is None:
        return "Unavailable"
    return f"{float(value):.2f}"


def _ratio(value: Optional[object], *, percentage: bool = False) -> str:
    if value is None:
        return "Unavailable"
    numeric = float(value)
    return f"{numeric:.2%}" if percentage else f"{numeric:.2f}"


def _yi_tokens(value: object) -> str:
    units = Decimal(int(value)) / Decimal(100_000_000)
    rendered = format(units, ".8f").rstrip("0").rstrip(".")
    return rendered or "0"


def _quality_note(flag: str) -> str:
    notes = {
        "non_exact_records": "Non-exact usage records are included.",
        "invalid_cost": "Invalid provider cost values were ignored.",
        "cost_overflow": "Provider cost totals overflowed and are unavailable.",
        "partial_coverage": "Partial coverage may affect comparisons.",
        "coverage_unknown": "Coverage is unknown because no diagnostics were supplied.",
        "no_usage": "No usage records were found for this period.",
    }
    return notes.get(flag, flag.replace("_", " ").capitalize() + ".")


def _table_cell(value: object) -> str:
    cleaned = []
    pending_control = False
    for character in str(value):
        category = unicodedata.category(character)
        if category.startswith("C") or category in ("Zl", "Zp"):
            pending_control = True
            continue
        if pending_control:
            cleaned.append(" ")
            pending_control = False
        cleaned.append(character)
    if pending_control:
        cleaned.append(" ")
    return "".join(cleaned).replace("\\", "\\\\").replace("|", "\\|")


def render_markdown(report: Mapping[str, object]) -> str:
    coverage = report["coverage"]
    periods = report["periods"]
    assert isinstance(coverage, Mapping)
    assert isinstance(periods, Mapping)

    lines = [
        "# Token Usage Report",
        "",
        f"Generated: {report['generated_at']} ({report['timezone']})",
        "",
        "## Coverage",
        "",
        f"- Status: {str(coverage['status']).replace('_', ' ').title()}",
        f"- Runtimes: {coverage['runtime_count']}",
        f"- Sources: {coverage['source_count']}",
        f"- Adapter records: {coverage['record_count']}",
        "",
        "## Period Summary",
        "",
        "| Period | Input (亿 tokens) | Output (亿 tokens) | Cache read (亿 tokens) | Cache write (亿 tokens) | Reasoning (亿 tokens) | Total (亿 tokens) | Messages | Cost |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for key, label in PERIODS:
        period = periods[key]
        assert isinstance(period, Mapping)
        totals = period["totals"]
        assert isinstance(totals, Mapping)
        lines.append(
            f"| {label} | {_yi_tokens(totals['input'])} | "
            f"{_yi_tokens(totals['output'])} | "
            f"{_yi_tokens(totals['cache_read'])} | "
            f"{_yi_tokens(totals['cache_write'])} | "
            f"{_yi_tokens(totals['reasoning'])} | "
            f"{_yi_tokens(totals['total'])} | "
            f"{totals['message_count']} | {_cost(totals['cost'])} |"
        )

    lines.extend(
        [
            "",
            "## Top Runtimes",
            "",
            "| Period | Rank | Runtime | Total (亿 tokens) | Share | Cost |",
            "| --- | ---: | --- | ---: | ---: | ---: |",
        ]
    )
    for key, label in PERIODS:
        period = periods[key]
        assert isinstance(period, Mapping)
        rows = period["runtimes"]
        assert isinstance(rows, list)
        if not rows:
            lines.append(f"| {label} | - | No usage | 0 | 0.00% | Unavailable |")
            continue
        for rank, row in enumerate(rows, start=1):
            assert isinstance(row, Mapping)
            lines.append(
                f"| {label} | {rank} | {_table_cell(row['runtime'])} | "
                f"{_yi_tokens(row['total'])} | "
                f"{_ratio(row['share'], percentage=True)} | {_cost(row['cost'])} |"
            )

    lines.extend(
        [
            "",
            "## Top Models",
            "",
            "| Period | Rank | Model | Total (亿 tokens) | Share | Cost |",
            "| --- | ---: | --- | ---: | ---: | ---: |",
        ]
    )
    for key, label in PERIODS:
        period = periods[key]
        assert isinstance(period, Mapping)
        rows = period["models"]
        assert isinstance(rows, list)
        if not rows:
            lines.append(f"| {label} | - | No usage | 0 | 0.00% | Unavailable |")
            continue
        for rank, row in enumerate(rows, start=1):
            assert isinstance(row, Mapping)
            lines.append(
                f"| {label} | {rank} | {_table_cell(row['model'])} | "
                f"{_yi_tokens(row['total'])} | "
                f"{_ratio(row['share'], percentage=True)} | {_cost(row['cost'])} |"
            )

    lines.extend(
        [
            "",
            "## Cache and Input/Output Structure",
            "",
            "| Period | Cache share of input side | Output/input ratio |",
            "| --- | ---: | ---: |",
        ]
    )
    for key, label in PERIODS:
        period = periods[key]
        assert isinstance(period, Mapping)
        lines.append(
            f"| {label} | "
            f"{_ratio(period['cache_share_input_side'], percentage=True)} | "
            f"{_ratio(period['output_input_ratio'])} |"
        )

    lines.extend(["", "## Data Quality", ""])
    for key, label in PERIODS:
        period = periods[key]
        assert isinstance(period, Mapping)
        quality = period["data_quality"]
        assert isinstance(quality, Mapping)
        flags = quality["flags"]
        assert isinstance(flags, list)
        note = " ".join(_quality_note(str(flag)) for flag in flags)
        lines.append(f"- {label}: {note or 'No data-quality flags.'}")

    return "\n".join(lines) + "\n"
