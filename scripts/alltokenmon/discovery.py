"""Pure cross-platform root resolution and bounded file discovery."""

import json
from pathlib import Path
from typing import Dict, Iterable, Mapping, Tuple

from .adapters.base import DiscoveryContext, SourceSpec

_MAX_VARIANT_FILE_BYTES = 1024 * 1024


def _env_path(env: Mapping[str, str], name: str, fallback: Path) -> Path:
    value = env.get(name, "").strip()
    return Path(value) if value else fallback


def _unique(paths: Iterable[Path]) -> Tuple[Path, ...]:
    result = []
    seen = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            result.append(path)
    return tuple(result)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _vscode_roots(
    os_name: str, env: Mapping[str, str], home: Path, extension: str
) -> Tuple[Path, ...]:
    suffix = Path("User/globalStorage") / extension / "tasks"
    server = home / ".vscode-server/data/User/globalStorage" / extension / "tasks"
    roots = [home / ".config/Code" / suffix, server]
    if os_name == "darwin":
        roots.append(home / "Library/Application Support/Code" / suffix)
    elif os_name == "windows":
        appdata = _env_path(env, "APPDATA", home / "AppData/Roaming")
        roots.append(appdata / "Code" / suffix)
    return _unique(roots)


def discover_cc_mirror_project_roots(home: Path) -> Tuple[Path, ...]:
    """Resolve immediate cc-mirror variants without scanning outside projects."""
    mirror_root = home / ".cc-mirror"
    try:
        variants = tuple(mirror_root.iterdir())
    except OSError:
        return ()

    projects_roots = []
    for variant in variants:
        variant_file = variant / "variant.json"
        try:
            with variant_file.open("rb") as handle:
                raw_metadata = handle.read(_MAX_VARIANT_FILE_BYTES + 1)
            if len(raw_metadata) > _MAX_VARIANT_FILE_BYTES:
                continue
            metadata = json.loads(raw_metadata)
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict):
            continue
        if any(
            key in metadata
            and metadata[key] is not None
            and not isinstance(metadata[key], str)
            for key in ("name", "provider", "providerId", "configDir")
        ):
            continue

        raw_config = metadata.get("configDir")
        trimmed = raw_config.strip() if isinstance(raw_config, str) else ""
        if trimmed.startswith("~/"):
            config_dir = home / trimmed[2:]
        elif trimmed and Path(trimmed).is_absolute():
            config_dir = Path(trimmed)
        elif trimmed:
            config_dir = variant / trimmed
        else:
            config_dir = variant / "config"

        projects = config_dir / "projects"
        if (
            projects.is_dir()
            and _is_within(projects.resolve(), home.resolve())
        ):
            projects_roots.append(projects)
    return tuple(
        sorted(_unique(projects_roots), key=lambda path: (str(path).casefold(), str(path)))
    )


