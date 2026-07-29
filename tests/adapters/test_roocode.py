import unittest

from scripts.alltokenmon.adapters.roocode import parse_roocode
from scripts.alltokenmon.normalize import stable_key
from scripts.alltokenmon.schema import TokenBreakdown
from tests.adapters.file_contract import parse_fixture


class RooCodeAdapterTests(unittest.TestCase):
    def test_shared_fixture_has_roocode_identity(self):
        result = parse_fixture(
            parse_roocode, "roocode", "tasks/task-frozen/ui_messages.json"
        )
        records = {record.dedup_key: record for record in result.records}

        self.assertEqual(len(records), 3)
        self.assertEqual(
            records[stable_key("roocode", "task-frozen", "request-1")].tokens,
            TokenBreakdown(101, 50, 20, 5),
        )
        self.assertEqual(
            records[stable_key("roocode", "task-frozen", "request-1")].model,
            "claude-sonnet-4-6",
        )
        self.assertTrue(
            all(record.runtime == "roocode" for record in result.records)
        )
