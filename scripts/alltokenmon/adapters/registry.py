"""Ordered registry of bounded local sources for supported runtimes."""

from pathlib import Path
from types import MappingProxyType
from typing import Callable, Dict, Optional, Tuple

from .base import DiscoveryContext, SourceSpec


RUNTIME_IDS = (
    "opencode", "claude", "codex", "cursor", "gemini", "amp", "droid",
    "openclaw", "pi", "kimi", "qwen", "roocode", "kilocode", "mux", "kilo",
    "crush", "hermes", "copilot", "goose", "codebuff", "antigravity", "zed",
    "kiro", "trae", "warp", "cline", "gjc", "grok", "jcode", "commandcode",
    "micode", "antigravity-cli", "junie", "zcode", "opencodereview",
    "codebuddy", "workbuddy", "devin-cli", "devin-desktop",
)


def _runtime_roots(
    runtime: str, selector: Optional[Callable[[Path], bool]] = None
):
    def roots(context: DiscoveryContext):
        from ..discovery import resolve_roots

        resolved = resolve_roots(context.os_name, context.env, context.home)[runtime]
        if selector is None:
            return resolved
        return tuple(path for path in resolved if selector(path))

    return roots


def _ends_with(path: Path, *parts: str) -> bool:
    return tuple(part.casefold() for part in path.parts[-len(parts):]) == tuple(
        part.casefold() for part in parts
    )


def _is_ascii_channel(channel: str) -> bool:
    return bool(channel) and all(
        character.isascii()
        and (character.isalnum() or character in "._-")
        for character in channel
    )


def _is_opencode_db(path: Path) -> bool:
    if not path.name.endswith(".db"):
        return False
    stem = path.name[:-3]
    if stem == "opencode":
        return True
    if not stem.startswith("opencode-"):
        return False
    channel = stem[len("opencode-"):]
    return _is_ascii_channel(channel)


def _is_kiro_ide_session(path: Path) -> bool:
    return path.name == "session.json" and path.parent.name.startswith("sess_")


def _is_kiro_global_storage(path: Path) -> bool:
    return path.suffix in (".chat", ".json") or not path.suffix


def _is_codebuddy_generic_log(path: Path) -> bool:
    return any(
        part.casefold() == "tencent-cloud.coding-copilot" for part in path.parts
    )


def _is_usage_cache(path: Path, suffix: str) -> bool:
    if any(part.casefold() == "archive" for part in path.parts):
        return False
    name = path.name
    if name == "usage" + suffix:
        return True
    return (
        name.startswith("usage.")
        and name.endswith(suffix)
        and not name.startswith("usage.backup")
    )


def _is_openclaw_transcript(path: Path) -> bool:
    name = path.name
    return (
        name.endswith(".jsonl")
        or ".jsonl.deleted." in name
        or ".jsonl.reset." in name
    )


def _is_copilot_workspace_session(path: Path) -> bool:
    parts = path.parts
    return (
        len(parts) >= 4
        and parts[-4] == "workspaceStorage"
        and parts[-2] == "chatSessions"
    )


def _is_micode_db(path: Path) -> bool:
    if not path.name.endswith(".db"):
        return False
    stem = path.name[:-3]
    if stem == "mimocode":
        return True
    if not stem.startswith("mimocode-"):
        return False
    channel = stem[len("mimocode-"):]
    return _is_ascii_channel(channel)


def _copilot_exporter_roots(context: DiscoveryContext) -> Tuple[Path, ...]:
    value = context.env.get("COPILOT_OTEL_FILE_EXPORTER_PATH", "").strip()
    path = Path(value) if value else None
    return (path,) if path is not None and path.is_file() else ()


def _copilot_desktop_roots(context: DiscoveryContext) -> Tuple[Path, ...]:
    return (context.home / ".copilot/data.db",)


def _claude_roots(context: DiscoveryContext) -> Tuple[Path, ...]:
    from ..discovery import discover_cc_mirror_project_roots, resolve_roots

    return (
        resolve_roots(context.os_name, context.env, context.home)["claude"]
        + discover_cc_mirror_project_roots(context.home)
    )


