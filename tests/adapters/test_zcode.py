import unittest
import sqlite3
import tempfile
from pathlib import Path

from scripts.alltokenmon.adapters.zcode import parse_zcode
from scripts.alltokenmon.schema import TokenBreakdown
from tests.adapters.file_contract import assert_status_isolation, parse_fixture


class ZcodeAdapterTests(unittest.TestCase):
    def test_contract_inclusive_usage_normalization(self):
        record = parse_fixture(parse_zcode, "zcode", "project/session.jsonl").records[0]
        self.assertEqual(record.provider, "zhipu")
        self.assertEqual(record.model, "glm-5.2")
        self.assertEqual(record.session_id, "zcode-session")
        self.assertEqual(record.tokens, TokenBreakdown(20, 12, 8, 2, 3))
        self.assertEqual(record.dedup_key, "zcode-session:0")

    def test_malformed_and_unsupported(self):
        assert_status_isolation(parse_zcode, ".jsonl")

    def test_sqlite_modern_inclusive_normalization(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite"
            connection = sqlite3.connect(str(path))
            try:
                connection.execute(
                    "CREATE TABLE model_usage (id TEXT, session_id TEXT, "
                    "model_id TEXT, started_at INTEGER, input_tokens INTEGER, "
                    "output_tokens INTEGER, reasoning_tokens INTEGER, "
                    "cache_read_input_tokens INTEGER, "
                    "cache_creation_input_tokens INTEGER, "
                    "computed_total_tokens INTEGER)"
                )
                connection.execute(
                    "INSERT INTO model_usage VALUES "
                    "('row','session','GLM-5.2',1784506260000,"
                    "30,15,3,8,2,45)"
                )
                connection.commit()
            finally:
                connection.close()
            result = parse_zcode((path,))
        self.assertEqual(result.records[0].source_kind, "sqlite")
        self.assertEqual(
            result.records[0].tokens, TokenBreakdown(20, 12, 8, 2, 3)
        )
        self.assertEqual(result.records[0].dedup_key, "zcode-sqlite:row")
