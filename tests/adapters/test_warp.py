import json
import tempfile
import unittest
from pathlib import Path

from scripts.alltokenmon.adapters import warp
from scripts.alltokenmon.adapters.base import DiscoveryContext
from scripts.alltokenmon.adapters.registry import SOURCE_SPECS
from scripts.alltokenmon.adapters.warp import parse_warp, scan
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown


FIXTURE_HOME = Path(__file__).parents[1] / "fixtures"


class WarpAdapterTests(unittest.TestCase):
    def test_committed_workspace_cache_never_invents_tokens(self):
        result = scan(
            DiscoveryContext("linux", FIXTURE_HOME, {}),
            SOURCE_SPECS["warp"],
        )
        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.tokens, TokenBreakdown())
        self.assertEqual(record.message_count, 7)
        self.assertEqual((record.cost, record.cost_source), (1.23, "provider_reported"))
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_missing_malformed_and_optional_cost(self):
        self.assertEqual(parse_warp(()).status, AdapterStatus.NO_DATA)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "usage.json"
            path.write_text("{SENTINEL_PRIVATE", encoding="utf-8")
            self.assertEqual(
                parse_warp((path,)).status, AdapterStatus.UNSUPPORTED_FORMAT
            )
            path.write_text(
                json.dumps(
                    {
                        "syncedAt": "2026-07-28T10:00:00Z",
                        "usage": {"requestsUsed": 3},
                    }
                ),
                encoding="utf-8",
            )
            record = parse_warp((path,)).records[0]
            self.assertEqual(record.tokens.total, 0)
            self.assertEqual(record.message_count, 3)
            self.assertIsNone(record.cost)
            self.assertIsNone(record.cost_source)
            self.assertNotIn("SENTINEL_PRIVATE", repr(record))

    def test_request_count_is_clamped_to_frozen_i32_maximum(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "usage.json"
            path.write_text(
                json.dumps(
                    {
                        "syncedAt": "2026-07-28T10:00:00Z",
                        "usage": {"requestsUsed": 2**63 - 1},
                    }
                ),
                encoding="utf-8",
            )
            record = parse_warp((path,)).records[0]
        self.assertEqual(record.message_count, 2**31 - 1)

    def test_deep_cached_json_returns_sanitized_unsupported_format(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "usage.json"
            path.write_text(
                "[" * 2_000 + '"SENTINEL_PRIVATE"' + "]" * 2_000,
                encoding="utf-8",
            )
            result = parse_warp((path,))
        self.assertEqual(result.status, AdapterStatus.UNSUPPORTED_FORMAT)
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_distinct_account_cache_files_do_not_collapse(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = []
            for name, requests in (
                ("usage.a@b.json", 1),
                ("usage.a#b.json", 2),
            ):
                path = Path(folder) / name
                path.write_text(
                    json.dumps(
                        {
                            "syncedAt": "2026-07-28T10:00:00Z",
                            "usage": {"requestsUsed": requests},
                        }
                    ),
                    encoding="utf-8",
                )
                paths.append(path)
            result = parse_warp(tuple(paths))
        self.assertEqual(len(result.records), 2)
        self.assertEqual(
            {record.message_count for record in result.records},
            {1, 2},
        )
        self.assertEqual(len({record.dedup_key for record in result.records}), 2)

    def test_scan_retains_nested_same_basename_cache_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            cache = home / ".config/tokscale/warp-cache"
            for directory, requests in (("one", 1), ("two", 2)):
                path = cache / directory / "usage.json"
                path.parent.mkdir(parents=True)
                path.write_text(
                    json.dumps(
                        {
                            "syncedAt": "2026-07-28T10:00:00Z",
                            "usage": {"requestsUsed": requests},
                        }
                    ),
                    encoding="utf-8",
                )
            result = scan(
                DiscoveryContext("linux", home, {}),
                SOURCE_SPECS["warp"],
            )
        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 2)
        self.assertEqual(
            {record.message_count for record in result.records},
            {1, 2},
        )
        self.assertEqual(len({record.dedup_key for record in result.records}), 2)

    def test_full_source_digest_resists_known_truncated_hash_collision(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            cache = home / ".config/tokscale/warp-cache"
            private_parts = ("acct-42881", "acct-129195")
            for directory, requests in zip(private_parts, (1, 2)):
                path = cache / directory / "usage.json"
                path.parent.mkdir(parents=True)
                path.write_text(
                    json.dumps(
                        {
                            "syncedAt": "2026-07-28T10:00:00Z",
                            "usage": {"requestsUsed": requests},
                        }
                    ),
                    encoding="utf-8",
                )
            result = scan(
                DiscoveryContext("linux", home, {}),
                SOURCE_SPECS["warp"],
            )
        self.assertEqual(len(result.records), 2)
        self.assertEqual(len({record.dedup_key for record in result.records}), 2)
        emitted_identity = " ".join(
            record.session_id + " " + record.dedup_key
            for record in result.records
        )
        for private_part in private_parts:
            self.assertNotIn(private_part, emitted_identity)

    def test_module_has_no_network_or_subprocess_imports(self):
        source = Path(warp.__file__).read_text(encoding="utf-8")
        self.assertNotIn("urllib", source)
        self.assertNotIn("http.client", source)
        self.assertNotIn("subprocess", source)


if __name__ == "__main__":
    unittest.main()
