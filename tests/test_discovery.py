import tempfile
import unittest
from pathlib import Path

from scripts.alltokenmon.adapters.base import DiscoveryContext, SourceSpec
from scripts.alltokenmon.adapters.registry import SOURCE_SPECS
from scripts.alltokenmon.discovery import discover, resolve_roots


class ResolveRootsTests(unittest.TestCase):
    def test_macos_uses_application_support_and_tool_homes(self):
        home = Path("/Users/tester")
        roots = resolve_roots("darwin", {}, home)

        self.assertIn(
            home / "Library/Application Support/Zed/threads/threads.db",
            roots["zed"],
        )
        self.assertIn(
            home / "Library/Application Support/Code/User/globalStorage/"
            "saoudrizwan.claude-dev/tasks",
            roots["cline"],
        )
        self.assertIn(
            home / "Library/Application Support/Devin/User/acp-events",
            roots["devin-desktop"],
        )
        self.assertIn(home / ".codex/sessions", roots["codex"])
        self.assertNotIn(home, (root for values in roots.values() for root in values))

    def test_linux_uses_xdg_config_data_and_vscode_server(self):
        home = Path("/home/tester")
        env = {
            "XDG_DATA_HOME": "/data",
            "XDG_CONFIG_HOME": "/config",
        }
        roots = resolve_roots("linux", env, home)

        self.assertIn(Path("/data/opencode"), roots["opencode"])
        self.assertIn(
            home / ".config/tokscale/cursor-cache", roots["cursor"]
        )
        self.assertIn(
            home / ".vscode-server/data/User/globalStorage/"
            "rooveterinaryinc.roo-cline/tasks",
            roots["roocode"],
        )
        self.assertIn(
            home / ".config/Devin/User/acp-events", roots["devin-desktop"]
        )
        self.assertNotIn(
            Path("/config/Devin/User/acp-events"), roots["devin-desktop"]
        )

    def test_windows_uses_appdata_localappdata_and_global_storage(self):
        home = Path("C:/Users/tester")
        env = {
            "APPDATA": "C:/Roaming",
            "LOCALAPPDATA": "C:/Local",
        }
        roots = resolve_roots("windows", env, home)

        self.assertIn(
            Path("C:/Roaming/Code/User/globalStorage/saoudrizwan.claude-dev/tasks"),
            roots["cline"],
        )
        self.assertIn(Path("C:/Local/Zed/threads/threads.db"), roots["zed"])
        self.assertIn(
            Path("C:/Roaming/Devin/User/acp-events"), roots["devin-desktop"]
        )
        self.assertIn(
            Path("C:/Roaming/tokscale/trae-cache/sessions"), roots["trae"]
        )

    def test_supported_environment_overrides_are_authoritative(self):
        home = Path("/home/tester")
        env = {
            "CODEX_HOME": "/override/codex",
            "HERMES_HOME": "/override/hermes",
            "GEMINI_CLI_HOME": "/override/gemini",
            "KIMI_CODE_HOME": "/override/kimi-code",
            "GROK_HOME": "/override/grok",
            "JCODE_HOME": "/override/jcode",
            "CODEBUFF_DATA_DIR": "/override/codebuff",
            "GJC_CODING_AGENT_DIR": "/override/gjc-agent",
        }
        roots = resolve_roots("linux", env, home)

        self.assertIn(Path("/override/codex/sessions"), roots["codex"])
        self.assertIn(Path("/override/hermes/state.db"), roots["hermes"])
        self.assertIn(Path("/override/gemini/tmp"), roots["gemini"])
        self.assertIn(Path("/override/kimi-code/sessions"), roots["kimi"])
        self.assertIn(Path("/override/grok/sessions"), roots["grok"])
        self.assertIn(Path("/override/jcode/sessions"), roots["jcode"])
        self.assertEqual(roots["codebuff"], (Path("/override/codebuff/projects"),))
        self.assertIn(Path("/override/gjc-agent/sessions"), roots["gjc"])

    def test_tokscale_config_dir_overrides_only_config_root_caches(self):
        roots = resolve_roots(
            "darwin",
            {"TOKSCALE_CONFIG_DIR": "/custom/tokscale"},
            Path("/Users/tester"),
        )

        self.assertEqual(
            roots["cursor"],
            (Path("/Users/tester/.config/tokscale/cursor-cache"),),
        )
        self.assertEqual(
            roots["antigravity"],
            (Path("/custom/tokscale/antigravity-cache/sessions"),),
        )
        self.assertEqual(
            roots["trae"], (Path("/custom/tokscale/trae-cache/sessions"),)
        )
        self.assertEqual(roots["warp"], (Path("/custom/tokscale/warp-cache"),))

    def test_linux_config_override_does_not_move_cursor_cache(self):
        home = Path("/home/tester")
        roots = resolve_roots(
            "linux",
            {
                "TOKSCALE_CONFIG_DIR": "/custom/tokscale",
                "XDG_CONFIG_HOME": "/xdg-config",
            },
            home,
        )

        self.assertEqual(
            roots["cursor"], (home / ".config/tokscale/cursor-cache",)
        )
        self.assertEqual(
            roots["antigravity"],
            (Path("/custom/tokscale/antigravity-cache/sessions"),),
        )

    def test_darwin_config_fallback_ignores_xdg_and_application_support(self):
        home = Path("/Users/tester")
        roots = resolve_roots(
            "darwin", {"XDG_CONFIG_HOME": "/xdg-config"}, home
        )

        self.assertEqual(
            roots["antigravity"],
            (home / ".config/tokscale/antigravity-cache/sessions",),
        )
        self.assertEqual(
            roots["trae"], (home / ".config/tokscale/trae-cache/sessions",)
        )
        self.assertEqual(
            roots["warp"], (home / ".config/tokscale/warp-cache",)
        )

    def test_codex_headless_defaults_and_override_are_exact(self):
        home = Path("/home/tester")
        defaults = resolve_roots("linux", {}, home)["codex"]
        self.assertIn(
            home / ".config/tokscale/headless/codex", defaults
        )
        self.assertIn(
            home / "Library/Application Support/tokscale/headless/codex",
            defaults,
        )

        overridden = resolve_roots(
            "linux", {"TOKSCALE_HEADLESS_DIR": "/headless"}, home
        )["codex"]
        self.assertEqual(
            overridden,
            (
                home / ".codex/sessions",
                home / ".codex/archived_sessions",
                Path("/headless/codex"),
            ),
        )

    def test_crush_global_and_literal_windows_fallbacks(self):
        home = Path("/home/tester")
        linux = resolve_roots(
            "linux",
            {
                "CRUSH_GLOBAL_DATA": "/global/crush",
                "LOCALAPPDATA": "/should-not-be-used",
            },
            home,
        )["crush"]
        self.assertIn(Path("/global/crush/projects.json"), linux)
        self.assertIn(home / "AppData/Local/crush/projects.json", linux)
        self.assertNotIn(
            Path("/should-not-be-used/crush/projects.json"), linux
        )

        windows = resolve_roots(
            "windows", {"LOCALAPPDATA": "C:/Local"}, Path("C:/Users/tester")
        )["crush"]
        self.assertIn(Path("C:/Local/crush/projects.json"), windows)
        self.assertIn(
            Path("C:/Users/tester/AppData/Local/crush/projects.json"), windows
        )

    def test_vscode_extension_roots_ignore_xdg(self):
        home = Path("/home/tester")
        roots = resolve_roots(
            "linux", {"XDG_CONFIG_HOME": "/xdg-config"}, home
        )
        self.assertIn(
            home
            / ".config/Code/User/globalStorage/"
            "rooveterinaryinc.roo-cline/tasks",
            roots["roocode"],
        )
        self.assertIn(
            home
            / ".config/Code/User/globalStorage/kilocode.kilo-code/tasks",
            roots["kilocode"],
        )
        self.assertIn(
            home
            / ".config/Code/User/globalStorage/"
            "saoudrizwan.claude-dev/tasks",
            roots["cline"],
        )
        self.assertTrue(
            all(not str(path).startswith("/xdg-config") for path in roots["cline"])
        )
        self.assertIn(
            home
            / "Library/Application Support/Code/User/globalStorage/"
            "saoudrizwan.claude-dev/tasks",
            roots["cline"],
        )
        self.assertIn(
            home
            / "AppData/Roaming/Code/User/globalStorage/"
            "saoudrizwan.claude-dev/tasks",
            roots["cline"],
        )

    def test_roo_and_kilocode_add_platform_vscode_roots(self):
        mac_home = Path("/Users/tester")
        mac = resolve_roots("darwin", {}, mac_home)
        self.assertIn(
            mac_home
            / "Library/Application Support/Code/User/globalStorage/"
            "rooveterinaryinc.roo-cline/tasks",
            mac["roocode"],
        )
        self.assertIn(
            mac_home
            / "Library/Application Support/Code/User/globalStorage/"
            "kilocode.kilo-code/tasks",
            mac["kilocode"],
        )

        windows_home = Path("C:/Users/tester")
        windows = resolve_roots(
            "windows", {"APPDATA": "C:/Roaming"}, windows_home
        )
        self.assertIn(
            Path(
                "C:/Roaming/Code/User/globalStorage/"
                "rooveterinaryinc.roo-cline/tasks"
            ),
            windows["roocode"],
        )
        self.assertIn(
            Path(
                "C:/Roaming/Code/User/globalStorage/"
                "kilocode.kilo-code/tasks"
            ),
            windows["kilocode"],
        )
        self.assertIn(
            windows_home
            / ".config/Code/User/globalStorage/"
            "rooveterinaryinc.roo-cline/tasks",
            windows["roocode"],
        )

    def test_copilot_workspace_roots_are_fixed_and_exclude_server(self):
        home = Path("/home/tester")
        roots = resolve_roots("linux", {}, home)["copilot"]
        self.assertIn(
            home / "Library/Application Support/Code/User/workspaceStorage",
            roots,
        )
        self.assertIn(home / ".config/Code/User/workspaceStorage", roots)
        self.assertIn(
            home / "AppData/Roaming/Code/User/workspaceStorage", roots
        )
        self.assertNotIn(
            home / ".vscode-server/data/User/workspaceStorage", roots
        )

        windows = resolve_roots(
            "windows", {"APPDATA": "C:/Roaming"}, Path("C:/Users/tester")
        )["copilot"]
        self.assertIn(Path("C:/Roaming/Code/User/workspaceStorage"), windows)

    def test_kiro_storage_unions_fixed_and_windows_fallbacks(self):
        home = Path("/home/tester")
        linux = resolve_roots("linux", {}, home)["kiro"]
        for root in (
            home
            / "Library/Application Support/Kiro/User/globalStorage/kiro.kiroagent",
            home
            / "Library/Application Support/kiro/User/globalStorage/kiro.kiroagent",
            home / ".config/Kiro/User/globalStorage/kiro.kiroagent",
            home / ".config/kiro/User/globalStorage/kiro.kiroagent",
        ):
            self.assertIn(root, linux)

        windows_home = Path("C:/Users/tester")
        windows = resolve_roots(
            "windows", {"APPDATA": "C:/Roaming"}, windows_home
        )["kiro"]
        self.assertIn(
            Path("C:/Roaming/Kiro/User/globalStorage/kiro.kiroagent"), windows
        )
        self.assertIn(
            windows_home
            / "AppData/Roaming/kiro/User/globalStorage/kiro.kiroagent",
            windows,
        )

    def test_hermes_literal_fallback_is_replaced_by_override(self):
        home = Path("/home/tester")
        defaults = resolve_roots("linux", {}, home)["hermes"]
        self.assertIn(home / "AppData/Local/hermes/state.db", defaults)

        overridden = resolve_roots(
            "linux", {"HERMES_HOME": "/isolated/hermes"}, home
        )["hermes"]
        self.assertEqual(
            overridden,
            (
                Path("/isolated/hermes/state.db"),
                Path("/isolated/hermes/profiles"),
            ),
        )

    def test_devin_desktop_unions_all_fixed_fallbacks(self):
        home = Path("/home/tester")
        linux = resolve_roots("linux", {}, home)["devin-desktop"]
        for root in (
            home / "Library/Application Support/Devin/User/acp-events",
            home / ".config/Devin/User/acp-events",
            home / ".config/devin/User/acp-events",
            home / "AppData/Roaming/Devin/User/acp-events",
        ):
            self.assertIn(root, linux)

        windows = resolve_roots(
            "windows", {"APPDATA": "C:/Roaming"}, Path("C:/Users/tester")
        )["devin-desktop"]
        self.assertIn(Path("C:/Roaming/Devin/User/acp-events"), windows)

    def test_kiro_cli_database_ignores_xdg_data_override(self):
        home = Path("/home/tester")
        roots = resolve_roots(
            "linux", {"XDG_DATA_HOME": "/xdg-data"}, home
        )["kiro"]
        self.assertIn(
            home / ".local/share/kiro-cli/data.sqlite3", roots
        )
        self.assertNotIn(Path("/xdg-data/kiro-cli/data.sqlite3"), roots)

    def test_codebuddy_unions_literal_and_platform_data_roots(self):
        home = Path("/home/tester")
        linux = resolve_roots(
            "linux",
            {
                "XDG_DATA_HOME": "/xdg-data",
                "XDG_CONFIG_HOME": "/xdg-config",
            },
            home,
        )["codebuddy"]
        for root in (
            Path("/xdg-data/CodeBuddyExtension/Logs/CodeBuddyIDE"),
            Path("/xdg-data/CodeBuddyExtension/Logs/VSCode"),
            Path("/xdg-config/CodeBuddy CN/logs"),
            Path("/xdg-config/Code/logs"),
            home / "AppData/Local/CodeBuddyExtension/Logs/CodeBuddyIDE",
            home / "AppData/Roaming/Code/logs",
        ):
            self.assertIn(root, linux)

        windows_home = Path("C:/Users/tester")
        windows = resolve_roots(
            "windows",
            {
                "LOCALAPPDATA": "C:/Local",
                "APPDATA": "C:/Roaming",
            },
            windows_home,
        )["codebuddy"]
        self.assertIn(
            Path("C:/Local/CodeBuddyExtension/Logs/CodeBuddyIDE"), windows
        )
        self.assertIn(
            windows_home
            / "AppData/Local/CodeBuddyExtension/Logs/CodeBuddyIDE",
            windows,
        )
        self.assertIn(Path("C:/Roaming/Code/logs"), windows)
        self.assertIn(
            windows_home / "AppData/Roaming/Code/logs", windows
        )

    def test_copilot_includes_desktop_db_and_explicit_exporter(self):
        home = Path("/home/tester")
        roots = resolve_roots(
            "linux",
            {"COPILOT_OTEL_FILE_EXPORTER_PATH": "/logs/copilot-events"},
            home,
        )

        self.assertIn(home / ".copilot/data.db", roots["copilot"])
        self.assertIn(Path("/logs/copilot-events"), roots["copilot"])

    def test_opencode_roots_separate_databases_from_legacy_messages(self):
        roots = resolve_roots(
            "linux", {"XDG_DATA_HOME": "/data"}, Path("/home/tester")
        )

        self.assertEqual(
            roots["opencode"],
            (Path("/data/opencode"), Path("/data/opencode/storage/message")),
        )


