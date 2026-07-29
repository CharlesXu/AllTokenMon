import tempfile
import unittest
from pathlib import Path

from scripts.alltokenmon.adapters.base import DiscoveryContext
from scripts.alltokenmon.adapters.openclaw import parse_openclaw, scan
from scripts.alltokenmon.adapters.registry import SOURCE_SPECS
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown


FIXTURES = Path(__file__).parent / "fixtures" / "openclaw"


class OpenClawAdapterTests(unittest.TestCase):
    def test_assistant_only_state_inheritance_cost_and_fields(self):
        result = parse_openclaw((FIXTURES / "session.jsonl",))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 2)
        first, second = result.records
        self.assertEqual(
            (
                first.provider,
                first.model,
                first.session_id,
                first.timestamp.isoformat(),
                first.tokens,
                first.cost,
                first.cost_source,
                first.dedup_key,
            ),
            (
                "openai-codex",
                "gpt-5.2",
                "session",
                "2026-07-20T02:01:00+00:00",
                TokenBreakdown(100, 50, 20, 5, 0),
                0.05,
                "provider_reported",
                "openclaw:session:message:openclaw-message-1",
            ),
        )
        self.assertEqual(second.provider, "anthropic")
        self.assertEqual(second.model, "claude-opus-4-6")
        self.assertEqual(second.tokens, TokenBreakdown(10, 5, 1, 2, 0))
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_archived_and_legacy_alias_copies_deduplicate_logically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "session.jsonl"
            archived = root / "session.jsonl.deleted.1784513000000"
            active.write_bytes((FIXTURES / "session.jsonl").read_bytes())
            archived.write_bytes((FIXTURES / "session.jsonl").read_bytes())
            result = parse_openclaw((archived, active))
        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 2)
        self.assertTrue(all(record.session_id == "session" for record in result.records))

    def test_message_ids_are_deduplicated_within_session_not_across_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = (FIXTURES / "session.jsonl").read_text(encoding="utf-8")
            session_a = root / "session-a.jsonl"
            session_b = root / "session-b.jsonl"
            session_a_alias = root / "session-a.jsonl.reset.2026-07-20"
            session_a.write_text(source, encoding="utf-8")
            session_b.write_text(source, encoding="utf-8")
            session_a_alias.write_text(source, encoding="utf-8")
            result = parse_openclaw(
                (session_a_alias, session_b, session_a)
            )

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 4)
        self.assertEqual(
            {record.session_id for record in result.records},
            {"session-a", "session-b"},
        )
        self.assertEqual(
            {
                record.dedup_key
                for record in result.records
                if record.dedup_key.endswith("openclaw-message-1")
            },
            {
                "openclaw:session-a:message:openclaw-message-1",
                "openclaw:session-b:message:openclaw-message-1",
            },
        )

    def test_statuses_partial_and_legacy_home_scan(self):
        self.assertEqual(
            parse_openclaw((FIXTURES / "missing.jsonl",)).status,
            AdapterStatus.NO_DATA,
        )
        self.assertEqual(
            parse_openclaw((FIXTURES / "unsupported.jsonl",)).status,
            AdapterStatus.NO_DATA,
        )
        with tempfile.TemporaryDirectory() as directory:
            unknown = Path(directory) / "unknown.jsonl"
            unknown.write_text(
                '{"unknown":"shape","content":"SENTINEL_PRIVATE_UNKNOWN"}\n',
                encoding="utf-8",
            )
            unknown_result = parse_openclaw((unknown,))
        self.assertEqual(unknown_result.status, AdapterStatus.UNSUPPORTED_FORMAT)
        self.assertNotIn("SENTINEL_PRIVATE", repr(unknown_result))
        with tempfile.TemporaryDirectory() as directory:
            partial = Path(directory) / "session.jsonl"
            partial.write_text(
                (FIXTURES / "session.jsonl").read_text()
                + '{"message":{"content":"SENTINEL_PRIVATE_TRUNCATED"',
                encoding="utf-8",
            )
            result = parse_openclaw((partial,))
        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(len(result.records), 2)
        with tempfile.TemporaryDirectory() as home_text:
            home = Path(home_text)
            sessions = home / ".clawdbot/agents/main/sessions"
            sessions.mkdir(parents=True)
            (sessions / "session.jsonl.reset.2026-07-20").write_bytes(
                (FIXTURES / "session.jsonl").read_bytes()
            )
            result = scan(
                DiscoveryContext("linux", home, {}),
                SOURCE_SPECS["openclaw"],
            )
        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(sum(record.tokens.total for record in result.records), 193)


if __name__ == "__main__":
    unittest.main()
