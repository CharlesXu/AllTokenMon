import unittest
from datetime import datetime, timezone
import os
import tempfile
from pathlib import Path

from scripts.alltokenmon.adapters.kimi import parse_kimi
from scripts.alltokenmon.schema import TokenBreakdown
from tests.adapters.file_contract import assert_status_isolation, parse_fixture


class KimiAdapterTests(unittest.TestCase):
    def test_contract_progressive_update_dedup(self):
        record = parse_fixture(parse_kimi, "kimi", "group/kimi-session/wire.jsonl").records[0]
        self.assertEqual(record.provider, "moonshot")
        self.assertEqual(record.model, "kimi-for-coding")
        self.assertEqual(record.session_id, "kimi-session")
        self.assertEqual(record.tokens, TokenBreakdown(14, 7, 4, 2))
        self.assertEqual(record.timestamp.isoformat(), "2026-07-20T00:03:01+00:00")
        self.assertEqual(record.dedup_key, "kimi:kimi-session:kimi-msg")

    def test_malformed_and_unsupported(self):
        assert_status_isolation(parse_kimi, ".jsonl")

    def test_kimi_code_turn_scope_and_symbolic_model(self):
        with tempfile.TemporaryDirectory() as directory:
            path = (
                Path(directory) / "sessions/ws/code-session/agents/main/wire.jsonl"
            )
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"type":"llm.request","model":"kimi-code/k3"}\n'
                '{"type":"usage.record","model":"__runtime_model__",'
                '"usage":{"inputOther":9,"output":2,"inputCacheRead":3},'
                '"usageScope":"turn","time":1784505781000}\n'
                '{"type":"usage.record","model":"k3","usage":{"inputOther":99},'
                '"usageScope":"session","time":1784505782000}\n',
                encoding="utf-8",
            )
            result = parse_kimi((path,))
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].model, "k3")
        self.assertEqual(result.records[0].tokens, TokenBreakdown(9, 2, 3))

    def test_huge_numeric_timestamps_fall_back_without_overflow(self):
        huge = "9" * 1000
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory) / "group/session/wire.jsonl"
            legacy.parent.mkdir(parents=True)
            legacy.write_text(
                '{"timestamp":' + huge + ',"message":{"type":"StatusUpdate",'
                '"payload":{"token_usage":{"input_other":1},'
                '"message_id":"legacy"}}}\n',
                encoding="utf-8",
            )
            code = Path(directory) / "code/agents/main/wire.jsonl"
            code.parent.mkdir(parents=True)
            code.write_text(
                '{"type":"usage.record","model":"k3","usageScope":"turn",'
                '"time":' + huge + ',"usage":{"inputOther":1}}\n',
                encoding="utf-8",
            )
            os.utime(legacy, (1, 1))
            os.utime(code, (2, 2))
            legacy_record = parse_kimi((legacy,)).records[0]
            code_record = parse_kimi((code,)).records[0]

        self.assertEqual(
            legacy_record.timestamp, datetime.fromtimestamp(1, timezone.utc)
        )
        self.assertEqual(code_record.timestamp, datetime.fromtimestamp(2, timezone.utc))
