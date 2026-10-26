# 评测工装使用说明

> 完整方法论见 `agentic_research/docs/测试方案_定量指标_v2.md`。
> 本文件只讲“怎么跑、产物在哪、每个数怎么来的”。

## 0. 前提

- 在 `agentic_research/backend` 下运行（需要 `app/` 与 `.env`）。
- Python 3.11（项目依赖装在 `Agent-2` conda 环境）。
- `.env` 里至少要有可用的 LLM key；搜索/行情 provider 按实际配置。
- 首次运行会把 SEC 缓存指到项目内 `.edgar/`；否则 `~/.edgar` 不可写会导致美股财务全失败。

```powershell
cd agentic_research/backend
$env:PYTHONIOENCODING='utf-8'
```

## 1. 一键全量

```powershell
python -m tests.eval.cli all --sample core --gt --force
```

依次执行：真值 → 30 家 E2E → 3 个无效代码 → 30 家基线 → 10 类故障 → 汇总。
预计 1–1.5 小时（不含人工评分）。

## 2. 分步执行

```powershell
# 真值（30 家 × 5 字段）
python -m tests.eval.cli gt --sample core --force

# 30 家端到端
python -m tests.eval.cli run --sample core --force

# 3 个无效代码
python -m tests.eval.cli invalid --force

# 单 LLM 基线（同 prompt、无工具）
python -m tests.eval.cli baseline --sample core --force

# 10 类故障注入
python -m tests.eval.cli faults --force

# 只跑某一类故障（调试）
python -m tests.eval.cli faults --force --only sec_down

# 汇总 21 项
python -m tests.eval.cli aggregate
```

试点 3 家：把 `--sample core` 换成 `--sample pilot`。

## 3. 产物

目录：`tests/eval/results/`

| 文件/目录 | 内容 |
|---|---|
| `ground_truth.csv` | 150 行真值：值、币种、期间、来源、tier、verified |
| `ground_truth.meta.json` | 真值覆盖度（各市场非空数/tier） |
| `runs.jsonl` | 30 家 E2E：状态、耗时、字段、工具、token、成本 |
| `runs/<run_id>/report.md` | 该次运行的原始报告（审计用） |
| `runs/<run_id>/result.json` | 去掉 metrics 的完整记录 |
| `runs/<run_id>/metrics.json` | 系统产出的 metrics 字典 |
| `baseline.jsonl` | 30 家基线报告与字段 |
| `runs_baseline/<run_id>/report.md` | 基线原始报告 |
| `faults.jsonl` | 10 类故障 + 契约检查明细 |
| `invalid.jsonl` | 3 个无效代码 + 契约检查明细 |
| `human_scores.csv` | 2 人 × 10 份 × 4 维评分（需人工填） |
| `prices.json` | 单价（运行日账单；未填则只报 token） |
| `metrics.json` | 21 项机读结果（含 CI、明细） |
| `指标回收表.md` | 21 行 Markdown 回收表 |

## 4. 口径速查

- **成功（指标 1）**：5 个核心 section 非空且无 fatal；`final_status` 不算成功。
- **完整（指标 3）**：5 个核心 section 非空（标题解析 + 实义内容 ≥60 字）。
- **150 字段**：30 家 × 5 字段 = `revenue / net_income / gross_margin / net_margin / operating_margin`；报告层解析；容差 1%。
- **真值**：US = SEC 10-K 同期 XBRL；CN = 新浪财务报表；HK = 东财港股报表（后两者 `source_tier=vendor`，最终需官方 PDF 复核）。
- **工具成功**：语义成功（非空/有效），不是“没抛异常”。
- **provider**：Tavily/yfinance/Finnhub 未启用时记 N/A，不记 0%。
- **延迟**：`perf_counter` 包住 `graph.ainvoke`；P50/P95 + bootstrap CI。
- **成本**：真实 token × `prices.json` 单价；单价缺失只报 token。
- **故障**：每类有契约；`final_status=failed` 不等于降级成功。
- **人工**：1–5 分，2 人独立盲评；未填时 N/A。

## 5. 常见问题

**Q：为什么 `指标 1` 不是 100%，但报告看起来都有 7 章？**
A：v2 要求 5 个核心 section 的 body 有实义内容；如果只有标题/“数据缺失”，不算非空。
另外 `final_status` 只做分布展示，不参与成功判定。

**Q：为什么 CN/HK 字段准确率可能很低？**
A：先看 `source_tier` 和 `period_end`。当前系统 CN 用同花顺摘要（可能是半年报 YTD）、HK 用东财利润表（只有利润表，没有权益），
而真值按同期官方/独立源算。若 raw 期间不同，归因 `period_mismatch`；若缺资产负债表，归因 `missing_data`。
这不是模型错，是数据层缺口，表内备注写清楚。

**Q：为什么 Tavily/yfinance/Finnhub 是 N/A？**
A：`.env` 当前是 `SEARCH_PROVIDER=serper`、`STOCK_QUOTE_PROVIDER=eastmoney`。
指标表按 provider 计，未启用就是 N/A；另报 serper/eastmoney 的成功率。

**Q：报告 `05_report.md` 为什么是转义字符串？**
A：`save_artifact` 对字符串做了 `json.dump`，这是生产缺陷；评测用内存态报告或先 `json.loads`。
建议单独开修复单：报告类产物应写原始 Markdown，而不是 JSON 字符串。

**Q：单价怎么填？**
A：运行日从 DeepSeek 控制台账单/价目页取 input/output/cached 单价，写入 `results/prices.json`，
并填 `as_of`。未填时汇总只报 token，不编造成本。
