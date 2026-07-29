import json
import unittest
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

        self.assertIn("# Token Usage Report", rendered)
        self.assertIn("## Coverage", rendered)
        self.assertIn("## Period Summary", rendered)
        self.assertIn("| Today |", rendered)
        self.assertIn("| Week |", rendered)
        self.assertIn("| Month |", rendered)
        self.assertIn("| All time |", rendered)
        self.assertIn("## Top Runtimes", rendered)
        self.assertIn("## Top Models", rendered)
        self.assertIn("## Cache and Input/Output Structure", rendered)
        self.assertIn("## Data Quality", rendered)
        self.assertIn("Unavailable", rendered)
        self.assertIn("Partial coverage", rendered)
        self.assertIn("Non-exact usage records", rendered)
        self.assertTrue(rendered.endswith("\n"))

    def test_markdown_renders_cost_when_provider_reported_and_is_private(self):
        rendered = render_markdown(_report(cost=2.5))

        self.assertIn("2.50", rendered)
        for sentinel in (
            "SECRET_SESSION",
            "SECRET_PATH",
            "SECRET_DEDUP_KEY",
            "SECRET_MESSAGE",
        ):
            self.assertNotIn(sentinel, rendered)

    def test_markdown_token_tables_use_exact_yi_units(self):
        rendered = render_markdown(
            _report(
                tokens=TokenBreakdown(
                    input=394_000_000,
                    output=1_000_000,
                    cache_read=5_000_000,
                )
            )
        )

        self.assertIn("Input (亿 tokens)", rendered)
        self.assertIn("Total (亿 tokens)", rendered)
        self.assertIn("| Today | 3.94 | 0.01 | 0.05 | 0 | 0 | 4 |", rendered)
        self.assertIn("| Today | 1 | codex | 4 | 100.00% |", rendered)

    def test_markdown_yi_units_do_not_round_small_nonzero_usage_to_zero(self):
        rendered = render_markdown(_report())

        self.assertIn("| Today | 0.0000008 | 0.0000002 | 0.0000002 |", rendered)

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
