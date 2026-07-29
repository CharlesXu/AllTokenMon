import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.alltokenmon.aggregate import aggregate
from scripts.alltokenmon.adapters.cline import parse_cline
from scripts.alltokenmon.adapters.kilocode import parse_kilocode
from scripts.alltokenmon.adapters.roocode import parse_roocode
from scripts.alltokenmon.normalize import stable_key
from scripts.alltokenmon.report import render_json, render_markdown
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown
from tests.adapters.file_contract import parse_fixture


class ClineAdapterTests(unittest.TestCase):
    def test_same_task_file_gets_three_distinct_runtime_namespaces(self):
        fixture = (
            Path(__file__).parent
            / "fixtures/cline/ui_messages.json"
        )
        results = (
            parse_cline((fixture,)),
            parse_roocode((fixture,)),
            parse_kilocode((fixture,)),
        )

        self.assertEqual(
            [result.runtime for result in results],
            ["cline", "roocode", "kilocode"],
        )
        self.assertEqual(
            [result.records[0].dedup_key for result in results],
            [
                stable_key(runtime, "cline", "request-1")
                for runtime in ("cline", "roocode", "kilocode")
            ],
        )
        self.assertEqual(
            [result.records[0].model for result in results],
            ["claude-sonnet-4-6"] * 3,
        )

    def test_frozen_task_usage_contract(self):
        result = parse_fixture(
            parse_cline, "cline", "tasks/task-frozen/ui_messages.json"
        )
        records = {record.dedup_key: record for record in result.records}

        self.assertEqual(len(records), 3)
        covered = records[stable_key("cline", "task-frozen", "request-1")]
        self.assertEqual(covered.provider, "anthropic")
        self.assertEqual(covered.model, "claude-sonnet-4-6")
        self.assertEqual(covered.session_id, "task-frozen")
        self.assertEqual(covered.tokens, TokenBreakdown(101, 50, 20, 5, 0))
        self.assertEqual(covered.timestamp.isoformat(), "2026-02-18T12:00:01+00:00")
        self.assertEqual(covered.cost, 0.13)
        self.assertEqual(covered.cost_source, "provider_reported")
        self.assertEqual(covered.source_kind, "json")

        nested = records[stable_key("cline", "task-frozen", "request-2")]
        self.assertEqual(nested.provider, "bedrock/anthropic")
        self.assertEqual(nested.timestamp.isoformat(), "2026-05-31T23:28:17.480000+00:00")
        self.assertEqual(nested.tokens, TokenBreakdown(0, 2, 0, 0, 0))
        self.assertIsNone(nested.cost)
        self.assertIsNone(nested.cost_source)

        zero = records[stable_key("cline", "task-frozen", "request-zero")]
        self.assertEqual(zero.tokens, TokenBreakdown())
        self.assertEqual(zero.cost, 0.0)
        self.assertEqual(zero.message_count, 1)
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_malformed_embedded_json_retains_earlier_usage_as_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks/task-partial/ui_messages.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    [
                        {
                            "id": "valid",
                            "type": "say",
                            "say": "api_req_started",
                            "ts": "2026-02-18T12:00:00Z",
                            "text": json.dumps(
                                {
                                    "tokensIn": 1,
                                    "tokensOut": 2,
                                    "apiProtocol": "openai",
                                }
                            ),
                        },
                        {
                            "id": "broken",
                            "type": "say",
                            "say": "api_req_started",
                            "ts": "2026-02-18T12:00:01Z",
                            "text": '{"tokensIn":3,SENTINEL_PRIVATE',
                        },
                    ]
                ),
                encoding="utf-8",
            )
            result = parse_cline((path,))

        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].tokens, TokenBreakdown(1, 2))
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_embedded_object_depth_is_bounded(self):
        nested = {}
        for _ in range(40):
            nested = {"nested": nested}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task/ui_messages.json"
            path.parent.mkdir()
            path.write_text(
                json.dumps(
                    [
                        {
                            "id": "too-deep",
                            "type": "say",
                            "say": "api_req_started",
                            "ts": 1780270097480,
                            "text": json.dumps(nested),
                        }
                    ]
                ),
                encoding="utf-8",
            )
            result = parse_cline((path,))

        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(result.records, ())

    def test_empty_payload_is_no_data_but_explicit_zero_usage_is_a_row(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task/ui_messages.json"
            path.parent.mkdir()
            base = {
                "type": "say",
                "say": "api_req_started",
                "ts": 1780270097480,
            }
            path.write_text(
                json.dumps(
                    [
                        dict(base, id="empty", text="{}"),
                        dict(
                            base,
                            id="provider-only",
                            text='{"apiProtocol":"openai"}',
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            empty_result = parse_cline((path,))
            path.write_text(
                json.dumps(
                    [
                        dict(
                            base,
                            id="zero",
                            text='{"tokensIn":0,"tokensOut":0}',
                        )
                    ]
                ),
                encoding="utf-8",
            )
            zero_result = parse_cline((path,))

        self.assertEqual(empty_result.status, AdapterStatus.NO_DATA)
        self.assertEqual(empty_result.records, ())
        self.assertEqual(len(zero_result.records), 1)
        self.assertEqual(zero_result.records[0].tokens, TokenBreakdown())

    def test_raw_ui_ids_are_hashed_and_invalid_costs_remain_unreported(self):
        invalid_costs = ("NaN", "inf", -1, {"amount": 1}, False)
        events = []
        for index, cost in enumerate(invalid_costs):
            events.append(
                {
                    "id": "SENTINEL_PRIVATE_ID_{}".format(index),
                    "type": "say",
                    "say": "api_req_started",
                    "ts": 1780270097480 + index,
                    "text": json.dumps({"tokensIn": index + 1, "cost": cost}),
                }
            )
        events.extend(
            [
                {
                    "id": "SENTINEL_PRIVATE_DUPLICATE",
                    "type": "say",
                    "say": "api_req_started",
                    "ts": 1780270097490,
                    "text": '{"tokensIn":1}',
                },
                {
                    "id": "SENTINEL_PRIVATE_DUPLICATE",
                    "type": "say",
                    "say": "api_req_started",
                    "ts": 1780270097491,
                    "text": '{"tokensIn":9}',
                },
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task/ui_messages.json"
            path.parent.mkdir()
            path.write_text(json.dumps(events), encoding="utf-8")
            result = parse_cline((path,))

        self.assertEqual(len(result.records), 6)
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))
        invalid_records = [
            record for record in result.records if record.tokens.input != 9
        ]
        self.assertTrue(all(record.cost is None for record in invalid_records))
        self.assertTrue(
            all(record.cost_source is None for record in invalid_records)
        )
        duplicate_key = stable_key(
            "cline", "task", "SENTINEL_PRIVATE_DUPLICATE"
        )
        duplicate = {
            record.dedup_key: record for record in result.records
        }[duplicate_key]
        self.assertEqual(duplicate.tokens.input, 9)

    def test_model_must_be_a_bounded_nonempty_string(self):
        models = ("  gpt-5  ", "", {"name": "private"}, "x" * 257)
        events = [
            {
                "id": "model-{}".format(index),
                "type": "say",
                "say": "api_req_started",
                "ts": 1780270097480 + index,
                "text": json.dumps({"tokensIn": 1, "model": model}),
            }
            for index, model in enumerate(models)
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task/ui_messages.json"
            path.parent.mkdir()
            path.write_text(json.dumps(events), encoding="utf-8")
            result = parse_cline((path,))

        self.assertEqual(
            [record.model for record in result.records],
            ["gpt-5", "unknown", "unknown", "unknown"],
        )

    def test_api_protocol_uses_a_bounded_frozen_canonical_table(self):
        cases = (
            (" ANTHROPIC ", "anthropic"),
            ("OpenAI", "openai"),
            ("openai-native", "openai"),
            ("Gemini", "google"),
            ("Vertex", "anthropic"),
            ("OpenRouter", "openrouter"),
            ("BEDROCK", "bedrock"),
            ("BedRock/Anthropic", "bedrock/anthropic"),
            ("Azure/OpenAI", "azure/openai"),
            ("x-ai", "xai"),
            ("unknown-provider", "unknown"),
            ("x" * 65, "unknown"),
            (None, "unknown"),
            ({"provider": "private"}, "unknown"),
            (True, "unknown"),
        )
        events = [
            {
                "id": "provider-{}".format(index),
                "type": "say",
                "say": "api_req_started",
                "ts": 1780270097480 + index,
                "text": json.dumps(
                    {"tokensIn": 1, "apiProtocol": protocol}
                ),
            }
            for index, (protocol, _) in enumerate(cases)
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task/ui_messages.json"
            path.parent.mkdir()
            path.write_text(json.dumps(events), encoding="utf-8")
            result = parse_cline((path,))

        self.assertEqual(
            [record.provider for record in result.records],
            [expected for _, expected in cases],
        )

    def test_private_api_protocol_never_reaches_results_or_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task/ui_messages.json"
            path.parent.mkdir()
            path.write_text(
                json.dumps(
                    [
                        {
                            "id": "private-provider",
                            "type": "say",
                            "say": "api_req_started",
                            "ts": 1780270097480,
                            "text": json.dumps(
                                {
                                    "tokensIn": 1,
                                    "apiProtocol": "SENTINEL_PRIVATE_PROVIDER",
                                }
                            ),
                        }
                    ]
                ),
                encoding="utf-8",
            )
            result = parse_cline((path,))

        report = aggregate(
            result.records,
            result.diagnostics,
            datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(result.records[0].provider, "unknown")
        self.assertNotIn("SENTINEL_PRIVATE_PROVIDER", repr(result))
        self.assertNotIn("SENTINEL_PRIVATE_PROVIDER", render_json(report))
        self.assertNotIn("SENTINEL_PRIVATE_PROVIDER", render_markdown(report))

    def test_whole_file_entry_depth_is_bounded(self):
        nested = {}
        for _ in range(20):
            nested = {"nested": nested}
        event = {
            "id": "too-deep",
            "type": "say",
            "say": "api_req_started",
            "ts": 1780270097480,
            "text": "{}",
            "extra": nested,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task/ui_messages.json"
            path.parent.mkdir()
            path.write_text(json.dumps([event]), encoding="utf-8")
            result = parse_cline((path,))

        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(result.records, ())
