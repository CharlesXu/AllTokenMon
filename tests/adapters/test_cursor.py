import ast
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.alltokenmon.adapters import cursor
from scripts.alltokenmon.adapters.base import DiscoveryContext
from scripts.alltokenmon.adapters.cursor import parse_cursor, scan
from scripts.alltokenmon.adapters.registry import SOURCE_SPECS
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown


FIXTURE_HOME = Path(__file__).parents[1] / "fixtures"


class CursorAdapterTests(unittest.TestCase):
    def test_committed_v3_cache_and_discovery(self):
        result = scan(
            DiscoveryContext("linux", FIXTURE_HOME, {}),
            SOURCE_SPECS["cursor"],
        )
        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.model, "gpt-5.3-codex")
        self.assertEqual(record.provider, "openai")
        self.assertEqual(record.tokens, TokenBreakdown(100, 40, 30, 20))
        self.assertEqual((record.cost, record.cost_source), (0.25, "provider_reported"))

    def test_missing_malformed_and_bounded_cache(self):
        self.assertEqual(parse_cursor(()).status, AdapterStatus.NO_DATA)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "usage.csv"
            path.write_text("not,a,cursor,cache\nSENTINEL_PRIVATE\n", encoding="utf-8")
            self.assertEqual(
                parse_cursor((path,)).status, AdapterStatus.UNSUPPORTED_FORMAT
            )
            path.write_text(
                "Date,Model,Input (w/ Cache Write),Input (w/o Cache Write),"
                "Cache Read,Output Tokens,Total Tokens,Cost,Cost to you\n"
                "2026-07-28,gpt-5,2,1,0,1,3,0.01,0.01\n"
                "2026-07-29,gpt-5,2,1,0,1,3,0.01,0.01\n",
                encoding="utf-8",
            )
            with mock.patch.object(cursor, "_MAX_ROWS", 1):
                result = parse_cursor((path,))
            self.assertEqual(result.status, AdapterStatus.PARTIAL)
            self.assertEqual(len(result.records), 1)
            self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_module_has_no_network_or_subprocess_imports(self):
        tree = ast.parse(Path(cursor.__file__).read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertTrue(imported.isdisjoint({"urllib", "http.client", "subprocess"}))


if __name__ == "__main__":
    unittest.main()
