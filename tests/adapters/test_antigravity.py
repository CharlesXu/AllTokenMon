import ast
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.alltokenmon.adapters import antigravity
from scripts.alltokenmon.adapters.antigravity import parse_antigravity, scan
from scripts.alltokenmon.adapters.base import DiscoveryContext
from scripts.alltokenmon.adapters.registry import SOURCE_SPECS
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown


FIXTURE_HOME = Path(__file__).parents[1] / "fixtures"


class AntigravityAdapterTests(unittest.TestCase):
    def test_committed_cache_preserves_unlisted_identity_and_discovery(self):
        result = scan(
            DiscoveryContext("linux", FIXTURE_HOME, {}),
            SOURCE_SPECS["antigravity"],
        )
        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.model, "MODEL_PLACEHOLDER_M26")
        self.assertEqual(record.provider, "antigravity")
        self.assertEqual(record.tokens, TokenBreakdown(12, 4, 2, 1, 3))
        self.assertEqual(record.dedup_key, "response-1")
        self.assertIsNone(record.cost)

    def test_missing_malformed_and_record_bound(self):
        self.assertEqual(parse_antigravity(()).status, AdapterStatus.NO_DATA)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "session.jsonl"
            path.write_text(
                '{"type":"usage","sessionId":"s","timestamp":1785232800000,'
                '"modelId":"gpt-5","input":1}\n'
                'SENTINEL_PRIVATE malformed\n'
                '{"type":"usage","sessionId":"s","timestamp":1785232800001,'
                '"modelId":"gpt-5","output":1}\n'
                '{"type":"usage","sessionId":"s","timestamp":9223372036854775807,'
                '"modelId":"gpt-5","output":1}\n',
                encoding="utf-8",
            )
            result = parse_antigravity((path,))
            self.assertEqual(result.status, AdapterStatus.PARTIAL)
            self.assertEqual(len(result.records), 2)
            self.assertNotIn("SENTINEL_PRIVATE", repr(result))
            with mock.patch.object(antigravity, "_MAX_ROWS", 0):
                bounded = parse_antigravity((path,))
            self.assertEqual(bounded.status, AdapterStatus.PARTIAL)

    def test_provider_ids_are_preserved_without_a_frozen_catalog(self):
        providers = (
            "vertex",
            "openai-codex",
            "moonshot",
            "azure",
            "meta",
            "together",
            "fireworks",
            "minimax_ai",
            "mistral",
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "session.jsonl"
            rows = [
                (
                    '{"type":"usage","sessionId":"s-%d","timestamp":%d,'
                    '"modelId":"unknown","providerId":"%s","input":1}'
                )
                % (index, 1785232800000 + index, provider)
                for index, provider in enumerate(providers)
            ]
            path.write_text("\n".join(rows) + "\n", encoding="utf-8")
            result = parse_antigravity((path,))
        self.assertEqual(
            [record.provider for record in result.records],
            [provider.replace("_", "-") for provider in providers],
        )

    def test_credential_shaped_provider_is_not_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "session.jsonl"
            path.write_text(
                '{"type":"usage","sessionId":"s","timestamp":1785232800000,'
                '"modelId":"future-model","providerId":"sk-live-123456",'
                '"input":1}\n',
                encoding="utf-8",
            )
            result = parse_antigravity((path,))
        self.assertEqual(result.records[0].provider, "antigravity")
        self.assertNotIn("sk-live-123456", repr(result))

    def test_deep_jsonl_is_partial_and_does_not_crash_or_leak(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "session.jsonl"
            deep = "[" * 2_000 + '"SENTINEL_PRIVATE"' + "]" * 2_000
            path.write_text(
                '{"type":"usage","sessionId":"s","timestamp":1785232800000,'
                '"modelId":"gpt-5","input":1}\n'
                '{"deep":' + deep + "}\n",
                encoding="utf-8",
            )
            result = parse_antigravity((path,))
        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(len(result.records), 1)
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_module_has_no_network_or_subprocess_imports(self):
        source = Path(antigravity.__file__).read_text(encoding="utf-8")
        self.assertNotIn("urllib", source)
        self.assertNotIn("http.client", source)
        self.assertNotIn("subprocess", source)


if __name__ == "__main__":
    unittest.main()
