import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.alltokenmon.adapters.base import DiscoveryContext
from scripts.alltokenmon.adapters.hermes import parse_hermes, scan
from scripts.alltokenmon.adapters.registry import SOURCE_SPECS
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown


FIXTURES = Path(__file__).parent / "fixtures" / "hermes"


def _create_db(path, per_model=True):
    connection = sqlite3.connect(str(path))
    connection.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, model TEXT, started_at REAL, "
        "message_count INTEGER, input_tokens INTEGER, output_tokens INTEGER, "
        "cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, "
        "billing_provider TEXT, estimated_cost_usd REAL, actual_cost_usd REAL)"
    )
    connection.execute(
        "INSERT INTO sessions VALUES "
        "('covered','claude-sonnet-4',1750000000.25,42,999,999,999,999,999,"
        "'anthropic',9.0,10.0)"
    )
    connection.execute(
        "INSERT INTO sessions VALUES "
        "('legacy','gpt-5.4',1750000100000,3,100,20,5,2,1,NULL,1.25,NULL)"
    )
    if per_model:
        connection.execute(
            "CREATE TABLE session_model_usage "
            "(session_id TEXT, model TEXT, billing_provider TEXT, input_tokens INTEGER, "
            "output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, "
            "reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL)"
        )
        connection.execute(
            "INSERT INTO session_model_usage VALUES "
            "('covered','claude-sonnet-4','anthropic',1200,300,50,20,10,0.12,0.34)"
        )
    connection.commit()
    connection.close()


class HermesAdapterTests(unittest.TestCase):
    def test_per_model_precedence_session_fallback_and_exact_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "state.db"
            _create_db(db)
            result = parse_hermes((db,))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 2)
        covered, legacy = result.records
        self.assertEqual(
            (
                covered.session_id,
                covered.provider,
                covered.model,
                covered.message_count,
                covered.timestamp.isoformat(),
                covered.tokens,
                covered.cost,
                covered.cost_source,
                covered.dedup_key,
            ),
            (
                "covered",
                "anthropic",
                "claude-sonnet-4",
                42,
                "2025-06-15T15:06:40.250000+00:00",
                TokenBreakdown(1200, 300, 50, 20, 10),
                0.34,
                "provider_reported",
                "hermes:covered:claude-sonnet-4:anthropic",
            ),
        )
        self.assertEqual(legacy.provider, "openai")
        self.assertEqual(legacy.tokens, TokenBreakdown(100, 20, 5, 2, 1))
        self.assertEqual(legacy.cost, 1.25)
        self.assertEqual(legacy.timestamp.isoformat(), "2025-06-15T15:08:20+00:00")

    def test_unknown_schema_corrupt_and_missing_statuses_are_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            unknown = Path(directory) / "state.db"
            sqlite3.connect(str(unknown)).execute("CREATE TABLE other (secret TEXT)").connection.close()
            unsupported = parse_hermes((unknown,))
        corrupt = parse_hermes((FIXTURES / "unsupported.db",))
        missing = parse_hermes((FIXTURES / "missing.db",))

        self.assertEqual(unsupported.status, AdapterStatus.UNSUPPORTED_FORMAT)
        self.assertEqual(corrupt.status, AdapterStatus.ERROR)
        self.assertEqual(missing.status, AdapterStatus.NO_DATA)
        self.assertNotIn("SENTINEL_PRIVATE", repr(corrupt))

    def test_profile_scan_and_duplicate_database_path(self):
        with tempfile.TemporaryDirectory() as home_text:
            home = Path(home_text)
            profile = home / ".hermes/profiles/research"
            profile.mkdir(parents=True)
            _create_db(profile / "state.db", per_model=False)
            result = scan(
                DiscoveryContext("linux", home, {}),
                SOURCE_SPECS["hermes"],
            )
        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 2)


if __name__ == "__main__":
    unittest.main()
