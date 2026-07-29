import hashlib
import json
import queue
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from scripts.alltokenmon.adapters import crush
from scripts.alltokenmon.adapters.base import DiscoveryContext
from scripts.alltokenmon.adapters.crush import parse_crush, scan
from scripts.alltokenmon.adapters.registry import SOURCE_SPECS
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown


class CrushAdapterTests(unittest.TestCase):
    SESSION_COLUMNS = (
        "id TEXT, parent_session_id TEXT, message_count INTEGER, cost REAL, "
        "updated_at INTEGER, created_at INTEGER"
    )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.registry = self.home / ".local/share/crush/projects.json"
        self.project = self.home / "project"
        self.db_path = self.project / ".crush/crush.db"
        self.registry.parent.mkdir(parents=True)
        self.db_path.parent.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def _db(self, sessions=None):
        connection = sqlite3.connect(str(self.db_path))
        connection.execute(
            "CREATE TABLE sessions ({})".format(sessions or self.SESSION_COLUMNS)
        )
        connection.execute(
            "CREATE TABLE messages (session_id TEXT, role TEXT, created_at INTEGER)"
        )
        return connection

    def _registry(self):
        self.registry.write_text(json.dumps({
            "projects": [{
                "path": str(self.project), "data_dir": ".crush",
                "secret": "SENTINEL_PRIVATE",
            }]
        }), encoding="utf-8")

    def test_cost_day_buckets_children_and_actual_registry_discovery(self):
        day_one = 1742300000
        day_two = 1742386400
        connection = self._db()
        connection.executemany(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
            [("root", None, 4, 30.0, day_two, day_one),
             ("child", "root", 1, 99.0, day_two, day_one)],
        )
        connection.executemany(
            "INSERT INTO messages VALUES (?, ?, ?)",
            [("root", "assistant", day_one),
             ("root", "assistant", day_two),
             ("child", "assistant", day_two + 1),
             ("root", "user", day_one)],
        )
        connection.commit()
        connection.close()
        self._registry()
        before = hashlib.sha256(self.db_path.read_bytes()).digest()
        result = scan(
            DiscoveryContext("linux", self.home, {}),
            SOURCE_SPECS["crush"],
        )
        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 2)
        self.assertEqual([record.message_count for record in result.records], [1, 2])
        self.assertEqual([record.cost for record in result.records], [10.0, 20.0])
        self.assertTrue(all(record.tokens == TokenBreakdown() for record in result.records))
        self.assertTrue(all(record.model == "session-total" for record in result.records))
        self.assertEqual(before, hashlib.sha256(self.db_path.read_bytes()).digest())
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_missing_column_row_cap_and_hostile_registry(self):
        connection = self._db("id TEXT")
        connection.commit()
        connection.close()
        self._registry()
        self.assertEqual(parse_crush((self.registry,)).status, AdapterStatus.UNSUPPORTED_FORMAT)
        self.db_path.unlink()
        connection = self._db()
        connection.executemany(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
            [("a", None, 1, "SENTINEL_PRIVATE", 1742300000, 1742300000),
             ("b", None, 1, 2.0, 1742300001, 1742300001)],
        )
        connection.commit()
        connection.close()
        with mock.patch.object(crush, "_MAX_ROWS", 1):
            result = parse_crush((self.registry,))
        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))
        self.db_path.write_bytes(b"not sqlite SENTINEL_PRIVATE")
        corrupt = parse_crush((self.registry,))
        self.assertEqual(corrupt.status, AdapterStatus.ERROR)
        self.assertNotIn("SENTINEL_PRIVATE", repr(corrupt))

    def test_duplicate_id_cycle_is_bounded_and_partial(self):
        connection = self._db()
        connection.executemany(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("root", None, 1, 3.0, 1742300000, 1742300000),
                ("root", "root", 1, 99.0, 1742300000, 1742300000),
            ],
        )
        connection.commit()
        connection.close()
        self._registry()
        outcomes = queue.Queue()
        worker = threading.Thread(
            target=lambda: outcomes.put(parse_crush((self.registry,))),
            daemon=True,
        )

        worker.start()
        worker.join(2)

        self.assertFalse(worker.is_alive(), "cyclic session traversal did not terminate")
        result = outcomes.get_nowait()
        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(len(result.records), 1)
