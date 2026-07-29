import json
import tempfile
import unittest
from pathlib import Path

from scripts.alltokenmon.adapters.devin_desktop import parse_devin_desktop
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown
from tests.adapters.file_contract import assert_status_isolation, parse_fixture


class DevinDesktopAdapterTests(unittest.TestCase):
    def test_contract_acp_cumulative_and_output_sum(self):
        record = parse_fixture(
            parse_devin_desktop, "devin-desktop", "session.ndjson"
        ).records[0]
        self.assertEqual(record.runtime, "devin-desktop")
        self.assertEqual(record.provider, "openai")
        self.assertEqual(record.model, "gpt-5")
        self.assertEqual(record.session_id, "session")
        self.assertEqual(record.tokens, TokenBreakdown(38, 15, 12, 3))
        self.assertEqual(record.dedup_key.endswith(":usage"), True)

    def test_malformed_and_unsupported(self):
        assert_status_isolation(parse_devin_desktop, ".ndjson")

    def test_legacy_metrics_are_parsed_when_cumulative_updates_are_absent(self):
        events = (
            {"ignored": True},
            {"notification": {"sessionUpdate": "session_info_update"}},
            {"notification": {"sessionUpdate": "usage_update", "_meta": {}}},
            {
                "notification": {
                    "content": {
                        "metadata": {
                            "metrics": {
                                "input_tokens": 4,
                                "output_tokens": 2,
                                "cache_read_tokens": 1,
                                "cache_creation_tokens": 3,
                            },
                            "generation_model": "claude-sonnet-4",
                            "created_at": "2026-07-29T00:00:00Z",
                        }
                    }
                }
            },
            {"notification": {"metrics": {"input_tokens": 0}}},
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.ndjson"
            path.write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )
            result = parse_devin_desktop((path,))

        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.provider, "anthropic")
        self.assertEqual(record.model, "claude-sonnet-4")
        self.assertEqual(record.tokens, TokenBreakdown(4, 2, 1, 3))
        self.assertEqual(record.timestamp.isoformat(), "2026-07-29T00:00:00+00:00")
