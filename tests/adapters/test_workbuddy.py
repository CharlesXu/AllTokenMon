import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.alltokenmon.adapters import workbuddy
from scripts.alltokenmon.adapters.base import DiscoveryContext
from scripts.alltokenmon.adapters.registry import SOURCE_SPECS
from scripts.alltokenmon.adapters.workbuddy import parse_workbuddy, scan
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown


class WorkBuddyAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.path = self.home / ".workbuddy/workbuddy.db"
        self.path.parent.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def _db(self, usage="session_id TEXT, used INTEGER, updated_at INTEGER"):
        connection = sqlite3.connect(str(self.path))
        connection.execute("CREATE TABLE sessions (id TEXT, model TEXT, cwd TEXT)")
        connection.execute("CREATE TABLE session_usage ({})".format(usage))
        return connection

    def test_frozen_aggregate_record_and_actual_discovery(self):
        connection = self._db()
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            ("session-1", "deepseek-v4-pro", "SENTINEL_PRIVATE"),
        )
        connection.execute(
            "INSERT INTO session_usage VALUES (?, ?, ?)",
            ("session-1", 1234, 1780000000000),
        )
        connection.commit()
        connection.close()
        before = hashlib.sha256(self.path.read_bytes()).digest()
        result = scan(
            DiscoveryContext("linux", self.home, {}),
            SOURCE_SPECS["workbuddy"],
        )
        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.provider, "deepseek")
        self.assertEqual(record.model, "deepseek-v4-pro")
        self.assertEqual(record.tokens, TokenBreakdown(input=1234))
        self.assertEqual(
            record.dedup_key,
            "workbuddy:sqlite:session-1:1780000000000",
        )
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).digest())
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_detailed_raw_usage_precedes_sqlite_and_deduplicates(self):
        connection = self._db()
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            ("fallback", "deepseek-v4-pro", "SENTINEL_PRIVATE"),
        )
        connection.execute(
            "INSERT INTO session_usage VALUES (?, ?, ?)",
            ("fallback", 1234, 1780000000000),
        )
        connection.commit()
        connection.close()
        detailed = self.home / ".workbuddy/projects/demo/session.jsonl"
        detailed.parent.mkdir(parents=True)
        base = {
            "type": "function_call",
            "status": "completed",
            "sessionId": "session-raw",
            "timestamp": 1780000000000,
            "providerData": {
                "messageId": "message-1",
                "requestModelId": "glm-5.2",
                "rawUsage": {
                    "prompt_tokens": 64_700,
                    "completion_tokens": 635,
                    "prompt_cache_hit_tokens": 76_032,
                },
            },
            "arguments": "SENTINEL_PRIVATE",
        }
        smaller = json.loads(json.dumps(base))
        smaller["providerData"]["rawUsage"]["prompt_tokens"] = 1
        detailed.write_text(
            json.dumps(smaller) + "\n" + json.dumps(base) + "\n",
            encoding="utf-8",
        )
        later = detailed.with_name("zz-session.jsonl")
        later.write_text(json.dumps(smaller) + "\n", encoding="utf-8")

        result = scan(
            DiscoveryContext("linux", self.home, {}),
            SOURCE_SPECS["workbuddy"],
        )

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.provider, "zai")
        self.assertEqual(record.model, "glm-5.2")
        self.assertEqual(record.session_id, "session-raw")
        self.assertEqual(record.tokens, TokenBreakdown(64_700, 635, 76_032, 0))
        self.assertEqual(
            record.dedup_key,
            "workbuddy:jsonl:session-raw:message-1",
        )
        self.assertEqual(record.source_kind, "jsonl")
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_malformed_detailed_source_falls_back_to_sqlite_as_partial(self):
        connection = self._db()
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            ("fallback", "deepseek-v4-pro", "SENTINEL_PRIVATE"),
        )
        connection.execute(
            "INSERT INTO session_usage VALUES (?, ?, ?)",
            ("fallback", 1234, 1780000000000),
        )
        connection.commit()
        connection.close()
        detailed = self.home / ".workbuddy/projects/demo/session.jsonl"
        detailed.parent.mkdir(parents=True)
        detailed.write_text("{SENTINEL_PRIVATE\n", encoding="utf-8")

        result = scan(
            DiscoveryContext("linux", self.home, {}),
            SOURCE_SPECS["workbuddy"],
        )

        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].source_kind, "sqlite")
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_partial_detailed_records_merge_sqlite_fallback(self):
        connection = self._db()
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            ("fallback-session", "deepseek-v4-pro", "SENTINEL_PRIVATE"),
        )
        connection.execute(
            "INSERT INTO session_usage VALUES (?, ?, ?)",
            ("fallback-session", 1000, 1780000000000),
        )
        connection.commit()
        connection.close()
        detailed = self.home / ".workbuddy/projects/demo/session.jsonl"
        detailed.parent.mkdir(parents=True)
        valid = {
            "type": "function_call",
            "sessionId": "detail-session",
            "providerData": {
                "messageId": "detail-message",
                "requestModelId": "glm-5.2",
                "rawUsage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
        }
        detailed.write_text(
            json.dumps(valid) + "\n{SENTINEL_PRIVATE\n",
            encoding="utf-8",
        )

        result = scan(
            DiscoveryContext("linux", self.home, {}),
            SOURCE_SPECS["workbuddy"],
        )

        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(len(result.records), 2)
        self.assertEqual(
            sum(record.tokens.total for record in result.records),
            1110,
        )
        self.assertEqual(
            {record.source_kind for record in result.records},
            {"jsonl", "sqlite"},
        )
        self.assertEqual(
            {record.dedup_key.split(":", 2)[1] for record in result.records},
            {"jsonl", "sqlite"},
        )

    def test_partial_detailed_session_suppresses_same_session_aggregate(self):
        connection = self._db()
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            ("shared-session", "glm-5.2", "SENTINEL_PRIVATE"),
        )
        connection.execute(
            "INSERT INTO session_usage VALUES (?, ?, ?)",
            ("shared-session", 1000, 1780000000000),
        )
        connection.commit()
        connection.close()
        detailed = self.home / ".workbuddy/projects/demo/session.jsonl"
        detailed.parent.mkdir(parents=True)
        valid = {
            "type": "function_call",
            "sessionId": "shared-session",
            "providerData": {
                "messageId": "detail-message",
                "requestModelId": "glm-5.2",
                "rawUsage": {"prompt_tokens": 100},
            },
        }
        detailed.write_text(
            json.dumps(valid) + "\n{SENTINEL_PRIVATE\n",
            encoding="utf-8",
        )

        result = scan(
            DiscoveryContext("linux", self.home, {}),
            SOURCE_SPECS["workbuddy"],
        )

        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].tokens.total, 100)
        self.assertEqual(result.records[0].source_kind, "jsonl")
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_detailed_usage_precedence_and_status_filter(self):
        detailed = self.home / ".workbuddy/projects/demo/session.jsonl"
        detailed.parent.mkdir(parents=True)
        rows = (
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "id": "line-1",
                "message": {
                    "model": "gpt-5",
                    "usage": {"inputTokens": 3, "outputTokens": 2},
                },
                "providerData": {
                    "rawUsage": {"prompt_tokens": 999},
                },
            },
            {
                "type": "function_call",
                "status": "pending",
                "providerData": {
                    "messageId": "ignored",
                    "rawUsage": {"prompt_tokens": 999},
                },
            },
        )
        detailed.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )

        result = parse_workbuddy((detailed,))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].tokens, TokenBreakdown(3, 2))
        self.assertEqual(result.records[0].model, "gpt-5")

    def test_required_column_row_cap_and_hostile_types(self):
        connection = self._db("session_id TEXT, used INTEGER")
        connection.commit()
        connection.close()
        self.assertEqual(
            parse_workbuddy((self.path,)).status,
            AdapterStatus.UNSUPPORTED_FORMAT,
        )
        self.path.unlink()
        connection = self._db()
        connection.executemany(
            "INSERT INTO session_usage VALUES (?, ?, ?)",
            [("s1", "SENTINEL_PRIVATE", "hostile"), ("s2", 2, 1780000001)],
        )
        connection.commit()
        connection.close()
        with mock.patch.object(workbuddy, "_MAX_ROWS", 1):
            result = parse_workbuddy((self.path,))
        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))
        self.path.write_bytes(b"not sqlite SENTINEL_PRIVATE")
        corrupt = parse_workbuddy((self.path,))
        self.assertEqual(corrupt.status, AdapterStatus.ERROR)
        self.assertNotIn("SENTINEL_PRIVATE", repr(corrupt))
