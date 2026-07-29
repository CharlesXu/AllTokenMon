import unittest
import tempfile
from pathlib import Path

from scripts.alltokenmon.adapters.grok import parse_grok
from scripts.alltokenmon.schema import TokenBreakdown
from tests.adapters.file_contract import assert_status_isolation, parse_fixture


class GrokAdapterTests(unittest.TestCase):
    def test_contract_cumulative_turn_delta(self):
        record = parse_fixture(parse_grok, "grok", "workspace/grok-session/updates.jsonl").records[0]
        self.assertEqual(record.provider, "xai")
        self.assertEqual(record.model, "grok-build")
        self.assertEqual(record.session_id, "grok-session")
        self.assertEqual(record.tokens, TokenBreakdown(input=19))
        self.assertEqual(record.timestamp.isoformat(), "2026-07-20T00:10:02+00:00")
        self.assertEqual(record.dedup_key, "grok:grok-session:0")

    def test_malformed_and_unsupported(self):
        assert_status_isolation(parse_grok, ".jsonl")

    def test_signals_reconciles_compacted_total(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workspace/session/updates.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"params":{"_meta":{"totalTokens":10,'
                '"agentTimestampMs":1784506200000}}}\n',
                encoding="utf-8",
            )
            path.with_name("signals.json").write_text(
                '{"primaryModelId":"grok-build",'
                '"totalTokensBeforeCompaction":20,"contextTokensUsed":5}',
                encoding="utf-8",
            )
            result = parse_grok((path,))
        self.assertEqual(
            [record.tokens.input for record in result.records], [10, 15]
        )
        self.assertEqual(result.records[1].dedup_key, "grok:session:signals")
