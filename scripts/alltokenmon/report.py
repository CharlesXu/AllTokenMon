import json
from datetime import datetime
from decimal import Decimal
import unicodedata
from typing import List, Mapping, Optional


PERIODS = (
    ("today", "今日"),
    ("week", "近 7 日"),
    ("month", "本月至今"),
    ("all_time", "全部历史"),
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
        return "—"
    return f"${float(value):.2f}"


def _ratio(value: Optional[object], *, percentage: bool = False) -> str:
    if value is None:
        return "—"
    numeric = float(value)
    return f"{numeric:.2%}" if percentage else f"{numeric:.2f}"


def _tokens(value: object) -> str:
    numeric = int(value)
    if numeric >= 100_000_000:
        divisor, unit = 100_000_000, "亿"
    elif numeric >= 1_000_000:
        divisor, unit = 1_000_000, "百万"
    elif numeric >= 1_000:
        divisor, unit = 1_000, "K"
    else:
        divisor, unit = 1, "Token"
    return f"{Decimal(numeric) / Decimal(divisor):.3f} {unit}"


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


def _generated_time(report: Mapping[str, object]) -> str:
    value = str(report["generated_at"])
    try:
        rendered = datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        rendered = value
    return f"{rendered} ({report['timezone']})"


def _coverage_summary(
    coverage: Mapping[str, object],
    diagnostics: object,
) -> str:
    counts = coverage["status_counts"]
    assert isinstance(counts, Mapping)
    ok_count = int(counts.get("ok", 0))
    no_data_count = int(counts.get("no_data", 0))
    partial_count = int(counts.get("partial", 0))
    unsupported_count = int(counts.get("unsupported_format", 0))
    error_count = int(counts.get("error", 0))

    parts = [
        f"{coverage['runtime_count']} 个运行时扫描",
        f"{ok_count} 个有数据（ok）",
        f"{no_data_count} 个无数据",
    ]
    if partial_count:
        partial_details = []
        if isinstance(diagnostics, list):
            for diagnostic in diagnostics:
                if (
                    isinstance(diagnostic, Mapping)
                    and diagnostic.get("status") == "partial"
                ):
                    partial_details.append(
                        f"{_table_cell(diagnostic['runtime'])} "
                        f"{int(diagnostic['record_count']):,} 条记录部分可解析"
                    )
        detail = f"（{'；'.join(partial_details)}）" if partial_details else ""
        parts.append(f"{partial_count} 个部分覆盖{detail}")
    if unsupported_count:
        parts.append(f"{unsupported_count} 个格式不支持")
    if error_count:
        parts.append(f"{error_count} 个扫描错误")

    return (
        f"**状态：** {coverage['status']} — {'，'.join(parts)}。"
        f"共 {int(coverage['record_count']):,} 条记录 / "
        f"{int(coverage['source_count']):,} 个数据源。"
    )


def _model_runtimes(period: Mapping[str, object], model: object) -> str:
    runtime_models = period["runtime_models"]
    assert isinstance(runtime_models, list)
    matching = [
        row
        for row in runtime_models
        if isinstance(row, Mapping)
        and row.get("model") == model
        and int(row.get("total", 0)) > 0
    ]
    model_total = sum(int(row["total"]) for row in matching)
    names = [
        _table_cell(row["runtime"])
        for row in matching
        if int(row["total"]) / model_total >= 0.01
    ]
    if not names and matching:
        names.append(_table_cell(matching[0]["runtime"]))
    return " / ".join(names) or "—"


def _commentary(periods: Mapping[str, object]) -> List[str]:
    all_time = periods["all_time"]
    assert isinstance(all_time, Mapping)
    totals = all_time["totals"]
    runtimes = all_time["runtimes"]
    models = all_time["models"]
    assert isinstance(totals, Mapping)
    assert isinstance(runtimes, list)
    assert isinstance(models, list)

    if int(totals["total"]) == 0:
        return ["未发现可用于点评的本地 Token 用量。"]

    observations = []
    if runtimes:
        runtime = runtimes[0]
        assert isinstance(runtime, Mapping)
        observations.append(
            f"**{_table_cell(runtime['runtime'])} 占比最高** — "
            f"全部历史 {_tokens(totals['total'])}中，"
            f"{_table_cell(runtime['runtime'])} 贡献 {_tokens(runtime['total'])}"
            f"（{_ratio(runtime['share'], percentage=True)}）。"
        )
    if models:
        model = models[0]
        assert isinstance(model, Mapping)
        observations.append(
            f"**{_table_cell(model['model'])} 是用量最高的模型** — "
            f"消耗 {_tokens(model['total'])}"
            f"（全部历史的 {_ratio(model['share'], percentage=True)}）。"
        )

    cache_values = []
    for key, label in (("all_time", "全部历史"), ("week", "近 7 日"), ("today", "今日")):
        period = periods[key]
        assert isinstance(period, Mapping)
        value = period["cache_share_input_side"]
        if value is not None:
            cache_values.append(f"{label} {_ratio(value, percentage=True)}")
    if cache_values:
        observations.append(
            f"**缓存占输入侧比例** — {'，'.join(cache_values)}。"
            "这是用量结构描述，不代表浪费或效率判断。"
        )

    output_ratio = all_time["output_input_ratio"]
    if output_ratio is not None:
        observations.append(
            f"**输出/输入比** — 全部历史为 {_ratio(output_ratio, percentage=True)}"
            f"（输出 {_tokens(totals['output'])} vs 输入 {_tokens(totals['input'])}）。"
        )
    return observations


def render_markdown(report: Mapping[str, object]) -> str:
    coverage = report["coverage"]
    periods = report["periods"]
    diagnostics = report.get("diagnostics", [])
    assert isinstance(coverage, Mapping)
    assert isinstance(periods, Mapping)

    lines = [
        "# All Token Monitor 完整报告",
        "",
        _coverage_summary(coverage, diagnostics),
        "",
        f"生成时间：{_generated_time(report)}",
        "",
        "## Token 用量周期表",
        "",
        "| 周期 | 输入 Token | 输出 Token | 推理 Token | 缓存读取 | 缓存写入 | 总量 | 费用 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for key, label in PERIODS:
        period = periods[key]
        assert isinstance(period, Mapping)
        totals = period["totals"]
        assert isinstance(totals, Mapping)
        lines.append(
            f"| {label} | {_tokens(totals['input'])} | "
            f"{_tokens(totals['output'])} | "
            f"{_tokens(totals['reasoning'])} | "
            f"{_tokens(totals['cache_read'])} | "
            f"{_tokens(totals['cache_write'])} | "
            f"**{_tokens(totals['total'])}** | {_cost(totals['cost'])} |"
        )

    lines.extend(
        [
            "",
            "*注：Token 数量按数值自动使用亿、百万、K 或 Token，固定保留三位小数。"
            "总量为输入、输出、缓存读取与缓存写入之和；推理 Token 是细分类，不重复计入。"
            "费用仅展示数据源明确提供的金额，未提供时不估算。"
            "“全部历史”仅指本机仍保留且可解析的记录。*",
            "",
            "## 运行时分布（全部历史）",
            "",
            "| 运行时 | 消息数 | Token 总量 | 占比 |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    all_time = periods["all_time"]
    assert isinstance(all_time, Mapping)
    runtime_rows = all_time["runtimes"]
    assert isinstance(runtime_rows, list)
    if not runtime_rows:
        lines.append("| — | 0 | 0.000 Token | 0.00% |")
    for row in runtime_rows:
        assert isinstance(row, Mapping)
        lines.append(
            f"| {_table_cell(row['runtime'])} | {int(row['message_count']):,} | "
            f"{_tokens(row['total'])} | {_ratio(row['share'], percentage=True)} |"
        )

    lines.extend(
        [
            "",
            "## 主要模型（全部历史，≥1% 占比）",
            "",
            "| 模型 | Token 总量 | 占比 | 主要用于 |",
            "| --- | ---: | ---: | --- |",
        ]
    )
    model_rows = all_time["models"]
    assert isinstance(model_rows, list)
    major_models = [
        row
        for row in model_rows
        if isinstance(row, Mapping) and float(row["share"]) >= 0.01
    ]
    if not major_models:
        lines.append("| — | 0.000 Token | 0.00% | — |")
    for row in major_models:
        assert isinstance(row, Mapping)
        lines.append(
            f"| {_table_cell(row['model'])} | {_tokens(row['total'])} | "
            f"{_ratio(row['share'], percentage=True)} | "
            f"{_model_runtimes(all_time, row['model'])} |"
        )

    lines.extend(["", "## 要点点评", ""])
    for observation in _commentary(periods):
        lines.extend([observation, ""])

    if coverage["status"] == "partial":
        lines.append(
            "*数据质量为 partial；跨运行时和模型占比仅代表当前本地可解析记录。*"
        )

    return "\n".join(lines).rstrip() + "\n"
