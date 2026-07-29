import tempfile
import unittest
from pathlib import Path

from scripts.alltokenmon.adapters.base import DiscoveryContext
from scripts.alltokenmon.adapters.claude import (
    _canonical_model,
    parse_claude,
    scan,
)
from scripts.alltokenmon.adapters.registry import SOURCE_SPECS
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown


FIXTURES = Path(__file__).parent / "fixtures" / "claude"


class ClaudeAdapterTests(unittest.TestCase):
    def test_shared_model_aliases_match_frozen_resolver(self):
        cases = (
            ("claude-sonnet-4.6", "claude-sonnet-4-6"),
            ("claude-opus-4.6-thinking", "claude-opus-4-6"),
            ("anthropic/claude-4-6-haiku", "claude-haiku-4-6"),
            ("gemini-3-flash-a", "gemini-3.5-flash-high"),
            ("BIG PICKLE", "glm-4.7"),
            ("K2P6", "kimi-k2.6"),
            ("kimi-for-coding-highspeed", "kimi-k2.7-code-highspeed"),
            ("MODEL_PLACEHOLDER_M187", "gemini-3.5-flash-extra-low"),
            ("model_placeholder_m20", "gemini-3.5-flash-medium"),
            ("MODEL_OPENAI_GPT_OSS_120B_MEDIUM", "gpt-oss-120b-medium"),
            ("gemini-3.5-flash-low", "gemini-3.5-flash-medium"),
            ("GROK-COMPOSER-2.5-FAST", "composer-2.5-fast"),
            ("kimi-k2.5-nvfp4", "kimi-k2.5"),
            ("unlisted-local-model", "unlisted-local-model"),
        )

        self.assertEqual(
            tuple(_canonical_model(raw) for raw, _ in cases),
            tuple(expected for _, expected in cases),
        )

    def test_assistant_usage_streaming_dedup_and_sidechain_fields_are_exact(self):
        result = parse_claude((FIXTURES / "session.jsonl",))

        self.assertEqual(result.runtime, "claude")
        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 2)
        main, sidechain = result.records
        self.assertEqual(
            (
                main.runtime,
                main.provider,
                main.model,
                main.session_id,
                main.timestamp.isoformat(),
                main.tokens,
                main.message_count,
                main.source_kind,
                main.confidence,
            ),
            (
                "claude",
                "anthropic",
                "claude-sonnet-4-6",
                "claude-session-001",
                "2026-07-22T02:00:00+00:00",
                TokenBreakdown(
                    input=12,
                    output=200,
                    cache_read=5,
                    cache_write=3,
                ),
                1,
                "jsonl",
                "exact",
            ),
        )
        self.assertEqual(sidechain.session_id, "claude-parent-001")
        self.assertEqual(sidechain.provider, "openrouter")
        self.assertEqual(sidechain.model, "claude-opus-4-6")
        self.assertEqual(
            sidechain.tokens,
            TokenBreakdown(
                input=30,
                output=8,
                cache_read=7,
                cache_write=4,
            ),
        )
        rendered = repr(result)
        self.assertNotIn("SENTINEL_PRIVATE_CONTENT", rendered)
        self.assertNotIn("SENTINEL_PRIVATE_TOOL_ARGUMENT", rendered)

    def test_streaming_duplicates_use_per_field_max_not_sum(self):
        record = parse_claude((FIXTURES / "session.jsonl",)).records[0]

        self.assertEqual(record.tokens.total, 220)
        self.assertEqual(record.message_count, 1)

    def test_streaming_duplicate_without_model_still_merges_usage_and_provider(self):
        result = parse_claude((FIXTURES / "stream-no-model.jsonl",))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.model, "claude-sonnet-4-6")
        self.assertEqual(record.provider, "openrouter")
        self.assertEqual(
            record.tokens,
            TokenBreakdown(
                input=120,
                output=75,
                cache_read=20,
                cache_write=8,
            ),
        )

    def test_explicit_provider_hints_use_first_frozen_canonical_tag(self):
        cases = (
            ("openrouter/google", "openrouter"),
            ("openai-codex", "openai"),
            ("vertex-ai/anthropic", "anthropic"),
            ("x-ai/anthropic", "xai"),
            ("gjc-model-4o/anthropic", "anthropic"),
            ("<synthetic>", "anthropic"),
            ("unknown", "anthropic"),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "providers.jsonl"
            rows = []
            for index, (provider, _) in enumerate(cases):
                rows.append(
                    '{"type":"assistant","provider":"'
                    + provider
                    + '","timestamp":"2026-07-26T03:00:00Z",'
                    + '"requestId":"request-'
                    + str(index)
                    + '","message":{"id":"message-'
                    + str(index)
                    + '","model":"claude-sonnet-4-6","usage":'
                    + '{"input_tokens":1,"output_tokens":1}}}'
                )
            path.write_text("\n".join(rows), encoding="utf-8")
            result = parse_claude((path,))

        self.assertEqual(
            tuple(record.provider for record in result.records),
            tuple(expected for _, expected in cases),
        )

    def test_model_normalization_and_provider_inference_match_frozen_cases(self):
        cases = (
            (
                "anthropic/claude-4-6-sonnet",
                None,
                "claude-sonnet-4-6",
                "anthropic",
            ),
            ("gpt-5.3-codex", None, "gpt-5.3-codex", "openai"),
            (
                "gemini-3-flash-preview",
                None,
                "gemini-3-flash-preview",
                "google",
            ),
            ("MiniMax-M2.1", None, "MiniMax-M2.1", "minimax"),
            ("mistral-large", None, "mistral-large", "mistralai"),
            ("llama-3.3-70b", None, "llama-3.3-70b", "meta_llama"),
            ("<synthetic>", None, "<synthetic>", "unknown"),
            ("gpt-5.3-codex", "anthropic", "gpt-5.3-codex", "openai"),
            (
                "gemini-3-flash-preview",
                "openrouter/google",
                "gemini-3-flash-preview",
                "openrouter",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models.jsonl"
            rows = []
            for index, (model, provider, _, _) in enumerate(cases):
                provider_field = (
                    ',"provider":"' + provider + '"'
                    if provider is not None
                    else ""
                )
                rows.append(
                    '{"type":"assistant"'
                    + provider_field
                    + ',"timestamp":"2026-07-26T04:00:00Z",'
                    + '"requestId":"model-request-'
                    + str(index)
                    + '","message":{"id":"model-message-'
                    + str(index)
                    + '","model":"'
                    + model
                    + '","usage":{"input_tokens":1,"output_tokens":1}}}'
                )
            path.write_text("\n".join(rows), encoding="utf-8")
            result = parse_claude((path,))

        self.assertEqual(
            tuple(
                (record.model, record.provider)
                for record in result.records
            ),
            tuple(
                (expected_model, expected_provider)
                for _, _, expected_model, expected_provider in cases
            ),
        )

    def test_model_family_inference_precedes_slash_prefix_fallback(self):
        cases = (
            ("openrouter/google/gemini-3-flash-preview", "google"),
            ("openrouter/openai/gpt-5.3-codex", "openai"),
            ("openrouter/vendor-model", "openrouter"),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prefixed-models.jsonl"
            rows = []
            for index, (model, _) in enumerate(cases):
                rows.append(
                    '{"type":"assistant","timestamp":"2026-07-26T05:00:00Z",'
                    + '"requestId":"prefix-request-'
                    + str(index)
                    + '","message":{"id":"prefix-message-'
                    + str(index)
                    + '","model":"'
                    + model
                    + '","usage":{"input_tokens":1,"output_tokens":1}}}'
                )
            path.write_text("\n".join(rows), encoding="utf-8")
            result = parse_claude((path,))

        self.assertEqual(
            tuple(record.provider for record in result.records),
            tuple(provider for _, provider in cases),
        )

    def test_truncated_jsonl_retains_valid_records_and_reports_partial(self):
        result = parse_claude((FIXTURES / "partial.jsonl",))

        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].tokens, TokenBreakdown(input=9, output=4))
        self.assertTrue(
            all("sentinel" not in item.message.lower() for item in result.diagnostics)
        )

    def test_missing_and_unknown_shapes_have_distinct_statuses(self):
        missing = parse_claude((FIXTURES / "missing.jsonl",))
        unsupported = parse_claude((FIXTURES / "unsupported.jsonl",))

        self.assertEqual(missing.status, AdapterStatus.NO_DATA)
        self.assertEqual(unsupported.status, AdapterStatus.UNSUPPORTED_FORMAT)
        self.assertTrue(all("/" not in item.message for item in missing.diagnostics))
        self.assertTrue(
            all("private" not in item.message.lower() for item in unsupported.diagnostics)
        )

    def test_supported_user_only_transcript_is_no_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "user-only.jsonl"
            path.write_text(
                '{"type":"user","sessionId":"safe-session",'
                '"message":{"content":"SENTINEL_NEVER_READ"}}\n',
                encoding="utf-8",
            )
            result = parse_claude((path,))

        self.assertEqual(result.status, AdapterStatus.NO_DATA)
        self.assertNotIn("SENTINEL_NEVER_READ", repr(result))

    def test_scan_discovers_registered_fixture_shape(self):
        with tempfile.TemporaryDirectory() as home_text:
            home = Path(home_text)
            destination = home / ".claude" / "projects" / "safe-project"
            destination.mkdir(parents=True)
            (destination / "session.jsonl").write_bytes(
                (FIXTURES / "session.jsonl").read_bytes()
            )
            result = scan(
                DiscoveryContext("linux", home, {}),
                SOURCE_SPECS["claude"],
            )

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(sum(record.tokens.total for record in result.records), 269)


if __name__ == "__main__":
    unittest.main()
