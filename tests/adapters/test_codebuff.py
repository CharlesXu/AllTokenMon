import unittest

from scripts.alltokenmon.adapters.codebuff import parse_codebuff
from scripts.alltokenmon.schema import TokenBreakdown
from tests.adapters.file_contract import assert_status_isolation, parse_fixture


class CodebuffAdapterTests(unittest.TestCase):
    def test_contract_snake_aliases_and_path_session(self):
        record = parse_fixture(
            parse_codebuff, "codebuff",
            "manicode/projects/project/chats/chat/chat-messages.json",
        ).records[0]
        self.assertEqual(record.provider, "anthropic")
        self.assertEqual(record.model, "claude-sonnet-4")
        self.assertEqual(record.session_id, "manicode/project/chat")
        self.assertEqual(record.tokens, TokenBreakdown(17, 10, 6, 2))
        self.assertEqual(record.cost, 0.5)
        self.assertEqual(record.dedup_key, "codebuff-msg")

    def test_malformed_and_unsupported(self):
        assert_status_isolation(parse_codebuff, ".json")
