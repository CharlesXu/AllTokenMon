import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from scripts.alltokenmon import cli
from scripts.alltokenmon.adapters import antigravity_cli
from scripts.alltokenmon.adapters.antigravity_cli import parse_antigravity_cli, scan
from scripts.alltokenmon.adapters.base import DiscoveryContext
from scripts.alltokenmon.adapters.registry import SOURCE_SPECS
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown


def _varint(value):
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        result.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(result)


def _v(field, value):
    return _varint(field << 3) + _varint(value)


def _l(field, value):
    return _varint((field << 3) | 2) + _varint(len(value)) + value


def _generation(response=b"resp-1"):
    usage = (
        _v(1, 1132) + _v(2, 500) + _v(5, 16000)
        + _v(9, 300) + _v(10, 40) + _l(11, response)
    )
    timestamp = _v(1, 1781000000) + _v(2, 250000000)
    chat = _l(4, usage) + _l(9, _l(4, timestamp)) + _l(19, b"gemini-3-flash-a")
    return _l(1, chat)


class AntigravityCliAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.path = (
            self.home / ".gemini/antigravity-cli/conversations/session-test.db"
        )
        self.path.parent.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def _db(self, columns="idx INTEGER, data BLOB"):
        connection = sqlite3.connect(str(self.path))
        connection.execute("CREATE TABLE gen_metadata ({})".format(columns))
        return connection

    def test_bounded_wire_tokens_timestamp_alias_dedup_and_discovery(self):
        connection = self._db()
        connection.executemany(
            "INSERT INTO gen_metadata VALUES (?, ?)",
            [(0, _generation()), (1, _generation())],
        )
        connection.commit()
        connection.close()
        before = hashlib.sha256(self.path.read_bytes()).digest()
        result = scan(
            DiscoveryContext("linux", self.home, {}),
            SOURCE_SPECS["antigravity-cli"],
        )
        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.provider, "google")
        self.assertEqual(record.model, "gemini-3-flash-a")
        self.assertEqual(record.session_id, "session-test")
        self.assertEqual(record.tokens, TokenBreakdown(1632, 300, 16000, 0, 40))
        self.assertEqual(record.dedup_key, "resp-1")
        self.assertEqual(record.timestamp.microsecond, 250000)
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).digest())

    def test_required_column_malformed_varint_oversize_hostile_and_unknown_wire(self):
        connection = self._db("idx INTEGER")
        connection.commit()
        connection.close()
        self.assertEqual(
            parse_antigravity_cli((self.path,)).status,
            AdapterStatus.UNSUPPORTED_FORMAT,
        )
        self.path.unlink()
        connection = self._db()
        connection.executemany(
            "INSERT INTO gen_metadata VALUES (?, ?)",
            [(0, b"\x80" * 11 + b"SENTINEL_PRIVATE"),
             (1, b"\x0bSENTINEL_PRIVATE"),
             (2, _generation(b"resp-good")),
             (3, b"x" * 300)],
        )
        connection.commit()
        connection.close()
        with mock.patch.object(antigravity_cli, "_MAX_PROTO_BYTES", 256):
            result = parse_antigravity_cli((self.path,))
        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(result.records[0].dedup_key, "resp-good")
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))
        self.path.write_bytes(b"not sqlite SENTINEL_PRIVATE")
        corrupt = parse_antigravity_cli((self.path,))
        self.assertEqual(corrupt.status, AdapterStatus.ERROR)
        self.assertNotIn("SENTINEL_PRIVATE", repr(corrupt))

    def test_protobuf_boundary_values_are_rejected_without_leaking(self):
        fixed_fields = list(
            antigravity_cli._Reader(
                b"\x09" + (b"\x00" * 8) + b"\x15" + (b"\x00" * 4)
            ).fields()
        )
        self.assertEqual(fixed_fields, [(1, 1, None), (2, 5, None)])

        malformed = (
            b"\x00",
            b"\x09\x00",
            b"\x15\x00",
            b"\xff" * 9 + b"\x02",
            b"\x0bSENTINEL_PRIVATE",
        )
        for payload in malformed:
            with self.subTest(payload=payload[:1]):
                with self.assertRaises(antigravity_cli._ProtoError) as caught:
                    list(antigravity_cli._Reader(payload).fields())
                self.assertNotIn("SENTINEL_PRIVATE", str(caught.exception))

        with self.assertRaises(antigravity_cli._ProtoError):
            antigravity_cli._field(b"", 1, 0, depth=5)
        self.assertIsNone(antigravity_cli._message(_v(1, 1), 1))
        self.assertIsNone(antigravity_cli._string(b"", 1))
        self.assertIsNone(antigravity_cli._string(_l(1, b"\xff"), 1))
        self.assertIsNone(antigravity_cli._timestamp(None))
        self.assertIsNone(
            antigravity_cli._timestamp(_v(1, 1) + _v(2, 1_000_000_000))
        )
        self.assertIsNone(antigravity_cli._timestamp(_v(2, 1)))
        self.assertIsNone(antigravity_cli._timestamp(_v(1, (1 << 63) - 1)))
        self.assertEqual(
            antigravity_cli._timestamp(_v(1, 1)).timestamp(),
            1,
        )
        self.assertEqual(
            antigravity_cli._mtime(self.home / "missing").timestamp(),
            0,
        )

        fallback = antigravity_cli._mtime(self.path)
        seen = set()
        self.assertIsNone(
            antigravity_cli._generation(self.path, "s", 1, b"", fallback, seen)
        )
        self.assertIsNone(
            antigravity_cli._generation(
                self.path, "s", 2, _l(1, b""), fallback, seen
            )
        )
        self.assertIsNone(
            antigravity_cli._generation(
                self.path, "s", 3, _l(1, _l(4, b"")), fallback, seen
            )
        )
        idless = antigravity_cli._generation(
            self.path,
            "s",
            4,
            _l(1, _l(4, _v(1, 5))),
            fallback,
            seen,
        )
        self.assertEqual(idless.model, "unknown")
        self.assertEqual(idless.tokens, TokenBreakdown(input=5))
        self.assertEqual(idless.dedup_key, "antigravity-cli:s:4")
        self.assertEqual(
            antigravity_cli._safe_path(self.home / "missing.db"),
            ((), False, False, True),
        )

    def test_trajectory_metadata_supplies_the_generation_fallback_timestamp(self):
        connection = self._db()
        connection.execute("CREATE TABLE trajectory_metadata_blob (data BLOB)")
        connection.execute(
            "INSERT INTO trajectory_metadata_blob VALUES (?)",
            (_l(2, _v(1, 123)),),
        )
        connection.execute(
            "INSERT INTO gen_metadata VALUES (?, ?)",
            (0, _l(1, _l(4, _v(1, 5)))),
        )
        connection.commit()
        connection.close()

        result = parse_antigravity_cli((self.path,))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(result.records[0].timestamp.timestamp(), 123)

    def test_cli_temp_home_reports_all_eight_sqlite_runtimes(self):
        kilo_path = self.home / ".local/share/kilo/kilo.db"
        kilo_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(kilo_path))
        connection.execute("CREATE TABLE message (id TEXT, session_id TEXT, data TEXT)")
        payload = {
            "id": "k1", "role": "assistant", "modelID": "gpt-5",
            "tokens": {"input": 1, "output": 1, "cache": {"read": 0, "write": 0}},
            "time": {"created": 1700000000000},
        }
        connection.execute("INSERT INTO message VALUES ('r', 's', ?)", (json.dumps(payload),))
        connection.commit()
        connection.close()

        crush_project = self.home / "project"
        crush_db = crush_project / ".crush/crush.db"
        crush_db.parent.mkdir(parents=True)
        connection = sqlite3.connect(str(crush_db))
        connection.execute(
            "CREATE TABLE sessions (id TEXT, parent_session_id TEXT, message_count INTEGER, "
            "cost REAL, updated_at INTEGER, created_at INTEGER)"
        )
        connection.execute(
            "CREATE TABLE messages (session_id TEXT, role TEXT, created_at INTEGER)"
        )
        connection.execute(
            "INSERT INTO sessions VALUES ('c', NULL, 1, 1.0, 1700000000, 1700000000)"
        )
        connection.commit()
        connection.close()
        crush_registry = self.home / ".local/share/crush/projects.json"
        crush_registry.parent.mkdir(parents=True)
        crush_registry.write_text(json.dumps({
            "projects": [{"path": str(crush_project), "data_dir": ".crush"}]
        }), encoding="utf-8")

        goose_path = self.home / ".local/share/goose/sessions/sessions.db"
        goose_path.parent.mkdir(parents=True)
        connection = sqlite3.connect(str(goose_path))
        connection.execute(
            "CREATE TABLE sessions (id TEXT, model_config_json TEXT, provider_name TEXT, "
            "created_at TEXT, total_tokens INTEGER, input_tokens INTEGER, "
            "output_tokens INTEGER, accumulated_total_tokens INTEGER, "
            "accumulated_input_tokens INTEGER, accumulated_output_tokens INTEGER)"
        )
        connection.execute(
            "INSERT INTO sessions VALUES ('g', ?, 'openai', '2026-01-01T00:00:00Z', "
            "2, 1, 1, NULL, NULL, NULL)",
            (json.dumps({"model_name": "gpt-5"}),),
        )
        connection.commit()
        connection.close()

        zed_path = self.home / ".local/share/zed/threads/threads.db"
        zed_path.parent.mkdir(parents=True)
        connection = sqlite3.connect(str(zed_path))
        connection.execute(
            "CREATE TABLE threads (id TEXT, updated_at TEXT, data_type TEXT, data BLOB)"
        )
        connection.execute(
            "INSERT INTO threads VALUES ('z', '2026-01-01T00:00:00Z', 'json', ?)",
            (json.dumps({
                "model": {"provider": "zed.dev", "model": "gpt-5"},
                "cumulative_token_usage": {"input_tokens": 1, "output_tokens": 1},
            }),),
        )
        connection.commit()
        connection.close()

        micode_path = self.home / ".local/share/mimocode/mimocode.db"
        micode_path.parent.mkdir(parents=True)
        connection = sqlite3.connect(str(micode_path))
        connection.execute("CREATE TABLE message (id TEXT, session_id TEXT, data TEXT)")
        connection.execute("INSERT INTO message VALUES ('m', 's', ?)", (json.dumps({
            "id": "m1", "role": "assistant", "modelID": "mimo-v2.5-pro",
            "providerID": "mimo", "tokens": {"input": 1, "output": 1},
            "time": {"created": 1700000000000},
        }),))
        connection.commit()
        connection.close()

        antigravity_path = (
            self.home / ".gemini/antigravity-cli/conversations/a.db"
        )
        antigravity_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(antigravity_path))
        connection.execute("CREATE TABLE gen_metadata (idx INTEGER, data BLOB)")
        connection.execute("INSERT INTO gen_metadata VALUES (0, ?)", (_generation(b"a1"),))
        connection.commit()
        connection.close()

        workbuddy_path = self.home / ".workbuddy/workbuddy.db"
        workbuddy_path.parent.mkdir(parents=True)
        connection = sqlite3.connect(str(workbuddy_path))
        connection.execute("CREATE TABLE sessions (id TEXT, model TEXT)")
        connection.execute(
            "CREATE TABLE session_usage (session_id TEXT, used INTEGER, updated_at INTEGER)"
        )
        connection.execute("INSERT INTO sessions VALUES ('w', 'deepseek-v4-pro')")
        connection.execute("INSERT INTO session_usage VALUES ('w', 1, 1780000000)")
        connection.commit()
        connection.close()

        devin_path = self.home / ".local/share/devin/cli/sessions.db"
        devin_path.parent.mkdir(parents=True)
        connection = sqlite3.connect(str(devin_path))
        connection.execute("CREATE TABLE sessions (id TEXT, model TEXT)")
        connection.execute(
            "CREATE TABLE message_nodes "
            "(row_id TEXT, session_id TEXT, chat_message TEXT, created_at INTEGER)"
        )
        connection.execute("INSERT INTO sessions VALUES ('d', 'gpt-5')")
        connection.execute("INSERT INTO message_nodes VALUES ('r', 'd', ?, 1780000000)", (
            json.dumps({"role": "assistant", "metadata": {"num_tokens": 1}}),
        ))
        connection.commit()
        connection.close()

        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            return_code = cli.main((
                "--runtime",
                "kilo,crush,goose,zed,micode,antigravity-cli,workbuddy,devin-cli",
                "--home", str(self.home), "--format", "json",
                "--now", "2027-01-01T00:00:00+00:00",
            ))
        report = json.loads(stdout.getvalue())
        self.assertEqual(return_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(report["coverage"]["runtime_count"], 8)
        self.assertEqual(report["coverage"]["status_counts"], {"ok": 8})
        self.assertEqual(
            {row["runtime"] for row in report["periods"]["all_time"]["runtimes"]},
            {
                "kilo", "crush", "goose", "zed", "micode",
                "antigravity-cli", "workbuddy", "devin-cli",
            },
        )
        self.assertGreater(report["periods"]["all_time"]["totals"]["total"], 0)
