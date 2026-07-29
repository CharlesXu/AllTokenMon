import unittest
from types import MappingProxyType

from scripts.alltokenmon.adapters.registry import (
    ADAPTERS,
    RUNTIME_IDS,
    SOURCE_SPECS,
    validate_registry,
)


class RegistryTests(unittest.TestCase):
    def test_runtime_ids_are_complete_and_in_contract_order(self):
        self.assertEqual(
            RUNTIME_IDS,
            (
                "opencode", "claude", "codex", "cursor", "gemini", "amp",
                "droid", "openclaw", "pi", "kimi", "qwen", "roocode",
                "kilocode", "mux", "kilo", "crush", "hermes", "copilot",
                "goose", "codebuff", "antigravity", "zed", "kiro", "trae",
                "warp", "cline", "gjc", "grok", "jcode", "commandcode",
                "micode", "antigravity-cli", "junie", "zcode",
                "opencodereview", "codebuddy", "workbuddy", "devin-cli",
                "devin-desktop",
            ),
        )
        self.assertEqual(len(RUNTIME_IDS), 39)

    def test_every_runtime_has_a_nonempty_registered_spec_tuple(self):
        self.assertEqual(tuple(SOURCE_SPECS), RUNTIME_IDS)
        self.assertTrue(all(SOURCE_SPECS[runtime] for runtime in RUNTIME_IDS))
        validate_registry()

    def test_every_runtime_has_an_immutable_public_parser(self):
        self.assertIsInstance(ADAPTERS, MappingProxyType)
        self.assertEqual(tuple(ADAPTERS), RUNTIME_IDS)
        self.assertTrue(all(callable(ADAPTERS[runtime]) for runtime in RUNTIME_IDS))
        with self.assertRaises(TypeError):
            ADAPTERS["codex"] = ADAPTERS["codex"]

    def test_only_synced_sources_are_cache_only(self):
        cache_only = {
            runtime
            for runtime, specs in SOURCE_SPECS.items()
            if any(spec.cache_only for spec in specs)
        }
        self.assertEqual(cache_only, {"cursor", "antigravity", "trae", "warp"})


if __name__ == "__main__":
    unittest.main()