class DiscoverTests(unittest.TestCase):
    def _discover_runtime(self, runtime, context):
        return {
            path
            for spec in SOURCE_SPECS[runtime]
            for path in discover(spec, context)
        }

    def test_missing_roots_return_no_candidates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "missing"
            context = DiscoveryContext("linux", Path(temp_dir), {})
            spec = SourceSpec(
                "codex", "jsonl", ("*.jsonl",), True, False, lambda _: (root,)
            )
            self.assertEqual(discover(spec, context), ())

    def test_discovery_includes_root_file_and_sorts_deduplicated_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root_file = base / "Root.JSONL"
            root_file.write_text("{}\n", encoding="utf-8")
            nested = base / "sessions"
            nested.mkdir()
            alpha = nested / "a.jsonl"
            alpha.write_text("{}\n", encoding="utf-8")
            duplicate = nested / "duplicate.jsonl"
            try:
                duplicate.symlink_to(alpha)
            except OSError as error:
                self.skipTest("symlinks unavailable: {}".format(error))

            context = DiscoveryContext("linux", base, {})
            spec = SourceSpec(
                "codex",
                "jsonl",
                ("*.jsonl", "*.JSONL"),
                True,
                False,
                lambda _: (nested, root_file, nested),
            )
            self.assertEqual(discover(spec, context), (root_file.resolve(), alpha.resolve()))

    def test_discovery_rejects_symlink_file_resolving_outside_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "root"
            outside = base / "outside.jsonl"
            root.mkdir()
            outside.write_text("", encoding="utf-8")
            linked = root / "linked.jsonl"
            try:
                linked.symlink_to(outside)
            except OSError as error:
                self.skipTest("symlinks unavailable: {}".format(error))

            spec = SourceSpec(
                "test", "jsonl", ("*.jsonl",), True, False, lambda _: (root,)
            )
            found = discover(spec, DiscoveryContext("linux", base, {}))
            self.assertEqual(found, ())

    def test_discovery_rejects_symlink_directory_root_before_traversal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            outside = base / "outside"
            outside.mkdir()
            (outside / "session.jsonl").write_text("", encoding="utf-8")
            lexical_parent = base / "registered"
            lexical_parent.mkdir()
            linked_root = lexical_parent / "sessions"
            try:
                linked_root.symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest("symlinks unavailable: {}".format(error))

            spec = SourceSpec(
                "test",
                "jsonl",
                ("*.jsonl",),
                True,
                False,
                lambda _: (linked_root,),
            )
            found = discover(spec, DiscoveryContext("linux", base, {}))
            self.assertEqual(found, ())

    def test_explicit_symlink_file_root_must_stay_within_parent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            registered = base / "registered"
            outside = base / "outside"
            registered.mkdir()
            outside.mkdir()
            safe_target = registered / "safe-target.jsonl"
            unsafe_target = outside / "unsafe-target.jsonl"
            safe_target.write_text("", encoding="utf-8")
            unsafe_target.write_text("", encoding="utf-8")
            safe_link = registered / "safe.jsonl"
            unsafe_link = registered / "unsafe.jsonl"
            try:
                safe_link.symlink_to(safe_target)
                unsafe_link.symlink_to(unsafe_target)
            except OSError as error:
                self.skipTest("symlinks unavailable: {}".format(error))

            context = DiscoveryContext("linux", base, {})
            safe_spec = SourceSpec(
                "test",
                "jsonl",
                ("*.jsonl",),
                False,
                False,
                lambda _: (safe_link,),
            )
            unsafe_spec = SourceSpec(
                "test",
                "jsonl",
                ("*.jsonl",),
                False,
                False,
                lambda _: (unsafe_link,),
            )
            self.assertEqual(discover(safe_spec, context), (safe_target.resolve(),))
            self.assertEqual(discover(unsafe_spec, context), ())

    def test_nonrecursive_discovery_does_not_descend(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            direct = root / "direct.json"
            direct.write_text("{}", encoding="utf-8")
            nested = root / "nested"
            nested.mkdir()
            (nested / "hidden.json").write_text("{}", encoding="utf-8")

            context = DiscoveryContext("linux", root, {})
            spec = SourceSpec(
                "test", "json", ("*.json",), False, False, lambda _: (root,)
            )
            self.assertEqual(discover(spec, context), (direct.resolve(),))

    def test_kiro_specs_accept_only_frozen_source_shapes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            cli = home / ".kiro/sessions/cli"
            ide = home / ".kiro/sessions/workspace/sess_123"
            stray = home / ".kiro/sessions/workspace/not_a_session"
            storage = (
                home
                / ".config/kiro/User/globalStorage/kiro.kiroagent/workspace"
            )
            db = home / ".local/share/kiro-cli/data.sqlite3"
            for directory in (cli, ide, stray, storage, db.parent):
                directory.mkdir(parents=True, exist_ok=True)

            accepted = (
                cli / "cli-session.json",
                ide / "session.json",
                storage / "execution.chat",
                storage / "metadata.json",
                storage / "extensionless",
                db,
            )
            rejected = (
                ide / "messages.jsonl",
                stray / "session.json",
                storage / "index.sqlite",
            )
            for path in accepted + rejected:
                path.write_text("{}", encoding="utf-8")

            found = self._discover_runtime(
                "kiro", DiscoveryContext("linux", home, {})
            )
            self.assertEqual(
                {path.name for path in found},
                {path.name for path in accepted},
            )
            self.assertEqual(len(found), len(accepted))
            session_metadata = [
                path for path in found if path.name == "session.json"
            ]
            self.assertEqual(len(session_metadata), 1)
            self.assertTrue(session_metadata[0].parent.name.startswith("sess_"))

    def test_opencode_specs_reject_arbitrary_json_and_invalid_databases(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            data = home / ".local/share/opencode"
            messages = data / "storage/message/session"
            messages.mkdir(parents=True)
            accepted = (
                data / "opencode.db",
                data / "opencode-stable.db",
                data / "opencode-A.Z_9-.db",
                messages / "message.json",
            )
            rejected = (
                data / "unrelated.json",
                data / "opencode-.db",
                data / "opencode-café.db",
                data / "opencode-βeta.db",
                data / "opencode.db-wal",
            )
            for path in accepted + rejected:
                path.write_text("{}", encoding="utf-8")

            found = self._discover_runtime(
                "opencode", DiscoveryContext("linux", home, {})
            )
            self.assertEqual(found, {path.resolve() for path in accepted})

    def test_codebuddy_generic_logs_require_tencent_component(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            dedicated = (
                home
                / "AppData/Local/CodeBuddyExtension/Logs/CodeBuddyIDE/day"
                / "dedicated.log"
            )
            generic = (
                home
                / "AppData/Roaming/Code/logs/day/"
                "Tencent-Cloud.coding-copilot/extension.log"
            )
            false_positive = home / "AppData/Roaming/Code/logs/day/other.log"
            project = home / ".codebuddy/projects/project/session.jsonl"
            for path in (dedicated, generic, false_positive, project):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")

            found = self._discover_runtime(
                "codebuddy", DiscoveryContext("windows", home, {})
            )
            self.assertEqual(
                found,
                {dedicated.resolve(), generic.resolve(), project.resolve()},
            )

    def test_copilot_specs_include_desktop_db_and_extensionless_exporter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            desktop_db = home / ".copilot/data.db"
            exporter = home / "custom/copilot-events"
            for path in (desktop_db, exporter):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")

            context = DiscoveryContext(
                "linux",
                home,
                {"COPILOT_OTEL_FILE_EXPORTER_PATH": str(exporter)},
            )
            found = self._discover_runtime("copilot", context)
            self.assertIn(desktop_db.resolve(), found)
            self.assertIn(exporter.resolve(), found)

            invalid_exporter = home / "custom/exporter-directory"
            invalid_child = invalid_exporter / "child.jsonl"
            invalid_exporter.mkdir()
            invalid_child.write_text("", encoding="utf-8")
            invalid_context = DiscoveryContext(
                "linux",
                home,
                {"COPILOT_OTEL_FILE_EXPORTER_PATH": str(invalid_exporter)},
            )
            invalid_found = self._discover_runtime("copilot", invalid_context)
            self.assertNotIn(invalid_child.resolve(), invalid_found)

    def test_cursor_and_warp_exclude_archives_and_backups(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            config = home / "config"
            cursor = home / ".config/tokscale/cursor-cache"
            warp = config / "warp-cache"
            for directory in (cursor / "archive", warp / "archive"):
                directory.mkdir(parents=True)

            accepted = (
                cursor / "usage.csv",
                cursor / "usage.account.csv",
                warp / "usage.json",
                warp / "usage.account.json",
            )
            rejected = (
                cursor / "usage.backup-1.csv",
                cursor / "archive/usage.csv",
                cursor / "usage-other.csv",
                warp / "usage.backup-1.json",
                warp / "archive/usage.json",
                warp / "usage-other.json",
            )
            for path in accepted + rejected:
                path.write_text("", encoding="utf-8")

            context = DiscoveryContext(
                "linux", home, {"TOKSCALE_CONFIG_DIR": str(config)}
            )
            found = self._discover_runtime("cursor", context)
            found.update(self._discover_runtime("warp", context))
            self.assertEqual(found, {path.resolve() for path in accepted})

    def test_openclaw_accepts_only_transcripts_and_known_archives(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            sessions = home / ".openclaw/agents/main/sessions"
            sessions.mkdir(parents=True)
            accepted = (
                sessions / "normal.jsonl",
                sessions / "deleted.jsonl.deleted.123",
                sessions / "reset.jsonl.reset.456",
            )
            rejected = (
                sessions / "backup.jsonl.bak",
                sessions / "almost.jsonl.deleted",
            )
            for path in accepted + rejected:
                path.write_text("", encoding="utf-8")

            found = self._discover_runtime(
                "openclaw", DiscoveryContext("linux", home, {})
            )
            self.assertEqual(found, {path.resolve() for path in accepted})

    def test_copilot_workspace_storage_only_accepts_chat_sessions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            workspace = home / ".config/Code/User/workspaceStorage/hash"
            accepted = workspace / "chatSessions/session.jsonl"
            rejected = (
                workspace / "other/session.jsonl",
                workspace / "nested/chatSessions/session.jsonl",
            )
            for path in (accepted,) + rejected:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")

            found = self._discover_runtime(
                "copilot", DiscoveryContext("linux", home, {})
            )
            self.assertIn(accepted.resolve(), found)
            self.assertTrue(all(path.resolve() not in found for path in rejected))

    def test_hermes_profiles_are_one_level_deep(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            hermes = home / ".hermes"
            accepted = (
                hermes / "state.db",
                hermes / "profiles/work/state.db",
            )
            rejected = hermes / "profiles/work/nested/state.db"
            for path in accepted + (rejected,):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")

            found = self._discover_runtime(
                "hermes", DiscoveryContext("linux", home, {})
            )
            self.assertEqual(found, {path.resolve() for path in accepted})

    def test_micode_database_names_follow_channel_rule(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            data = home / ".local/share/mimocode"
            data.mkdir(parents=True)
            accepted = (
                data / "mimocode.db",
                data / "mimocode-stable.db",
                data / "mimocode-A.Z_9-.db",
            )
            rejected = (
                data / "mimocodefoo.db",
                data / "mimocode-.db",
                data / "mimocode-café.db",
                data / "mimocode-βeta.db",
                data / "mimocode.db-wal",
            )
            for path in accepted + rejected:
                path.write_text("", encoding="utf-8")

            found = self._discover_runtime(
                "micode", DiscoveryContext("linux", home, {})
            )
            self.assertEqual(found, {path.resolve() for path in accepted})

    def test_zcode_and_workbuddy_keep_root_specific_patterns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            accepted = (
                home / ".zcode/projects/demo/session.jsonl",
                home / ".zcode/cli/db/db.sqlite",
                home / ".workbuddy/projects/demo/session.jsonl",
                home / ".workbuddy/workbuddy.db",
            )
            rejected = (
                home / ".zcode/projects/demo/db.sqlite",
                home / ".zcode/cli/db/session.jsonl",
                home / ".workbuddy/projects/demo/workbuddy.db",
                home / ".workbuddy/session.jsonl",
            )
            for path in accepted + rejected:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")

            context = DiscoveryContext("linux", home, {})
            found = self._discover_runtime("zcode", context)
            found.update(self._discover_runtime("workbuddy", context))
            self.assertEqual(found, {path.resolve() for path in accepted})

    def test_claude_discovers_valid_cc_mirror_project_roots_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            mirror = home / ".cc-mirror"
            absolute_config = home / "external-config"
            valid_variants = (
                (mirror / "absolute", {"configDir": str(absolute_config)}),
                (mirror / "tilde", {"configDir": "~/tilde-config"}),
                (mirror / "relative", {"configDir": "relative-config"}),
                (mirror / "default", {"name": "default"}),
            )
            accepted = []
            import json

            for variant, payload in valid_variants:
                variant.mkdir(parents=True)
                (variant / "variant.json").write_text(
                    json.dumps(payload), encoding="utf-8"
                )
                raw = payload.get("configDir")
                if raw is None:
                    projects = variant / "config/projects"
                elif raw.startswith("~/"):
                    projects = home / raw[2:] / "projects"
                elif Path(raw).is_absolute():
                    projects = Path(raw) / "projects"
                else:
                    projects = variant / raw / "projects"
                session = projects / "workspace/session.jsonl"
                session.parent.mkdir(parents=True)
                session.write_text("", encoding="utf-8")
                accepted.append(session)

            malformed = mirror / "malformed"
            malformed.mkdir()
            (malformed / "variant.json").write_text("{", encoding="utf-8")
            (malformed / "config/projects/bad.jsonl").parent.mkdir(parents=True)
            (malformed / "config/projects/bad.jsonl").write_text(
                "", encoding="utf-8"
            )

            wrong_type = mirror / "wrong-type"
            wrong_type.mkdir()
            (wrong_type / "variant.json").write_text(
                '{"configDir": 42}', encoding="utf-8"
            )
            (wrong_type / "config/projects/bad.jsonl").parent.mkdir(parents=True)
            (wrong_type / "config/projects/bad.jsonl").write_text(
                "", encoding="utf-8"
            )

            missing_projects = mirror / "missing-projects"
            missing_projects.mkdir()
            (missing_projects / "variant.json").write_text(
                '{"configDir": "absent"}', encoding="utf-8"
            )

            missing_variant = mirror / "missing-variant"
            missing_variant_session = (
                missing_variant / "config/projects/bad.jsonl"
            )
            missing_variant_session.parent.mkdir(parents=True)
            missing_variant_session.write_text("", encoding="utf-8")

            nested = mirror / "container/nested"
            nested.mkdir(parents=True)
            (nested / "variant.json").write_text("{}", encoding="utf-8")
            nested_session = nested / "config/projects/bad.jsonl"
            nested_session.parent.mkdir(parents=True)
            nested_session.write_text("", encoding="utf-8")

            found = self._discover_runtime(
                "claude", DiscoveryContext("linux", home, {})
            )
            self.assertTrue(
                {path.resolve() for path in accepted}.issubset(found)
            )
            self.assertNotIn(
                (malformed / "config/projects/bad.jsonl").resolve(), found
            )
            self.assertNotIn(
                (wrong_type / "config/projects/bad.jsonl").resolve(), found
            )
            self.assertNotIn(missing_variant_session.resolve(), found)
            self.assertNotIn(nested_session.resolve(), found)

    def test_cc_mirror_rejects_config_roots_escaping_home(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            home = base / "home"
            mirror = home / ".cc-mirror"
            outside = base / "outside"
            outside.mkdir()
            import json

            rejected_sessions = []
            variants = (
                (
                    mirror / "relative-escape",
                    {"configDir": "../../../outside"},
                    outside,
                ),
                (
                    mirror / "absolute-escape",
                    {"configDir": str(base / "absolute-outside")},
                    base / "absolute-outside",
                ),
            )
            for variant, payload, config in variants:
                variant.mkdir(parents=True)
                (variant / "variant.json").write_text(
                    json.dumps(payload), encoding="utf-8"
                )
                session = config / "projects/session.jsonl"
                session.parent.mkdir(parents=True, exist_ok=True)
                session.write_text("", encoding="utf-8")
                rejected_sessions.append(session)

            symlink_variant = mirror / "symlink-escape"
            symlink_variant.mkdir()
            (symlink_variant / "variant.json").write_text(
                '{"configDir": "config"}', encoding="utf-8"
            )
            symlink_target = base / "symlink-outside/projects"
            symlink_target.mkdir(parents=True)
            symlink_session = symlink_target / "session.jsonl"
            symlink_session.write_text("", encoding="utf-8")
            config = symlink_variant / "config"
            config.mkdir()
            try:
                (config / "projects").symlink_to(
                    symlink_target, target_is_directory=True
                )
            except OSError as error:
                self.skipTest("symlinks unavailable: {}".format(error))
            rejected_sessions.append(symlink_session)

            found = self._discover_runtime(
                "claude", DiscoveryContext("linux", home, {})
            )
            self.assertTrue(
                all(path.resolve() not in found for path in rejected_sessions)
            )


if __name__ == "__main__":
    unittest.main()
