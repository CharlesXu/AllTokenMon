# All Token Monitor

[中文](#中文说明) | [English](#english)

## 中文说明

All Token Monitor 是一个极轻量、本地优先的 Codex Skill，可汇总 39 种
AI 编程运行时的 token 用量，并按周期、运行时和模型生成确定性的 JSON
数据或可直接阅读的 Markdown 简报。

- 支持 Windows、Linux 和 macOS。
- 支持 Python 3.9 及以上版本。
- 零安装依赖、零运行时第三方包依赖，不依赖 Tokscale。
- 仅读取受支持的本地数据，不联网、不启动外部命令、不修改源文件。
- 支持今日、近 7 个本地自然日、本月至今和本地全部可用历史。
- 支持运行时及模型筛选，并可生成有数据依据的简报和点评。

### 安装

将仓库复制到 Codex Skill 目录，并保持 `SKILL.md`、`scripts/` 和其中的
vendored 文件位于同一目录树。例如：

```text
~/.codex/skills/all-token-monitor/
```

无需安装 Python 包，也无需构建。

### 运行

Linux 和 macOS：

```sh
python3 scripts/token_usage.py --format markdown
python3 scripts/token_usage.py --format json
```

Windows PowerShell 或命令提示符：

```powershell
py -3 scripts\token_usage.py --format markdown
py -3 scripts\token_usage.py --format json
```

筛选运行时：

```sh
python3 scripts/token_usage.py --runtime codex,claude,gemini --format markdown
```

筛选模型（支持区分大小写的 shell 风格 glob）：

```sh
python3 scripts/token_usage.py --model "gpt-5*,claude-*" --format json
```

`--runtime` 与 `--model` 可以组合使用。使用 `--diagnostics` 可将脱敏后的
适配器状态码写入标准错误；`--home PATH` 仅用于扫描替代的主目录或测试夹具。
Markdown 表格中的 token 数按数值自动使用“亿、百万、K、Token”单位，
并固定保留三位小数；JSON 仍保留精确整数，便于后续计算。

### 周期定义

所有边界均采用运行时的本地时区，并排除未来时间的记录。

- **今日：** 从本地当天 00:00 到当前时刻。
- **近 7 日：** 从 6 个自然日前的本地 00:00 到当前时刻，共 7 个本地自然日，并非滚动 168 小时。
- **本月至今：** 从本月 1 日的本地 00:00 到当前时刻。
- **本地全部历史：** 当前时刻之前，本地仍保留的全部受支持记录。

“本地全部历史”不是账号终身总计；运行时的数据保留、压缩、归档删除、缓存
淘汰或本地清理都可能移除旧记录。

### 隐私边界

报告只包含汇总计数、运行时名、模型名、覆盖情况和脱敏状态码，不序列化提示词、
回复、原始记录、源路径、会话 ID、API key、Cookie 或 token。SQLite 数据源
以只读方式打开；Cursor、Antigravity IDE、Trae 和 Warp 仅使用已有的兼容缓存，
不会下载、登录、同步或刷新缓存。

缺少缓存时会报告 `no_data`；遇到新版本、截断、损坏或只能部分理解的本地格式时，
适配器可能报告 `partial` 或 `unsupported_format`。费用仅展示数据源实际报告的值，
从不估算缺失费用。

模型和 provider 以用量记录或会话元数据中的显式值为准，未知或未来型号原样保留，
不会依赖需要持续更新的内置型号目录。Claude 适配器只在记录缺少身份信息时读取
`--home` 下精确的 `.claude/settings.json` 白名单字段作当前配置补充；它不会把
当前配置宣称为历史会话事实，也不会读取或输出密钥、请求头、账号、区域或私有端点。

### 开发验证

```sh
python3 -m unittest discover -s tests -v
python3 -m unittest tests.test_registry tests.test_privacy -v
```

运行时覆盖证据见 [`docs/runtime-coverage.md`](docs/runtime-coverage.md)，第三方许可
见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。项目采用
[MIT License](LICENSE)。

## English

All Token Monitor is a small, local-first Codex Skill for summarizing token
usage from 39 AI coding runtimes. It produces deterministic JSON or a readable
Markdown brief grouped by period, runtime, and model.

The command supports Python 3.9 or newer and has zero install-time or runtime
package dependencies. It does not require Tokscale.

## Install

Copy this repository into a Codex skill directory, keeping `SKILL.md`,
`scripts/`, and the vendored files together. For example:

```text
~/.codex/skills/all-token-monitor/
```

No package installation or build step is required.

## Run

Linux and macOS:

```sh
python3 scripts/token_usage.py --format markdown
python3 scripts/token_usage.py --format json
```

Windows PowerShell or Command Prompt:

```powershell
py -3 scripts\token_usage.py --format markdown
py -3 scripts\token_usage.py --format json
```

The Markdown output is a ready-to-read brief:

```markdown
# All Token Monitor 完整报告

## Token 用量周期表

| 周期 | 输入 Token | 输出 Token | 推理 Token | 缓存读取 | 缓存写入 | 总量 | 费用 |
```

Markdown token tables select `亿` (100 million), `百万` (million), `K`, or
`Token` according to each value and always show three decimal places. JSON
retains exact integer token counts for deterministic downstream analysis. The
following is an abbreviated shape; the real output also includes runtime/model
rankings, quality metrics, and sanitized diagnostics:

```json
{
  "coverage": {
    "runtime_count": 39,
    "status": "complete"
  },
  "periods": {
    "today": {
      "totals": {
        "input": 120,
        "output": 30,
        "total": 150
      }
    }
  }
}
```

Provider-reported cost is shown only when present in a source. Missing cost is
reported as unavailable and is never estimated.

## Filters

Select one or more runtimes with a comma-separated list:

```sh
python3 scripts/token_usage.py --runtime codex,claude,gemini --format markdown
```

Select models with one or more comma-separated, case-sensitive shell-style
globs:

```sh
python3 scripts/token_usage.py --model "gpt-5*,claude-*" --format json
```

The filters can be combined. Runtime IDs are:

```text
opencode, claude, codex, cursor, gemini, amp, droid, openclaw, pi,
kimi, qwen, roocode, kilocode, mux, kilo, crush, hermes, copilot,
goose, codebuff, antigravity, zed, kiro, trae, warp, cline, gjc,
grok, jcode, commandcode, micode, antigravity-cli, junie, zcode,
opencodereview, codebuddy, workbuddy, devin-cli, devin-desktop
```

Use `--diagnostics` to write privacy-safe adapter status codes to stderr.
Use `--home PATH` only when scanning an alternate home directory or fixtures.

## Period definitions

All boundaries use the local timezone of the run, and future-dated records are
excluded.

- **Today:** local midnight through the current instant.
- **Trailing 7 days:** local midnight six calendar days ago through the
  current instant. This is seven local calendar dates, not a rolling 168-hour
  window.
- **Month:** local midnight on the first day of the current calendar month
  through the current instant.
- **All time:** every supported record still retained locally, through the
  current instant.

All-time is therefore not a lifetime account total. Runtime retention,
compaction, archive deletion, cache eviction, and local cleanup can remove
older records.

## Local-only and privacy boundary

The bundled command scans only registered local paths. Its runtime code has no
network client and does not launch external commands. SQLite sources are opened
read-only, source files are not modified, and cache-only adapters never refresh
their caches.

Reports contain aggregate counts, runtime names, model names, coverage, and
sanitized status codes. They do not serialize prompts, responses, raw record
bodies, source paths, session IDs, API keys, cookies, or tokens. Model names
are retained because model-level reporting is a requested feature; treat the
resulting report according to your own metadata policy.

The four cache-only runtimes are Cursor, Antigravity IDE, Trae, and Warp. They
are reported only when a compatible existing Tokscale cache is already present:

- no cache download, login, sync, or refresh is attempted;
- missing caches produce `no_data`;
- stale or incomplete caches produce correspondingly stale or incomplete
  results;
- Warp token counts remain zero when its cache contains requests or cost but
  no provider-reported token counts.

An adapter can report `partial` or `unsupported_format` when a local schema is
newer, truncated, malformed, or only partly understood. Qualify comparisons
when coverage is partial.

Explicit model and provider values in usage or session evidence are authoritative.
Unknown and future model identifiers are preserved instead of being rewritten
through a time-sensitive built-in catalog. The Claude adapter consults only
allowlisted fields in the exact `.claude/settings.json` below `--home`, and only
to fill missing identity metadata. Current settings are not presented as
historical session facts; credentials, headers, account or region fields, and
private endpoint values are neither retained nor reported.

## Development

Run the standard-library test suite:

```sh
python3 -m unittest discover -s tests -v
```

Run the registry/privacy audit:

```sh
python3 -m unittest tests.test_registry tests.test_privacy -v
```

If the development-only `coverage` package is installed, run the coverage gate:

```sh
python3 -m coverage run --branch --source=scripts/alltokenmon -m unittest discover -s tests
python3 -m coverage report --omit="*/_vendor/*" --precision=2 --fail-under=80
```

The authoritative runtime evidence is in
[`docs/runtime-coverage.md`](docs/runtime-coverage.md). Third-party attribution
for the frozen format baseline and bundled Zed decoder is in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

## License

All Token Monitor is released under the [MIT License](LICENSE). Bundled
third-party components remain subject to the licenses listed in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
