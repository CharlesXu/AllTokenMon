import unittest
import tempfile
from pathlib import Path

from scripts.alltokenmon.adapters.amp import parse_amp
from scripts.alltokenmon.schema import TokenBreakdown
from tests.adapters.file_contract import assert_status_isolation, parse_fixture


class AmpAdapterTests(unittest.TestCase):
    def test_contract_and_reconciliation(self):
        record = parse_fixture(parse_amp, "amp", "T-contract.json").records[0]
        self.assertEqual(record.provider, "anthropic")
        self.assertEqual(record.model, "claude-sonnet-4")
        self.assertEqual(record.session_id, "amp-session")
        self.assertEqual(record.timestamp.isoformat(), "2026-07-20T00:00:02+00:00")
        self.assertEqual(record.tokens, TokenBreakdown(10, 3, 2, 1))
        self.assertEqual(record.cost, 0.25)
        self.assertEqual(record.dedup_key, "amp:amp-session:1")

    def test_malformed_and_unsupported(self):
        assert_status_isolation(parse_amp, ".json")

    def test_huge_message_id_falls_back_without_datetime_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "thread.json"
            path.write_text(
                '{"id":"amp-session","created":1784505600000,"messages":[{'
                '"role":"assistant","messageId":"' + ("9" * 10000) + '",'
                '"usage":{"model":"claude-sonnet-4","inputTokens":1}}]}',
                encoding="utf-8",
            )
            record = parse_amp((path,)).records[0]

        self.assertEqual(record.timestamp.isoformat(), "2026-07-20T00:00:00+00:00")
