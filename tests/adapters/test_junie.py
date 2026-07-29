import unittest
from datetime import datetime, timezone
import os
import tempfile
from pathlib import Path

from scripts.alltokenmon.adapters.junie import parse_junie
from scripts.alltokenmon.schema import TokenBreakdown
from tests.adapters.file_contract import assert_status_isolation, parse_fixture


class JunieAdapterTests(unittest.TestCase):
    def test_contract_aliases_cost_and_start_anchor(self):
        record = parse_fixture(
            parse_junie, "junie", "session-contract/events.jsonl"
        ).records[0]
        self.assertEqual(record.provider, "openai")
        self.assertEqual(record.model, "gpt-4.1")
        self.assertEqual(record.session_id, "session-contract")
        self.assertEqual(record.tokens, TokenBreakdown(20, 13, 7, 2, 3))
        self.assertEqual(record.timestamp.isoformat(), "2026-07-20T00:15:04+00:00")
        self.assertEqual(record.cost, 0.4)

    def test_malformed_and_unsupported(self):
        assert_status_isolation(parse_junie, ".jsonl")

    def test_missing_timestamp_uses_session_id_without_back_anchoring(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session-260618-191750-jnus"
            session.mkdir()
            path = session / "events.jsonl"
            path.write_text(
                '{"event":{"agentEvent":{"kind":"LlmResponseMetadataEvent",'
                '"modelUsage":[{"model":"gpt-5","inputTokens":100,'
                '"outputTokens":50,"time":2000}]}}}\n',
                encoding="utf-8",
            )
            os.utime(path, (1, 1))
            record = parse_junie((path,)).records[0]

        expected = datetime.strptime(
            "260618191750", "%y%m%d%H%M%S"
        ).astimezone()
        self.assertEqual(record.timestamp, expected)

    def test_invalid_session_id_falls_back_to_file_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session-invalid"
            session.mkdir()
            path = session / "events.jsonl"
            path.write_text(
                '{"event":{"agentEvent":{"kind":"LlmResponseMetadataEvent",'
                '"modelUsage":[{"model":"gpt-5","inputTokens":1}]}}}\n',
                encoding="utf-8",
            )
            os.utime(path, (1, 1))
            record = parse_junie((path,)).records[0]

        self.assertEqual(record.timestamp, datetime.fromtimestamp(1, timezone.utc))

    def test_huge_duration_is_ignored_without_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session-contract"
            session.mkdir()
            path = session / "events.jsonl"
            path.write_text(
                '{"timestampMs":1784506505000,"event":{"agentEvent":{'
                '"kind":"LlmResponseMetadataEvent","modelUsage":[{'
                '"model":"gpt-5","inputTokens":1,"time":"'
                + ("9" * 10000)
                + '"}]}}}\n',
                encoding="utf-8",
            )
            record = parse_junie((path,)).records[0]

        self.assertEqual(record.timestamp.isoformat(), "2026-07-20T00:15:05+00:00")
