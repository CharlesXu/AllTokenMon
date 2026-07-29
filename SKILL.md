---
name: all-token-monitor
description: >-
  Analyze aggregate token usage from supported AI coding runtimes with a local,
  read-only, no-network Python command. Use when a user asks for token counts,
  runtime or model comparisons, today or trailing-7-day usage, month-to-date
  usage, all locally available history, token briefs, or usage commentary.
  使用本地只读、无网络的 Python 命令分析 AI 编程运行时的汇总 token 用量；适用于
  token 统计、运行时或模型对比、日/近 7 日/月/本地全部历史简报及用量点评。
---

# All Token Monitor

## 中文说明

1. 按 `python3`、`python`、Windows `py -3` 的顺序选择可用的 Python 3.9+ 启动命令。
2. 定位本 Skill 目录，并从任意工作目录执行：

   ```text
   <python-launcher> <skill-directory>/scripts/token_usage.py --format json
   ```

   将命令视为本地、只读、无网络操作，只使用其标准输出中的汇总 JSON 进行分析。
3. 仅当用户明确要求时添加 `--runtime <逗号分隔列表>` 或 `--model <逗号分隔的 glob>`，不要擅自过滤。
4. 命令成功后，不要直接打开运行时对话记录、提示词、回复、源文件或数据库。
5. 输出简洁的“今日、近 7 个本地自然日、本月至今、本地全部可用历史”周期表，包含已报告的 token 分类及总量；表格中的 token 数统一除以 `100,000,000`，以“亿 token”为单位，并保留足够小数以避免把非零值显示成零；只有数据源提供费用时才展示费用。
6. 给出 2–4 条点评，每条必须有已报告的数值、占比或比率支撑。
7. 准确区分以下状态：
   - `no_data`：未发现受支持的本地记录。
   - 零用量：数据源已覆盖，但所选周期合计为零。
   - `partial`：部分数据源或记录无法完整分析；所有对比都要注明局限。
8. 不推断任务内容、浪费程度、生产力或用户意图，不补算或估算未报告的费用。

## English instructions

1. Resolve an available Python launcher in this order: `python3`, `python`, then Windows `py -3`.
2. Locate this skill directory and run the bundled script from any working directory:

   ```text
   <python-launcher> <skill-directory>/scripts/token_usage.py --format json
   ```

   Treat the command as local, read-only, and no-network. Use only its aggregate JSON stdout for analysis.
3. Add `--runtime <csv>` or `--model <csv-globs>` only when the user requests those filters. Add no unrequested filters.
4. After a successful run, never open runtime transcripts, prompts, responses, source files, or databases directly.
5. Present a compact period table for Today, Trailing 7 days, Month to date, and All locally available history. Express every token count in `亿 tokens` (100 million tokens), retaining enough decimals to avoid displaying a nonzero value as zero. Include the reported token categories and total; show provider-reported cost only when present.
6. Add two to four observations. Back every observation with a reported number, share, or ratio.
7. Distinguish these outcomes precisely:
   - `no_data`: no supported local records were found.
   - Zero usage: sources were covered, but the requested period totals zero.
   - `partial`: some sources or records could not be fully analyzed; qualify comparisons.
8. Never infer task content, waste, productivity, intent, or a missing cost. Never estimate unreported cost.
