import unittest

from scripts.alltokenmon.adapters.commandcode import parse_commandcode
from scripts.alltokenmon.schema import TokenBreakdown
from tests.adapters.file_contract import assert_status_isolation, parse_fixture


class CommandCodeAdapterTests(unittest.TestCase):
    def test_contract_exact_frozen_character_estimate(self):
        record = parse_fixture(
            parse_commandcode, "commandcode",
            ".commandcode/projects/project/session.jsonl",
        ).records[0]
        self.assertEqual(record.provider, "command-code")
        self.assertEqual(record.model, "unknown")
        self.assertEqual(record.session_id, "command-session")
        self.assertEqual(record.tokens, TokenBreakdown(3, 2))
        self.assertEqual(record.confidence, "estimated")
        self.assertEqual(record.dedup_key, "command-session:0")

    def test_malformed_and_unsupported(self):
        assert_status_isolation(parse_commandcode, ".jsonl")
