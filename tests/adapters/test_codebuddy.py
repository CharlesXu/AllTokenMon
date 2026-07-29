import unittest
import tempfile
from datetime import datetime
import os
from pathlib import Path

from scripts.alltokenmon.adapters.codebuddy import parse_codebuddy
from scripts.alltokenmon.schema import TokenBreakdown
from tests.adapters.file_contract import assert_status_isolation, parse_fixture


class CodeBuddyAdapterTests(unittest.TestCase):
    def test_contract_usage_precedence_and_dedup(self):
        record = parse_fixture(parse_codebuddy, "codebuddy", "session.jsonl").records[0]
        self.assertEqual(record.provider, "zai")
        self.assertEqual(record.model, "glm-5.2")
        self.assertEqual(record.session_id, "buddy-session")
        self.assertEqual(record.tokens, TokenBreakdown(23, 15, 9, 2, 3))
        self.assertEqual(record.dedup_key, "codebuddy:buddy-session:provider-msg")

    def test_malformed_and_unsupported(self):
        assert_status_isolation(parse_codebuddy, ".jsonl")

    def test_extension_log_cache_split(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project__agent.log"
            path.write_text(
                "[2026/7/1 16:56:01.100] [Info] [CraftInvokableAgent] "
                "[agent-1] Model prepared: GLM (glm-5.2)\n"
                "[2026/7/1 16:56:02.200] [Info] [AgentReporter] [agent-1] "
                'Agent execution successful with usage: {"inputTokens":40,'
                '"outputTokens":5,"cacheTokens":30,"cachedMissTokens":10}\n',
                encoding="utf-8",
            )
            result = parse_codebuddy((path,))
        self.assertEqual(result.records[0].tokens, TokenBreakdown(10, 5, 30))
        self.assertEqual(result.records[0].source_kind, "log")

    def test_vscode_log_prefix_uses_log_timestamp_for_record_and_dedup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vscode-extension.log"
            path.write_text(
                "2026-07-01 17:00:31.780 [info] [CraftInvokableAgent] "
                "[agent-2] Model prepared: GLM-5v-Turbo (glm-5v-turbo)\n"
                "2026-07-01 17:00:59.790 [info] [AgentReporter] [agent-2] "
                "Agent execution successful with usage: "
                '{"inputTokens":32604,"outputTokens":557,'
                '"cacheTokens":20841,"cachedMissTokens":11763}\n',
                encoding="utf-8",
            )
            os.utime(path, (1, 1))
            result = parse_codebuddy((path,))

        expected = datetime.strptime(
            "2026-07-01 17:00:59.790", "%Y-%m-%d %H:%M:%S.%f"
        ).astimezone()
        record = result.records[0]
        self.assertEqual(record.timestamp, expected)
        self.assertIn(":{}:".format(int(expected.timestamp())), record.dedup_key)
        self.assertEqual(record.model, "glm-5v-turbo")
