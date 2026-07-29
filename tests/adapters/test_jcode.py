import unittest
import tempfile
from pathlib import Path

from scripts.alltokenmon.adapters.jcode import parse_jcode
from scripts.alltokenmon.schema import TokenBreakdown
from tests.adapters.file_contract import assert_status_isolation, parse_fixture


class JcodeAdapterTests(unittest.TestCase):
    def test_contract_cache_subset_normalization(self):
        record = parse_fixture(parse_jcode, "jcode", "session_contract.json").records[0]
        self.assertEqual(record.provider, "openai")
        self.assertEqual(record.model, "gpt-5")
        self.assertEqual(record.session_id, "jcode-session")
        self.assertEqual(record.tokens, TokenBreakdown(20, 12, 10, 0, 2))
        self.assertEqual(record.dedup_key, "jcode:jcode-session:jcode-msg")

    def test_malformed_and_unsupported(self):
        assert_status_isolation(parse_jcode, ".json")

    def test_journal_authoritatively_replaces_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session_test.json"
            path.write_text(
                '{"id":"session","provider_key":"openai","model":"gpt-5",'
                '"messages":[{"id":"same","role":"assistant",'
                '"timestamp":"2026-07-20T00:00:00Z",'
                '"token_usage":{"input_tokens":5,"output_tokens":1}}]}',
                encoding="utf-8",
            )
            path.with_name("session_test.journal.jsonl").write_text(
                '{"append_messages":[{"id":"same","role":"assistant",'
                '"timestamp":"2026-07-20T00:00:01Z",'
                '"token_usage":{"input_tokens":9,"output_tokens":2}}]}\n',
                encoding="utf-8",
            )
            result = parse_jcode((path,))
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].tokens, TokenBreakdown(9, 2))

    def test_journal_correction_wins_over_replayed_snapshot_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session_dup.json"
            path.write_text(
                '{"id":"session","provider_key":"openai","model":"gpt-5",'
                '"messages":[{"id":"same","role":"assistant",'
                '"timestamp":"2026-09-02T00:00:01Z",'
                '"token_usage":{"input_tokens":5,"output_tokens":1}},'
                '{"id":"same","role":"assistant",'
                '"timestamp":"2026-09-02T00:00:02Z",'
                '"token_usage":{"input_tokens":6,"output_tokens":1}}]}',
                encoding="utf-8",
            )
            path.with_name("session_dup.journal.jsonl").write_text(
                '{"append_messages":[{"id":"same","role":"assistant",'
                '"timestamp":"2026-09-02T00:00:03Z",'
                '"token_usage":{"input_tokens":9,"output_tokens":2}}]}\n',
                encoding="utf-8",
            )
            result = parse_jcode((path,))

        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].tokens, TokenBreakdown(9, 2))
        self.assertEqual(
            result.records[0].timestamp.isoformat(), "2026-09-02T00:00:03+00:00"
        )

    def test_huge_duration_is_ignored_without_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session_huge.json"
            path.write_text(
                '{"id":"session","model":"gpt-5","messages":[{"id":"same",'
                '"timestamp":"2026-09-02T00:00:03Z",'
                '"tool_duration_ms":"' + ("9" * 10000) + '",'
                '"token_usage":{"input_tokens":9,"output_tokens":2}}]}',
                encoding="utf-8",
            )
            record = parse_jcode((path,)).records[0]

        self.assertEqual(record.timestamp.isoformat(), "2026-09-02T00:00:03+00:00")
