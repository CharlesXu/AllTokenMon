# Runtime coverage

This matrix audits the frozen 39-runtime A3 scope against committed code and
tests. `complete` means the row has a registered adapter, a discoverable
sanitized fixture that produces usage, token-field assertions, malformed or
unsupported-input coverage, privacy coverage, and aggregation coverage.

Shared evidence used by every row:

- **P39:** [`test_all_39_public_parsers_execute_committed_fixtures`](../tests/test_privacy.py)
  discovers each runtime through its registered path and requires `ok` plus at
  least one record.
- **OS:** [`tests/test_discovery.py`](../tests/test_discovery.py) simulates
  Windows, Linux, and macOS roots, environment overrides, bounded patterns, and
  containment rules.
- **PR39:** [`tests/test_privacy.py`](../tests/test_privacy.py) places a unique
  ignored secret in every runtime fixture, then verifies records, JSON,
  Markdown, stdout, and stderr do not expose any sentinel.
- **AG39:** the same privacy integration test aggregates records from all 39
  runtimes; [`tests/test_aggregate.py`](../tests/test_aggregate.py) separately
  verifies the four windows, token categories, runtime/model rollups, stable
  ranking, bounds, cost handling, and partial-coverage flags.

Links in both assertion columns point to the adapter's direct tests. Roo Code
and Kilo Code use the shared Cline-family parser, so their malformed-input
evidence is the shared Cline test module as indicated.

