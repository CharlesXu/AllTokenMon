import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from scripts.alltokenmon.aggregate import aggregate, period_windows
from scripts.alltokenmon.normalize import MAX_TOKEN_VALUE
from scripts.alltokenmon.schema import (
    AdapterStatus,
    Diagnostic,
    TokenBreakdown,
    UsageRecord,
)


FIXED_NOW = datetime(
    2026,
    7,
    28,
    12,
    tzinfo=timezone(timedelta(hours=8)),
)


def _zone_or_skip(test_case, key):
    try:
        return ZoneInfo(key)
    except ZoneInfoNotFoundError:
        test_case.skipTest(f"{key} timezone data is unavailable")


def _record(
    timestamp,
    *,
    runtime="codex",
    model="gpt-5",
    input_tokens=1,
    output_tokens=0,
    cache_read=0,
    cache_write=0,
    reasoning=0,
    message_count=1,
    cost=None,
    confidence="exact",
    identity="record",
):
    return UsageRecord(
        runtime=runtime,
        provider="provider",
        model=model,
        session_id=f"session-{identity}",
        timestamp=timestamp,
        tokens=TokenBreakdown(
            input=input_tokens,
            output=output_tokens,
            cache_read=cache_read,
            cache_write=cache_write,
            reasoning=reasoning,
        ),
        message_count=message_count,
        source_kind="fixture",
        source_path=f"/private/SECRET_PATH/{identity}",
        dedup_key=f"SECRET_DEDUP:{identity}",
        confidence=confidence,
        cost=cost,
        cost_source="provider" if cost is not None else None,
    )


def _diagnostic(
    runtime="codex",
    status=AdapterStatus.OK,
    *,
    code="parsed",
    sources=1,
    records=1,
):
    return Diagnostic(
        runtime=runtime,
        status=status,
        code=code,
        message="SECRET_DIAGNOSTIC_PATH /private/source",
        source_count=sources,
        record_count=records,
    )


class PeriodWindowTests(unittest.TestCase):
    def test_fixed_clock_windows_use_local_calendar_boundaries(self):
        windows = {window.name: window for window in period_windows(FIXED_NOW)}

        self.assertEqual(
            windows["today"].start,
            datetime(2026, 7, 28, tzinfo=FIXED_NOW.tzinfo),
        )
        self.assertEqual(
            windows["week"].start,
            datetime(2026, 7, 22, tzinfo=FIXED_NOW.tzinfo),
        )
        self.assertEqual(
            windows["month"].start,
            datetime(2026, 7, 1, tzinfo=FIXED_NOW.tzinfo),
        )
        self.assertIsNone(windows["all_time"].start)
        self.assertTrue(all(window.end == FIXED_NOW for window in windows.values()))

    def test_windows_follow_dst_month_year_and_non_hour_offset_boundaries(self):
        zone = _zone_or_skip(self, "America/New_York")
        new_york_now = datetime(2026, 3, 10, 12, tzinfo=zone)
        windows = {window.name: window for window in period_windows(new_york_now)}
        self.assertEqual(windows["week"].start.isoformat(), "2026-03-04T00:00:00-05:00")
        self.assertEqual(windows["today"].start.isoformat(), "2026-03-10T00:00:00-04:00")

        offset_now = datetime(
            2027,
            1,
            2,
            8,
            tzinfo=timezone(timedelta(hours=5, minutes=30)),
        )
        offset_windows = {
            window.name: window for window in period_windows(offset_now)
        }
        self.assertEqual(
            offset_windows["week"].start.isoformat(),
            "2026-12-27T00:00:00+05:30",
        )
        self.assertEqual(
            offset_windows["month"].start.isoformat(),
            "2027-01-01T00:00:00+05:30",
        )

    def test_naive_now_is_rejected(self):
        with self.assertRaises(ValueError):
            period_windows(datetime(2026, 7, 28, 12))


