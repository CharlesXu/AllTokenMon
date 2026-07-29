import unittest
import tempfile
from pathlib import Path

from scripts.alltokenmon.adapters.opencodereview import parse_opencodereview
from scripts.alltokenmon.schema import TokenBreakdown
from tests.adapters.file_contract import assert_status_isolation, parse_fixture


class OpenCodeReviewAdapterTests(unittest.TestCase):
    def test_contract_dedup_and_start_anchor(self):
        record = parse_fixture(
            parse_opencodereview, "opencodereview", "session.jsonl"
        ).records[0]
        self.assertEqual(record.provider, "anthropic")
        self.assertEqual(record.model, "claude-sonnet-4")
        self.assertEqual(record.session_id, "session")
        self.assertEqual(record.tokens, TokenBreakdown(22, 14, 8, 2))
        self.assertEqual(record.timestamp.isoformat(), "2026-07-20T00:12:04+00:00")

    def test_malformed_and_unsupported(self):
        assert_status_isolation(parse_opencodereview, ".jsonl")

    def test_huge_duration_is_ignored_without_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            path.write_text(
                '{"type":"llm_response","timestamp":"2026-07-20T00:12:05Z",'
                '"duration_ms":"' + ("9" * 10000) + '","model":"gpt-5",'
                '"usage":{"prompt_tokens":1}}\n',
                encoding="utf-8",
            )
            record = parse_opencodereview((path,)).records[0]

        self.assertEqual(record.timestamp.isoformat(), "2026-07-20T00:12:05+00:00")