| Runtime | Adapter | Source type | Path tests | Fixture | Token assertions | Malformed input | Privacy | Aggregation | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| opencode | [`opencode.py`](../scripts/alltokenmon/adapters/opencode.py) | SQLite + JSON | P39 + OS | [`message.json`](../tests/fixtures/.local/share/opencode/storage/message/safe/message.json) | [`test_opencode.py`](../tests/adapters/test_opencode.py) | [`test_opencode.py`](../tests/adapters/test_opencode.py) | PR39 | AG39 | complete |
| claude | [`claude.py`](../scripts/alltokenmon/adapters/claude.py) | JSONL + allowlisted current-settings enrichment | P39 + OS | [`session.jsonl`](../tests/fixtures/.claude/projects/safe/session.jsonl) | [`test_claude.py`](../tests/adapters/test_claude.py) | [`test_claude.py`](../tests/adapters/test_claude.py) | PR39 | AG39 | complete |
| codex | [`codex.py`](../scripts/alltokenmon/adapters/codex.py) | JSONL | P39 + OS | [`session.jsonl`](../tests/fixtures/.codex/sessions/session.jsonl) | [`test_codex.py`](../tests/adapters/test_codex.py) | [`test_codex.py`](../tests/adapters/test_codex.py) | PR39 | AG39 | complete |
| cursor | [`cursor.py`](../scripts/alltokenmon/adapters/cursor.py) | CSV cache | P39 + OS | [`usage.csv`](../tests/fixtures/.config/tokscale/cursor-cache/usage.csv) | [`test_cursor.py`](../tests/adapters/test_cursor.py) | [`test_cursor.py`](../tests/adapters/test_cursor.py) | PR39 | AG39 | complete |
| gemini | [`gemini.py`](../scripts/alltokenmon/adapters/gemini.py) | JSON + JSONL | P39 + OS | [`conversation.json`](../tests/fixtures/.gemini/tmp/safe/chats/conversation.json) | [`test_gemini.py`](../tests/adapters/test_gemini.py) | [`test_gemini.py`](../tests/adapters/test_gemini.py) | PR39 | AG39 | complete |
| amp | [`amp.py`](../scripts/alltokenmon/adapters/amp.py) | JSON | P39 + OS | [`T-fixture.json`](../tests/fixtures/.local/share/amp/threads/T-fixture.json) | [`test_amp.py`](../tests/adapters/test_amp.py) | [`test_amp.py`](../tests/adapters/test_amp.py) | PR39 | AG39 | complete |
| droid | [`droid.py`](../scripts/alltokenmon/adapters/droid.py) | JSON | P39 + OS | [`fixture.settings.json`](../tests/fixtures/.factory/sessions/fixture.settings.json) | [`test_droid.py`](../tests/adapters/test_droid.py) | [`test_droid.py`](../tests/adapters/test_droid.py) | PR39 | AG39 | complete |
| openclaw | [`openclaw.py`](../scripts/alltokenmon/adapters/openclaw.py) | JSONL + archived JSONL | P39 + OS | [`session.jsonl`](../tests/fixtures/.openclaw/agents/main/sessions/session.jsonl) | [`test_openclaw.py`](../tests/adapters/test_openclaw.py) | [`test_openclaw.py`](../tests/adapters/test_openclaw.py) | PR39 | AG39 | complete |
| pi | [`pi.py`](../scripts/alltokenmon/adapters/pi.py) | JSONL | P39 + OS | [`session.jsonl`](../tests/fixtures/.pi/agent/sessions/session.jsonl) | [`test_pi.py`](../tests/adapters/test_pi.py) | [`test_pi.py`](../tests/adapters/test_pi.py) | PR39 | AG39 | complete |
| kimi | [`kimi.py`](../scripts/alltokenmon/adapters/kimi.py) | JSONL | P39 + OS | [`wire.jsonl`](../tests/fixtures/.kimi/sessions/demo/wire.jsonl) | [`test_kimi.py`](../tests/adapters/test_kimi.py) | [`test_kimi.py`](../tests/adapters/test_kimi.py) | PR39 | AG39 | complete |
| qwen | [`qwen.py`](../scripts/alltokenmon/adapters/qwen.py) | JSONL | P39 + OS | [`session.jsonl`](../tests/fixtures/.qwen/projects/session.jsonl) | [`test_qwen.py`](../tests/adapters/test_qwen.py) | [`test_qwen.py`](../tests/adapters/test_qwen.py) | PR39 | AG39 | complete |
| roocode | [`roocode.py`](../scripts/alltokenmon/adapters/roocode.py) | JSON | P39 + OS | [`ui_messages.json`](../tests/fixtures/.config/Code/User/globalStorage/rooveterinaryinc.roo-cline/tasks/demo/ui_messages.json) | [`test_roocode.py`](../tests/adapters/test_roocode.py) | [shared Cline-family tests](../tests/adapters/test_cline.py) | PR39 | AG39 | complete |
| kilocode | [`kilocode.py`](../scripts/alltokenmon/adapters/kilocode.py) | JSON | P39 + OS | [`ui_messages.json`](../tests/fixtures/.config/Code/User/globalStorage/kilocode.kilo-code/tasks/demo/ui_messages.json) | [`test_kilocode.py`](../tests/adapters/test_kilocode.py) | [shared Cline-family tests](../tests/adapters/test_cline.py) | PR39 | AG39 | complete |
| mux | [`mux.py`](../scripts/alltokenmon/adapters/mux.py) | JSON | P39 + OS | [`session-usage.json`](../tests/fixtures/.mux/sessions/demo/session-usage.json) | [`test_mux.py`](../tests/adapters/test_mux.py) | [`test_mux.py`](../tests/adapters/test_mux.py) | PR39 | AG39 | complete |
| kilo | [`kilo.py`](../scripts/alltokenmon/adapters/kilo.py) | SQLite | P39 + OS | [`kilo.db`](../tests/fixtures/.local/share/kilo/kilo.db) | [`test_kilo.py`](../tests/adapters/test_kilo.py) | [`test_kilo.py`](../tests/adapters/test_kilo.py) | PR39 | AG39 | complete |
| crush | [`crush.py`](../scripts/alltokenmon/adapters/crush.py) | JSON registry + SQLite | P39 + OS | [`projects.json`](../tests/fixtures/.local/share/crush/projects.json) + [`crush.db`](../tests/fixtures/crush-project/.crush/crush.db) | [`test_crush.py`](../tests/adapters/test_crush.py) | [`test_crush.py`](../tests/adapters/test_crush.py) | PR39 | AG39 | complete |
| hermes | [`hermes.py`](../scripts/alltokenmon/adapters/hermes.py) | SQLite | P39 + OS | [`state.db`](../tests/fixtures/.hermes/state.db) | [`test_hermes.py`](../tests/adapters/test_hermes.py) | [`test_hermes.py`](../tests/adapters/test_hermes.py) | PR39 | AG39 | complete |
| copilot | [`copilot.py`](../scripts/alltokenmon/adapters/copilot.py) | JSONL + SQLite | P39 + OS | [`events.jsonl`](../tests/fixtures/.copilot/otel/events.jsonl) | [`test_copilot.py`](../tests/adapters/test_copilot.py) | [`test_copilot.py`](../tests/adapters/test_copilot.py) | PR39 | AG39 | complete |
| goose | [`goose.py`](../scripts/alltokenmon/adapters/goose.py) | SQLite | P39 + OS | [`sessions.db`](../tests/fixtures/.local/share/goose/sessions/sessions.db) | [`test_goose.py`](../tests/adapters/test_goose.py) | [`test_goose.py`](../tests/adapters/test_goose.py) | PR39 | AG39 | complete |
| codebuff | [`codebuff.py`](../scripts/alltokenmon/adapters/codebuff.py) | JSON | P39 + OS | [`chat-messages.json`](../tests/fixtures/.config/manicode/projects/demo/chat-messages.json) | [`test_codebuff.py`](../tests/adapters/test_codebuff.py) | [`test_codebuff.py`](../tests/adapters/test_codebuff.py) | PR39 | AG39 | complete |
| antigravity | [`antigravity.py`](../scripts/alltokenmon/adapters/antigravity.py) | JSONL cache | P39 + OS | [`session.jsonl`](../tests/fixtures/.config/tokscale/antigravity-cache/sessions/session.jsonl) | [`test_antigravity.py`](../tests/adapters/test_antigravity.py) | [`test_antigravity.py`](../tests/adapters/test_antigravity.py) | PR39 | AG39 | complete |
| zed | [`zed.py`](../scripts/alltokenmon/adapters/zed.py) | SQLite + Zstandard | P39 + OS | [`threads.db`](../tests/fixtures/.local/share/zed/threads/threads.db) | [`test_zed.py`](../tests/adapters/test_zed.py) | [`test_zed.py`](../tests/adapters/test_zed.py) | PR39 | AG39 | complete |
| kiro | [`kiro.py`](../scripts/alltokenmon/adapters/kiro.py) | JSON + global storage + SQLite | P39 + OS | [`execution.json`](../tests/fixtures/.kiro/sessions/cli/execution.json) | [`test_kiro.py`](../tests/adapters/test_kiro.py) | [`test_kiro.py`](../tests/adapters/test_kiro.py) | PR39 | AG39 | complete |
| trae | [`trae.py`](../scripts/alltokenmon/adapters/trae.py) | JSON cache | P39 + OS | [`session.json`](../tests/fixtures/.config/tokscale/trae-cache/sessions/session.json) | [`test_trae.py`](../tests/adapters/test_trae.py) | [`test_trae.py`](../tests/adapters/test_trae.py) | PR39 | AG39 | complete |
| warp | [`warp.py`](../scripts/alltokenmon/adapters/warp.py) | JSON cache | P39 + OS | [`usage.json`](../tests/fixtures/.config/tokscale/warp-cache/usage.json) | [`test_warp.py`](../tests/adapters/test_warp.py) | [`test_warp.py`](../tests/adapters/test_warp.py) | PR39 | AG39 | complete |
| cline | [`cline.py`](../scripts/alltokenmon/adapters/cline.py) | JSON | P39 + OS | [`ui_messages.json`](../tests/fixtures/.config/Code/User/globalStorage/saoudrizwan.claude-dev/tasks/demo/ui_messages.json) | [`test_cline.py`](../tests/adapters/test_cline.py) | [`test_cline.py`](../tests/adapters/test_cline.py) | PR39 | AG39 | complete |
| gjc | [`gjc.py`](../scripts/alltokenmon/adapters/gjc.py) | JSONL | P39 + OS | [`session.jsonl`](../tests/fixtures/.gjc/agent/sessions/session.jsonl) | [`test_gjc.py`](../tests/adapters/test_gjc.py) | [`test_gjc.py`](../tests/adapters/test_gjc.py) | PR39 | AG39 | complete |
| grok | [`grok.py`](../scripts/alltokenmon/adapters/grok.py) | JSONL | P39 + OS | [`updates.jsonl`](../tests/fixtures/.grok/sessions/demo/updates.jsonl) | [`test_grok.py`](../tests/adapters/test_grok.py) | [`test_grok.py`](../tests/adapters/test_grok.py) | PR39 | AG39 | complete |
| jcode | [`jcode.py`](../scripts/alltokenmon/adapters/jcode.py) | JSON | P39 + OS | [`session_fixture.json`](../tests/fixtures/.jcode/sessions/session_fixture.json) | [`test_jcode.py`](../tests/adapters/test_jcode.py) | [`test_jcode.py`](../tests/adapters/test_jcode.py) | PR39 | AG39 | complete |
| commandcode | [`commandcode.py`](../scripts/alltokenmon/adapters/commandcode.py) | JSONL | P39 + OS | [`session.jsonl`](../tests/fixtures/.commandcode/projects/demo/session.jsonl) | [`test_commandcode.py`](../tests/adapters/test_commandcode.py) | [`test_commandcode.py`](../tests/adapters/test_commandcode.py) | PR39 | AG39 | complete |
| micode | [`micode.py`](../scripts/alltokenmon/adapters/micode.py) | SQLite | P39 + OS | [`mimocode.db`](../tests/fixtures/.local/share/mimocode/mimocode.db) | [`test_micode.py`](../tests/adapters/test_micode.py) | [`test_micode.py`](../tests/adapters/test_micode.py) | PR39 | AG39 | complete |
| antigravity-cli | [`antigravity_cli.py`](../scripts/alltokenmon/adapters/antigravity_cli.py) | SQLite + wire blobs | P39 + OS | [`fixture.db`](../tests/fixtures/.gemini/antigravity-cli/conversations/fixture.db) | [`test_antigravity_cli.py`](../tests/adapters/test_antigravity_cli.py) | [`test_antigravity_cli.py`](../tests/adapters/test_antigravity_cli.py) | PR39 | AG39 | complete |
| junie | [`junie.py`](../scripts/alltokenmon/adapters/junie.py) | JSONL | P39 + OS | [`events.jsonl`](../tests/fixtures/.junie/sessions/demo/events.jsonl) | [`test_junie.py`](../tests/adapters/test_junie.py) | [`test_junie.py`](../tests/adapters/test_junie.py) | PR39 | AG39 | complete |
| zcode | [`zcode.py`](../scripts/alltokenmon/adapters/zcode.py) | JSONL + SQLite | P39 + OS | [`session.jsonl`](../tests/fixtures/.zcode/projects/demo/session.jsonl) | [`test_zcode.py`](../tests/adapters/test_zcode.py) | [`test_zcode.py`](../tests/adapters/test_zcode.py) | PR39 | AG39 | complete |
| opencodereview | [`opencodereview.py`](../scripts/alltokenmon/adapters/opencodereview.py) | JSONL | P39 + OS | [`session.jsonl`](../tests/fixtures/.opencodereview/sessions/session.jsonl) | [`test_opencodereview.py`](../tests/adapters/test_opencodereview.py) | [`test_opencodereview.py`](../tests/adapters/test_opencodereview.py) | PR39 | AG39 | complete |
| codebuddy | [`codebuddy.py`](../scripts/alltokenmon/adapters/codebuddy.py) | JSONL + logs | P39 + OS | [`session.jsonl`](../tests/fixtures/.codebuddy/projects/demo/session.jsonl) | [`test_codebuddy.py`](../tests/adapters/test_codebuddy.py) | [`test_codebuddy.py`](../tests/adapters/test_codebuddy.py) | PR39 | AG39 | complete |
| workbuddy | [`workbuddy.py`](../scripts/alltokenmon/adapters/workbuddy.py) | SQLite + JSONL | P39 + OS | [`workbuddy.db`](../tests/fixtures/.workbuddy/workbuddy.db) | [`test_workbuddy.py`](../tests/adapters/test_workbuddy.py) | [`test_workbuddy.py`](../tests/adapters/test_workbuddy.py) | PR39 | AG39 | complete |
| devin-cli | [`devin_cli.py`](../scripts/alltokenmon/adapters/devin_cli.py) | SQLite | P39 + OS | [`sessions.db`](../tests/fixtures/.local/share/devin/cli/sessions.db) | [`test_devin_cli.py`](../tests/adapters/test_devin_cli.py) | [`test_devin_cli.py`](../tests/adapters/test_devin_cli.py) | PR39 | AG39 | complete |
| devin-desktop | [`devin_desktop.py`](../scripts/alltokenmon/adapters/devin_desktop.py) | NDJSON | P39 + OS | [`session.ndjson`](../tests/fixtures/.config/devin/User/acp-events/session.ndjson) | [`test_devin_desktop.py`](../tests/adapters/test_devin_desktop.py) | [`test_devin_desktop.py`](../tests/adapters/test_devin_desktop.py) | PR39 | AG39 | complete |

## Audit commands

```sh
python3 -m unittest tests.test_registry tests.test_privacy tests.test_aggregate -v
python3 -m unittest discover -s tests -v
```

The table order is the authoritative registry order in
[`RUNTIME_IDS`](../scripts/alltokenmon/adapters/registry.py).