class AggregateTests(unittest.TestCase):
    def test_fallback_earlier_edt_record_is_included_before_est_now(self):
        zone = _zone_or_skip(self, "America/New_York")
        now = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
        earlier_record = _record(
            datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=0),
            input_tokens=7,
            identity="earlier-edt",
        )

        period = aggregate([earlier_record], [], now)["periods"]["today"]

        self.assertEqual(period["totals"]["input"], 7)

    def test_fallback_later_est_record_is_excluded_after_edt_now(self):
        zone = _zone_or_skip(self, "America/New_York")
        now = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
        later_record = _record(
            datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=1),
            input_tokens=7,
            identity="later-est",
        )

        period = aggregate([later_record], [], now)["periods"]["today"]

        self.assertEqual(period["totals"]["input"], 0)

    def test_period_membership_converts_offsets_and_excludes_future_records(self):
        records = [
            _record(FIXED_NOW, input_tokens=1, identity="today"),
            _record(FIXED_NOW - timedelta(days=6), input_tokens=2, identity="six"),
            _record(FIXED_NOW - timedelta(days=7), input_tokens=4, identity="seven"),
            _record(
                datetime(2026, 7, 1, tzinfo=FIXED_NOW.tzinfo),
                input_tokens=8,
                identity="month-start",
            ),
            _record(
                datetime(2026, 6, 30, tzinfo=FIXED_NOW.tzinfo),
                input_tokens=16,
                identity="prior-month",
            ),
            _record(
                datetime(2026, 7, 28, 3, tzinfo=timezone.utc),
                input_tokens=32,
                identity="same-day-other-offset",
            ),
            _record(
                FIXED_NOW + timedelta(microseconds=1),
                input_tokens=64,
                identity="future",
            ),
        ]

        report = aggregate(records, [_diagnostic(records=6)], FIXED_NOW)

        self.assertEqual(report["periods"]["today"]["totals"]["input"], 33)
        self.assertEqual(report["periods"]["week"]["totals"]["input"], 35)
        self.assertEqual(report["periods"]["month"]["totals"]["input"], 47)
        self.assertEqual(report["periods"]["all_time"]["totals"]["input"], 63)
        self.assertEqual(report["periods"]["all_time"]["data_quality"]["record_count"], 6)

    def test_rollups_rank_stably_and_keep_models_and_runtime_models_distinct(self):
        records = [
            _record(FIXED_NOW, runtime="beta", model="shared", input_tokens=10, identity="1"),
            _record(FIXED_NOW, runtime="Alpha", model="shared", input_tokens=10, identity="2"),
            _record(FIXED_NOW, runtime="Alpha", model="zeta", input_tokens=5, identity="3"),
            _record(FIXED_NOW, runtime="beta", model="solo", input_tokens=5, identity="4"),
        ]

        period = aggregate(records, [_diagnostic(records=4)], FIXED_NOW)["periods"]["today"]

        self.assertEqual(
            [row["runtime"] for row in period["runtimes"]],
            ["Alpha", "beta"],
        )
        self.assertEqual(
            [(row["model"], row["total"]) for row in period["models"]],
            [("shared", 20), ("solo", 5), ("zeta", 5)],
        )
        self.assertEqual(
            [(row["runtime"], row["model"]) for row in period["runtime_models"]],
            [
                ("Alpha", "shared"),
                ("beta", "shared"),
                ("Alpha", "zeta"),
                ("beta", "solo"),
            ],
        )
        self.assertEqual(period["runtimes"][0]["share"], 0.5)
        self.assertEqual(period["models"][0]["share"], 2 / 3)
        self.assertEqual(
            aggregate(records, [_diagnostic(records=4)], FIXED_NOW),
            aggregate(reversed(records), [_diagnostic(records=4)], FIXED_NOW),
        )

    def test_bounded_addition_preserves_integer_precision_and_excludes_reasoning(self):
        records = [
            _record(
                FIXED_NOW,
                input_tokens=MAX_TOKEN_VALUE,
                output_tokens=1,
                reasoning=17,
                message_count=MAX_TOKEN_VALUE,
                identity="max",
            ),
            _record(
                FIXED_NOW,
                input_tokens=10,
                output_tokens=2,
                reasoning=5,
                message_count=10,
                identity="overflow",
            ),
        ]

        totals = aggregate(records, [], FIXED_NOW)["periods"]["today"]["totals"]

        self.assertEqual(totals["input"], MAX_TOKEN_VALUE)
        self.assertEqual(totals["output"], 3)
        self.assertEqual(totals["reasoning"], 22)
        self.assertEqual(totals["message_count"], MAX_TOKEN_VALUE)
        self.assertEqual(totals["total"], MAX_TOKEN_VALUE)

    def test_invalid_costs_are_ignored_flagged_and_do_not_affect_tokens(self):
        invalid_costs = (
            float("nan"),
            float("inf"),
            float("-inf"),
            -1.0,
            "1.25",
            True,
        )
        for index, invalid_cost in enumerate(invalid_costs):
            with self.subTest(cost=invalid_cost):
                report = aggregate(
                    [
                        _record(
                            FIXED_NOW,
                            input_tokens=3,
                            cost=invalid_cost,
                            identity=f"invalid-cost-{index}",
                        )
                    ],
                    [],
                    FIXED_NOW,
                )
                period = report["periods"]["today"]

                self.assertEqual(period["totals"]["input"], 3)
                self.assertIsNone(period["totals"]["cost"])
                self.assertIsNone(period["runtimes"][0]["cost"])
                self.assertIn("invalid_cost", period["data_quality"]["flags"])

    def test_invalid_cost_does_not_suppress_valid_provider_cost(self):
        period = aggregate(
            [
                _record(FIXED_NOW, input_tokens=2, cost=2.5, identity="valid-cost"),
                _record(FIXED_NOW, input_tokens=3, cost="bad", identity="invalid-cost"),
            ],
            [],
            FIXED_NOW,
        )["periods"]["today"]

        self.assertEqual(period["totals"]["input"], 5)
        self.assertEqual(period["totals"]["cost"], 2.5)
        self.assertEqual(period["runtimes"][0]["cost"], 2.5)
        self.assertIn("invalid_cost", period["data_quality"]["flags"])

    def test_provider_cost_sum_is_stable_across_supported_python_versions(self):
        period = aggregate(
            [
                _record(FIXED_NOW, cost=0.1, identity="cost-a"),
                _record(FIXED_NOW, cost=0.05, identity="cost-b"),
            ],
            [],
            FIXED_NOW,
        )["periods"]["today"]

        self.assertEqual(period["totals"]["cost"], 0.15)

    def test_cost_sum_overflow_is_unavailable_flagged_and_tokens_remain(self):
        period = aggregate(
            [
                _record(FIXED_NOW, input_tokens=2, cost=1e308, identity="large-1"),
                _record(FIXED_NOW, input_tokens=3, cost=1e308, identity="large-2"),
            ],
            [],
            FIXED_NOW,
        )["periods"]["today"]

        self.assertEqual(period["totals"]["input"], 5)
        self.assertIsNone(period["totals"]["cost"])
        self.assertIsNone(period["runtimes"][0]["cost"])
        self.assertIn("cost_overflow", period["data_quality"]["flags"])

    def test_cost_ratios_coverage_diagnostics_and_quality_are_privacy_safe(self):
        records = [
            _record(
                FIXED_NOW,
                input_tokens=60,
                output_tokens=20,
                cache_read=20,
                cache_write=0,
                cost=1.25,
                identity="paid",
            ),
            _record(
                FIXED_NOW,
                input_tokens=0,
                output_tokens=10,
                cost=None,
                confidence="estimated",
                identity="unknown-cost",
            ),
        ]
        diagnostics = [
            _diagnostic("codex", AdapterStatus.OK, sources=2, records=2),
            _diagnostic(
                "claude",
                AdapterStatus.PARTIAL,
                code="truncated",
                sources=1,
                records=0,
            ),
        ]

        report = aggregate(records, diagnostics, FIXED_NOW)
        period = report["periods"]["today"]

        self.assertEqual(period["totals"]["cost"], 1.25)
        self.assertEqual(period["cache_share_input_side"], 0.25)
        self.assertEqual(period["output_input_ratio"], 0.5)
        self.assertEqual(report["coverage"]["status"], "partial")
        self.assertEqual(report["coverage"]["source_count"], 3)
        self.assertEqual(report["coverage"]["record_count"], 2)
        self.assertEqual(report["coverage"]["status_counts"], {"ok": 1, "partial": 1})
        self.assertEqual(
            set(report["diagnostics"][0]),
            {"runtime", "status", "code", "source_count", "record_count"},
        )
        self.assertEqual(
            period["data_quality"]["flags"],
            ["non_exact_records", "partial_coverage"],
        )
        self.assertIsNone(
            aggregate(
                [
                    _record(
                        FIXED_NOW,
                        input_tokens=0,
                        output_tokens=1,
                        cost=None,
                        identity="free",
                    )
                ],
                [],
                FIXED_NOW,
            )["periods"]["today"]["totals"]["cost"]
        )
        zero_input_period = aggregate(
            [
                _record(
                    FIXED_NOW,
                    input_tokens=0,
                    output_tokens=1,
                    cost=None,
                    identity="zero-input",
                )
            ],
            [],
            FIXED_NOW,
        )["periods"]["today"]
        self.assertIsNone(zero_input_period["cache_share_input_side"])
        self.assertIsNone(zero_input_period["output_input_ratio"])


if __name__ == "__main__":
    unittest.main()
