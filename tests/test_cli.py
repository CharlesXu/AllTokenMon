import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from scripts.alltokenmon import cli
from scripts.alltokenmon.schema import (
    AdapterResult,
    AdapterStatus,
    Diagnostic,
    TokenBreakdown,
    UsageRecord,
)


NOW = "2026-07-28T12:00:00+08:00"
ROOT = Path(__file__).resolve().parents[1]


def _record(
    runtime="codex",
    model="gpt-5",
    timestamp="2026-07-28T01:00:00+08:00",
    total=10,
):
    return UsageRecord(
        runtime=runtime,
        provider="provider",
        model=model,
        session_id="session",
        timestamp=datetime.fromisoformat(timestamp),
        tokens=TokenBreakdown(input=total),
        message_count=1,
        source_kind="fixture",
        source_path="/PRIVATE/source.jsonl",
        dedup_key="{}:{}:{}".format(runtime, model, timestamp),
        confidence="exact",
    )


def _result(runtime, status=AdapterStatus.OK, records=(), code=None, message="Safe"):
    diagnostic = Diagnostic(
        runtime=runtime,
        status=status,
        code=code or status.value,
        message=message,
        source_count=1,
        record_count=len(records),
    )
    return AdapterResult(runtime, status, tuple(records), (diagnostic,))


def _run(*arguments):
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        return_code = cli.main(arguments)
    return return_code, stdout.getvalue(), stderr.getvalue()


@contextmanager
def _process_timezone(value):
    previous = os.environ.get("TZ")
    os.environ["TZ"] = value
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