def resolve_roots(
    os_name: str, env: Mapping[str, str], home: Path
) -> Mapping[str, Tuple[Path, ...]]:
    """Resolve known local roots without reading process-global environment."""
    platform = os_name.strip().lower()
    if platform in ("mac", "macos"):
        platform = "darwin"
    elif platform in ("win", "win32"):
        platform = "windows"

    xdg_data = _env_path(env, "XDG_DATA_HOME", home / ".local/share")
    xdg_config = _env_path(env, "XDG_CONFIG_HOME", home / ".config")
    appdata = _env_path(env, "APPDATA", home / "AppData/Roaming")
    localappdata = _env_path(env, "LOCALAPPDATA", home / "AppData/Local")

    config_override = env.get("TOKSCALE_CONFIG_DIR", "").strip()
    if config_override:
        cache_roots = (Path(config_override),)
    elif platform == "windows":
        cache_roots = (appdata / "tokscale",)
    elif platform == "darwin":
        cache_roots = (home / ".config/tokscale",)
    else:
        cache_roots = (xdg_config / "tokscale",)

    codex_home = _env_path(env, "CODEX_HOME", home / ".codex")
    headless_override = env.get("TOKSCALE_HEADLESS_DIR", "").strip()
    codex_headless = (
        (Path(headless_override) / "codex",)
        if headless_override
        else (
            home / ".config/tokscale/headless/codex",
            home / "Library/Application Support/tokscale/headless/codex",
        )
    )
    gemini_home = _env_path(env, "GEMINI_CLI_HOME", home / ".gemini")
    kimi_code_home = _env_path(env, "KIMI_CODE_HOME", home / ".kimi-code")
    grok_home = _env_path(env, "GROK_HOME", home / ".grok")
    jcode_home = _env_path(env, "JCODE_HOME", home / ".jcode")

    hermes_override = env.get("HERMES_HOME", "").strip()
    hermes_homes = (
        (Path(hermes_override),)
        if hermes_override
        else _unique(
            (
                home / ".hermes",
                localappdata / "hermes" if platform == "windows" else home / ".hermes",
                home / "AppData/Local/hermes",
            )
        )
    )

    codebuff_override = env.get("CODEBUFF_DATA_DIR", "").strip()
    if codebuff_override:
        codebuff_roots = (Path(codebuff_override) / "projects",)
    else:
        codebuff_roots = tuple(
            home / ".config" / channel / "projects"
            for channel in ("manicode", "manicode-dev", "manicode-staging")
        )

    gjc_roots = [
        _env_path(env, "GJC_CODING_AGENT_DIR", home / ".gjc/agent") / "sessions"
    ]
    for variable in ("GJC_CONFIG_DIR", "PI_CONFIG_DIR"):
        value = env.get(variable, "").strip()
        if value:
            gjc_roots.append(Path(value) / "agent/sessions")
    if platform != "windows" and env.get("XDG_DATA_HOME", "").strip():
        gjc_roots.append(xdg_data / "gjc/sessions")
    gjc_roots.append(home / ".gjc/agent/sessions")

    goose_override = env.get("GOOSE_PATH_ROOT", "").strip()
    goose_roots = []
    if goose_override:
        goose_roots.append(Path(goose_override) / "data/sessions/sessions.db")
    goose_roots.extend(
        (
            xdg_data / "goose/sessions/sessions.db",
            home / "Library/Application Support/goose/sessions/sessions.db",
            home / "Library/Application Support/Block/goose/sessions/sessions.db",
            home / ".local/share/Block/goose/sessions/sessions.db",
        )
    )

    kiro_storage = [
        home / "Library/Application Support/Kiro/User/globalStorage/kiro.kiroagent",
        home / "Library/Application Support/kiro/User/globalStorage/kiro.kiroagent",
        home / ".config/Kiro/User/globalStorage/kiro.kiroagent",
        home / ".config/kiro/User/globalStorage/kiro.kiroagent",
    ]
    devin_desktop_roots = [
        home / "Library/Application Support/Devin/User/acp-events",
        home / ".config/Devin/User/acp-events",
        home / ".config/devin/User/acp-events",
        home / "AppData/Roaming/Devin/User/acp-events",
    ]
    if platform == "windows":
        zed_roots = (
            localappdata / "Zed/threads/threads.db",
            xdg_data / "zed/threads/threads.db",
        )
        explicit_appdata = env.get("APPDATA", "").strip()
        if explicit_appdata:
            kiro_storage.extend(
                (
                    Path(explicit_appdata)
                    / "Kiro/User/globalStorage/kiro.kiroagent",
                    Path(explicit_appdata)
                    / "kiro/User/globalStorage/kiro.kiroagent",
                )
            )
            devin_desktop_roots.append(
                Path(explicit_appdata) / "Devin/User/acp-events"
            )
        kiro_storage.extend(
            (
                home
                / "AppData/Roaming/Kiro/User/globalStorage/kiro.kiroagent",
                home
                / "AppData/Roaming/kiro/User/globalStorage/kiro.kiroagent",
            )
        )
    elif platform == "darwin":
        zed_roots = (
            home / "Library/Application Support/Zed/threads/threads.db",
            xdg_data / "zed/threads/threads.db",
        )
    else:
        zed_roots = (xdg_data / "zed/threads/threads.db",)

    copilot_workspace = [
        home / "Library/Application Support/Code/User/workspaceStorage",
        home / ".config/Code/User/workspaceStorage",
        home / "AppData/Roaming/Code/User/workspaceStorage",
    ]
    if platform == "windows":
        explicit_appdata = env.get("APPDATA", "").strip()
        if explicit_appdata:
            copilot_workspace.append(
                Path(explicit_appdata) / "Code/User/workspaceStorage"
            )

    cline_extension = "saoudrizwan.claude-dev"
    cline_suffix = Path("Code/User/globalStorage") / cline_extension / "tasks"
    cline_roots = [
        home / ".config" / cline_suffix,
        home / "Library/Application Support" / cline_suffix,
        home / "AppData/Roaming" / cline_suffix,
        home
        / ".vscode-server/data/User/globalStorage"
        / cline_extension
        / "tasks",
    ]
    if platform == "windows":
        explicit_appdata = env.get("APPDATA", "").strip()
        if explicit_appdata:
            cline_roots.append(
                Path(explicit_appdata)
                / "Code/User/globalStorage"
                / cline_extension
                / "tasks"
            )

    codebuddy_roots = [
        home / ".codebuddy/projects",
        home / "AppData/Local/CodeBuddyExtension/Logs/CodeBuddyIDE",
        home / "AppData/Local/CodeBuddyExtension/Logs/VSCode",
        home / "AppData/Roaming/CodeBuddy CN/logs",
        home / "AppData/Roaming/Code/logs",
    ]
    if platform == "windows":
        codebuddy_roots.extend(
            (
                localappdata / "CodeBuddyExtension/Logs/CodeBuddyIDE",
                localappdata / "CodeBuddyExtension/Logs/VSCode",
                appdata / "CodeBuddy CN/logs",
                appdata / "Code/logs",
            )
        )
    else:
        codebuddy_roots.extend(
            (
                xdg_data / "CodeBuddyExtension/Logs/CodeBuddyIDE",
                xdg_data / "CodeBuddyExtension/Logs/VSCode",
                xdg_config / "CodeBuddy CN/logs",
                xdg_config / "Code/logs",
            )
        )

    roots: Dict[str, Tuple[Path, ...]] = {
        "opencode": (
            xdg_data / "opencode",
            xdg_data / "opencode/storage/message",
        ),
        "claude": (home / ".claude/projects", home / ".claude/transcripts"),
        "codex": (
            codex_home / "sessions",
            codex_home / "archived_sessions",
        )
        + codex_headless,
        "cursor": (home / ".config/tokscale/cursor-cache",),
        "gemini": (gemini_home / "tmp",),
        "amp": (xdg_data / "amp/threads",),
        "droid": (home / ".factory/sessions",),
        "openclaw": tuple(
            home / name / "agents"
            for name in (".openclaw", ".clawdbot", ".moltbot", ".moldbot")
        ),
        "pi": (home / ".pi/agent/sessions", home / ".omp/agent/sessions"),
        "kimi": (home / ".kimi/sessions", kimi_code_home / "sessions"),
        "qwen": (home / ".qwen/projects",),
        "roocode": _vscode_roots(
            platform, env, home, "rooveterinaryinc.roo-cline"
        ),
        "kilocode": _vscode_roots(platform, env, home, "kilocode.kilo-code"),
        "mux": (home / ".mux/sessions",),
        "kilo": (xdg_data / "kilo/kilo.db",),
        "crush": _unique(
            (
                (Path(env["CRUSH_GLOBAL_DATA"].strip()) / "projects.json",)
                if env.get("CRUSH_GLOBAL_DATA", "").strip()
                else ()
            )
            + (xdg_data / "crush/projects.json",)
            + (
                (localappdata / "crush/projects.json",)
                if platform == "windows"
                else ()
            )
            + (home / "AppData/Local/crush/projects.json",)
        ),
        "hermes": _unique(
            path
            for hermes_home in hermes_homes
            for path in (hermes_home / "state.db", hermes_home / "profiles")
        ),
        "copilot": _unique(
            (
                home / ".copilot/otel",
                home / ".copilot/data.db",
            )
            + tuple(copilot_workspace)
            + (
                (Path(env["COPILOT_OTEL_FILE_EXPORTER_PATH"].strip()),)
                if env.get("COPILOT_OTEL_FILE_EXPORTER_PATH", "").strip()
                else ()
            )
        ),
        "goose": _unique(goose_roots),
        "codebuff": codebuff_roots,
        "antigravity": tuple(
            root / "antigravity-cache/sessions" for root in cache_roots
        ),
        "zed": _unique(zed_roots),
        "kiro": _unique(
            (
                home / ".kiro/sessions/cli",
                home / ".kiro/sessions",
                home / ".local/share/kiro-cli/data.sqlite3",
                home / "Library/Application Support/kiro-cli/data.sqlite3",
            )
            + tuple(kiro_storage)
        ),
        "trae": tuple(root / "trae-cache/sessions" for root in cache_roots),
        "warp": tuple(root / "warp-cache" for root in cache_roots),
        "cline": _unique(cline_roots),
        "gjc": _unique(gjc_roots),
        "grok": (grok_home / "sessions",),
        "jcode": (jcode_home / "sessions",),
        "commandcode": (home / ".commandcode/projects",),
        "micode": (
            xdg_data / "mimocode",
            home / "Library/Application Support/orca/mimocode-hooks/shared/data",
        ),
        "antigravity-cli": (gemini_home / "antigravity-cli/conversations",),
        "junie": (home / ".junie/sessions",),
        "zcode": (home / ".zcode/projects", home / ".zcode/cli/db/db.sqlite"),
        "opencodereview": (home / ".opencodereview/sessions",),
        "codebuddy": _unique(codebuddy_roots),
        "workbuddy": (home / ".workbuddy/workbuddy.db", home / ".workbuddy/projects"),
        "devin-cli": (xdg_data / "devin/cli/sessions.db",),
        "devin-desktop": _unique(devin_desktop_roots),
    }
    return roots


def discover(spec: SourceSpec, context: DiscoveryContext) -> Tuple[Path, ...]:
    """Return matching existing files under a spec's explicit roots."""
    found = {}
    for root in spec.roots(context):
        if not root.exists():
            continue
        if root.is_symlink():
            if root.is_dir():
                continue
            if not root.is_file():
                continue
            if not _is_within(root.resolve(), root.parent.resolve()):
                continue
        resolved_root = root.resolve()
        candidates = (root,) if root.is_file() else (
            root.rglob("*") if spec.recursive else root.glob("*")
        )
        for candidate in candidates:
            if not candidate.is_file():
                continue
            if not any(candidate.match(pattern) for pattern in spec.patterns):
                continue
            if spec.matcher is not None and not spec.matcher(candidate):
                continue
            resolved = candidate.resolve()
            if not _is_within(resolved, resolved_root):
                continue
            try:
                stat = resolved.stat()
                key = (stat.st_dev, stat.st_ino) if stat.st_ino else str(resolved)
            except OSError:
                key = str(resolved)
            found[key] = resolved
    return tuple(sorted(found.values(), key=lambda path: (str(path).casefold(), str(path))))
