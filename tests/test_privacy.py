import json
import subprocess
import sys
import unittest
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from scripts.alltokenmon.adapters.base import DiscoveryContext
from scripts.alltokenmon.adapters.registry import ADAPTERS, RUNTIME_IDS, SOURCE_SPECS
from scripts.alltokenmon.aggregate import aggregate
from scripts.alltokenmon.discovery import discover
from scripts.alltokenmon.report import render_json, render_markdown
from scripts.alltokenmon.schema import AdapterResult, AdapterStatus


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_HOME = REPO_ROOT / "tests" / "fixtures"
SECRET_PREFIXES = (
    "PROMPT_SENTINEL_DO_NOT_LEAK",
    "sk-test-secret",
    "COOKIE_SENTINEL",
)
RUNTIME_SENTINELS = {
    "opencode": "PROMPT_SENTINEL_DO_NOT_LEAK::opencode",
    "claude": "sk-test-secret::claude",
    "codex": "COOKIE_SENTINEL::codex",
    "cursor": "PROMPT_SENTINEL_DO_NOT_LEAK::cursor",
    "gemini": "sk-test-secret::gemini",
    "amp": "COOKIE_SENTINEL::amp",
    "droid": "PROMPT_SENTINEL_DO_NOT_LEAK::droid",
    "openclaw": "sk-test-secret::openclaw",
    "pi": "COOKIE_SENTINEL::pi",
    "kimi": "PROMPT_SENTINEL_DO_NOT_LEAK::kimi",
    "qwen": "sk-test-secret::qwen",
    "roocode": "COOKIE_SENTINEL::roocode",
    "kilocode": "PROMPT_SENTINEL_DO_NOT_LEAK::kilocode",
    "mux": "sk-test-secret::mux",
    "kilo": "COOKIE_SENTINEL::kilo",
    "crush": "PROMPT_SENTINEL_DO_NOT_LEAK::crush",
    "hermes": "sk-test-secret::hermes",
    "copilot": "PROMPT_SENTINEL_DO_NOT_LEAK::copilot",
    "goose": "sk-test-secret::goose",
    "codebuff": "COOKIE_SENTINEL::codebuff",
    "antigravity": "COOKIE_SENTINEL::antigravity",
    "zed": "COOKIE_SENTINEL::zed",
    "kiro": "sk-test-secret::kiro",
    "trae": "COOKIE_SENTINEL::trae",
    "warp": "PROMPT_SENTINEL_DO_NOT_LEAK::warp",
    "cline": "sk-test-secret::cline",
    "gjc": "COOKIE_SENTINEL::gjc",
    "grok": "PROMPT_SENTINEL_DO_NOT_LEAK::grok",
    "jcode": "sk-test-secret::jcode",
    "commandcode": "COOKIE_SENTINEL::commandcode",
    "micode": "PROMPT_SENTINEL_DO_NOT_LEAK::micode",
    "antigravity-cli": "sk-test-secret::antigravity-cli",
    "junie": "PROMPT_SENTINEL_DO_NOT_LEAK::junie",
    "zcode": "sk-test-secret::zcode",
    "opencodereview": "COOKIE_SENTINEL::opencodereview",
    "codebuddy": "PROMPT_SENTINEL_DO_NOT_LEAK::codebuddy",
    "workbuddy": "COOKIE_SENTINEL::workbuddy",
    "devin-cli": "PROMPT_SENTINEL_DO_NOT_LEAK::devin-cli",
    "devin-desktop": "sk-test-secret::devin-desktop",
}


def _runtime_paths(runtime):
    context = DiscoveryContext("linux", FIXTURE_HOME, {})
    return tuple(
        path
        for spec in SOURCE_SPECS[runtime]
        for path in discover(spec, context)
    )


def _assert_private(test_case, rendered):
    for prefix in SECRET_PREFIXES:
        test_case.assertNotIn(prefix, rendered)
    for sentinel in RUNTIME_SENTINELS.values():
        test_case.assertNotIn(sentinel, rendered)


class RegistryExecutionAndPrivacyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = {}
        cls.results = {}
        for runtime in RUNTIME_IDS:
            paths = _runtime_paths(runtime)
            cls.paths[runtime] = paths
            cls.results[runtime] = ADAPTERS[runtime](paths)

    def test_all_39_public_parsers_execute_committed_fixtures(self):
        self.assertEqual(tuple(RUNTIME_SENTINELS), RUNTIME_IDS)
        self.assertEqual(tuple(self.results), RUNTIME_IDS)
        for runtime in RUNTIME_IDS:
            with self.subTest(runtime=runtime):
                self.assertTrue(self.paths[runtime], "fixture was not discoverable")
                result = self.results[runtime]
                self.assertIsInstance(result, AdapterResult)
                self.assertEqual(result.runtime, runtime)
                self.assertEqual(result.status, AdapterStatus.OK)
                self.assertTrue(result.records, "fixture produced no usage records")

    def test_every_runtime_fixture_contains_its_unique_ignored_secret(self):
        self.assertEqual(
            len(set(RUNTIME_SENTINELS.values())),
            len(RUNTIME_IDS),
        )
        for runtime, sentinel in RUNTIME_SENTINELS.items():
            with self.subTest(runtime=runtime):
                encoded = sentinel.encode("utf-8")
                self.assertTrue(
                    any(encoded in path.read_bytes() for path in self.paths[runtime]),
                    "fixture does not carry its privacy sentinel",
                )

    def test_records_json_and_markdown_never_serialize_fixture_secrets(self):
        records = tuple(
            record
            for runtime in RUNTIME_IDS
            for record in self.results[runtime].records
        )
        diagnostics = tuple(
            diagnostic
            for runtime in RUNTIME_IDS
            for diagnostic in self.results[runtime].diagnostics
        )
        serialized_records = repr(tuple(asdict(record) for record in records))
        report = aggregate(
            records,
            diagnostics,
            datetime(2026, 7, 29, tzinfo=timezone.utc),
        )

        _assert_private(self, serialized_records)
        _assert_private(self, render_json(report))
        _assert_private(self, render_markdown(report))

    def test_cli_stdout_and_stderr_never_emit_fixture_secrets(self):
        base = (
            sys.executable,
            "scripts/token_usage.py",
            "--home",
            "tests/fixtures",
            "--diagnostics",
            "--now",
            "2026-07-29T00:00:00+00:00",
        )
        for output_format in ("json", "markdown"):
            with self.subTest(output_format=output_format):
                completed = subprocess.run(
                    base + ("--format", output_format),
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertTrue(completed.stdout)
                self.assertTrue(completed.stderr)
                _assert_private(self, completed.stdout)
                _assert_private(self, completed.stderr)
                if output_format == "json":
                    self.assertEqual(
                        json.loads(completed.stdout)["coverage"]["runtime_count"],
                        39,
                    )


if __name__ == "__main__":
    unittest.main()
