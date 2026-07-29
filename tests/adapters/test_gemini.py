import tempfile
import unittest
from pathlib import Path

from scripts.alltokenmon.adapters.base import DiscoveryContext
from scripts.alltokenmon.adapters.gemini import parse_gemini, scan
from scripts.alltokenmon.adapters.registry import SOURCE_SPECS
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown


FIXTURES = Path(__file__).parent / "fixtures" / "gemini"


class GeminiAdapterTests(unittest.TestCase):
    def test_conversation_aliases_and_cache_normalization(self):
        result = parse_gemini((FIXTURES / "conversation.json",))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.provider, "google")
        self.assertEqual(record.model, "gemini-3-flash-preview")
        self.assertEqual(record.session_id, "gemini-session-json")
        self.assertEqual(record.timestamp.isoformat(), "2026-07-20T01:01:00+00:00")
        self.assertEqual(record.tokens, TokenBreakdown(80, 50, 20, 0, 7))
        self.assertEqual(record.dedup_key, "gemini:gemini-session-json:gemini-message-json")
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_stream_duplicate_id_replaces_and_stats_are_distinct(self):
        result = parse_gemini((FIXTURES / "stream.jsonl",))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 2)
        direct, stats = result.records
        self.assertEqual(direct.tokens, TokenBreakdown(15, 2, 5, 0, 3))
        self.assertEqual(direct.timestamp.isoformat(), "2026-07-20T02:00:01+00:00")
        self.assertEqual(stats.model, "gemini-2.5-pro")
        self.assertEqual(stats.tokens, TokenBreakdown(7, 4, 5, 0, 2))

    def test_stream_duplicate_ids_replace_only_within_the_same_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.jsonl"
            path.write_text(
                "\n".join(
                    (
                        '{"type":"init","model":"gemini-2.5-pro",'
                        '"session_id":"session-a"}',
                        '{"type":"gemini","id":"shared-id","tokens":'
                        '{"input":10,"output":1}}',
                        '{"type":"gemini","id":"shared-id","tokens":'
                        '{"input":20,"output":2}}',
                        '{"type":"init","model":"gemini-2.5-pro",'
                        '"session_id":"session-b"}',
                        '{"type":"gemini","id":"shared-id","tokens":'
                        '{"input":30,"output":3}}',
                    )
                ),
                encoding="utf-8",
            )
            result = parse_gemini((path,))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 2)
        self.assertEqual(
            {
                record.session_id: record.tokens
                for record in result.records
            },
            {
                "session-a": TokenBreakdown(input=20, output=2),
                "session-b": TokenBreakdown(input=30, output=3),
            },
        )

    def test_v0391_flat_input_alias_is_already_net_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.jsonl"
            path.write_text(
                '{"type":"init","model":"gemini-2.5-pro",'
                '"session_id":"gemini-v0391"}\n'
                '{"type":"result","stats":{"total_tokens":32,'
                '"output_tokens":20,"cached":5,"input":7}}\n',
                encoding="utf-8",
            )
            result = parse_gemini((path,))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(
            result.records[0].tokens,
            TokenBreakdown(input=7, output=20, cache_read=5),
        )

    def test_partial_unknown_no_data_and_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            partial = Path(directory) / "partial.jsonl"
            partial.write_text(
                (FIXTURES / "stream.jsonl").read_text()
                + '{"content":"SENTINEL_PRIVATE_TRUNCATED"',
                encoding="utf-8",
            )
            partial_result = parse_gemini((partial,))
        self.assertEqual(partial_result.status, AdapterStatus.PARTIAL)
        self.assertEqual(len(partial_result.records), 2)
        self.assertNotIn("SENTINEL_PRIVATE", repr(partial_result))
        self.assertEqual(
            parse_gemini((FIXTURES / "unsupported.json",)).status,
            AdapterStatus.UNSUPPORTED_FORMAT,
        )
        self.assertEqual(
            parse_gemini((FIXTURES / "missing.json",)).status,
            AdapterStatus.NO_DATA,
        )
        with tempfile.TemporaryDirectory() as home_text:
            home = Path(home_text)
            chats = home / ".gemini/tmp/hash/chats"
            chats.mkdir(parents=True)
            (chats / "conversation.json").write_bytes(
                (FIXTURES / "conversation.json").read_bytes()
            )
            result = scan(
                DiscoveryContext("linux", home, {}),
                SOURCE_SPECS["gemini"],
            )
        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(result.records[0].tokens.total, 150)


if __name__ == "__main__":
    unittest.main()
