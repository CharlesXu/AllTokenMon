import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.alltokenmon.adapters import devin_cli
from scripts.alltokenmon.adapters.base import DiscoveryContext
from scripts.alltokenmon.adapters.devin_cli import parse_devin_cli, scan
from scripts.alltokenmon.adapters.registry import SOURCE_SPECS
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown


class DevinCliAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.path = self.home / ".local/share/devin/cli/sessions.db"
        self.path.parent.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def _db(self, message_columns=None):
        connection = sqlite3.connect(str(self.path))
        connection.execute("CREATE TABLE sessions (id TEXT, model TEXT)")
        connection.execute(
            "CREATE TABLE message_nodes ({})".format(
                message_columns
                or "row_id TEXT, session_id TEXT, chat_message TEXT, created_at INTEGER"
            )
        )
        return connection

    def test_metrics_model_back_anchor_and_actual_discovery(self):
        payload = {
            "role": "assistant",
            "metadata": {
                "generation_model": "gpt-5",
                "metrics": {
                    "input_tokens": 20, "output_tokens": 8,
                    "cache_read_tokens": 5, "cache_creation_tokens": 2,
                    "total_time_ms": 1000,
                },
            },
            "content": "SENTINEL_PRIVATE",
        }
        connection = self._db()
        connection.execute("INSERT INTO sessions VALUES (?, ?)", ("s1", "adaptive"))
        connection.execute(
            "INSERT INTO message_nodes VALUES (?, ?, ?, ?)",
            ("r1", "s1", json.dumps(payload), 1780000000),
        )
        connection.commit()
        connection.close()
        before = hashlib.sha256(self.path.read_bytes()).digest()
        result = scan(
            DiscoveryContext("linux", self.home, {}),
            SOURCE_SPECS["devin-cli"],
        )
        self.assertEqual(result.status, AdapterStatus.OK)
        record = result.records[0]
        self.assertEqual(record.provider, "openai")
        self.assertEqual(record.model, "gpt-5")
        self.assertEqual(record.tokens, TokenBreakdown(20, 8, 5, 2))
        self.assertEqual(record.dedup_key, "devin-cli:s1:r1")
        self.assertEqual(int(record.timestamp.timestamp()), 1779999999)
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).digest())
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_required_column_malformed_oversize_and_fallback_tokens(self):
        connection = self._db("row_id TEXT, session_id TEXT, chat_message TEXT")
        connection.commit()
        connection.close()
        self.assertEqual(parse_devin_cli((self.path,)).status, AdapterStatus.UNSUPPORTED_FORMAT)
        self.path.unlink()
        connection = self._db()
        connection.execute("INSERT INTO sessions VALUES (?, ?)", ("s1", "claude-opus-4"))
        connection.executemany(
            "INSERT INTO message_nodes VALUES (?, ?, ?, ?)",
            [("bad", "s1", "{SENTINEL_PRIVATE", 1780000000),
             ("fallback", "s1", json.dumps({
                 "role": "assistant",
                 "metadata": {"num_tokens": 9},
             }), 1780000001),
             ("oversize", "s1", "x" * 200, 1780000002)],
        )
        connection.commit()
        connection.close()
        with mock.patch.object(devin_cli, "MAX_JSON_BYTES", 128):
            result = parse_devin_cli((self.path,))
        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(result.records[0].tokens, TokenBreakdown(output=9))
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))
        self.path.write_bytes(b"not sqlite SENTINEL_PRIVATE")
        corrupt = parse_devin_cli((self.path,))
        self.assertEqual(corrupt.status, AdapterStatus.ERROR)
        self.assertNotIn("SENTINEL_PRIVATE", repr(corrupt))