class CliTests(unittest.TestCase):
    def test_imported_main_matches_script_for_unpatched_no_data_scan(self):
        with tempfile.TemporaryDirectory() as home:
            arguments = (
                "--runtime",
                "codex",
                "--home",
                home,
                "--format",
                "json",
                "--now",
                NOW,
            )
            return_code, imported_stdout, imported_stderr = _run(*arguments)
            script = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "token_usage.py"),
                    *arguments,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(return_code, 0)
        self.assertEqual(script.returncode, 0, script.stderr)
        self.assertEqual(imported_stdout, script.stdout)
        self.assertEqual(imported_stderr, script.stderr)
        self.assertEqual(json.loads(imported_stdout)["coverage"]["status"], "no_data")

    def test_markdown_uses_utf8_when_process_default_is_cp1252(self):
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "cp1252"
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "token_usage.py"),
                "--runtime",
                "codex",
                "--home",
                str(ROOT / "tests" / "fixtures"),
                "--format",
                "markdown",
                "--now",
                NOW,
            ],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(
            completed.returncode,
            0,
            completed.stderr.decode("utf-8", errors="replace"),
        )
        self.assertIn("亿 tokens", completed.stdout.decode("utf-8"))

    def test_scan_runtime_only_treats_missing_target_module_as_unavailable(self):
        target = "{}.adapters.codex".format(cli.__package__)
        missing_target = ModuleNotFoundError(name=target)
        missing_dependency = ModuleNotFoundError(name="nested_dependency")

        with patch.object(
            cli.importlib, "import_module", side_effect=missing_target
        ):
            result = cli._scan_runtime(
                "codex",
                cli.DiscoveryContext("linux", Path("/home/tester"), {}),
            )
        self.assertEqual(result.status, AdapterStatus.NO_DATA)

        with patch.object(
            cli.importlib, "import_module", side_effect=missing_dependency
        ):
            with self.assertRaises(ModuleNotFoundError):
                cli._scan_runtime(
                    "codex",
                    cli.DiscoveryContext("linux", Path("/home/tester"), {}),
                )

    def test_runtime_csv_is_deduplicated_and_runs_in_registry_order(self):
        calls = []

        def scan(runtime, context):
            calls.append(runtime)
            return _result(runtime)

        with patch.object(cli, "_scan_runtime", side_effect=scan):
            return_code, stdout, stderr = _run(
                "--runtime",
                "codex,claude,codex",
                "--format",
                "json",
                "--now",
                NOW,
            )

        self.assertEqual(return_code, 0)
        self.assertEqual(calls, ["claude", "codex"])
        self.assertEqual(json.loads(stdout)["coverage"]["runtime_count"], 2)
        self.assertEqual(stderr, "")

    def test_model_csv_globs_are_or_combined_and_case_sensitive(self):
        records = (
            _record(model="gpt-5"),
            _record(model="GPT-5"),
            _record(model="Claude-opus-4"),
            _record(model="other"),
        )
        with patch.object(
            cli, "_scan_runtime", return_value=_result("codex", records=records)
        ):
            return_code, stdout, _ = _run(
                "--runtime",
                "codex",
                "--model",
                "gpt-*,Claude-*",
                "--format",
                "json",
                "--now",
                NOW,
            )

        report = json.loads(stdout)
        models = [
            row["model"] for row in report["periods"]["all_time"]["models"]
        ]
        self.assertEqual(return_code, 0)
        self.assertEqual(models, ["Claude-opus-4", "gpt-5"])

    def test_json_format_is_parseable_and_stdout_contains_only_json(self):
        with patch.object(
            cli,
            "_scan_runtime",
            return_value=_result("codex", records=(_record(),)),
        ):
            return_code, stdout, stderr = _run(
                "--runtime", "codex", "--format", "json", "--now", NOW
            )

        self.assertEqual(return_code, 0)
        self.assertEqual(json.loads(stdout)["schema_version"], "1")
        self.assertTrue(stdout.startswith("{\n"))
        self.assertEqual(stderr, "")

    def test_markdown_format_is_a_brief_and_stdout_contains_only_markdown(self):
        with patch.object(
            cli,
            "_scan_runtime",
            return_value=_result("codex", records=(_record(),)),
        ):
            return_code, stdout, stderr = _run(
                "--runtime", "codex", "--format", "markdown", "--now", NOW
            )

        self.assertEqual(return_code, 0)
        self.assertTrue(stdout.startswith("# Token Usage Report\n"))
        self.assertIn("## Period Summary", stdout)
        self.assertEqual(stderr, "")

    def test_home_exactly_replaces_context_home_and_env_is_copied(self):
        contexts = []

        def scan(runtime, context):
            contexts.append(context)
            return _result(runtime, AdapterStatus.NO_DATA)

        home = Path("portable-profile")
        with patch.object(cli, "_scan_runtime", side_effect=scan):
            return_code, _, _ = _run(
                "--runtime", "codex", "--home", str(home), "--now", NOW
            )

        self.assertEqual(return_code, 0)
        self.assertEqual(contexts[0].home, home)
        self.assertEqual(dict(contexts[0].env), dict(os.environ))
        self.assertIsNot(contexts[0].env, os.environ)

    def test_now_preserves_offset_for_local_period_boundaries(self):
        records = (
            _record(
                model="at-midnight",
                timestamp="2026-07-28T00:00:00+08:00",
                total=10,
            ),
            _record(
                model="before-midnight",
                timestamp="2026-07-27T23:59:59+08:00",
                total=20,
            ),
        )
        with patch.object(
            cli, "_scan_runtime", return_value=_result("codex", records=records)
        ):
            _, stdout, _ = _run(
                "--runtime", "codex", "--format", "json", "--now", NOW
            )

        report = json.loads(stdout)
        self.assertEqual(report["generated_at"], NOW)
        self.assertEqual(report["periods"]["today"]["totals"]["total"], 10)
        self.assertEqual(report["periods"]["all_time"]["totals"]["total"], 30)

    def test_explicit_tz_uses_zoneinfo_across_dst_week_boundary(self):
        try:
            ZoneInfo("America/New_York")
        except ZoneInfoNotFoundError:
            self.skipTest("system IANA timezone database is unavailable")

        records = (
            _record(
                model="at-week-start",
                timestamp="2026-03-04T00:00:00-05:00",
                total=10,
            ),
            _record(
                model="before-week-start",
                timestamp="2026-03-03T23:59:59-05:00",
                total=20,
            ),
        )
        with patch.dict(os.environ, {"TZ": "America/New_York"}):
            with patch.object(
                cli,
                "_scan_runtime",
                return_value=_result("codex", records=records),
            ):
                _, stdout, _ = _run(
                    "--runtime",
                    "codex",
                    "--format",
                    "json",
                    "--now",
                    "2026-03-10T12:00:00Z",
                )

        report = json.loads(stdout)
        self.assertEqual(report["generated_at"], "2026-03-10T08:00:00-04:00")
        self.assertEqual(report["timezone"], "America/New_York")
        self.assertEqual(report["periods"]["week"]["totals"]["total"], 10)
        self.assertEqual(report["periods"]["all_time"]["totals"]["total"], 30)

    def test_invalid_explicit_tz_falls_back_without_leaking_value(self):
        invalid_tz = "PRIVATE_SECRET_TIMEZONE"
        with patch.object(
            cli,
            "_timezone_from_path",
            side_effect=AssertionError("must not inspect localtime"),
        ):
            timezone_value = cli._local_timezone({"TZ": invalid_tz})
        self.assertIsNotNone(timezone_value)

        with patch.dict(os.environ, {"TZ": invalid_tz}):
            with patch.object(
                cli,
                "_scan_runtime",
                return_value=_result("codex", AdapterStatus.NO_DATA),
            ):
                _, stdout, stderr = _run(
                    "--runtime",
                    "codex",
                    "--format",
                    "json",
                    "--now",
                    NOW,
                )

        self.assertEqual(json.loads(stdout)["generated_at"], NOW)
        self.assertNotIn(invalid_tz, stdout + stderr)

    @unittest.skipUnless(hasattr(time, "tzset"), "time.tzset is unavailable")
    def test_posix_tz_uses_process_timezone_without_inspecting_localtime(self):
        with _process_timezone("UTC0"):
            with patch.object(
                cli,
                "_timezone_from_path",
                side_effect=AssertionError("must not inspect localtime"),
            ):
                timezone_value = cli._local_timezone(dict(os.environ))

        self.assertEqual(
            datetime(2026, 1, 1, tzinfo=timezone_value).utcoffset(),
            timedelta(0),
        )

    def test_valid_no_data_scan_exits_zero(self):
        with patch.object(
            cli,
            "_scan_runtime",
            return_value=_result("codex", AdapterStatus.NO_DATA),
        ):
            return_code, stdout, stderr = _run(
                "--runtime", "codex", "--format", "json", "--now", NOW
            )

        self.assertEqual(return_code, 0)
        self.assertEqual(json.loads(stdout)["coverage"]["status"], "no_data")
        self.assertEqual(stderr, "")

    def test_partial_scan_exits_zero_and_emits_only_safe_diagnostic(self):
        result = _result(
            "codex",
            AdapterStatus.PARTIAL,
            records=(_record(),),
            code="malformed_source",
            message="/PRIVATE/path SECRET_MESSAGE",
        )
        with patch.object(cli, "_scan_runtime", return_value=result):
            return_code, stdout, stderr = _run(
                "--runtime",
                "codex",
                "--format",
                "json",
                "--diagnostics",
                "--now",
                NOW,
            )

        self.assertEqual(return_code, 0)
        self.assertEqual(stderr, "codex:partial:malformed_source\n")
        self.assertNotIn("PRIVATE", stdout + stderr)
        self.assertNotIn("SECRET_MESSAGE", stdout + stderr)
        self.assertEqual(
            json.loads(stdout)["diagnostics"][0]["code"], "malformed_source"
        )

    def test_adapter_exception_is_isolated_without_leaking_details(self):
        def scan(runtime, context):
            if runtime == "claude":
                raise ValueError("/PRIVATE/path SECRET_EXCEPTION")
            return _result(runtime, records=(_record(runtime=runtime),))

        with patch.object(cli, "_scan_runtime", side_effect=scan):
            return_code, stdout, stderr = _run(
                "--runtime",
                "claude,codex",
                "--format",
                "json",
                "--diagnostics",
                "--now",
                NOW,
            )

        report = json.loads(stdout)
        self.assertEqual(return_code, 0)
        self.assertEqual(report["coverage"]["status"], "partial")
        self.assertEqual(report["periods"]["all_time"]["totals"]["total"], 10)
        self.assertIn("claude:error:adapter_valueerror\n", stderr)
        self.assertNotIn("PRIVATE", stdout + stderr)
        self.assertNotIn("SECRET_EXCEPTION", stdout + stderr)

    def test_sole_exception_or_error_result_is_fatal_and_private(self):
        failures = (
            ValueError("/PRIVATE/path SECRET_EXCEPTION"),
            _result(
                "codex",
                AdapterStatus.ERROR,
                code="read_error",
                message="/PRIVATE/path SECRET_MESSAGE",
            ),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                if isinstance(failure, Exception):
                    scan = patch.object(
                        cli,
                        "_scan_runtime",
                        side_effect=failure,
                    )
                else:
                    scan = patch.object(
                        cli,
                        "_scan_runtime",
                        return_value=failure,
                    )
                with scan:
                    return_code, stdout, stderr = _run(
                        "--runtime",
                        "codex",
                        "--format",
                        "json",
                        "--now",
                        NOW,
                    )

                self.assertNotEqual(return_code, 0)
                self.assertEqual(stdout, "")
                self.assertEqual(
                    stderr,
                    "all-token-monitor:error:no_scan\n",
                )
                self.assertNotIn("PRIVATE", stdout + stderr)
                self.assertNotIn("SECRET", stdout + stderr)

    def test_two_all_error_adapters_are_fatal(self):
        def scan(runtime, context):
            if runtime == "claude":
                raise OSError("/PRIVATE/claude")
            return _result(
                runtime,
                AdapterStatus.ERROR,
                code="read_error",
                message="/PRIVATE/codex",
            )

        with patch.object(cli, "_scan_runtime", side_effect=scan):
            return_code, stdout, stderr = _run(
                "--runtime",
                "claude,codex",
                "--format",
                "json",
                "--now",
                NOW,
            )

        self.assertNotEqual(return_code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "all-token-monitor:error:no_scan\n")
        self.assertNotIn("PRIVATE", stderr)

    def test_error_plus_no_data_is_a_successful_partial_scan(self):
        def scan(runtime, context):
            if runtime == "claude":
                raise OSError("/PRIVATE/claude")
            return _result(runtime, AdapterStatus.NO_DATA)

        with patch.object(cli, "_scan_runtime", side_effect=scan):
            return_code, stdout, stderr = _run(
                "--runtime",
                "claude,codex",
                "--format",
                "json",
                "--now",
                NOW,
            )

        self.assertEqual(return_code, 0)
        self.assertEqual(json.loads(stdout)["coverage"]["status"], "partial")
        self.assertEqual(stderr, "")

    def test_error_plus_ok_is_a_successful_partial_scan_without_diagnostics(self):
        def scan(runtime, context):
            if runtime == "claude":
                raise OSError("/PRIVATE/claude")
            return _result(runtime, records=(_record(runtime=runtime),))

        with patch.object(cli, "_scan_runtime", side_effect=scan):
            return_code, stdout, stderr = _run(
                "--runtime",
                "claude,codex",
                "--format",
                "json",
                "--now",
                NOW,
            )

        report = json.loads(stdout)
        self.assertEqual(return_code, 0)
        self.assertEqual(report["coverage"]["status"], "partial")
        self.assertEqual(report["periods"]["all_time"]["totals"]["total"], 10)
        self.assertEqual(stderr, "")

    def test_unsafe_adapter_diagnostic_code_is_replaced(self):
        result = _result(
            "codex",
            AdapterStatus.PARTIAL,
            code="/PRIVATE/source.jsonl",
        )
        with patch.object(cli, "_scan_runtime", return_value=result):
            _, stdout, stderr = _run(
                "--runtime",
                "codex",
                "--format",
                "json",
                "--diagnostics",
                "--now",
                NOW,
            )

        self.assertEqual(stderr, "codex:partial:adapter_diagnostic\n")
        self.assertNotIn("PRIVATE", stdout + stderr)

    def test_secret_shaped_and_absolute_diagnostic_codes_never_reach_outputs(self):
        class EvilCode(str):
            __hash__ = None

        hostile_codes = (
            "sk-test-secret-path",
            "/Users/private/COOKIE_SENTINEL/session.jsonl",
            EvilCode("PROMPT_SENTINEL_DO_NOT_LEAK-evil-code"),
        )
        for code in hostile_codes:
            for output_format in ("json", "markdown"):
                with self.subTest(code=code, output_format=output_format):
                    result = _result(
                        "codex",
                        AdapterStatus.PARTIAL,
                        records=(_record(),),
                        code=code,
                        message="/Users/private/PROMPT_SENTINEL_DO_NOT_LEAK",
                    )
                    with patch.object(cli, "_scan_runtime", return_value=result):
                        _, stdout, stderr = _run(
                            "--runtime",
                            "codex",
                            "--format",
                            output_format,
                            "--diagnostics",
                            "--now",
                            NOW,
                        )

                    self.assertEqual(
                        stderr,
                        "codex:partial:adapter_diagnostic\n",
                    )
                    for secret in (
                        code,
                        "/Users/private",
                        "PROMPT_SENTINEL_DO_NOT_LEAK",
                        "COOKIE_SENTINEL",
                        "sk-test-secret",
                    ):
                        self.assertNotIn(secret, stdout + stderr)
                    if output_format == "json":
                        self.assertEqual(
                            json.loads(stdout)["diagnostics"][0]["code"],
                            "adapter_diagnostic",
                        )
                    else:
                        self.assertTrue(stdout.startswith("# Token Usage Report\n"))

    def test_unhashable_exception_type_is_sanitized_across_output_surfaces(self):
        class UnhashableExceptionMeta(type):
            __hash__ = None

        class CookieSentinelException(
            Exception,
            metaclass=UnhashableExceptionMeta,
        ):
            pass

        def scan(runtime, context):
            if runtime == "claude":
                raise CookieSentinelException(
                    "/Users/private/sk-test-secret-exception"
                )
            return _result(
                runtime,
                records=(_record(runtime=runtime),),
            )

        for output_format in ("json", "markdown"):
            with self.subTest(output_format=output_format):
                with patch.object(cli, "_scan_runtime", side_effect=scan):
                    return_code, stdout, stderr = _run(
                        "--runtime",
                        "claude,codex",
                        "--format",
                        output_format,
                        "--diagnostics",
                        "--now",
                        NOW,
                    )

                self.assertEqual(return_code, 0)
                self.assertEqual(stderr, "claude:error:adapter_error\ncodex:ok:ok\n")
                for secret in (
                    "CookieSentinelException",
                    "/Users/private",
                    "sk-test-secret",
                    "COOKIE_SENTINEL",
                ):
                    self.assertNotIn(secret, stdout + stderr)
                if output_format == "json":
                    diagnostics = json.loads(stdout)["diagnostics"]
                    self.assertEqual(diagnostics[0]["code"], "adapter_error")
                else:
                    self.assertTrue(stdout.startswith("# Token Usage Report\n"))

    def test_known_internal_diagnostic_codes_remain_stable(self):
        known_codes = (
            "ok",
            "no_data",
            "unsupported_format",
            "partial",
            "error",
            "read_error",
            "partial_source",
            "resource_limit",
            "malformed_source",
            "adapter_unavailable",
            "adapter_valueerror",
        )
        self.assertEqual(
            tuple(cli._safe_code(code) for code in known_codes),
            known_codes,
        )
        self.assertEqual(
            cli._safe_code(["sk-test-secret-path"]),
            "adapter_diagnostic",
        )

    def test_exception_codes_use_fixed_types_and_never_class_names(self):
        class SkTestSecretPath(Exception):
            pass

        cases = (
            (ValueError("private"), "adapter_valueerror"),
            (OSError("private"), "adapter_oserror"),
            (TypeError("private"), "adapter_typeerror"),
            (SkTestSecretPath("private"), "adapter_error"),
        )
        for error, expected in cases:
            with self.subTest(error=type(error).__name__):
                diagnostic = cli._exception_diagnostic("codex", error)
                self.assertEqual(diagnostic.code, expected)
                self.assertNotIn("sk", diagnostic.code.lower())
                self.assertNotIn("private", diagnostic.message.lower())

    def test_invalid_runtime_and_now_are_argparse_errors(self):
        for arguments in (
            ("--runtime", "unknown-runtime"),
            ("--runtime", "codex", "--now", "not-a-timestamp"),
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(SystemExit) as raised:
                    _run(*arguments)
                self.assertNotEqual(raised.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
