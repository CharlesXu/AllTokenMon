import unittest
import tempfile
from pathlib import Path

from scripts.alltokenmon.adapters.pi import parse_pi
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown
from tests.adapters.file_contract import assert_status_isolation, parse_fixture


class PiAdapterTests(unittest.TestCase):
    def test_contract_title_alias_and_provider_inference(self):
        record = parse_fixture(parse_pi, "pi", "session.jsonl").records[0]
        self.assertEqual(record.provider, "openai")
        self.assertEqual(record.model, "gpt-5")
        self.assertEqual(record.session_id, "pi-session")
        self.assertEqual(record.tokens, TokenBreakdown(12, 5, 3, 2))
        self.assertEqual(record.dedup_key, "pi:pi-session:pi-msg")

    def test_malformed_and_unsupported(self):
        assert_status_isolation(parse_pi, ".jsonl")

    def test_zero_token_message_does_not_inflate_count(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            path.write_text(
                '{"type":"session","id":"pi-session"}\n'
                '{"type":"message","id":"zero","message":{"role":"assistant",'
                '"model":"gpt-5","usage":{"input":0,"output":0,'
                '"cacheRead":0,"cacheWrite":0}}}\n',
                encoding="utf-8",
            )
            result = parse_pi((path,))

        self.assertEqual(result.status, AdapterStatus.NO_DATA)
        self.assertEqual(result.records, ())
