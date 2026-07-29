import json
import unittest
import unicodedata
from datetime import datetime, timedelta, timezone

from scripts.alltokenmon.aggregate import aggregate
from scripts.alltokenmon.report import render_json, render_markdown
from scripts.alltokenmon.schema import (
    AdapterStatus,
    Diagnostic,
    TokenBreakdown,
    UsageRecord,
)


NOW = datetime(2026, 7, 28, 12, tzinfo=timezone(timedelta(hours=8)))


def _cells(rendered, prefix):
    line = next(line for line in rendered.splitlines() if line.startswith(prefix))
    return [cell.strip() for cell in line.split("|")[1:-1]]


def _display_width(value):
    return sum(
        0
        if unicodedata.combining(character)
        else 2
        if unicodedata.east_asian_width(character) in ("W", "F")
        else 1
        for character in value
    )


def _report(cost=None, runtime="codex", model="gpt-5", tokens=None):
    record = UsageRecord(
        runtime=runtime,
        provider="openai",
        model=model,
        session_id="SECRET_SESSION",
        timestamp=NOW,
        tokens=tokens
        or TokenBreakdown(
            input=80,
            output=20,
            cache_read=20,
            reasoning=11,
        ),
        message_count=2,
        source_kind="fixture",
        source_path="/SECRET_PATH/session.jsonl",
        dedup_key="SECRET_DEDUP_KEY",
        confidence="estimated",
        cost=cost,
        cost_source="provider" if cost is not None else None,
    )
    diagnostic = Diagnostic(
        runtime="codex",
        status=AdapterStatus.PARTIAL,
        code="partial",
        message="/SECRET_PATH diagnostic SECRET_MESSAGE",
        source_count=1,
        record_count=1,
    )
    return aggregate([record], [diagnostic], NOW)


