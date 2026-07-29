"""Bounded Zstandard decoding for fixed, local Zed thread payloads."""

import hashlib
import io
from pathlib import Path
import threading


MAX_COMPRESSED_BYTES = 32 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 32 * 1024 * 1024
ZSTDDEC_SHA256 = "de7e4cb73ab269db0450b6c5561e52a0f816704ec02eddcee2da548aeb88a0fe"
_ASSET = Path(__file__).parent.parent / "_vendor" / "zstddec.wasm"
_EXPORTS = frozenset({
    "malloc",
    "free",
    "ZSTD_findDecompressedSize",
    "ZSTD_decompress",
    "ZSTD_isError",
})
_WASM_RUNTIME = None
_WASM_LOCK = threading.Lock()


class ZstdDecodeError(ValueError):
    """A sanitized local decoding failure."""

    def __init__(self) -> None:
        super().__init__("unsupported_zstd")


def _validate_input(value: object, max_output: int) -> bytes:
    if not isinstance(value, bytes):
        raise ZstdDecodeError()
    if (
        len(value) == 0
        or len(value) > MAX_COMPRESSED_BYTES
        or type(max_output) is not int
        or max_output < 0
        or max_output > MAX_DECOMPRESSED_BYTES
    ):
        raise ZstdDecodeError()
    return value


def _runtime_exports(runtime) -> frozenset:
    return frozenset(export.name for export in runtime.module.exports)


def _load_wasm_runtime():
    global _WASM_RUNTIME
    if _WASM_RUNTIME is not None:
        return _WASM_RUNTIME
    try:
        asset = _ASSET.read_bytes()
        if hashlib.sha256(asset).hexdigest() != ZSTDDEC_SHA256:
            raise ZstdDecodeError()
        from .._vendor import pywasm

        module = pywasm.structure.Module.from_reader(io.BytesIO(asset))
        runtime = pywasm.Runtime(
            module,
            {"env": {"emscripten_notify_memory_growth": lambda _pages: None}},
        )
        exports = _runtime_exports(runtime)
        required = _EXPORTS - {"free"}
        if not required.issubset(exports):
            raise ZstdDecodeError()
        _WASM_RUNTIME = runtime
        return runtime
    except ZstdDecodeError:
        raise
    except Exception:
        raise ZstdDecodeError() from None


def _execute(runtime, name: str, arguments):
    if name not in _EXPORTS:
        raise ZstdDecodeError()
    try:
        return runtime.exec(name, list(arguments))
    except Exception:
        raise ZstdDecodeError() from None


def _wasm_decompress(value: bytes, max_output: int) -> bytes:
    with _WASM_LOCK:
        runtime = _load_wasm_runtime()
        exports = _runtime_exports(runtime)
        compressed_pointer = None
        output_pointer = None
        try:
            compressed_pointer = _execute(runtime, "malloc", (len(value),))
            if type(compressed_pointer) is not int or compressed_pointer < 0:
                raise ZstdDecodeError()
            memory = runtime.store.mems[0].data
            compressed_end = compressed_pointer + len(value)
            if compressed_end > len(memory):
                raise ZstdDecodeError()
            memory[compressed_pointer:compressed_end] = value

            expected = _execute(
                runtime,
                "ZSTD_findDecompressedSize",
                (compressed_pointer, len(value)),
            )
            if (
                type(expected) is not int
                or expected < 0
                or expected > max_output
            ):
                raise ZstdDecodeError()

            output_pointer = _execute(runtime, "malloc", (expected,))
            if type(output_pointer) is not int or output_pointer < 0:
                raise ZstdDecodeError()
            output_end = output_pointer + expected
            memory = runtime.store.mems[0].data
            if output_end > len(memory):
                raise ZstdDecodeError()
            actual = _execute(
                runtime,
                "ZSTD_decompress",
                (output_pointer, expected, compressed_pointer, len(value)),
            )
            if (
                type(actual) is not int
                or _execute(runtime, "ZSTD_isError", (actual,)) != 0
                or actual != expected
            ):
                raise ZstdDecodeError()
            return bytes(memory[output_pointer:output_end])
        finally:
            if "free" in exports:
                try:
                    if output_pointer is not None:
                        _execute(runtime, "free", (output_pointer,))
                    if compressed_pointer is not None:
                        _execute(runtime, "free", (compressed_pointer,))
                except ZstdDecodeError:
                    pass


def decompress_zstd(
    value: bytes,
    max_output: int = MAX_DECOMPRESSED_BYTES,
) -> bytes:
    """Decode one known-size, dictionary-free Zstandard frame."""
    compressed = _validate_input(value, max_output)
    try:
        return _wasm_decompress(compressed, max_output)
    except ZstdDecodeError:
        raise
    except Exception:
        raise ZstdDecodeError() from None
