import unittest
import tempfile
from pathlib import Path

from scripts.alltokenmon.adapters.gjc import parse_gjc
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown
from tests.adapters.file_contract import assert_status_isolation, parse_fixture


class GjcAdapterTests(unittest.TestCase):
    def test_contract_embedded_cost(self):
        record = parse_fixture(parse_gjc, "gjc", "session.jsonl").records[0]
        self.assertEqual(record.provider, "openai")
        self.assertEqual(record.model, "gpt-4o")
        self.assertEqual(record.session_id, "gjc-session")
        self.assertEqual(record.tokens, TokenBreakdown(18, 11, 6, 2))
        self.assertEqual(record.cost, 0.3)
        self.assertEqual(record.dedup_key, "gjc-session:gjc-msg")

    def test_malformed_and_unsupported(self):
        assert_status_isolation(parse_gjc, ".jsonl")

    def test_zero_tokens_require_positive_provider_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            path.write_text(
                '{"type":"session","id":"gjc-session"}\n'
                '{"type":"message","id":"empty","message":{"role":"assistant",'
                '"model":"gpt-4o","usage":{"input":0,"output":0}}}\n',
                encoding="utf-8",
            )
            empty = parse_gjc((path,))
            path.write_text(
                '{"type":"session","id":"gjc-session"}\n'
                '{"type":"message","id":"cost-only","message":{"role":"assistant",'
                '"model":"gpt-4o","usage":{"input":0,"output":0,'
                '"cost":{"total":0.25}}}}\n',
                encoding="utf-8",
            )
            cost_only = parse_gjc((path,))

        self.assertEqual(empty.status, AdapterStatus.NO_DATA)
        self.assertEqual(empty.records, ())
        self.assertEqual(len(cost_only.records), 1)
        self.assertEqual(cost_only.records[0].tokens, TokenBreakdown())
        self.assertEqual(cost_only.records[0].cost, 0.25)
