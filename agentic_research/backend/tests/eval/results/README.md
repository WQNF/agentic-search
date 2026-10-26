# 评测结果目录说明

本目录由 `tests/eval` 生成。不要手工改 `runs.jsonl` / `baseline.jsonl` / `faults.jsonl` / `invalid.jsonl`；
需要重跑用 `--force`。

## 主要文件

- `ground_truth.csv`：150 行真值。字段：`symbol, market, field, value, currency, period_end,
  fiscal_year, source, source_url, source_tier, verified, notes`。
  - `source_tier=official_filing` 才能直接用于最终 1% 准确率；
  - `vendor` 只用于预跑，最终需官方 PDF 复核后把 `verified` 改 `True`。
- `runs.jsonl`：30 家 E2E 一行一条；同 symbol 重复时汇总取最后一条。
- `baseline.jsonl`：30 家单 LLM 基线。
- `faults.jsonl`：10 类故障 + `contract` 明细。
- `invalid.jsonl`：3 个无效代码 + `contract` 明细。
- `metrics.json`：21 项机读结果；`metrics["4"].rate` 等。
- `指标回收表.md`：21 行 Markdown，可直接贴回报告。
- `修复后复测结果.md`：修复后的全量结果（149/150）与前后对比。
- `首轮实测结果解读.md`：修复前的首轮结果，仅用于定位问题。
- `prices.json`：单价；未填则成本列为“待单价”。

## 审计

每次运行的原始报告在 `runs/<run_id>/report.md`，`result.json` 含状态/工具/token/字段。
字段评分明细在 `metrics.json` 的 `field_rows`（实际写在顶层 `field_rows`）。

## 人工评分

`human_scores.csv` 初始只有表头；按 `human_rubric.md` 填 10 份 × 2 人 × 4 维。
填完重跑 `python -m tests.eval.cli aggregate` 即可更新指标 17–20。
