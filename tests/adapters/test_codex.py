import tempfile
import unittest
from pathlib import Path

from scripts.alltokenmon.adapters.base import DiscoveryContext
from scripts.alltokenmon.adapters.codex import parse_codex, scan
from scripts.alltokenmon.adapters.registry import SOURCE_SPECS
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown


FIXTURES = Path(__file__).parent / "fixtures" / "codex"


class CodexAdapterTests(unittest.TestCase):
    def test_last_usage_and_turn_completed_have_exact_normalized_fields(self):
        result = parse_codex((FIXTURES / "session.jsonl",))

        self.assertEqual(result.runtime, "codex")
        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 2)
        first, second = result.records
        self.assertEqual(
            (
                first.runtime,
                first.provider,
                first.model,
                first.session_id,
                first.timestamp.isoformat(),
                first.tokens,
                first.message_count,
                first.source_kind,
                first.confidence,
            ),
            (
                "codex",
                "openai",
                "gpt-5.2-codex",
                "codex-session-001",
                "2026-07-20T01:00:02+00:00",
                TokenBreakdown(
                    input=60,
                    output=20,
                    cache_read=40,
                    cache_write=0,
                    reasoning=8,
                ),
                1,
                "jsonl",
                "exact",
            ),
        )
        self.assertEqual(
            second.tokens,
            TokenBreakdown(
                input=20,
                output=5,
                cache_read=10,
                cache_write=0,
                reasoning=2,
            ),
        )
        self.assertEqual(first.tokens.total, 120)
        self.assertNotEqual(first.dedup_key, second.dedup_key)

    def test_total_usage_fallback_deltas_and_resets(self):
        result = parse_codex((FIXTURES / "cumulative-reset.jsonl",))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(
            tuple(record.tokens for record in result.records),
            (
                TokenBreakdown(input=40, output=5, cache_read=10),
                TokenBreakdown(input=15, output=3, cache_read=5),
                TokenBreakdown(input=3, output=3, cache_read=2),
            ),
        )
        self.assertEqual(sum(record.message_count for record in result.records), 3)

    def test_stale_regression_with_last_usage_is_skipped(self):
        result = parse_codex((FIXTURES / "stale-regression.jsonl",))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(
            tuple(record.tokens for record in result.records),
            (
                TokenBreakdown(
                    input=80,
                    output=30,
                    cache_read=20,
                    reasoning=5,
                ),
                TokenBreakdown(
                    input=8,
                    output=3,
                    cache_read=2,
                    reasoning=1,
                ),
                TokenBreakdown(input=8, output=3, cache_read=2),
            ),
        )

    def test_nested_thread_spawn_parent_deduplicates_child_replay(self):
        result = parse_codex(
            (
                FIXTURES / "fork-source-parent.jsonl",
                FIXTURES / "fork-source-child.jsonl",
            )
        )

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(
            result.records[0].tokens,
            TokenBreakdown(input=30, output=4),
        )

    def test_nested_thread_spawn_skips_inherited_child_rows(self):
        result = parse_codex((FIXTURES / "fork-source-replay.jsonl",))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(
            result.records[0].tokens,
            TokenBreakdown(input=10, output=2),
        )

    def test_nested_parent_turn_context_does_not_open_child_gate(self):
        parent = parse_codex((FIXTURES / "nested-parent.jsonl",))
        child = parse_codex((FIXTURES / "nested-child.jsonl",))

        self.assertEqual(len(parent.records), 1)
        self.assertEqual(len(child.records), 1)
        self.assertEqual(
            child.records[0].session_id,
            "019e5c03-1e99-7000-8000-000000000001",
        )
        self.assertEqual(child.records[0].model, "gpt-5.5")
        self.assertEqual(
            child.records[0].tokens,
            TokenBreakdown(input=20, output=2),
        )
        self.assertNotEqual(
            parent.records[0].dedup_key,
            child.records[0].dedup_key,
        )

    def test_repeated_active_user_child_meta_does_not_reopen_gate(self):
        result = parse_codex((FIXTURES / "user-fork-repeated-meta.jsonl",))

        self.assertEqual(len(result.records), 1)
        self.assertEqual(
            result.records[0].tokens,
            TokenBreakdown(input=200, output=20, cache_read=50),
        )
        self.assertEqual(
            result.records[0].session_id,
            "22222222-2222-7222-8222-222222222222",
        )

    def test_equal_millisecond_user_fork_opens_without_task_started(self):
        result = parse_codex((FIXTURES / "user-fork-equal-ms.jsonl",))

        self.assertEqual(len(result.records), 1)
        self.assertEqual(
            result.records[0].tokens,
            TokenBreakdown(input=200, output=20, cache_read=50),
        )
        self.assertEqual(
            result.records[0].session_id,
            "22222222-2222-7222-8222-222222222222",
        )

    def test_archived_and_forked_copies_with_explicit_ids_are_deduplicated(self):
        source = (FIXTURES / "session.jsonl").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archived = root / "archived.jsonl"
            archived.write_text(source, encoding="utf-8")
            forked = root / "forked.jsonl"
            forked.write_text(
                source.replace(
                    '"id":"codex-session-001","model_provider":"openai"',
                    '"id":"codex-child-001","forked_from_id":'
                    '"codex-session-001","model_provider":"openai"',
                ),
                encoding="utf-8",
            )
            result = parse_codex((archived, forked))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 2)

    def test_same_turn_token_counts_without_event_ids_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "same-turn.jsonl"
            path.write_text(
                "\n".join(
                    (
                        '{"timestamp":"2026-07-24T00:00:00Z","type":"session_meta",'
                        '"payload":{"id":"same-turn-session","model_provider":"openai"}}',
                        '{"timestamp":"2026-07-24T00:00:01Z","type":"turn_context",'
                        '"payload":{"turn_id":"same-turn","model":"gpt-5"}}',
                        '{"timestamp":"2026-07-24T00:00:02Z","type":"event_msg",'
                        '"payload":{"type":"token_count","turn_id":"same-turn","info":'
                        '{"last_token_usage":{"input_tokens":10,"output_tokens":1}}}}',
                        '{"timestamp":"2026-07-24T00:00:03Z","type":"event_msg",'
                        '"payload":{"type":"token_count","turn_id":"same-turn","info":'
                        '{"last_token_usage":{"input_tokens":20,"output_tokens":2}}}}',
                    )
                ),
                encoding="utf-8",
            )
            result = parse_codex((path,))

        self.assertEqual(
            tuple(record.tokens for record in result.records),
            (
                TokenBreakdown(input=10, output=1),
                TokenBreakdown(input=20, output=2),
            ),
        )

    def test_archived_idless_token_count_copy_is_deduplicated(self):
        source = "\n".join(
            (
                '{"timestamp":"2026-07-24T00:00:00Z","type":"session_meta",'
                '"payload":{"id":"archived-idless-session","model_provider":"openai"}}',
                '{"timestamp":"2026-07-24T00:00:01Z","type":"turn_context",'
                '"payload":{"turn_id":"archived-turn","model":"gpt-5"}}',
                '{"timestamp":"2026-07-24T00:00:02Z","type":"event_msg",'
                '"payload":{"type":"token_count","turn_id":"archived-turn","info":'
                '{"last_token_usage":{"input_tokens":10,"output_tokens":1}}}}',
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "active.jsonl"
            archived = root / "archived.jsonl"
            active.write_text(source, encoding="utf-8")
            archived.write_text(source, encoding="utf-8")
            result = parse_codex((active, archived))

        self.assertEqual(len(result.records), 1)
        self.assertEqual(
            result.records[0].tokens,
            TokenBreakdown(input=10, output=1),
        )

    def test_cached_input_is_clamped_and_reasoning_does_not_inflate_total(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "clamp.jsonl"
            path.write_text(
                "\n".join(
                    (
                        '{"timestamp":"2026-07-24T00:00:00Z","type":"session_meta",'
                        '"payload":{"id":"clamp-session","model_provider":"openai"}}',
                        '{"timestamp":"2026-07-24T00:00:01Z","type":"turn_context",'
                        '"payload":{"model":"gpt-5"}}',
                        '{"timestamp":"2026-07-24T00:00:02Z","type":"event_msg",'
                        '"payload":{"id":"clamp-event","type":"token_count","info":'
                        '{"last_token_usage":{"input_tokens":5,"cached_input_tokens":50,'
                        '"output_tokens":7,"reasoning_output_tokens":6}}}}',
                    )
                ),
                encoding="utf-8",
            )

            record = parse_codex((path,)).records[0]

        self.assertEqual(
            record.tokens,
            TokenBreakdown(input=0, output=7, cache_read=5, reasoning=6),
        )
        self.assertEqual(record.tokens.total, 12)

    def test_missing_and_unknown_shapes_have_distinct_statuses(self):
        missing = parse_codex((FIXTURES / "missing.jsonl",))
        unsupported = parse_codex((FIXTURES / "unsupported.jsonl",))

        self.assertEqual(missing.status, AdapterStatus.NO_DATA)
        self.assertEqual(unsupported.status, AdapterStatus.UNSUPPORTED_FORMAT)
        self.assertTrue(all("/" not in item.message for item in missing.diagnostics))
        self.assertTrue(
            all("private" not in item.message.lower() for item in unsupported.diagnostics)
        )

    def test_truncated_file_retains_earlier_usage_and_is_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "partial.jsonl"
            path.write_text(
                (FIXTURES / "session.jsonl").read_text(encoding="utf-8")
                + '{"type":"event_msg","payload":{"message":"SENTINEL_PRIVATE"',
                encoding="utf-8",
            )
            result = parse_codex((path,))

        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(len(result.records), 2)
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_scan_discovers_registered_fixture_shape(self):
        with tempfile.TemporaryDirectory() as home_text:
            home = Path(home_text)
            destination = home / ".codex" / "sessions" / "nested"
            destination.mkdir(parents=True)
            (destination / "session.jsonl").write_bytes(
                (FIXTURES / "session.jsonl").read_bytes()
            )
            result = scan(
                DiscoveryContext("linux", home, {}),
                SOURCE_SPECS["codex"],
            )

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(sum(record.tokens.total for record in result.records), 155)


if __name__ == "__main__":
    unittest.main()
