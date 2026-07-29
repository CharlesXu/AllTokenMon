import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.alltokenmon.adapters import copilot
from scripts.alltokenmon.adapters.copilot import parse_copilot
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown


class CopilotAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _desktop_db(self, session_id="session-1"):
        path = self.root / ".copilot/data.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(path))
        connection.execute(
            "CREATE TABLE sessions ("
            "id TEXT, title TEXT, model TEXT, total_input_tokens INTEGER, "
            "total_output_tokens INTEGER, total_cached_tokens INTEGER, "
            "total_reasoning_tokens INTEGER, total_nano_aiu INTEGER, created_at TEXT)"
        )
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                "SENTINEL_PRIVATE",
                "gpt-5",
                100,
                20,
                30,
                4,
                0,
                "2026-07-28T10:00:00Z",
            ),
        )
        connection.commit()
        connection.close()
        return path

    def test_otel_precedes_vscode_but_does_not_erase_desktop_aggregate(self):
        desktop = self._desktop_db()
        otel = self.root / "copilot-otel.jsonl"
        otel.write_text(
            json.dumps(
                {
                    "type": "span",
                    "name": "chat gpt-5",
                    "traceId": "trace-1",
                    "spanId": "span-1",
                    "startTime": "2026-07-28T10:00:00Z",
                    "attributes": {
                        "gen_ai.operation.name": "chat",
                        "gen_ai.response.id": "response-1",
                        "gen_ai.conversation.id": "session-1",
                        "gen_ai.response.model": "gpt-5",
                        "gen_ai.usage.input_tokens": 90,
                        "gen_ai.usage.output_tokens": 25,
                        "gen_ai.usage.cache_read.input_tokens": 40,
                        "prompt": "SENTINEL_PRIVATE",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        vscode = self.root / "workspaceStorage/hash/chatSessions/session-1.jsonl"
        vscode.parent.mkdir(parents=True)
        vscode.write_text(
            json.dumps(
                {
                    "kind": 2,
                    "k": ["requests"],
                    "v": [
                        {
                            "requestId": "response-1",
                            "timestamp": 1785232800000,
                            "modelId": "copilot/gpt-5",
                            "promptTokens": 999,
                            "completionTokens": 999,
                            "prompt": "SENTINEL_PRIVATE",
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = parse_copilot((desktop, vscode, otel))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 2)
        record = next(
            item for item in result.records if item.source_kind == "otel_jsonl"
        )
        self.assertEqual(record.source_kind, "otel_jsonl")
        self.assertEqual(record.dedup_key, "copilot:response:response-1")
        self.assertEqual(record.tokens, TokenBreakdown(50, 25, 40, 0))
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_otel_array_clock_and_end_duration_are_back_anchored(self):
        path = self.root / "otel.jsonl"
        rows = (
            {
                "type": "span",
                "name": "chat one",
                "traceId": "trace-a",
                "spanId": "span-a",
                "startTime": [1785232800, 500_000_000],
                "attributes": {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.usage.input_tokens": 1,
                },
            },
            {
                "type": "span",
                "name": "chat huge duration",
                "traceId": "trace-d",
                "spanId": "span-d",
                "endTime": [1785232830, 0],
                "duration": [10**30, 0],
                "attributes": {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.usage.input_tokens": 4,
                },
            },
            {
                "type": "span",
                "name": "chat three",
                "traceId": "trace-c",
                "spanId": "span-c",
                "hrTime": [1785232820, 250_000_000],
                "attributes": {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.usage.input_tokens": 3,
                },
            },
            {
                "type": "span",
                "name": "chat two",
                "traceId": "trace-b",
                "spanId": "span-b",
                "endTime": [1785232810, 0],
                "duration": [2, 0],
                "attributes": {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.usage.input_tokens": 2,
                },
            },
        )
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )

        result = parse_copilot((path,))

        self.assertEqual(
            [record.timestamp.timestamp() for record in result.records],
            [1785232800.5, 1785232808.0, 1785232820.25, 1785232830.0],
        )

    def test_same_timestamp_and_counts_do_not_fuzzy_deduplicate(self):
        path = self.root / "chatSessions/session.jsonl"
        path.parent.mkdir()
        requests = [
            {
                "requestId": request_id,
                "timestamp": 1785232800000,
                "modelId": "copilot/gpt-5",
                "promptTokens": 10,
                "completionTokens": 5,
            }
            for request_id in ("request-a", "request-b")
        ]
        path.write_text(
            json.dumps({"kind": 0, "v": {"requests": requests}}) + "\n",
            encoding="utf-8",
        )

        result = parse_copilot((path,))

        self.assertEqual(len(result.records), 2)
        self.assertEqual(
            {record.dedup_key for record in result.records},
            {
                "copilot:response:request-a",
                "copilot:response:request-b",
            },
        )

    def test_chat_lane_suppresses_lower_lane_on_exact_trace(self):
        path = self.root / "otel.jsonl"
        context = {
            "type": "span",
            "name": "context",
            "traceId": "shared-trace",
            "spanId": "context-span",
            "attributes": {
                "gen_ai.response.model": "gpt-5",
                "gen_ai.conversation.id": "shared-session",
            },
        }
        inference = {
            "traceId": "shared-trace",
            "attributes": {
                "event.name": "gen_ai.client.inference.operation.details",
                "gen_ai.usage.input_tokens": 999,
                "gen_ai.usage.output_tokens": 999,
            },
        }
        chat = {
            "type": "span",
            "name": "chat gpt-5",
            "traceId": "shared-trace",
            "spanId": "chat-span",
            "startTime": "2026-07-28T10:00:00Z",
            "attributes": {
                "gen_ai.operation.name": "chat",
                "gen_ai.usage.input_tokens": 10,
                "gen_ai.usage.output_tokens": 5,
            },
        }
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in (context, inference, chat)),
            encoding="utf-8",
        )

        result = parse_copilot((path,))

        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].session_id, "shared-session")
        self.assertEqual(result.records[0].model, "gpt-5")
        self.assertEqual(result.records[0].tokens, TokenBreakdown(10, 5))

    def test_desktop_database_is_read_only_and_normalizes_cached_input(self):
        path = self._desktop_db("desktop-only")
        events = path.parent / "session-state/desktop-only/events.jsonl"
        events.parent.mkdir(parents=True)
        events.write_text(
            json.dumps(
                {
                    "type": "session.model_change",
                    "data": {
                        "newModel": "gpt-5.3-codex",
                        "secret": "SENTINEL_PRIVATE",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with events.open("a", encoding="utf-8") as stream:
            stream.write("{broken\n")
        before = hashlib.sha256(path.read_bytes()).digest()

        result = parse_copilot((path,))

        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(result.records[0].tokens, TokenBreakdown(70, 20, 30, 0, 4))
        self.assertEqual(result.records[0].model, "gpt-5.3-codex")
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).digest())

    def test_desktop_session_state_rejects_traversal_and_symlink_escape(self):
        external = self.root / "external"
        external.mkdir()
        (external / "events.jsonl").write_text(
            json.dumps(
                {
                    "type": "session.model_change",
                    "data": {"newModel": "SENTINEL_PRIVATE"},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        path = self._desktop_db("../external")
        result = parse_copilot((path,))
        self.assertEqual(result.records[0].model, "gpt-5")
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

        path.unlink()
        path = self._desktop_db(str(external))
        absolute_result = parse_copilot((path,))
        self.assertEqual(absolute_result.records[0].model, "gpt-5")
        self.assertNotIn("SENTINEL_PRIVATE", repr(absolute_result))

        path.unlink()
        path = self._desktop_db("linked")
        state = path.parent / "session-state"
        state.mkdir(exist_ok=True)
        try:
            (state / "linked").symlink_to(external, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable")
        linked_result = parse_copilot((path,))
        self.assertEqual(linked_result.records[0].model, "gpt-5")
        self.assertNotIn("SENTINEL_PRIVATE", repr(linked_result))

    def test_scalar_otel_timestamp_magnitudes_share_the_same_instant(self):
        path = self.root / "scalar.jsonl"
        values = (
            1_785_232_800,
            1_785_232_800_000,
            1_785_232_800_000_000,
            1_785_232_800_000_000_000,
        )
        rows = [
            {
                "type": "span",
                "name": "chat scalar",
                "traceId": "trace-{}".format(index),
                "spanId": "span-{}".format(index),
                "timestamp": value,
                "attributes": {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.usage.input_tokens": index + 1,
                },
            }
            for index, value in enumerate(values)
        ]
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )

        result = parse_copilot((path,))

        self.assertEqual(
            {record.timestamp.timestamp() for record in result.records},
            {1785232800.0},
        )

    def test_nested_vscode_requests_are_capped_and_fixture_is_executable(self):
        path = self.root / "chatSessions/session.jsonl"
        path.parent.mkdir()
        requests = [
            {
                "requestId": "request-{}".format(index),
                "timestamp": 1785232800000 + index,
                "modelId": "copilot/gpt-5",
                "promptTokens": 1,
            }
            for index in range(3)
        ]
        path.write_text(
            json.dumps({"kind": 0, "v": {"requests": requests}}) + "\n",
            encoding="utf-8",
        )
        with mock.patch.object(copilot, "_MAX_ROWS", 2):
            result = parse_copilot((path,))
        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(len(result.records), 2)

        fixture = Path(__file__).parents[1] / "fixtures/copilot/otel.jsonl"
        self.assertEqual(parse_copilot((fixture,)).status, AdapterStatus.OK)


if __name__ == "__main__":
    unittest.main()
