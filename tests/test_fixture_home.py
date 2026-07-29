import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.alltokenmon.adapters.hermes import parse_hermes
from scripts.alltokenmon.schema import AdapterStatus


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_HOME = REPO_ROOT / "tests" / "fixtures"
RUNTIMES = {"codex", "claude", "opencode", "hermes", "gemini", "openclaw"}


class FixtureHomeIntegrationTests(unittest.TestCase):
    def test_core_runtime_fixture_home_is_discoverable_and_private(self):
        command = (
            sys.executable,
            "scripts/token_usage.py",
            "--home",
            "tests/fixtures",
            "--runtime",
            "codex,claude,opencode,hermes,gemini,openclaw",
            "--format",
            "json",
            "--now",
            "2026-07-29T00:00:00+08:00",
        )
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        repeated = subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(repeated.stderr, "")
        self.assertEqual(repeated.stdout, completed.stdout)
        self.assertNotIn("SENTINEL_PRIVATE", completed.stdout)

        report = json.loads(completed.stdout)
        self.assertEqual(
            report["coverage"],
            {
                "diagnostic_count": 6,
                "record_count": 6,
                "runtime_count": 6,
                "source_count": 6,
                "status": "complete",
                "status_counts": {"ok": 6},
            },
        )
        self.assertEqual(
            {
                entry["runtime"]: entry["status"]
                for entry in report["diagnostics"]
            },
            {runtime: "ok" for runtime in RUNTIMES},
        )

        all_time = report["periods"]["all_time"]
        self.assertEqual(all_time["totals"]["total"], 100)
        self.assertEqual(
            [
                (row["runtime"], row["total"])
                for row in all_time["runtimes"]
            ],
            [
                ("claude", 18),
                ("openclaw", 18),
                ("hermes", 17),
                ("opencode", 17),
                ("codex", 15),
                ("gemini", 15),
            ],
        )

    def test_committed_hermes_fixture_parses_read_only(self):
        source = FIXTURE_HOME / ".hermes" / "state.db"
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "state.db"
            shutil.copyfile(source, copied)
            copied.chmod(0o444)

            result = parse_hermes((copied,))

            self.assertEqual(result.status, AdapterStatus.OK)
            self.assertEqual(len(result.records), 1)
            self.assertGreater(result.records[0].tokens.total, 0)
            self.assertEqual(
                {path.name for path in Path(directory).iterdir()},
                {"state.db"},
            )
            self.assertNotIn("SENTINEL_PRIVATE", repr(result))


if __name__ == "__main__":
    unittest.main()
