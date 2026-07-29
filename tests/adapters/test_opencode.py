import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from scripts.alltokenmon import cli
from scripts.alltokenmon.adapters import opencode as opencode_adapter
from scripts.alltokenmon.adapters.base import DiscoveryContext
from scripts.alltokenmon.adapters.opencode import parse_opencode, scan
from scripts.alltokenmon.adapters.registry import SOURCE_SPECS
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown


FIXTURES = Path(__file__).parent / "fixtures" / "opencode"


def _create_db(path, payload, *, table="message", row_id="row-1", session_id="db-session"):
    connection = sqlite3.connect(str(path))
    try:
        if table == "message":
            connection.execute(
                "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, data TEXT)"
            )
            connection.execute(
                "INSERT INTO message VALUES (?, ?, ?)",
                (row_id, session_id, json.dumps(payload)),
            )
        else:
            connection.execute(
                "CREATE TABLE session_message "
                "(id TEXT PRIMARY KEY, session_id TEXT, type TEXT, data TEXT)"
            )
            connection.execute(
                "INSERT INTO session_message VALUES (?, ?, 'assistant', ?)",
                (row_id, session_id, json.dumps(payload)),
            )
        connection.commit()
    finally:
        connection.close()


class OpenCodeAdapterTests(unittest.TestCase):
    def test_sqlite_precedes_legacy_and_explicit_id_prevents_double_count(self):
        payload = json.loads((FIXTURES / "legacy-message.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "opencode.db"
            _create_db(db, payload)
            result = parse_opencode((FIXTURES / "legacy-message.json", db))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.source_kind, "sqlite")
        self.assertEqual(record.session_id, "db-session")
        self.assertEqual(record.provider, "anthropic")
        self.assertEqual(record.model, "claude-sonnet-4")
        self.assertEqual(record.timestamp.isoformat(), "2026-07-12T18:51:19.705000+00:00")
        self.assertEqual(
            record.tokens,
            TokenBreakdown(
                input=100,
                output=20,
                cache_read=10,
                cache_write=5,
                reasoning=3,
            ),
        )
        self.assertEqual(record.cost, 0.0123)
        self.assertEqual(record.cost_source, "provider_reported")
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_v2_nested_model_and_distinct_embedded_ids(self):
        base = {
            "time": {"created": 1783882279705},
            "model": {"id": "gpt-5.2", "providerID": "openai-codex"},
            "tokens": {
                "input": 5,
                "output": 2,
                "reasoning": 1,
                "cache": {"read": 3, "write": 4},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "opencode-stable.db"
            connection = sqlite3.connect(str(db))
            try:
                connection.execute(
                    "CREATE TABLE session_message "
                    "(id TEXT PRIMARY KEY, session_id TEXT, type TEXT, data TEXT)"
                )
                for message_id in ("one", "two", "one"):
                    payload = dict(base, id=message_id)
                    connection.execute(
                        "INSERT INTO session_message VALUES (?, ?, ?, ?)",
                        (
                            "row-" + message_id + str(connection.total_changes),
                            "session-" + message_id,
                            "assistant",
                            json.dumps(payload),
                        ),
                    )
                connection.commit()
            finally:
                connection.close()
            result = parse_opencode((db,))

        self.assertEqual(len(result.records), 2)
        self.assertEqual({record.provider for record in result.records}, {"openai"})
        self.assertEqual({record.dedup_key for record in result.records}, {
            "opencode:message:one",
            "opencode:message:two",
        })

    def test_fallback_row_promotes_first_explicit_id_and_keeps_distinct_second(self):
        base = {
            "time": {"created": 1783882279705},
            "model": {"id": "gpt-5.2", "providerID": "openai-codex"},
            "tokens": {
                "input": 5,
                "output": 2,
                "reasoning": 1,
                "cache": {"read": 3, "write": 4},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "opencode.db"
            connection = sqlite3.connect(str(db))
            try:
                connection.execute(
                    "CREATE TABLE session_message "
                    "(id TEXT PRIMARY KEY, session_id TEXT, type TEXT, data TEXT)"
                )
                for row_id, message_id in (
                    ("row-a", None),
                    ("row-b", "message-one"),
                    ("row-c", "message-two"),
                ):
                    payload = dict(base)
                    if message_id is not None:
                        payload["id"] = message_id
                    connection.execute(
                        "INSERT INTO session_message VALUES (?, ?, 'assistant', ?)",
                        (row_id, "session", json.dumps(payload)),
                    )
                connection.commit()
            finally:
                connection.close()
            result = parse_opencode((db,))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(
            {record.dedup_key for record in result.records},
            {
                "opencode:message:message-one",
                "opencode:message:message-two",
            },
        )

    def test_idless_sqlite_fingerprint_includes_completed_time_bits(self):
        base = {
            "time": {"created": 1783882279705, "completed": 1783882279800},
            "model": {"id": "gpt-5.2", "providerID": "openai-codex"},
            "tokens": {
                "input": 5,
                "output": 2,
                "reasoning": 1,
                "cache": {"read": 3, "write": 4},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "opencode.db"
            connection = sqlite3.connect(str(db))
            try:
                connection.execute(
                    "CREATE TABLE session_message "
                    "(id TEXT PRIMARY KEY, session_id TEXT, type TEXT, data TEXT)"
                )
                for row_id, completed in (
                    ("row-a", 1783882279800),
                    ("row-a-copy", 1783882279800),
                    ("row-b", 1783882279900),
                ):
                    payload = dict(base)
                    payload["time"] = dict(base["time"], completed=completed)
                    connection.execute(
                        "INSERT INTO session_message VALUES (?, ?, 'assistant', ?)",
                        (row_id, "session", json.dumps(payload)),
                    )
                connection.commit()
            finally:
                connection.close()
            result = parse_opencode((db,))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 2)
        self.assertEqual(
            {record.dedup_key for record in result.records},
            {
                "opencode:row:row-a:agent:",
                "opencode:row:row-b:agent:",
            },
        )

    def test_sqlite_stops_before_cumulative_payload_limit(self):
        payloads = []
        for row_id, completed in (
            ("row-a", 1783882279800),
            ("row-b", 1783882279900),
        ):
            payloads.append(
                (
                    row_id,
                    json.dumps(
                        {
                            "time": {
                                "created": 1783882279705,
                                "completed": completed,
                            },
                            "model": {
                                "id": "gpt-5.2",
                                "providerID": "openai-codex",
                            },
                            "tokens": {
                                "input": 5,
                                "output": 2,
                                "reasoning": 1,
                                "cache": {"read": 3, "write": 4},
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
        byte_lengths = [
            len(payload.encode("utf-8")) for _, payload in payloads
        ]
        cumulative_limit = max(byte_lengths) + 1

        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "opencode.db"
            connection = sqlite3.connect(str(db))
            try:
                connection.execute(
                    "CREATE TABLE session_message "
                    "(id TEXT PRIMARY KEY, session_id TEXT, type TEXT, data TEXT)"
                )
                connection.executemany(
                    "INSERT INTO session_message VALUES (?, 'session', "
                    "'assistant', ?)",
                    payloads,
                )
                connection.commit()
            finally:
                connection.close()
            with mock.patch(
                "scripts.alltokenmon.adapters.opencode.MAX_JSON_BYTES",
                cumulative_limit,
            ):
                result = parse_opencode((db,))

        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(result.diagnostics[0].code, "resource_limit")
        self.assertEqual(len(result.records), 1)
        self.assertEqual(
            result.records[0].dedup_key,
            "opencode:row:row-a:agent:",
        )

    def test_sqlite_record_limit_stops_before_extra_payload(self):
        payload = json.dumps(
            {
                "time": {"created": 1783882279705},
                "model": {"id": "gpt-5.2", "providerID": "openai-codex"},
                "tokens": {
                    "input": 5,
                    "output": 2,
                    "cache": {"read": 3, "write": 4},
                },
            },
            separators=(",", ":"),
        )
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "opencode.db"
            connection = sqlite3.connect(str(db))
            try:
                connection.execute(
                    "CREATE TABLE session_message "
                    "(id TEXT PRIMARY KEY, session_id TEXT, type TEXT, data TEXT)"
                )
                connection.executemany(
                    "INSERT INTO session_message VALUES (?, 'session', "
                    "'assistant', ?)",
                    (("row-a", payload), ("row-b", payload)),
                )
                connection.commit()
            finally:
                connection.close()
            with mock.patch(
                "scripts.alltokenmon.adapters.opencode._MAX_ROWS", 1
            ):
                result = parse_opencode((db,))

        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(result.diagnostics[0].code, "resource_limit")
        self.assertEqual(len(result.records), 1)

    def test_sqlite_never_selects_oversized_data_cell(self):
        small_payload = json.dumps(
            {
                "time": {"created": 1783882279705},
                "model": {"id": "gpt-5.2", "providerID": "openai-codex"},
                "tokens": {
                    "input": 5,
                    "output": 2,
                    "cache": {"read": 3, "write": 4},
                },
            },
            separators=(",", ":"),
        )
        byte_limit = len(small_payload.encode("utf-8")) + 1
        oversized_payload = small_payload + (" " * byte_limit)

        class TrackingConnection:
            def __init__(self, connection):
                self.connection = connection
                self.data_fetches = []

            def execute(self, query, parameters=()):
                if query.lstrip().startswith('SELECT "data"'):
                    self.data_fetches.append(parameters)
                return self.connection.execute(query, parameters)

            def close(self):
                self.connection.close()

        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "opencode.db"
            connection = sqlite3.connect(str(db))
            try:
                connection.execute(
                    "CREATE TABLE session_message "
                    "(id TEXT PRIMARY KEY, session_id TEXT, type TEXT, data TEXT)"
                )
                connection.executemany(
                    "INSERT INTO session_message VALUES (?, 'session', "
                    "'assistant', ?)",
                    (
                        ("row-a", small_payload),
                        ("row-b", oversized_payload),
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            tracking = TrackingConnection(sqlite3.connect(str(db)))
            with mock.patch.object(
                opencode_adapter,
                "open_sqlite_readonly",
                return_value=tracking,
            ), mock.patch.object(
                opencode_adapter, "MAX_JSON_BYTES", byte_limit
            ):
                result = parse_opencode((db,))

        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(result.diagnostics[0].code, "resource_limit")
        self.assertEqual(len(result.records), 1)
        self.assertEqual(len(tracking.data_fetches), 1)

    def test_statuses_partial_and_scan(self):
        self.assertEqual(
            parse_opencode((FIXTURES / "missing.json",)).status,
            AdapterStatus.NO_DATA,
        )
        self.assertEqual(
            parse_opencode((FIXTURES / "unsupported.json",)).status,
            AdapterStatus.UNSUPPORTED_FORMAT,
        )
        with tempfile.TemporaryDirectory() as home_text:
            home = Path(home_text)
            target = home / ".local/share/opencode/storage/message/safe"
            target.mkdir(parents=True)
            (target / "message.json").write_bytes(
                (FIXTURES / "legacy-message.json").read_bytes()
            )
            result = scan(
                DiscoveryContext("linux", home, {}),
                SOURCE_SPECS["opencode"],
            )
        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(result.records[0].tokens.total, 135)

    def test_malformed_sibling_retains_usage_as_partial_without_leaking_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "a-valid.json"
            valid.write_bytes((FIXTURES / "legacy-message.json").read_bytes())
            malformed = root / "z-malformed.json"
            malformed.write_text(
                '{"content":"SENTINEL_PRIVATE_TRUNCATED"',
                encoding="utf-8",
            )
            result = parse_opencode((malformed, valid))

        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(len(result.records), 1)
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_cli_smoke_reports_all_four_core_runtimes_with_nonzero_usage(self):
        with tempfile.TemporaryDirectory() as home_text:
            home = Path(home_text)

            opencode_messages = home / ".local/share/opencode/storage/message/safe"
            opencode_messages.mkdir(parents=True)
            (opencode_messages / "message.json").write_bytes(
                (FIXTURES / "legacy-message.json").read_bytes()
            )

            gemini_chats = home / ".gemini/tmp/hash/chats"
            gemini_chats.mkdir(parents=True)
            gemini_fixture = (
                Path(__file__).parent / "fixtures" / "gemini" / "conversation.json"
            )
            (gemini_chats / "conversation.json").write_bytes(
                gemini_fixture.read_bytes()
            )

            openclaw_sessions = home / ".openclaw/agents/main/sessions"
            openclaw_sessions.mkdir(parents=True)
            openclaw_fixture = (
                Path(__file__).parent / "fixtures" / "openclaw" / "session.jsonl"
            )
            (openclaw_sessions / "session.jsonl").write_bytes(
                openclaw_fixture.read_bytes()
            )

            hermes_home = home / ".hermes"
            hermes_home.mkdir()
            connection = sqlite3.connect(str(hermes_home / "state.db"))
            try:
                connection.execute(
                    "CREATE TABLE sessions (id TEXT PRIMARY KEY, model TEXT, "
                    "started_at REAL, message_count INTEGER, input_tokens INTEGER, "
                    "output_tokens INTEGER, cache_read_tokens INTEGER, "
                    "cache_write_tokens INTEGER, reasoning_tokens INTEGER, "
                    "billing_provider TEXT, estimated_cost_usd REAL, actual_cost_usd REAL)"
                )
                connection.execute(
                    "INSERT INTO sessions VALUES "
                    "('cli-hermes','gpt-5.4',1750000100,1,3,2,1,0,0,"
                    "'openai',NULL,NULL)"
                )
                connection.commit()
            finally:
                connection.close()

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                return_code = cli.main(
                    (
                        "--runtime",
                        "opencode,hermes,gemini,openclaw",
                        "--home",
                        str(home),
                        "--format",
                        "json",
                        "--now",
                        "2026-07-28T12:00:00+08:00",
                    )
                )

        report = json.loads(stdout.getvalue())
        self.assertEqual(return_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(report["coverage"]["runtime_count"], 4)
        self.assertEqual(report["coverage"]["status_counts"], {"ok": 4})
        self.assertGreater(report["coverage"]["record_count"], 0)
        self.assertGreater(report["periods"]["all_time"]["totals"]["total"], 0)
        self.assertEqual(
            {row["runtime"] for row in report["periods"]["all_time"]["runtimes"]},
            {"opencode", "hermes", "gemini", "openclaw"},
        )


if __name__ == "__main__":
    unittest.main()
