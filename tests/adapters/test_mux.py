import unittest

from scripts.alltokenmon.adapters.mux import parse_mux
from scripts.alltokenmon.schema import TokenBreakdown
from tests.adapters.file_contract import assert_status_isolation, parse_fixture


class MuxAdapterTests(unittest.TestCase):
    def test_contract_cumulative_model_snapshot(self):
        record = parse_fixture(parse_mux, "mux", "workspace/session-usage.json").records[0]
        self.assertEqual(record.provider, "anthropic")
        self.assertEqual(record.model, "claude-opus-4-6")
        self.assertEqual(record.session_id, "workspace")
        self.assertEqual(record.tokens, TokenBreakdown(16, 9, 5, 2, 1))
        self.assertAlmostEqual(record.cost, 0.15)
        self.assertEqual(record.dedup_key, "mux:workspace:anthropic:claude-opus-4-6")

    def test_malformed_and_unsupported(self):
        assert_status_isolation(parse_mux, ".json")
