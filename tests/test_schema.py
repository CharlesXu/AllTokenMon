import unittest
from datetime import datetime, timezone

from scripts.alltokenmon.schema import (
    AdapterResult,
    AdapterStatus,
    Diagnostic,
    TokenBreakdown,
    UsageRecord,
)


class SchemaTests(unittest.TestCase):
    def _record(self, **overrides):
        values = {
            "runtime": "codex",
            "provider": "openai",
            "model": "gpt-5",
            "session_id": "s1",
            "timestamp": datetime(2026, 7, 28, tzinfo=timezone.utc),
            "tokens": TokenBreakdown(input=10),
            "message_count": 1,
            "source_kind": "jsonl",
            "source_path": "/redacted/session.jsonl",
            "dedup_key": "codex:event-1",
            "confidence": "exact",
        }
        return UsageRecord(**{**values, **overrides})

    def test_token_breakdown_rejects_every_negative_category(self):
        for field in ("input", "output", "cache_read", "cache_write", "reasoning"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    TokenBreakdown(**{field: -1})

    def test_token_breakdown_rejects_float_and_bool_categories(self):
        for field in ("input", "output", "cache_read", "cache_write", "reasoning"):
            for value in (1.0, True):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        TokenBreakdown(**{field: value})

    def test_total_excludes_reasoning_to_prevent_double_counting(self):
        tokens = TokenBreakdown(input=10, output=5, cache_read=3, cache_write=2, reasoning=4)
        self.assertEqual(tokens.total, 20)

    def test_record_is_immutable_and_requires_aware_timestamp(self):
        record = UsageRecord(
            runtime="codex",
            provider="openai",
            model="gpt-5",
            session_id="s1",
            timestamp=datetime(2026, 7, 28, tzinfo=timezone.utc),
            tokens=TokenBreakdown(input=10),
            message_count=1,
            source_kind="jsonl",
            source_path="/redacted/session.jsonl",
            dedup_key="codex:event-1",
            confidence="exact",
        )
        with self.assertRaises(Exception):
            record.runtime = "claude"

    def test_record_rejects_naive_timestamp(self):
        with self.assertRaises(ValueError):
            self._record(timestamp=datetime(2026, 7, 28))

    def test_record_rejects_empty_required_identity(self):
        for field in ("runtime", "provider", "model", "session_id", "dedup_key"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    self._record(**{field: ""})

    def test_record_rejects_negative_and_non_int_message_count(self):
        for value in (-1, 1.0, True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self._record(message_count=value)

    def test_record_rejects_non_token_breakdown(self):
        with self.assertRaises(ValueError):
            self._record(tokens={"input": 10})

    def test_diagnostic_validates_required_fields_status_and_counts(self):
        valid = {
            "runtime": "codex",
            "status": AdapterStatus.OK,
            "code": "parsed",
            "message": "Parsed records",
        }
        for field in ("runtime", "code", "message"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    Diagnostic(**{**valid, field: ""})
        with self.assertRaises(ValueError):
            Diagnostic(**{**valid, "status": "ok"})
        for field in ("source_count", "record_count"):
            for value in (-1, 1.0, True):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        Diagnostic(**{**valid, field: value})

    def test_adapter_result_copies_input_iterables_to_tuples(self):
        records = [self._record()]
        diagnostics = [
            Diagnostic(
                runtime="codex",
                status=AdapterStatus.OK,
                code="parsed",
                message="Parsed records",
            )
        ]
        result = AdapterResult(
            runtime="codex",
            status=AdapterStatus.OK,
            records=records,
            diagnostics=diagnostics,
        )

        records.append(self._record(dedup_key="codex:event-2"))
        diagnostics.append(
            Diagnostic(
                runtime="codex",
                status=AdapterStatus.PARTIAL,
                code="partial",
                message="Partial records",
            )
        )

        self.assertIsInstance(result.records, tuple)
        self.assertIsInstance(result.diagnostics, tuple)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(len(result.diagnostics), 1)

    def test_adapter_result_validates_runtime_status_and_item_types(self):
        valid = {
            "runtime": "codex",
            "status": AdapterStatus.OK,
        }
        with self.assertRaises(ValueError):
            AdapterResult(**{**valid, "runtime": ""})
        with self.assertRaises(ValueError):
            AdapterResult(**{**valid, "status": "ok"})
        with self.assertRaises(ValueError):
            AdapterResult(**{**valid, "records": [object()]})
        with self.assertRaises(ValueError):
            AdapterResult(**{**valid, "diagnostics": [object()]})

    def test_adapter_result_rejects_string_as_record_iterable(self):
        with self.assertRaises(ValueError):
            AdapterResult(
                runtime="codex",
                status=AdapterStatus.OK,
                records="record",
            )

    def test_status_values_are_stable(self):
        self.assertEqual(
            [status.value for status in AdapterStatus],
            ["ok", "no_data", "unsupported_format", "partial", "error"],
        )
