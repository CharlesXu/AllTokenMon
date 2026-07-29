import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class EntrypointTests(unittest.TestCase):
    def test_help_is_available_without_third_party_packages(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "token_usage.py"), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("local token usage", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
