import unittest

from scripts.alltokenmon.adapters.qwen import parse_qwen
from scripts.alltokenmon.schema import TokenBreakdown
from tests.adapters.file_contract import assert_status_isolation, parse_fixture


class QwenAdapterTests(unittest.TestCase):
    def test_contract_usage_metadata_aliases(self):
        record = parse_fixture(parse_qwen, "qwen", "project/chats/session.jsonl").records[0]
        self.assertEqual(record.provider, "qwen")
        self.assertEqual(record.model, "qwen3.5-plus")
        self.assertEqual(record.session_id, "qwen-session")
        self.assertEqual(record.tokens, TokenBreakdown(15, 8, 5, 0, 2))
        self.assertEqual(record.dedup_key, "qwen:qwen-session:0")

    def test_malformed_and_unsupported(self):
        assert_status_isolation(parse_qwen, ".jsonl")
