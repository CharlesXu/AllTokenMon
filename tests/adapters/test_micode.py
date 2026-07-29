import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.alltokenmon.adapters import micode
from scripts.alltokenmon.adapters.base import DiscoveryContext
from scripts.alltokenmon.adapters.micode import parse_micode, scan
from scripts.alltokenmon.adapters.registry import SOURCE_SPECS
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown


class MiCodeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.path = self.home / ".local/share/mimocode/mimocode.db"
        self.path.parent.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def _db(self, columns="id TEXT, session_id TEXT, data TEXT"):
        connection = sqlite3.connect(str(self.path))
        connection.execute("CREATE TABLE message ({})".format(columns))
        return connection

    def test_fork_fingerprint_embedded_dedup_and_actual_discovery(self):
        payload = {
            "id": "embedded-1", "role": "assistant",
            "modelID": "mimo-v2.5-pro", "providerID": "mimo", "cost": 0.05,
            "tokens": {"input": 1000, "output": 500, "reasoning": 100,
                       "cache": {"read": 200, "write": 50}},
            "time": {"created": 1700000000, "completed": 1700000001.234},
            "content": "SENTINEL_PRIVATE",
        }
        connection = self._db()
        connection.executemany(
            "INSERT INTO message VALUES (?, ?, ?)",
            [("row-a", "root", json.dumps(payload)),
             ("row-b", "fork", json.dumps(payload))],
        )
        connection.commit()
        connection.close()
        before = hashlib.sha256(self.path.read_bytes()).digest()
        result = scan(
            DiscoveryContext("linux", self.home, {}),
            SOURCE_SPECS["micode"],
        )
        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.provider, "mimo")
        self.assertEqual(record.tokens, TokenBreakdown(1000, 500, 200, 50, 100))
        self.assertEqual(record.dedup_key, "embedded-1")
        self.assertEqual(record.cost, 0.05)
        self.assertEqual(int(record.timestamp.timestamp()), 1700000000)
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).digest())
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_required_column_malformed_oversize_and_cross_db_keys(self):
        connection = self._db("id TEXT, session_id TEXT")
        connection.commit()
        connection.close()
        self.assertEqual(parse_micode((self.path,)).status, AdapterStatus.UNSUPPORTED_FORMAT)
        self.path.unlink()
        connection = self._db()
        connection.executemany(
            "INSERT INTO message VALUES (?, ?, ?)",
            [("bad", "s", "{SENTINEL_PRIVATE"),
             ("hostile", "s", sqlite3.Binary(b"SENTINEL_PRIVATE"))],
        )
        connection.commit()
        connection.close()
        with mock.patch.object(micode, "MAX_JSON_BYTES", 8):
            result = parse_micode((self.path,))
        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))
        self.path.write_bytes(b"not sqlite SENTINEL_PRIVATE")
        corrupt = parse_micode((self.path,))
        self.assertEqual(corrupt.status, AdapterStatus.ERROR)
        self.assertNotIn("SENTINEL_PRIVATE", repr(corrupt))
