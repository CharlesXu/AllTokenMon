import base64
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.alltokenmon import cli
from scripts.alltokenmon.adapters import zed, zstdlite
from scripts.alltokenmon.adapters.base import DiscoveryContext
from scripts.alltokenmon.adapters.registry import SOURCE_SPECS
from scripts.alltokenmon.adapters.zed import parse_zed, scan
from scripts.alltokenmon.schema import AdapterStatus, TokenBreakdown


_ZSTD_FIXTURE = base64.b64decode(
    "KLUv/WCwAB0IAKZQNSMgbawDDIwo5ax45kpT4qYnlLiiTK4FIMX+1ZKiFCGCIIAArCs"
    "ALgAqAJKfK0unNaw77gij512nnvCqUCjWZc8Ieo9YZ0eTk0HsAJux9Lo585nNIwIQX4"
    "/sngRjdP2iAlQzBEGp2M3mNHfYwefK8AVDkMA4MGAFiwaDaSwQvrAu2dFkZ6dfX6dp"
    "YFF0WmU30U1c46gLg+DrxrFh/Ghak6jIcrbk+cDxOl1ihzgO0xdJIPt1CD4ZWbpeYX"
    "dtYikJk6xaVgkoq3y9G3PczUXz+kcDtGTmKxAAACbgqbyjSgLYjQNGYB51042fnQzN"
    "sKNJtYKF4jJoWbmyDNa+2WdwmGQ="
)
_UNKNOWN_SIZE_FIXTURE = base64.b64decode("KLUv/QBYVQAAGGFiYwEAJqpuCA==")
_DICTIONARY_FIXTURE = base64.b64decode(
    "KLUv/SNt3PZN1G0AAAg5A/zhFAT/2dRBgQI="
)


class ZedAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.path = self.home / ".local/share/zed/threads/threads.db"
        self.path.parent.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def _db(self, columns=None):
        connection = sqlite3.connect(str(self.path))
        connection.execute(
            "CREATE TABLE threads ({})".format(
                columns
                or "id TEXT, updated_at TEXT, created_at TEXT, data_type TEXT, data BLOB"
            )
        )
        return connection

    def test_request_usage_hosted_filter_and_actual_discovery(self):
        payload = {
            "model": {"provider": "zed.dev", "model": "claude-sonnet-4-5"},
            "imported": False,
            "request_token_usage": {
                "one": {"input_tokens": 100, "output_tokens": 20,
                        "cache_read_input_tokens": 10,
                        "cache_creation_input_tokens": 5},
                "two": {"input_tokens": 50, "output_tokens": 7},
            },
            "messages": ["SENTINEL_PRIVATE"],
        }
        connection = self._db()
        connection.execute(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
            ("thread-1", "2026-05-01T12:30:00Z", "2026-05-01T12:00:00Z",
             "json", json.dumps(payload)),
        )
        connection.commit()
        connection.close()
        before = hashlib.sha256(self.path.read_bytes()).digest()
        result = scan(DiscoveryContext("linux", self.home, {}), SOURCE_SPECS["zed"])
        self.assertEqual(result.status, AdapterStatus.OK)
        record = result.records[0]
        self.assertEqual(record.provider, "zed.dev")
        self.assertEqual(record.model, "claude-sonnet-4-5")
        self.assertEqual(record.tokens, TokenBreakdown(150, 27, 10, 5))
        self.assertEqual(record.message_count, 2)
        self.assertEqual(record.dedup_key, "zed:thread-1")
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).digest())
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_real_zstd_uses_vendored_fallback(self):
        connection = self._db()
        connection.execute(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
            ("thread-zstd", "2026-05-01T12:30:00Z",
             "2026-05-01T12:00:00Z", "zstd",
             sqlite3.Binary(_ZSTD_FIXTURE)),
        )
        connection.commit()
        connection.close()

        with zstdlite._WASM_LOCK:
            zstdlite._WASM_RUNTIME = None
        result = parse_zed((self.path,))
        self.assertEqual(result.status, AdapterStatus.OK)
        self.assertEqual(result.records[0].tokens, TokenBreakdown(150, 27, 10, 5))
        self.assertEqual(result.records[0].message_count, 2)
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))

    def test_aa_malicious_compression_package_is_never_imported(self):
        package = self.home / "malicious/compression"
        package.mkdir(parents=True)
        marker = self.home / "compression-imported"
        package.joinpath("__init__.py").write_text(
            "from pathlib import Path\n"
            "Path({!r}).write_text('imported', encoding='utf-8')\n".format(
                str(marker)
            ),
            encoding="utf-8",
        )
        saved = {
            name: module
            for name, module in sys.modules.items()
            if name == "compression" or name.startswith("compression.")
        }
        for name in saved:
            sys.modules.pop(name, None)
        try:
            with mock.patch.object(
                sys,
                "path",
                [str(package.parent), *sys.path],
            ):
                decoded = zstdlite.decompress_zstd(_ZSTD_FIXTURE)
        finally:
            for name in tuple(sys.modules):
                if name == "compression" or name.startswith("compression."):
                    sys.modules.pop(name, None)
            sys.modules.update(saved)
        self.assertEqual(len(decoded), 432)
        self.assertFalse(marker.exists())

    def test_fixed_wasm_checksum_and_safe_zstd_rejections(self):
        self.assertEqual(
            hashlib.sha256(zstdlite._ASSET.read_bytes()).hexdigest(),
            zstdlite.ZSTDDEC_SHA256,
        )
        with self.assertRaisesRegex(zstdlite.ZstdDecodeError, "unsupported_zstd"):
            zstdlite.decompress_zstd(b"SENTINEL_PRIVATE")
        with mock.patch.object(
            zstdlite,
            "_wasm_decompress",
            side_effect=RuntimeError("SENTINEL_PRIVATE"),
        ):
            with self.assertRaisesRegex(
                zstdlite.ZstdDecodeError,
                "^unsupported_zstd$",
            ) as raised:
                zstdlite.decompress_zstd(_ZSTD_FIXTURE)
        self.assertNotIn("SENTINEL_PRIVATE", str(raised.exception))
        for fixture in (_UNKNOWN_SIZE_FIXTURE, _DICTIONARY_FIXTURE):
            with self.assertRaisesRegex(
                zstdlite.ZstdDecodeError,
                "unsupported_zstd",
            ):
                zstdlite.decompress_zstd(fixture)
        with mock.patch.object(zstdlite, "MAX_COMPRESSED_BYTES", 2):
            with self.assertRaisesRegex(
                zstdlite.ZstdDecodeError,
                "unsupported_zstd",
            ):
                zstdlite.decompress_zstd(_ZSTD_FIXTURE)
        altered = self.home / "altered.wasm"
        altered.write_bytes(zstdlite._ASSET.read_bytes() + b"SENTINEL_PRIVATE")
        with mock.patch.object(zstdlite, "_ASSET", altered):
            with zstdlite._WASM_LOCK:
                zstdlite._WASM_RUNTIME = None
            with self.assertRaisesRegex(
                zstdlite.ZstdDecodeError,
                "unsupported_zstd",
            ):
                zstdlite.decompress_zstd(_ZSTD_FIXTURE)

    def test_cli_reports_real_zstd_usage(self):
        connection = self._db()
        connection.execute(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
            ("thread-zstd", "2026-05-01T12:30:00Z",
             "2026-05-01T12:00:00Z", "zstd",
             sqlite3.Binary(_ZSTD_FIXTURE)),
        )
        connection.commit()
        connection.close()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = cli.main((
                "--runtime", "zed",
                "--format", "json",
                "--home", str(self.home),
                "--now", "2026-05-01T13:00:00+00:00",
            ))
        report = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(report["periods"]["all_time"]["totals"]["total"], 192)

    def test_required_column_cumulative_malformed_oversize_and_zstd(self):
        connection = self._db("id TEXT, updated_at TEXT, data_type TEXT")
        connection.commit()
        connection.close()
        self.assertEqual(parse_zed((self.path,)).status, AdapterStatus.UNSUPPORTED_FORMAT)
        self.path.unlink()
        connection = self._db()
        cumulative = json.dumps({
            "model": {"provider": "zed.dev", "model": "gpt-5.2"},
            "request_token_usage": {},
            "cumulative_token_usage": {"input_tokens": 12, "output_tokens": 3},
        })
        connection.executemany(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
            [("good", "2026-01-01T00:00:00Z", None, "json", cumulative),
             ("bad", "2026-01-01T00:00:00Z", None, "json", "{SENTINEL_PRIVATE"),
             ("compressed", "2026-01-01T00:00:00Z", None, "zstd",
              sqlite3.Binary(b"SENTINEL_PRIVATE")),
             ("oversize", "2026-01-01T00:00:00Z", None, "json", "x" * 600)],
        )
        connection.commit()
        connection.close()
        with mock.patch.object(zed, "_MAX_BLOB_BYTES", 512):
            result = parse_zed((self.path,))
        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(result.records[0].tokens, TokenBreakdown(12, 3))
        self.assertNotIn("SENTINEL_PRIVATE", repr(result))
        connection = sqlite3.connect(str(self.path))
        connection.execute("DELETE FROM threads WHERE data_type = 'json'")
        connection.commit()
        connection.close()
        self.assertEqual(parse_zed((self.path,)).status, AdapterStatus.PARTIAL)
        self.path.write_bytes(b"not sqlite SENTINEL_PRIVATE")
        corrupt = parse_zed((self.path,))
        self.assertEqual(corrupt.status, AdapterStatus.ERROR)
        self.assertNotIn("SENTINEL_PRIVATE", repr(corrupt))
