import unittest
import tempfile
from pathlib import Path

from scripts.alltokenmon.adapters.droid import parse_droid
from scripts.alltokenmon.schema import TokenBreakdown
from tests.adapters.file_contract import assert_status_isolation, parse_fixture


class DroidAdapterTests(unittest.TestCase):
    def test_contract_and_model_normalization(self):
        record = parse_fixture(parse_droid, "droid", "session.settings.json").records[0]
        self.assertEqual(record.provider, "anthropic")
        self.assertEqual(record.model, "claude-opus-4-5-0")
        self.assertEqual(record.session_id, "session")
        self.assertEqual(record.tokens, TokenBreakdown(11, 4, 3, 2, 1))
        self.assertEqual(record.confidence, "exact")

    def test_malformed_and_unsupported(self):
        assert_status_isolation(parse_droid, ".settings.json")

    def test_missing_model_uses_bounded_transcript_reminder(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "fallback.settings.json"
            settings.write_text(
                '{"providerLock":"anthropic","tokenUsage":{"inputTokens":1}}',
                encoding="utf-8",
            )
            settings.with_name("fallback.jsonl").write_text(
                '{"content":"Model: Claude Opus 4.5 Thinking [Anthropic]"}\n',
                encoding="utf-8",
            )
            result = parse_droid((settings,))
        self.assertEqual(result.records[0].model, "claude opus 4-5 thinking")