_DEFINITIONS = {
    "codex": ("jsonl", ("*.jsonl",), True),
    "cursor": ("csv-cache", ("usage*.csv",), False),
    "gemini": ("json", ("*.json", "*.jsonl"), True),
    "amp": ("json", ("T-*.json",), True),
    "droid": ("json", ("*.settings.json",), True),
    "openclaw": ("jsonl", ("*.jsonl", "*.jsonl.*"), True),
    "pi": ("jsonl", ("*.jsonl",), True),
    "kimi": ("jsonl", ("wire.jsonl",), True),
    "qwen": ("jsonl", ("*.jsonl",), True),
    "roocode": ("json", ("ui_messages.json",), True),
    "kilocode": ("json", ("ui_messages.json",), True),
    "mux": ("json", ("session-usage.json",), True),
    "kilo": ("sqlite", ("kilo.db",), False),
    "crush": ("json", ("projects.json",), False),
    "hermes": ("sqlite", ("state.db",), True),
    "goose": ("sqlite", ("sessions.db",), False),
    "codebuff": ("json", ("chat-messages.json",), True),
    "antigravity": ("jsonl-cache", ("*.jsonl",), True),
    "zed": ("sqlite", ("threads.db",), False),
    "trae": ("json-cache", ("*.json",), True),
    "warp": ("json-cache", ("usage*.json",), False),
    "cline": ("json", ("ui_messages.json",), True),
    "gjc": ("jsonl", ("*.jsonl",), True),
    "grok": ("jsonl", ("updates.jsonl",), True),
    "jcode": ("json", ("session_*.json",), True),
    "commandcode": ("jsonl", ("*.jsonl",), True),
    "micode": ("sqlite", ("mimocode*.db",), False),
    "antigravity-cli": ("sqlite", ("*.db",), False),
    "junie": ("jsonl", ("events.jsonl",), True),
    "zcode": ("jsonl-sqlite", ("*.jsonl", "db.sqlite"), True),
    "opencodereview": ("jsonl", ("*.jsonl",), True),
    "workbuddy": ("sqlite-jsonl", ("workbuddy.db", "*.jsonl"), True),
    "devin-cli": ("sqlite", ("sessions.db",), False),
    "devin-desktop": ("ndjson", ("*.ndjson",), True),
}

_CACHE_ONLY = {"cursor", "antigravity", "trae", "warp"}

SOURCE_SPECS: Dict[str, Tuple[SourceSpec, ...]] = {
    runtime: (
        SourceSpec(
            runtime=runtime,
            source_kind=_DEFINITIONS[runtime][0],
            patterns=_DEFINITIONS[runtime][1],
            recursive=_DEFINITIONS[runtime][2],
            cache_only=runtime in _CACHE_ONLY,
            roots=_runtime_roots(runtime),
        ),
    )
    for runtime in RUNTIME_IDS
    if runtime not in (
        "opencode",
        "claude",
        "cursor",
        "openclaw",
        "hermes",
        "copilot",
        "micode",
        "kiro",
        "codebuddy",
        "warp",
        "zcode",
        "workbuddy",
    )
}

SOURCE_SPECS["claude"] = (
    SourceSpec(
        "claude",
        "jsonl",
        ("*.jsonl",),
        True,
        False,
        _claude_roots,
    ),
)

SOURCE_SPECS["cursor"] = (
    SourceSpec(
        "cursor",
        "csv-cache",
        ("usage*.csv",),
        True,
        True,
        _runtime_roots("cursor"),
        lambda path: _is_usage_cache(path, ".csv"),
    ),
)

SOURCE_SPECS["warp"] = (
    SourceSpec(
        "warp",
        "json-cache",
        ("usage*.json",),
        True,
        True,
        _runtime_roots("warp"),
        lambda path: _is_usage_cache(path, ".json"),
    ),
)

SOURCE_SPECS["openclaw"] = (
    SourceSpec(
        "openclaw",
        "jsonl",
        ("*.jsonl", "*.jsonl.*"),
        True,
        False,
        _runtime_roots("openclaw"),
        _is_openclaw_transcript,
    ),
)

SOURCE_SPECS["opencode"] = (
    SourceSpec(
        "opencode",
        "sqlite",
        ("opencode.db", "opencode-*.db"),
        False,
        False,
        _runtime_roots(
            "opencode", lambda path: path.name.casefold() == "opencode"
        ),
        _is_opencode_db,
    ),
    SourceSpec(
        "opencode",
        "json",
        ("*.json",),
        True,
        False,
        _runtime_roots("opencode", lambda path: path.name == "message"),
    ),
)

