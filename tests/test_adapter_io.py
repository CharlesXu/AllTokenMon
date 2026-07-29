import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.alltokenmon.adapters import jsonio
from scripts.alltokenmon.adapters.jsonio import (
    MAX_JSON_BYTES,
    MAX_JSONL_LINE_BYTES,
    MAX_JSONL_RECORDS,
    read_json,
    read_json_lines,
)
from scripts.alltokenmon.adapters.sqliteio import (
    SqliteReadError,
    open_sqlite_readonly,
    quote_identifier,
    sqlite_schema,
)


class JsonIoTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_json_lines_retains_valid_mappings_before_malformed_record(self):
        path = self.root / "records.jsonl"
        path.write_bytes(b'{"first": 1}\n{"second": 2}\n{"secret":\n')

        result = read_json_lines(path)

        self.assertEqual(result.values, ({"first": 1}, {"second": 2}))
        self.assertTrue(result.partial)
        self.assertEqual(result.error_code, "malformed_json:JSONDecodeError")
        self.assertNotIn("secret", result.error_code)

    def test_json_lines_invalid_utf8_does_not_expose_input(self):
        path = self.root / "records.jsonl"
        path.write_bytes(b'{"first": 1}\nprivate-token-\xff\n')

        result = read_json_lines(path)

        self.assertEqual(result.values, ({"first": 1},))
        self.assertTrue(result.partial)
        self.assertEqual(result.error_code, "decode_error:UnicodeDecodeError")
        self.assertNotIn("private-token", result.error_code)
        self.assertNotIn("\\xff", result.error_code)

    def test_oversized_json_is_rejected_before_file_body_is_opened(self):
        path = self.root / "large.json"
        path.touch()

        with mock.patch.object(
            Path, "stat", autospec=True
        ) as stat, mock.patch.object(
            Path, "open", autospec=True
        ) as opened:
            stat.return_value.st_size = MAX_JSON_BYTES + 1

            result = read_json(path)

        self.assertIsNone(result.value)
        self.assertFalse(result.partial)
        self.assertEqual(result.error_code, "unsupported_format")
        opened.assert_not_called()

    def test_json_reader_handles_valid_and_routine_corrupt_inputs(self):
        valid = self.root / "valid.json"
        valid.write_text('{"ok": true}', encoding="utf-8")
        malformed = self.root / "malformed.json"
        malformed.write_text('{"private":', encoding="utf-8")
        invalid_utf8 = self.root / "invalid.json"
        invalid_utf8.write_bytes(b'{"private":"\xff"}')

        self.assertEqual(read_json(valid).value, {"ok": True})
        self.assertEqual(
            read_json(malformed).error_code,
            "malformed_json:JSONDecodeError",
        )
        self.assertEqual(
            read_json(invalid_utf8).error_code,
            "decode_error:UnicodeDecodeError",
        )
        self.assertEqual(
            read_json(self.root / "missing.json").error_code,
            "io_error:FileNotFoundError",
        )

    def test_deep_json_recursion_is_sanitized(self):
        private = "SENTINEL_PRIVATE"
        nested = "[" * 2_000 + '"{}"'.format(private) + "]" * 2_000
        document = self.root / "deep.json"
        document.write_text(nested, encoding="utf-8")
        lines = self.root / "deep.jsonl"
        lines.write_text(
            '{"ok":true}\n{"deep":' + nested + "}\n",
            encoding="utf-8",
        )

        document_result = read_json(document)
        lines_result = read_json_lines(lines)

        self.assertIsNone(document_result.value)
        self.assertEqual(
            document_result.error_code,
            "malformed_json:nesting_limit",
        )
        self.assertEqual(lines_result.values, ({"ok": True},))
        self.assertTrue(lines_result.partial)
        self.assertEqual(
            lines_result.error_code,
            "malformed_json:nesting_limit",
        )
        self.assertNotIn(private, repr((document_result, lines_result)))

    def test_json_recursion_errors_are_sanitized_at_both_reader_boundaries(self):
        document = self.root / "shallow.json"
        document.write_text('{"SENTINEL_PRIVATE":true}', encoding="utf-8")
        lines = self.root / "shallow.jsonl"
        lines.write_text('{"SENTINEL_PRIVATE":true}\n', encoding="utf-8")

        with mock.patch.object(
            jsonio.json, "loads", side_effect=RecursionError
        ):
            document_result = read_json(document)
            lines_result = read_json_lines(lines)

        self.assertEqual(
            document_result.error_code,
            "malformed_json:RecursionError",
        )
        self.assertEqual(
            lines_result.error_code,
            "malformed_json:RecursionError",
        )
        self.assertNotIn(
            "SENTINEL_PRIVATE",
            repr((document_result, lines_result)),
        )

    @unittest.skipUnless(
        hasattr(sys, "get_int_max_str_digits"),
        "Python runtime has no integer digit limit",
    )
    def test_json_reader_handles_integer_digit_limit_as_malformed(self):
        path = self.root / "huge-number.json"
        digit_limit = sys.get_int_max_str_digits()
        if not digit_limit:
            self.skipTest("integer digit limit is disabled")
        digits = b"9" * (digit_limit + 1)
        path.write_bytes(b'{"private":' + digits + b"}")

        result = read_json(path)

        self.assertIsNone(result.value)
        self.assertFalse(result.partial)
        self.assertEqual(result.error_code, "malformed_json:ValueError")

    @unittest.skipUnless(
        hasattr(sys, "get_int_max_str_digits"),
        "Python runtime has no integer digit limit",
    )
    def test_json_lines_retains_rows_before_integer_digit_limit(self):
        path = self.root / "huge-number.jsonl"
        digit_limit = sys.get_int_max_str_digits()
        if not digit_limit:
            self.skipTest("integer digit limit is disabled")
        digits = b"9" * (digit_limit + 1)
        path.write_bytes(b'{"ok":true}\n{"private":' + digits + b"}\n")

        result = read_json_lines(path)

        self.assertEqual(result.values, ({"ok": True},))
        self.assertTrue(result.partial)
        self.assertEqual(result.error_code, "malformed_json:ValueError")

    def test_json_lines_skips_blank_lines(self):
        path = self.root / "records.jsonl"
        path.write_bytes(b'\n  \r\n{"ok": true}\n')

        result = read_json_lines(path)

        self.assertEqual(result.values, ({"ok": True},))
        self.assertFalse(result.partial)
        self.assertIsNone(result.error_code)

    def test_json_lines_stops_at_non_object_record(self):
        path = self.root / "records.jsonl"
        path.write_bytes(b'{"ok": true}\n[1, 2]\n{"ignored": true}\n')

        result = read_json_lines(path)

        self.assertEqual(result.values, ({"ok": True},))
        self.assertTrue(result.partial)
        self.assertEqual(result.error_code, "non_object")

    def test_json_lines_detects_overlong_line_with_bounded_read(self):
        path = self.root / "records.jsonl"
        path.write_bytes(b'{"ok": true}\n' + b"x" * (MAX_JSONL_LINE_BYTES + 1))

        result = read_json_lines(path)

        self.assertEqual(result.values, ({"ok": True},))
        self.assertTrue(result.partial)
        self.assertEqual(result.error_code, "line_too_long")

    def test_json_lines_stops_when_total_byte_bound_is_exceeded(self):
        path = self.root / "records.jsonl"
        path.write_bytes(b'{"n":1}\n{"n":2}\n{"n":3}\n')

        with mock.patch.object(jsonio, "MAX_JSON_BYTES", 18):
            result = read_json_lines(path)

        self.assertEqual(result.values, ({"n": 1}, {"n": 2}))
        self.assertTrue(result.partial)
        self.assertEqual(result.error_code, "file_too_large")

    def test_json_lines_enforces_record_limit_after_exact_boundary(self):
        boundary = self.root / "boundary.jsonl"
        boundary.write_bytes(b'{"n":1}\n{"n":2}\n')
        overflow = self.root / "overflow.jsonl"
        overflow.write_bytes(b'{"n":1}\n{"n":2}\n{"n":3}\n')

        self.assertEqual(MAX_JSONL_RECORDS, 100_000)
        with mock.patch.object(jsonio, "MAX_JSONL_RECORDS", 2):
            boundary_result = read_json_lines(boundary)
            overflow_result = read_json_lines(overflow)

        self.assertEqual(boundary_result.values, ({"n": 1}, {"n": 2}))
        self.assertFalse(boundary_result.partial)
        self.assertIsNone(boundary_result.error_code)
        self.assertEqual(overflow_result.values, ({"n": 1}, {"n": 2}))
        self.assertTrue(overflow_result.partial)
        self.assertEqual(overflow_result.error_code, "record_limit")


class SqliteIoTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.path = self.root / "db ?#% ü.sqlite"
        connection = sqlite3.connect(str(self.path))
        try:
            connection.execute('CREATE TABLE "z table" ("second" TEXT, "first" INT)')
            connection.execute('CREATE TABLE alpha (one TEXT)')
            connection.execute(
                'CREATE VIEW "v#?" AS SELECT "first" AS selected FROM "z table"'
            )
            connection.commit()
        finally:
            connection.close()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_readonly_connection_enforces_query_only_and_rejects_writes(self):
        connection = open_sqlite_readonly(self.path)
        try:
            self.assertEqual(
                connection.execute("PRAGMA query_only").fetchone(),
                (1,),
            )
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("CREATE TABLE forbidden (value TEXT)")
        finally:
            connection.close()

    def test_schema_is_sorted_and_contains_only_column_name_tuples(self):
        connection = open_sqlite_readonly(self.path)
        try:
            schema = sqlite_schema(connection)
        finally:
            connection.close()

        self.assertEqual(
            list(schema.items()),
            [
                ("alpha", ("one",)),
                ("v#?", ("selected",)),
                ("z table", ("second", "first")),
            ],
        )

    def test_quote_identifier_doubles_embedded_quotes(self):
        self.assertEqual(quote_identifier('a"b'), '"a""b"')

    def test_corrupt_schema_raises_sanitized_read_error(self):
        path = self.root / "private-secret.sqlite"
        path.write_bytes(b"not a database: private body")
        connection = open_sqlite_readonly(path)
        try:
            with self.assertRaises(SqliteReadError) as raised:
                sqlite_schema(connection)
        finally:
            connection.close()

        self.assertEqual(
            raised.exception.error_code,
            "sqlite_error:DatabaseError",
        )
        rendered = str(raised.exception)
        self.assertEqual(rendered, raised.exception.error_code)
        self.assertIsNone(raised.exception.__context__)
        self.assertIsNone(raised.exception.__cause__)
        self.assertNotIn("private", rendered)
        self.assertNotIn(str(path), rendered)
        self.assertNotIn("file is not a database", rendered)

    def test_missing_database_open_error_is_sanitized(self):
        path = self.root / "private-missing.sqlite"

        with self.assertRaises(SqliteReadError) as raised:
            open_sqlite_readonly(path)

        self.assertEqual(
            raised.exception.error_code,
            "sqlite_error:OperationalError",
        )
        self.assertEqual(str(raised.exception), raised.exception.error_code)
        self.assertIsNone(raised.exception.__context__)
        self.assertIsNone(raised.exception.__cause__)
        self.assertNotIn(str(path), str(raised.exception))


if __name__ == "__main__":
    unittest.main()