class JsonReportTests(unittest.TestCase):
    def test_json_is_sorted_indented_unicode_and_newline_terminated(self):
        rendered = render_json({"z": "令牌", "a": {"b": 1}})

        self.assertEqual(
            rendered,
            '{\n  "a": {\n    "b": 1\n  },\n  "z": "令牌"\n}\n',
        )
        self.assertEqual(json.loads(rendered)["z"], "令牌")

    def test_json_rejects_non_finite_numbers(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    render_json({"cost": value})

    def test_invalid_aggregate_cost_renders_without_nonstandard_numbers(self):
        rendered = render_json(_report(cost=float("nan")))
        parsed = json.loads(rendered)

        self.assertNotIn("NaN", rendered)
        self.assertNotIn("Infinity", rendered)
        self.assertIsNone(parsed["periods"]["today"]["totals"]["cost"])
        self.assertIn(
            "invalid_cost",
            parsed["periods"]["today"]["data_quality"]["flags"],
        )

    def test_report_contract_is_stable_and_private(self):
        rendered = render_json(_report())
        parsed = json.loads(rendered)

        self.assertEqual(
            set(parsed),
            {
                "schema_version",
                "generated_at",
                "timezone",
                "coverage",
                "periods",
                "diagnostics",
            },
        )
        self.assertEqual(
            list(parsed["periods"]),
            ["all_time", "month", "today", "week"],
        )
        self.assertEqual(parsed["schema_version"], "1")
        self.assertEqual(parsed["generated_at"], NOW.isoformat())
        self.assertEqual(parsed["timezone"], "UTC+08:00")
        self.assertEqual(parsed["periods"]["today"]["totals"]["reasoning"], 11)
        self.assertEqual(parsed["periods"]["today"]["totals"]["total"], 120)
        for sentinel in (
            "SECRET_SESSION",
            "SECRET_PATH",
            "SECRET_DEDUP_KEY",
            "SECRET_MESSAGE",
        ):
            self.assertNotIn(sentinel, rendered)


class MarkdownReportTests(unittest.TestCase):
    def test_markdown_contains_required_sections_and_unavailable_cost(self):
        rendered = render_markdown(_report())

        self.assertIn("# All Token Monitor 完整报告", rendered)
        self.assertIn("**状态：** partial", rendered)
        self.assertIn("## Token 用量周期表", rendered)
        for prefix, label in (
            ("| 今日", "今日"),
            ("| 近 7 日", "近 7 日"),
            ("| 本月至今", "本月至今"),
            ("| 全部历史", "全部历史"),
        ):
            self.assertEqual(_cells(rendered, prefix)[0], label)
        self.assertIn("## 运行时分布（全部历史）", rendered)
        self.assertIn("## 主要模型（全部历史，≥1% 占比）", rendered)
        self.assertIn("## 要点点评", rendered)
        self.assertIn("—", rendered)
        self.assertIn("部分覆盖", rendered)
        self.assertTrue(rendered.endswith("\n"))

    def test_markdown_renders_cost_when_provider_reported_and_is_private(self):
        rendered = render_markdown(_report(cost=2.5))

        self.assertIn("$2.50", rendered)
        for sentinel in (
            "SECRET_SESSION",
            "SECRET_PATH",
            "SECRET_DEDUP_KEY",
            "SECRET_MESSAGE",
        ):
            self.assertNotIn(sentinel, rendered)

    def test_markdown_token_tables_use_adaptive_units_with_three_decimals(self):
        rendered = render_markdown(
            _report(
                tokens=TokenBreakdown(
                    input=394_000_000,
                    output=12_500_000,
                    cache_read=999_999,
                    reasoning=80,
                )
            )
        )

        self.assertEqual(
            _cells(rendered, "| 今日"),
            [
                "今日",
                "3.940 亿",
                "12.500 百万",
                "80.000 Token",
                "999.999 K",
                "0.000 Token",
                "**4.075 亿**",
                "—",
            ],
        )
        self.assertEqual(
            _cells(rendered, "| codex"),
            ["codex", "2", "4.075 亿", "100.00%"],
        )

    def test_markdown_small_usage_uses_token_unit_instead_of_zero(self):
        rendered = render_markdown(_report())

        self.assertEqual(
            _cells(rendered, "| 今日")[:4],
            ["今日", "80.000 Token", "20.000 Token", "11.000 Token"],
        )

    def test_markdown_lists_model_runtime_usage_and_fact_based_commentary(self):
        rendered = render_markdown(_report())

        self.assertEqual(
            _cells(rendered, "| gpt-5"),
            ["gpt-5", "120.000 Token", "100.00%", "codex"],
        )
        self.assertIn("**codex 占比最高**", rendered)
        self.assertIn("**gpt-5 是用量最高的模型**", rendered)
        self.assertIn("缓存占输入侧比例", rendered)
        self.assertIn("输出/输入比", rendered)

    def test_markdown_tables_align_by_terminal_display_width(self):
        rendered = render_markdown(
            _report(
                runtime="中文-runtime",
                model="模型-model",
                tokens=TokenBreakdown(
                    input=394_000_000,
                    output=12_500_000,
                    cache_read=999_999,
                ),
            )
        )
        blocks = []
        current = []
        for line in rendered.splitlines():
            if line.startswith("|"):
                current.append(line)
            elif current:
                blocks.append(current)
                current = []
        if current:
            blocks.append(current)

        self.assertEqual(len(blocks), 3)
        for block in blocks:
            column_widths = [
                [_display_width(cell) for cell in line.split("|")[1:-1]]
                for line in block
            ]
            for column in zip(*column_widths):
                self.assertEqual(len(set(column)), 1, block)

    def test_markdown_consumes_only_report_mapping(self):
        report = _report()
        copied = json.loads(render_json(report))

        self.assertEqual(render_markdown(report), render_markdown(copied))

    def test_markdown_sanitizes_runtime_and_model_table_cells(self):
        rendered = render_markdown(
            _report(
                runtime="codex|x\n## Injected",
                model="gpt\\5|\r\n## Model Injected\x00",
            )
        )

        self.assertIn(r"codex\|x ## Injected", rendered)
        self.assertIn(r"gpt\\5\| ## Model Injected", rendered)
        self.assertNotIn("\n## Injected", rendered)
        self.assertNotIn("\n## Model Injected", rendered)
        self.assertNotIn("\x00", rendered)


if __name__ == "__main__":
    unittest.main()