SOURCE_SPECS["copilot"] = (
    SourceSpec(
        "copilot",
        "jsonl",
        ("*.jsonl",),
        True,
        False,
        _runtime_roots(
            "copilot", lambda path: _ends_with(path, ".copilot", "otel")
        ),
    ),
    SourceSpec(
        "copilot",
        "jsonl",
        ("*.jsonl",),
        True,
        False,
        _runtime_roots("copilot", lambda path: path.name == "workspaceStorage"),
        _is_copilot_workspace_session,
    ),
    SourceSpec(
        "copilot",
        "sqlite",
        ("data.db",),
        False,
        False,
        _copilot_desktop_roots,
    ),
    SourceSpec(
        "copilot",
        "jsonl",
        ("*",),
        False,
        False,
        _copilot_exporter_roots,
    ),
)

SOURCE_SPECS["hermes"] = (
    SourceSpec(
        "hermes",
        "sqlite",
        ("state.db",),
        False,
        False,
        _runtime_roots("hermes", lambda path: path.name == "state.db"),
    ),
    SourceSpec(
        "hermes",
        "sqlite",
        ("state.db",),
        True,
        False,
        _runtime_roots("hermes", lambda path: path.name == "profiles"),
        lambda path: len(path.parts) >= 3 and path.parts[-3] == "profiles",
    ),
)

SOURCE_SPECS["micode"] = (
    SourceSpec(
        "micode",
        "sqlite",
        ("mimocode.db", "mimocode-*.db"),
        False,
        False,
        _runtime_roots("micode"),
        _is_micode_db,
    ),
)

SOURCE_SPECS["kiro"] = (
    SourceSpec(
        "kiro",
        "json",
        ("*.json",),
        True,
        False,
        _runtime_roots(
            "kiro", lambda path: _ends_with(path, ".kiro", "sessions", "cli")
        ),
    ),
    SourceSpec(
        "kiro",
        "json",
        ("session.json",),
        True,
        False,
        _runtime_roots(
            "kiro", lambda path: _ends_with(path, ".kiro", "sessions")
        ),
        _is_kiro_ide_session,
    ),
    SourceSpec(
        "kiro",
        "global-storage",
        ("*",),
        True,
        False,
        _runtime_roots("kiro", lambda path: path.name == "kiro.kiroagent"),
        _is_kiro_global_storage,
    ),
    SourceSpec(
        "kiro",
        "sqlite",
        ("data.sqlite3",),
        False,
        False,
        _runtime_roots("kiro", lambda path: path.name == "data.sqlite3"),
    ),
)

SOURCE_SPECS["codebuddy"] = (
    SourceSpec(
        "codebuddy",
        "jsonl",
        ("*.jsonl",),
        True,
        False,
        _runtime_roots("codebuddy", lambda path: path.name == "projects"),
    ),
    SourceSpec(
        "codebuddy",
        "log",
        ("*.log",),
        True,
        False,
        _runtime_roots(
            "codebuddy", lambda path: path.name in ("CodeBuddyIDE", "VSCode")
        ),
    ),
    SourceSpec(
        "codebuddy",
        "log",
        ("*.log",),
        True,
        False,
        _runtime_roots("codebuddy", lambda path: path.name == "logs"),
        _is_codebuddy_generic_log,
    ),
)

SOURCE_SPECS["zcode"] = (
    SourceSpec(
        "zcode",
        "jsonl",
        ("*.jsonl",),
        True,
        False,
        _runtime_roots("zcode", lambda path: path.name == "projects"),
    ),
    SourceSpec(
        "zcode",
        "sqlite",
        ("db.sqlite",),
        False,
        False,
        _runtime_roots("zcode", lambda path: path.name == "db.sqlite"),
    ),
)

SOURCE_SPECS["workbuddy"] = (
    SourceSpec(
        "workbuddy",
        "sqlite",
        ("workbuddy.db",),
        False,
        False,
        _runtime_roots("workbuddy", lambda path: path.name == "workbuddy.db"),
    ),
    SourceSpec(
        "workbuddy",
        "jsonl",
        ("*.jsonl",),
        True,
        False,
        _runtime_roots("workbuddy", lambda path: path.name == "projects"),
    ),
)

SOURCE_SPECS = {
    runtime: SOURCE_SPECS[runtime]
    for runtime in RUNTIME_IDS
}

