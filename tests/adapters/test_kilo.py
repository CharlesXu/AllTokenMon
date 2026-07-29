import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.alltokenmon.adapters import kilo
from scripts.alltokenmon.adapters.kilo import parse_kilo, scan
from scripts.alltokenmon.adapters.registry import SOURCE_SPECS
from scripts.alltokenmon.adapters.sqliteio import open_sqlite_readonly
from scripts.alltokenmon.adapters.base import DiscoveryContext
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown


class KiloAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.path = self.home / ".local/share/kilo/kilo.db"
        self.path.parent.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def _db(self, columns="id TEXT, session_id TEXT, data TEXT"):
        connection = sqlite3.connect(str(self.path))
        connection.execute("CREATE TABLE message ({})".format(columns))
        return connection

    def test_frozen_record_dedup_readonly_and_actual_discovery(self):
        payload = {
            "id": "embedded-1", "session_id": "session-1", "role": "assistant",
            "modelID": "claude-sonnet-4", "providerID": "anthropic", "cost": 0.42,
            "tokens": {"input": 1200, "output": 300, "reasoning": 40,
                       "cache": {"read": 75, "write": 25}},
            "time": {"created": 1700000000123},
            "prompt": "SENTINEL_PRIVATE",
        }
        connection = self._db()
        connection.executemany(
            "INSERT INTO message VALUES (?, ?, ?)",
            [("row-1", "session-1", json.dumps(payload)),
             ("row-2", "session-1", json.dumps(payload))],
        )
        connection.commit()
        connection.close()
        before = hashlib.sha256(self.path.read_bytes()).digest()

        result = scan(
            DiscoveryContext("linux", self.home, {}),
            SOURCE_SPECS["kilo"],
        )

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.provider, "anthropic")
        self.assertEqual(record.model, "claude-sonnet-4")
        self.assertEqual(record.session_id, "session-1")
        self.assertEqual(record.tokens, TokenBreakdown(1200, 300, 75, 25, 40))
        self.assertEqual(record.dedup_key, "embedded-1")
        self.assertEqual(record.cost, 0.42)
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).digest())
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))
        readonly = open_sqlite_readonly(self.path)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                readonly.execute("DELETE FROM message")
        finally:
            readonly.close()

    def test_missing_column_malformed_oversize_and_hostile_types(self):
        connection = self._db("id TEXT, session_id TEXT")
        connection.commit()
        connection.close()
        self.assertEqual(parse_kilo((self.path,)).status, AdapterStatus.UNSUPPORTED_FORMAT)
        self.path.unlink()

        connection = self._db()
        connection.executemany(
            "INSERT INTO message VALUES (?, ?, ?)",
            [("bad", "s", "{SENTINEL_PRIVATE"),
             ("hostile", "s", sqlite3.Binary(b"SENTINEL_PRIVATE"))],
        )
        connection.commit()
        connection.close()
        with mock.patch.object(kilo, "MAX_JSON_BYTES", 8):
            result = parse_kilo((self.path,))
        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))
        self.path.write_bytes(b"not sqlite SENTINEL_PRIVATE")
        corrupt = parse_kilo((self.path,))
        self.assertEqual(corrupt.status, AdapterStatus.ERROR)
        self.assertNotIn("SENTINEL_PRIVATE", repr(corrupt))
