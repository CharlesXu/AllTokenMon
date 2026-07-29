import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.alltokenmon.adapters import goose
from scripts.alltokenmon.adapters.base import DiscoveryContext
from scripts.alltokenmon.adapters.goose import parse_goose, scan
from scripts.alltokenmon.adapters.registry import SOURCE_SPECS
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown


class GooseAdapterTests(unittest.TestCase):
    COLUMNS = (
        "id TEXT, model_config_json TEXT, provider_name TEXT, created_at TEXT, "
        "total_tokens INTEGER, input_tokens INTEGER, output_tokens INTEGER, "
        "accumulated_total_tokens INTEGER, accumulated_input_tokens INTEGER, "
        "accumulated_output_tokens INTEGER"
    )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.path = self.home / ".local/share/goose/sessions/sessions.db"
        self.path.parent.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def _db(self, columns=None):
        connection = sqlite3.connect(str(self.path))
        connection.execute("CREATE TABLE sessions ({})".format(columns or self.COLUMNS))
        return connection

    def test_cumulative_frozen_record_and_actual_discovery(self):
        connection = self._db()
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("session-1", json.dumps({"model_name": "claude-sonnet-4",
                                     "secret": "SENTINEL_PRIVATE"}),
             "", "2026-04-14T16:18:53Z", 12, 5, 7, 40, 25, 10),
        )
        connection.commit()
        connection.close()
        before = hashlib.sha256(self.path.read_bytes()).digest()
        result = scan(DiscoveryContext("linux", self.home, {}), SOURCE_SPECS["goose"])
        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.provider, "anthropic")
        self.assertEqual(record.model, "claude-sonnet-4")
        self.assertEqual(record.session_id, "session-1")
        self.assertEqual(record.tokens, TokenBreakdown(25, 10, 0, 0, 5))
        self.assertEqual(record.dedup_key, "session-1")
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).digest())
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_required_column_malformed_oversize_and_hostile(self):
        connection = self._db("id TEXT")
        connection.commit()
        connection.close()
        self.assertEqual(parse_goose((self.path,)).status, AdapterStatus.UNSUPPORTED_FORMAT)
        self.path.unlink()
        connection = self._db()
        connection.executemany(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [("bad", "{SENTINEL_PRIVATE", None, "bad", 1, 1, 0, None, None, None),
             ("blob", sqlite3.Binary(b"SENTINEL_PRIVATE"), None, "bad",
              1, 1, 0, None, None, None)],
        )
        connection.commit()
        connection.close()
        with mock.patch.object(goose, "MAX_JSON_BYTES", 8):
            result = parse_goose((self.path,))
        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))
        self.path.write_bytes(b"not sqlite SENTINEL_PRIVATE")
        corrupt = parse_goose((self.path,))
        self.assertEqual(corrupt.status, AdapterStatus.ERROR)
        self.assertNotIn("SENTINEL_PRIVATE", repr(corrupt))
