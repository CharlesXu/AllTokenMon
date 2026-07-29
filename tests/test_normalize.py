import hashlib
import unittest
from datetime import datetime, timedelta, timezone

from scripts.alltokenmon.normalize import (
    MAX_TOKEN_VALUE,
    CumulativeCounter,
    deduplicate,
    parse_timestamp,
    safe_int,
    stable_key,
)
from scripts.alltokenmon.schema import TokenBreakdown, UsageRecord


def _record(key, session):
    return UsageRecord(
        runtime="codex",
        provider="openai",
        model="gpt-5",
        session_id=session,
        timestamp=parse_timestamp("2026-07-28T10:00:00Z"),
        tokens=TokenBreakdown(input=10),
        message_count=1,
        source_kind="jsonl",
        source_path="/redacted",
        dedup_key=key,
        confidence="exact",
    )


class NormalizeTests(unittest.TestCase):
    def test_safe_int_clamps_negative_and_huge_values(self):
        self.assertEqual(safe_int(-1), 0)
        self.assertEqual(safe_int("-12.5"), 0)
        self.assertEqual(safe_int(str(MAX_TOKEN_VALUE + 1)), MAX_TOKEN_VALUE)
        self.assertEqual(safe_int("1e100"), MAX_TOKEN_VALUE)

    def test_safe_int_preserves_large_integer_precision(self):
        beyond_float_precision = 9_007_199_254_740_993
        self.assertEqual(safe_int(beyond_float_precision), beyond_float_precision)
        self.assertEqual(safe_int(str(beyond_float_precision)), beyond_float_precision)
        self.assertEqual(safe_int(str(MAX_TOKEN_VALUE - 1)), MAX_TOKEN_VALUE - 1)

    def test_safe_int_normalizes_external_values_without_leaking_conversion_errors(self):
        self.assertEqual(safe_int(True), 0)
        self.assertEqual(safe_int(" 12.9 "), 12)
        for value in (None, "", "invalid", "nan", "inf", "-inf"):
            with self.subTest(value=value):
                self.assertEqual(safe_int(value), 0)

    def test_parse_timestamp_accepts_seconds_milliseconds_and_iso_z(self):
        expected = datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
        self.assertEqual(parse_timestamp(1_700_000_000), expected)
        self.assertEqual(parse_timestamp(1_700_000_000_000), expected)
        self.assertEqual(parse_timestamp("2023-11-14T22:13:20Z"), expected)

    def test_parse_timestamp_converts_aware_values_to_utc(self):
        source = datetime(
            2023,
            11,
            15,
            6,
            13,
            20,
            tzinfo=timezone(timedelta(hours=8)),
        )
        self.assertEqual(
            parse_timestamp(source),
            datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc),
        )

    def test_parse_timestamp_rejects_naive_and_invalid_values_consistently(self):
        invalid_values = (
            datetime(2023, 11, 14, 22, 13, 20),
            "2023-11-14T22:13:20",
            "not-a-timestamp",
            float("inf"),
            10**30,
            True,
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_timestamp(value)

    def test_stable_key_hashes_nul_separated_string_parts(self):
        expected = hashlib.sha256(b"codex\0session-1\0message-2\0").hexdigest()
        self.assertEqual(
            stable_key("codex", "session-1", "message-2"),
            f"sha256:{expected}",
        )

    def test_deduplicate_uses_only_explicit_key_and_preserves_first_seen_order(self):
        first = _record("event-1", "s1")
        duplicate = _record("event-1", "s1")
        equal_usage_different_key = _record("event-2", "s1")

        self.assertEqual(
            deduplicate([first, duplicate, equal_usage_different_key]),
            [first, equal_usage_different_key],
        )

    def test_cumulative_counter_returns_per_bucket_delta(self):
        previous = CumulativeCounter(
            input=10,
            output=6,
            cache_read=4,
            cache_write=2,
            reasoning=3,
        )
        current = CumulativeCounter(
            input=15,
            output=8,
            cache_read=7,
            cache_write=5,
            reasoning=9,
        )

        self.assertEqual(
            current.delta_from(previous),
            CumulativeCounter(5, 2, 3, 3, 6),
        )

    def test_cumulative_counter_treats_any_decrease_as_full_reset(self):
        previous = CumulativeCounter(100, 50, 10, 0, 0)
        current = CumulativeCounter(90, 80, 20, 0, 0)

        self.assertEqual(current.delta_from(previous), current)

    def test_cumulative_counter_normalizes_numeric_strings_before_delta(self):
        previous = CumulativeCounter("10", "6", "4", "2", "3")
        current = CumulativeCounter("15", "8", "7", "5", "9")

        self.assertEqual(
            current.delta_from(previous),
            CumulativeCounter(5, 2, 3, 3, 6),
        )

    def test_cumulative_counter_string_reset_returns_normalized_current_values(self):
        previous = CumulativeCounter("100", "50", "10", "0", "0")
        current = CumulativeCounter("90", "80", "20", "0", "0")

        self.assertEqual(
            current.delta_from(previous),
            CumulativeCounter(90, 80, 20, 0, 0),
        )

    def test_cumulative_counter_normalizes_all_buckets_before_schema_construction(self):
        counter = CumulativeCounter(
            input="-1",
            output="2.9",
            cache_read=True,
            cache_write="1e100",
            reasoning=None,
        )

        self.assertEqual(
            counter.to_tokens(),
            TokenBreakdown(
                input=0,
                output=2,
                cache_read=0,
                cache_write=MAX_TOKEN_VALUE,
                reasoning=0,
            ),
        )


if __name__ == "__main__":
    unittest.main()
