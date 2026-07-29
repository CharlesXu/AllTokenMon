import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.alltokenmon.adapters import kiro
from scripts.alltokenmon.adapters.kiro import parse_kiro
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown


class KiroAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _cli(self, session_id="session-1"):
        path = self.root / ".kiro/sessions/cli/session.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "cwd": "/SENTINEL_PRIVATE",
                    "session_state": {
                        "rts_model_state": {
                            "model_info": {
                                "model_id": "claude-sonnet-4-5",
                                "context_window_tokens": 200000,
                            }
                        },
                        "conversation_metadata": {
                            "user_turn_metadatas": [
                                {
                                    "input_token_count": 30,
                                    "output_token_count": 10,
                                    "end_timestamp": 1785232801,
                                    "total_request_count": 1,
                                    "message_ids": ["answer-1"],
                                }
                            ]
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        path.with_suffix(".jsonl").write_text(
            json.dumps(
                {
                    "kind": "Prompt",
                    "data": {
                        "message_id": "prompt-1",
                        "content": [{"kind": "text", "data": "SENTINEL_PRIVATE"}],
                        "meta": {"timestamp": 1785232800},
                    },
                }
            )
            + "\n"
            + json.dumps(
                {
                    "kind": "AssistantMessage",
                    "data": {
                        "message_id": "answer-1",
                        "content": [{"kind": "text", "data": "private response"}],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def _sqlite(self, session_id="session-1"):
        path = self.root / "data.sqlite3"
        connection = sqlite3.connect(str(path))
        connection.execute(
            "CREATE TABLE conversations_v2 (key TEXT, conversation_id TEXT, value TEXT)"
        )
        value = {
            "model_info": {
                "model_id": "claude-sonnet-4-5",
                "context_window_tokens": 200000,
            },
            "history": [
                {
                    "request_metadata": {
                        "context_usage_percentage": 10,
                        "response_size": 40,
                        "request_start_timestamp_ms": 1785232800000,
                        "stream_end_timestamp_ms": 1785232801000,
                    },
                    "prompt": "SENTINEL_PRIVATE",
                }
            ],
        }
        connection.execute(
            "INSERT INTO conversations_v2 VALUES (?, ?, ?)",
            ("/SENTINEL_PRIVATE", session_id, json.dumps(value)),
        )
        connection.commit()
        connection.close()
        return path

    def test_cli_precedes_sqlite_for_same_explicit_session_turn(self):
        cli = self._cli()
        database = self._sqlite()
        before = hashlib.sha256(database.read_bytes()).digest()

        result = parse_kiro((database, cli, cli.with_suffix(".jsonl")))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.source_kind, "kiro_cli")
        self.assertEqual(record.dedup_key, "kiro:turn:session-1:0")
        self.assertEqual(record.tokens, TokenBreakdown(30, 10))
        self.assertEqual(record.confidence, "exact")
        self.assertEqual(before, hashlib.sha256(database.read_bytes()).digest())
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_execution_precedes_snapshot_only_on_shared_execution_id(self):
        snapshot = self.root / "globalStorage/kiro.kiroagent/ws/chat.chat"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text(
            json.dumps(
                {
                    "executionId": "exec-1",
                    "messages": [
                        {"role": "user", "content": "SENTINEL_PRIVATE"},
                        {"role": "assistant", "content": "private response"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        execution = snapshot.with_name("execution.json")
        execution.write_text(
            json.dumps(
                {
                    "executionId": "exec-1",
                    "chatSessionId": "chat-1",
                    "status": "succeed",
                    "startTime": 1785232800,
                    "actions": [
                        {"actionType": "say", "output": {"message": "abcdefgh"}}
                    ],
                    "context": {
                        "messages": [
                            {"entries": [{"type": "text", "text": "abcdefghijkl"}]}
                        ]
                    },
                    "modelId": "claude-sonnet-4-5",
                    "secret": "SENTINEL_PRIVATE",
                }
            ),
            encoding="utf-8",
        )

        result = parse_kiro((snapshot, execution))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].source_kind, "kiro_execution")
        self.assertTrue(result.records[0].dedup_key.endswith(":exec-1"))
        self.assertEqual(result.records[0].tokens, TokenBreakdown(3, 2))
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_equal_timestamp_and_counts_with_distinct_ids_are_retained(self):
        first = self.root / "exec-a.json"
        second = self.root / "exec-b.json"
        for path, identity in ((first, "exec-a"), (second, "exec-b")):
            path.write_text(
                json.dumps(
                    {
                        "executionId": identity,
                        "status": "succeed",
                        "startTime": 1785232800,
                        "actions": [{"actionType": "say", "output": "abcdefgh"}],
                        "context": {
                            "messages": [
                                {"entries": [{"type": "text", "text": "abcdefgh"}]}
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )

        result = parse_kiro((first, second))

        self.assertEqual(len(result.records), 2)
        self.assertEqual(
            {record.dedup_key for record in result.records},
            {
                "kiro:execution:global:exec-a",
                "kiro:execution:global:exec-b",
            },
        )

    def test_execution_identity_is_workspace_scoped_and_requires_success(self):
        paths = []
        for workspace, status in (("one", "succeed"), ("two", "succeed"), ("bad", "failed")):
            path = (
                self.root
                / "globalStorage/kiro.kiroagent"
                / workspace
                / "execution.json"
            )
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "executionId": "shared",
                        "status": status,
                        "actions": [{"actionType": "say", "output": "abcdefgh"}],
                        "context": {
                            "messages": [
                                {"entries": [{"type": "text", "text": "abcdefgh"}]}
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            paths.append(path)

        result = parse_kiro(tuple(paths))

        self.assertEqual(len(result.records), 2)
        self.assertEqual(len({record.dedup_key for record in result.records}), 2)

    def test_structured_ide_turn_uses_metadata_tool_args_elapsed_and_model(self):
        session = self.root / ".kiro/sessions/ws/sess_123/session.json"
        session.parent.mkdir(parents=True)
        session.write_text(json.dumps({"id": "session-ide"}), encoding="utf-8")
        rows = (
            {"timestamp": "2026-07-28T10:00:00Z", "payload": {"type": "user", "content": "abcd"}},
            {"payload": {"type": "assistant", "content": "abcdefgh"}},
            {"payload": {"type": "tool_call", "args": "abcd", "modelId": "claude-4"}},
            {"payload": {"type": "session_metadata", "key": "contextUsage", "value": {"usagePercentage": 10}}},
            {"payload": {"type": "usage_summary", "elapsedTime": 2000}},
            {"timestamp": "2026-07-28T10:00:02Z", "payload": {"type": "turn_end"}},
        )
        session.with_name("messages.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )

        result = parse_kiro((session, session.with_name("messages.jsonl")))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.model, "claude-4")
        self.assertEqual(record.tokens, TokenBreakdown(20_000, 3))
        self.assertEqual(record.timestamp.isoformat(), "2026-07-28T10:00:00+00:00")

    def test_workspace_history_and_index_file_formats(self):
        path = self.root / "globalStorage/kiro.kiroagent/ws/session.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "sessionId": "workspace-session",
                    "selectedModel": "claude-4",
                    "history": [
                        {
                            "promptLogs": [
                                {"prompt": "abcdefgh", "response": "ignored"}
                            ],
                            "message": {"role": "assistant", "content": "abcd"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        index = path.with_name("index.json")
        index.write_text(
            json.dumps({"version": 1, "executions": ["SENTINEL_PRIVATE"]}),
            encoding="utf-8",
        )

        result = parse_kiro((path, index))

        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].tokens, TokenBreakdown(2, 1))
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_empty_cli_session_and_prompt_response_snapshot_are_supported(self):
        empty = self.root / ".kiro/sessions/cli/empty.json"
        empty.parent.mkdir(parents=True)
        empty.write_text(
            json.dumps(
                {
                    "session_id": "empty",
                    "session_state": {"conversation_metadata": {}},
                }
            ),
            encoding="utf-8",
        )
        snapshot = (
            self.root
            / "globalStorage/kiro.kiroagent/ws/aliases.chat"
        )
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text(
            json.dumps(
                {
                    "prompt": {"role": "user", "text": "abcdefgh"},
                    "response": {"role": "assistant", "text": "abcd"},
                    "completionOptions": {"modelId": "claude-top-level"},
                }
            ),
            encoding="utf-8",
        )

        result = parse_kiro((empty, snapshot))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].tokens, TokenBreakdown(2, 1))
        self.assertEqual(result.records[0].model, "claude-top-level")

        nested = snapshot.with_name("nested.chat")
        nested.write_text(
            json.dumps(
                {
                    "prompt": {
                        "role": "user",
                        "content": "abcd",
                        "parts": [{"model_id": "claude-sonnet-4-5"}],
                    },
                    "response": {"role": "assistant", "content": "abcd"},
                    "secret": {"model": "MODEL_SENTINEL_DO_NOT_LEAK"},
                    "metadata": {"model": "METADATA_MODEL_SENTINEL"},
                    "modelId": "qdev",
                    "context": {
                        "completionOptions": {"modelId": "agent"}
                    },
                }
            ),
            encoding="utf-8",
        )
        nested_result = parse_kiro((nested,))
        self.assertEqual(
            nested_result.records[0].model, "claude-sonnet-4-5"
        )
        self.assertNotIn("MODEL_SENTINEL_DO_NOT_LEAK", repr(nested_result))
        self.assertNotIn("METADATA_MODEL_SENTINEL", repr(nested_result))

        duplicate = snapshot.with_name("duplicate.chat")
        duplicate.write_text(
            json.dumps(
                {"role": "user", "prompt": "abcd", "content": "abcd"}
            ),
            encoding="utf-8",
        )
        duplicate_result = parse_kiro((duplicate,))
        self.assertEqual(duplicate_result.records[0].tokens.input, 1)

        distinct = snapshot.with_name("distinct.chat")
        distinct.write_text(
            json.dumps(
                {"role": "user", "prompt": "abcd", "content": "efgh"}
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            parse_kiro((distinct,)).records[0].tokens.input, 2
        )

        inherited = snapshot.with_name("inherited.chat")
        inherited.write_text(
            json.dumps(
                {
                    "messages": [
                        {"role": "user", "parts": ["abcd"]},
                        {
                            "role": "assistant",
                            "items": [{"nodes": ["efgh"]}],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            parse_kiro((inherited,)).records[0].tokens,
            TokenBreakdown(1, 1),
        )

        recursive_equal = snapshot.with_name("recursive-equal.chat")
        recursive_equal.write_text(
            json.dumps(
                {
                    "role": "user",
                    "content": {"text": "abcd", "data": "abcd"},
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            parse_kiro((recursive_equal,)).records[0].tokens.input, 1
        )

        equal_container = snapshot.with_name("equal-container.chat")
        equal_messages = [{"role": "user", "content": "abcd"}]
        equal_container.write_text(
            json.dumps(
                {"messages": equal_messages, "history": equal_messages}
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            parse_kiro((equal_container,)).records[0].tokens.input, 1
        )

        untagged = snapshot.with_name("untagged.chat")
        untagged.write_text(
            json.dumps({"prompt": "abcdefgh", "response": "abcd"}),
            encoding="utf-8",
        )
        self.assertEqual(parse_kiro((untagged,)).status, AdapterStatus.UNSUPPORTED_FORMAT)

    def test_recursive_snapshot_budget_is_global_and_reports_partial(self):
        path = self.root / "globalStorage/kiro.kiroagent/ws/capped.chat"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": "abcd"},
                        {"role": "user", "content": "efgh"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        with mock.patch.object(kiro, "_MAX_TREE_NODES", 5):
            result = parse_kiro((path,))
        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(result.records[0].tokens.input, 1)

    def test_default_tree_budget_exhaustion_without_records_is_partial(self):
        path = self.root / "globalStorage/kiro.kiroagent/ws/empty.chat"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"messages": [{} for _ in range(100_000)]}),
            encoding="utf-8",
        )

        result = parse_kiro((path,))

        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(result.records, ())

    def test_execution_suppresses_workspace_session_on_exact_chat_session(self):
        snapshot = (
            self.root
            / "globalStorage/kiro.kiroagent/workspace-sessions/session.json"
        )
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text(
            json.dumps(
                {
                    "sessionId": "shared-session",
                    "selectedModel": "claude-4",
                    "history": [{"promptLogs": [{"prompt": "abcdefgh"}]}],
                }
            ),
            encoding="utf-8",
        )
        execution = (
            self.root
            / "globalStorage/kiro.kiroagent/hash/execution.json"
        )
        execution.parent.mkdir(parents=True)
        execution.write_text(
            json.dumps(
                {
                    "executionId": "execution-1",
                    "chatSessionId": "shared-session",
                    "status": "succeed",
                    "actions": [{"actionType": "say", "output": "abcd"}],
                    "context": {
                        "messages": [
                            {"entries": [{"type": "text", "text": "abcdefgh"}]}
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )

        result = parse_kiro((snapshot, execution))

        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].source_kind, "kiro_execution")

    def test_mixed_explicit_estimated_turn_and_corrupt_sidecar_are_partial(self):
        cli = self._cli("mixed")
        header = json.loads(cli.read_text(encoding="utf-8"))
        turns = header["session_state"]["conversation_metadata"]["user_turn_metadatas"]
        turns[0]["output_token_count"] = 0
        cli.write_text(json.dumps(header), encoding="utf-8")
        with cli.with_suffix(".jsonl").open("a", encoding="utf-8") as stream:
            stream.write("{broken\n")

        result = parse_kiro((cli, cli.with_suffix(".jsonl")))

        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(result.records[0].confidence, "estimated")

    def test_nested_sqlite_history_is_capped_and_fixture_is_executable(self):
        database = self._sqlite("capped")
        connection = sqlite3.connect(str(database))
        value = json.loads(
            connection.execute(
                "SELECT value FROM conversations_v2"
            ).fetchone()[0]
        )
        value["history"] *= 3
        connection.execute(
            "UPDATE conversations_v2 SET value = ?", (json.dumps(value),)
        )
        connection.commit()
        connection.close()
        with mock.patch.object(kiro, "_MAX_ROWS", 2):
            result = parse_kiro((database,))
        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(len(result.records), 2)

        fixture = Path(__file__).parents[1] / "fixtures/kiro/execution.json"
        fixture_result = parse_kiro((fixture,))
        self.assertEqual(fixture_result.status, AdapterStatus.OK)


if __name__ == "__main__":
    unittest.main()
