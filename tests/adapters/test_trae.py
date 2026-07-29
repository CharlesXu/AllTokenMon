import ast
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.alltokenmon.adapters import trae
from scripts.alltokenmon.adapters.base import DiscoveryContext
from scripts.alltokenmon.adapters.registry import SOURCE_SPECS
from scripts.alltokenmon.adapters.trae import _latest, parse_trae, scan
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown


FIXTURE_HOME = Path(__file__).parents[1] / "fixtures"


class TraeAdapterTests(unittest.TestCase):
    def test_committed_cache_cost_and_discovery(self):
        result = scan(
            DiscoveryContext("linux", FIXTURE_HOME, {}),
            SOURCE_SPECS["trae"],
        )
        self.assertEqual(result.status, AdapterStatus.OK)
        record = result.records[0]
        self.assertEqual(record.model, "Claude Sonnet 4.6")
        self.assertEqual(record.provider, "trae")
        self.assertEqual(record.tokens, TokenBreakdown(100, 20, 30, 10))
        self.assertEqual((record.cost, record.cost_source), (0.5, "provider_reported"))

    def test_missing_malformed_boundary_and_invalid_cost(self):
        self.assertEqual(parse_trae(()).status, AdapterStatus.NO_DATA)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "session.json"
            path.write_text("{SENTINEL_PRIVATE", encoding="utf-8")
            self.assertEqual(
                parse_trae((path,)).status, AdapterStatus.UNSUPPORTED_FORMAT
            )
            row = {
                "model_name": "",
                "mode": "Auto",
                "session_id": "s",
                "usage_time": 1785232800,
                "dollar_float": "0.5",
                "extra_info": {"input_token": 1},
            }
            path.write_text(json.dumps([row, row]), encoding="utf-8")
            with mock.patch.object(trae, "_MAX_ROWS", 1):
                result = parse_trae((path,))
            self.assertEqual(result.status, AdapterStatus.PARTIAL)
            self.assertEqual(result.records[0].model, "trae-auto")
            self.assertIsNone(result.records[0].cost)
            self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_repeated_cache_batches_keep_only_latest_session_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = []
            for index, usage_time in enumerate((1785232800, 1785232900)):
                path = Path(folder) / "batch-{}.json".format(index)
                path.write_text(
                    json.dumps(
                        [
                            {
                                "model_name": "GPT-5.4",
                                "session_id": "same-session",
                                "usage_time": usage_time,
                                "dollar_float": index + 0.1,
                                "extra_info": {
                                    "input_token": 10 + index,
                                    "output_token": 1,
                                },
                            }
                        ]
                    ),
                    encoding="utf-8",
                )
                paths.append(path)
            result = parse_trae(tuple(paths))

        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].tokens.input, 11)
        self.assertEqual(result.records[0].cost, 1.1)
        self.assertEqual(result.records[0].timestamp.timestamp(), 1785232900)
        self.assertEqual(result.diagnostics[0].record_count, 1)

    def test_latest_tie_uses_highest_dedup_key_and_sorts_by_session(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "batch.json"
            rows = [
                {
                    "model_name": "GPT-5.4",
                    "session_id": session,
                    "usage_time": 1785232800,
                    "extra_info": {"input_token": tokens},
                }
                for session, tokens in (("z-session", 1), ("a-session", 2))
            ]
            path.write_text(json.dumps(rows), encoding="utf-8")
            parsed = parse_trae((path,)).records
        low = parsed[1]
        high = low.__class__(
            runtime=low.runtime,
            provider=low.provider,
            model=low.model,
            session_id=low.session_id,
            timestamp=low.timestamp,
            tokens=TokenBreakdown(99),
            message_count=low.message_count,
            source_kind=low.source_kind,
            source_path=low.source_path,
            dedup_key="zzzz",
            confidence=low.confidence,
            cost=low.cost,
            cost_source=low.cost_source,
        )
        selected = _latest((low, parsed[0], high))
        self.assertEqual(
            [record.session_id for record in selected],
            ["a-session", "z-session"],
        )
        self.assertEqual(selected[1].tokens.input, 99)

    def test_future_model_and_explicit_provider_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "batch.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "model_name": "Future Model 2030",
                            "provider_id": "future-provider",
                            "session_id": "glm-session",
                            "usage_time": 1785232800,
                            "extra_info": {"input_token": 1},
                        }
                    ]
                ),
                encoding="utf-8",
            )
            result = parse_trae((path,))
        self.assertEqual(result.records[0].model, "Future Model 2030")
        self.assertEqual(result.records[0].provider, "future-provider")

    def test_credential_shaped_provider_is_not_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "batch.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "model_name": "Future Model",
                            "provider_id": "sk-live-123456",
                            "session_id": "safe-session",
                            "usage_time": 1785232800,
                            "extra_info": {"input_token": 1},
                        }
                    ]
                ),
                encoding="utf-8",
            )
            result = parse_trae((path,))
        self.assertEqual(result.records[0].provider, "trae")
        self.assertNotIn("sk-live-123456", repr(result))

    def test_deep_cached_json_returns_sanitized_unsupported_format(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "deep.json"
            path.write_text(
                "[" * 2_000 + '"SENTINEL_PRIVATE"' + "]" * 2_000,
                encoding="utf-8",
            )
            result = parse_trae((path,))
        self.assertEqual(result.status, AdapterStatus.UNSUPPORTED_FORMAT)
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_module_has_no_network_or_subprocess_imports(self):
        source = Path(trae.__file__).read_text(encoding="utf-8")
        self.assertNotIn("urllib", source)
        self.assertNotIn("http.client", source)
        self.assertNotIn("subprocess", source)


if __name__ == "__main__":
    unittest.main()
