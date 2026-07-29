import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.alltokenmon.adapters.base import DiscoveryContext
from scripts.alltokenmon.adapters.kilocode import parse_kilocode, scan
from scripts.alltokenmon.normalize import stable_key
from scripts.alltokenmon.schema import TokenBreakdown
from tests.adapters.file_contract import parse_fixture


class KiloCodeAdapterTests(unittest.TestCase):
    def test_shared_fixture_has_kilocode_identity(self):
        result = parse_fixture(
            parse_kilocode, "kilocode", "tasks/task-frozen/ui_messages.json"
        )
        records = {record.dedup_key: record for record in result.records}

        self.assertEqual(len(records), 3)
        self.assertEqual(
            records[stable_key("kilocode", "task-frozen", "request-1")].tokens,
            TokenBreakdown(101, 50, 20, 5),
        )
        self.assertEqual(
            records[stable_key("kilocode", "task-frozen", "request-1")].model,
            "claude-sonnet-4-6",
        )
        self.assertEqual(
            records[stable_key("kilocode", "task-frozen", "request-2")].provider,
            "azure/openai",
        )
        self.assertTrue(
            all(record.runtime == "kilocode" for record in result.records)
        )

    def test_scan_passes_discovered_paths_to_public_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task/ui_messages.json"
            path.parent.mkdir()
            path.write_text("[]", encoding="utf-8")
            context = DiscoveryContext("linux", Path(directory), {})
            with patch(
                "scripts.alltokenmon.adapters.cline_family.discover",
                return_value=(path,),
            ):
                result = scan(context, (object(),))

        self.assertEqual(result.runtime, "kilocode")
