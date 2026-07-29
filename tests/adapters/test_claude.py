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
    def test_models_are_preserved_without_current_config_evidence(self):
        cases = (
            "claude-sonnet-4.6",
            "claude-opus-4.6-thinking",
            "anthropic/claude-4-6-haiku",
            "gemini-3-flash-a",
            "BIG PICKLE",
            "K2P6",
            "kimi-for-coding-highspeed",
            "MODEL_PLACEHOLDER_M187",
            "model_placeholder_m20",
            "MODEL_OPENAI_GPT_OSS_120B_MEDIUM",
            "gemini-3.5-flash-low",
            "GROK-COMPOSER-2.5-FAST",
            "kimi-k2.5-nvfp4",
            "unlisted-local-model",
        )

        self.assertEqual(
            tuple(_canonical_model(raw) for raw in cases),
            cases,
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

    def test_explicit_provider_hints_are_preserved_before_model_inference(self):
        cases = (
            ("openrouter/google", "openrouter"),
            ("openai-codex", "openai-codex"),
            ("vertex-ai/anthropic", "vertex-ai"),
            ("x-ai/anthropic", "x-ai"),
            ("gjc-model-4o/anthropic", "gjc-model-4o"),
            ("sk-live-123456", "anthropic"),
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
        self.assertNotIn("sk-live-123456", repr(result))

    def test_unknown_models_pass_through_and_avoid_catalog_inference(self):
        cases = (
            (
                "anthropic/claude-4-6-sonnet",
                None,
                "anthropic/claude-4-6-sonnet",
                "anthropic",
            ),
            ("gpt-5.3-codex", None, "gpt-5.3-codex", "unknown"),
            (
                "gemini-3-flash-preview",
                None,
                "gemini-3-flash-preview",
                "unknown",
            ),
            (
                "future-provider-model-2028",
                None,
                "future-provider-model-2028",
                "unknown",
            ),
            ("<synthetic>", None, "<synthetic>", "unknown"),
            ("gpt-5.3-codex", "anthropic", "gpt-5.3-codex", "anthropic"),
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

    def test_model_prefix_is_only_a_structural_provider_fallback(self):
        cases = (
            ("openrouter/google/gemini-3-flash-preview", "openrouter"),
            ("openrouter/openai/gpt-5.3-codex", "openrouter"),
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

    def test_scan_uses_allowlisted_route_config_without_leaking_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            projects = home / ".claude" / "projects" / "safe"
            projects.mkdir(parents=True)
            (projects / "session.jsonl").write_text(
                '{"type":"assistant","timestamp":"2026-07-26T06:00:00Z",'
                '"requestId":"request","message":{"id":"message",'
                '"model":"future-model-2028","usage":'
                '{"input_tokens":2,"output_tokens":1}}}\n',
                encoding="utf-8",
            )
            (home / ".claude" / "settings.json").write_text(
                '{"env":{"CLAUDE_CODE_USE_VERTEX":"1",'
                '"ANTHROPIC_AUTH_TOKEN":"SECRET_TOKEN_DO_NOT_LEAK",'
                '"ANTHROPIC_VERTEX_PROJECT_ID":"PRIVATE_PROJECT_DO_NOT_LEAK"},'
                '"apiKey":"PRIVATE_API_KEY_DO_NOT_LEAK"}',
                encoding="utf-8",
            )

            result = scan(
                DiscoveryContext("linux", home, {}),
                SOURCE_SPECS["claude"],
            )

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(result.records[0].model, "future-model-2028")
        self.assertEqual(result.records[0].provider, "google-vertex")
        self.assertEqual(result.records[0].confidence, "estimated")
        rendered = repr(result)
        self.assertNotIn("SECRET_TOKEN_DO_NOT_LEAK", rendered)
        self.assertNotIn("PRIVATE_PROJECT_DO_NOT_LEAK", rendered)
        self.assertNotIn("PRIVATE_API_KEY_DO_NOT_LEAK", rendered)

    def test_model_overrides_resolve_dynamically_without_retaining_deployment(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            projects = home / ".claude" / "projects" / "safe"
            projects.mkdir(parents=True)
            private_deployment = "PRIVATE_DEPLOYMENT_DO_NOT_LEAK"
            (projects / "session.jsonl").write_text(
                '{"type":"assistant","timestamp":"2026-07-26T07:00:00Z",'
                '"requestId":"request","message":{"id":"message",'
                '"model":"' + private_deployment + '","usage":'
                '{"input_tokens":3,"output_tokens":1}}}\n',
                encoding="utf-8",
            )
            (home / ".claude" / "settings.json").write_text(
                '{"modelOverrides":{"claude-opus-4-7":"'
                + private_deployment
                + '"},"env":{"CLAUDE_CODE_USE_BEDROCK":"1"}}',
                encoding="utf-8",
            )

            result = scan(
                DiscoveryContext("linux", home, {}),
                SOURCE_SPECS["claude"],
            )

        self.assertEqual(result.records[0].model, "claude-opus-4-7")
        self.assertEqual(result.records[0].provider, "amazon-bedrock")
        self.assertEqual(result.records[0].confidence, "estimated")
        self.assertNotIn(private_deployment, repr(result))

    def test_process_route_evidence_overrides_settings_route(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            projects = home / ".claude" / "projects" / "safe"
            projects.mkdir(parents=True)
            (projects / "session.jsonl").write_text(
                '{"type":"assistant","timestamp":"2026-07-26T08:00:00Z",'
                '"requestId":"request","message":{"id":"message",'
                '"model":"claude-future","usage":{"input_tokens":1}}}\n',
                encoding="utf-8",
            )
            (home / ".claude" / "settings.json").write_text(
                '{"env":{"CLAUDE_CODE_USE_VERTEX":"1"}}',
                encoding="utf-8",
            )

            result = scan(
                DiscoveryContext(
                    "windows",
                    home,
                    {
                        "CLAUDE_CODE_USE_VERTEX": "0",
                        "CLAUDE_CODE_USE_FOUNDRY": "1",
                    },
                ),
                SOURCE_SPECS["claude"],
            )

        self.assertEqual(result.records[0].provider, "microsoft-foundry")
        self.assertEqual(result.records[0].confidence, "estimated")

    def test_config_only_never_creates_usage_records(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".claude").mkdir()
            (home / ".claude" / "settings.json").write_text(
                '{"model":"claude-current","env":'
                '{"CLAUDE_CODE_USE_BEDROCK":"1"}}',
                encoding="utf-8",
            )

            result = scan(
                DiscoveryContext("linux", home, {}),
                SOURCE_SPECS["claude"],
            )

        self.assertEqual(result.status, AdapterStatus.NO_DATA)
        self.assertEqual(result.records, ())

    def test_explicit_record_identity_wins_and_config_only_fills_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            projects = home / ".claude" / "projects" / "safe"
            projects.mkdir(parents=True)
            (projects / "session.jsonl").write_text(
                '{"type":"assistant","provider":"openrouter",'
                '"timestamp":"2026-07-26T09:00:00Z","requestId":"one",'
                '"message":{"id":"one","model":"future-explicit",'
                '"usage":{"input_tokens":1}}}\n'
                '{"type":"assistant","timestamp":"2026-07-26T09:00:01Z",'
                '"requestId":"two","message":{"id":"two",'
                '"usage":{"output_tokens":2}}}\n',
                encoding="utf-8",
            )
            (home / ".claude" / "settings.json").write_text(
                '{"model":"claude-configured","env":'
                '{"CLAUDE_CODE_USE_VERTEX":"1"}}',
                encoding="utf-8",
            )

            result = scan(
                DiscoveryContext("linux", home, {}),
                SOURCE_SPECS["claude"],
            )

        self.assertEqual(
            tuple(
                (record.model, record.provider, record.confidence)
                for record in result.records
            ),
            (
                ("future-explicit", "openrouter", "exact"),
                ("claude-configured", "google-vertex", "estimated"),
            ),
        )

    def test_conflicting_routes_and_unsafe_models_do_not_leak(self):
        sentinel = "sk-secret-SENTINEL_DO_NOT_LEAK"
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            projects = home / ".claude" / "projects" / "safe"
            projects.mkdir(parents=True)
            (projects / "session.jsonl").write_text(
                '{"type":"assistant","timestamp":"2026-07-26T10:00:00Z",'
                '"requestId":"one","message":{"id":"one",'
                '"model":"' + sentinel + '","usage":{"input_tokens":1}}}\n',
                encoding="utf-8",
            )
            (home / ".claude" / "settings.json").write_text(
                '{"env":{"CLAUDE_CODE_USE_VERTEX":"1",'
                '"CLAUDE_CODE_USE_BEDROCK":"1",'
                '"ANTHROPIC_BASE_URL":"https://PRIVATE_ENDPOINT_DO_NOT_LEAK"},'
                '"headers":{"Authorization":"PRIVATE_HEADER_DO_NOT_LEAK"}}',
                encoding="utf-8",
            )

            result = scan(
                DiscoveryContext("linux", home, {}),
                SOURCE_SPECS["claude"],
            )

        self.assertEqual(result.records, ())
        rendered = repr(result)
        self.assertNotIn(sentinel, rendered)
        self.assertNotIn("PRIVATE_ENDPOINT_DO_NOT_LEAK", rendered)
        self.assertNotIn("PRIVATE_HEADER_DO_NOT_LEAK", rendered)

    def test_surrogate_models_are_rejected_without_adapter_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            projects = home / ".claude" / "projects" / "safe"
            projects.mkdir(parents=True)
            (projects / "session.jsonl").write_text(
                '{"type":"assistant","requestId":"one","message":'
                '{"id":"one","model":"\\ud800","usage":'
                '{"input_tokens":1}}}\n',
                encoding="utf-8",
            )
            (home / ".claude" / "settings.json").write_text(
                '{"model":"\\ud800","modelOverrides":'
                '{"claude-safe":"\\ud800"}}',
                encoding="utf-8",
            )

            result = scan(
                DiscoveryContext("linux", home, {}),
                SOURCE_SPECS["claude"],
            )

        self.assertEqual(result.records, ())
        self.assertEqual(result.status, AdapterStatus.NO_DATA)

    def test_model_override_is_applied_exactly_once(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            projects = home / ".claude" / "projects" / "safe"
            projects.mkdir(parents=True)
            (projects / "session.jsonl").write_text(
                '{"type":"assistant","requestId":"one","message":'
                '{"id":"one","model":"deploy-x","usage":'
                '{"input_tokens":1}}}\n',
                encoding="utf-8",
            )
            (home / ".claude" / "settings.json").write_text(
                '{"modelOverrides":{"claude-a":"deploy-x",'
                '"claude-b":"claude-a"}}',
                encoding="utf-8",
            )

            result = scan(
                DiscoveryContext("linux", home, {}),
                SOURCE_SPECS["claude"],
            )

        self.assertEqual(result.records[0].model, "claude-a")

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