# Imports stay below source registration so adapter modules can share the
# discovery contracts without making the authoritative order implicit.
from .amp import parse_amp
from .antigravity import parse_antigravity
from .antigravity_cli import parse_antigravity_cli
from .claude import parse_claude
from .cline import parse_cline
from .codebuddy import parse_codebuddy
from .codebuff import parse_codebuff
from .codex import parse_codex
from .commandcode import parse_commandcode
from .copilot import parse_copilot
from .crush import parse_crush
from .cursor import parse_cursor
from .devin_cli import parse_devin_cli
from .devin_desktop import parse_devin_desktop
from .droid import parse_droid
from .gemini import parse_gemini
from .gjc import parse_gjc
from .goose import parse_goose
from .grok import parse_grok
from .hermes import parse_hermes
from .jcode import parse_jcode
from .junie import parse_junie
from .kilo import parse_kilo
from .kilocode import parse_kilocode
from .kimi import parse_kimi
from .kiro import parse_kiro
from .micode import parse_micode
from .mux import parse_mux
from .openclaw import parse_openclaw
from .opencode import parse_opencode
from .opencodereview import parse_opencodereview
from .pi import parse_pi
from .qwen import parse_qwen
from .roocode import parse_roocode
from .trae import parse_trae
from .warp import parse_warp
from .workbuddy import parse_workbuddy
from .zcode import parse_zcode
from .zed import parse_zed


ADAPTERS = MappingProxyType(dict((
    ("opencode", parse_opencode),
    ("claude", parse_claude),
    ("codex", parse_codex),
    ("cursor", parse_cursor),
    ("gemini", parse_gemini),
    ("amp", parse_amp),
    ("droid", parse_droid),
    ("openclaw", parse_openclaw),
    ("pi", parse_pi),
    ("kimi", parse_kimi),
    ("qwen", parse_qwen),
    ("roocode", parse_roocode),
    ("kilocode", parse_kilocode),
    ("mux", parse_mux),
    ("kilo", parse_kilo),
    ("crush", parse_crush),
    ("hermes", parse_hermes),
    ("copilot", parse_copilot),
    ("goose", parse_goose),
    ("codebuff", parse_codebuff),
    ("antigravity", parse_antigravity),
    ("zed", parse_zed),
    ("kiro", parse_kiro),
    ("trae", parse_trae),
    ("warp", parse_warp),
    ("cline", parse_cline),
    ("gjc", parse_gjc),
    ("grok", parse_grok),
    ("jcode", parse_jcode),
    ("commandcode", parse_commandcode),
    ("micode", parse_micode),
    ("antigravity-cli", parse_antigravity_cli),
    ("junie", parse_junie),
    ("zcode", parse_zcode),
    ("opencodereview", parse_opencodereview),
    ("codebuddy", parse_codebuddy),
    ("workbuddy", parse_workbuddy),
    ("devin-cli", parse_devin_cli),
    ("devin-desktop", parse_devin_desktop),
)))


def validate_registry() -> None:
    registered = tuple(SOURCE_SPECS)
    if registered != RUNTIME_IDS:
        missing = tuple(runtime for runtime in RUNTIME_IDS if runtime not in SOURCE_SPECS)
        extra = tuple(runtime for runtime in registered if runtime not in RUNTIME_IDS)
        raise ValueError(
            "runtime registry order mismatch; missing={!r}, extra={!r}".format(
                missing, extra
            )
        )
    empty = tuple(runtime for runtime, specs in SOURCE_SPECS.items() if not specs)
    if empty:
        raise ValueError("runtime registry has empty specs: {!r}".format(empty))
    mismatched = tuple(
        runtime
        for runtime, specs in SOURCE_SPECS.items()
        if any(spec.runtime != runtime or not spec.patterns for spec in specs)
    )
    if mismatched:
        raise ValueError("runtime registry has invalid specs: {!r}".format(mismatched))
    if tuple(ADAPTERS) != RUNTIME_IDS:
        missing = tuple(runtime for runtime in RUNTIME_IDS if runtime not in ADAPTERS)
        extra = tuple(runtime for runtime in ADAPTERS if runtime not in RUNTIME_IDS)
        raise ValueError(
            "adapter registry order mismatch; missing={!r}, extra={!r}".format(
                missing, extra
            )
        )
    invalid = tuple(runtime for runtime, parser in ADAPTERS.items() if not callable(parser))
    if invalid:
        raise ValueError("adapter registry has invalid parsers: {!r}".format(invalid))


validate_registry()
