"""离线解析器单测：不联网、不调 LLM，保证评分口径稳定。"""
from tests.eval.text import (
    core_sections_non_empty,
    extract_fields,
    is_substantive,
    relative_error,
)

REPORT = """# 测试公司（TEST）研究报告

## 1. 公司概览

测试公司是一家示例制造业公司，主营工业设备与相关服务，业务覆盖国内与海外市场。
公司通过直销与经销相结合的渠道体系服务客户，并在多个区域设有服务网点，
收入主要来自设备销售、备件与售后服务，客户结构以工业制造企业为主。

## 2. 业务与行业

公司主营工业设备与相关服务，行业处于成熟期，竞争格局稳定。公司在细分市场份额领先，
并通过海外渠道扩张与售后服务提升客户黏性；行业需求与宏观资本开支高度相关，
短期存在库存周期波动，但长期替换需求稳定。

## 3. 近期新闻与舆情

- 公司发布 2026 年半年度业绩，收入同比增长 6%，订单储备保持增长。
- 公司宣布回购计划，市场情绪偏正面，机构关注利润率修复节奏。
- 行业层面，原材料价格回落有利于下半年毛利率改善。

## 4. 财务分析

| 指标 | 金额（亿元） | 同比 |
|---|---:|---:|
| 营业总收入 | 1,234.56 | +6.0% |
| 营业成本 | 740.74 | +5.0% |
| 归母净利润 | 123.46 | +8.0% |
| 毛利率 | 40.00% | +0.5pct |
| 净利率 | 10.00% | +0.2pct |
| 营业利润率 | 12.50% | +0.1pct |

## 5. 风险提示

- 原材料价格波动风险，可能压制毛利率。
- 海外需求不及预期风险，影响出口收入。
- 汇率波动风险，影响海外业务结算。
- 行业竞争加剧风险，可能导致费用投放上升。
"""


def test_sections_pass():
    ok, secs = core_sections_non_empty(REPORT)
    assert ok
    assert all(v["substantive"] for v in secs.values())


def test_placeholder_only_section_fails():
    md = REPORT.replace(
        "测试公司是一家示例制造业公司，主营工业设备与相关服务，业务覆盖国内与海外市场。\n"
        "公司通过直销与经销相结合的渠道体系服务客户，并在多个区域设有服务网点，\n"
        "收入主要来自设备销售、备件与售后服务，客户结构以工业制造企业为主。",
        "数据缺失",
    )
    ok, _ = core_sections_non_empty(md)
    assert not ok


def test_fields_units_and_percent():
    f = extract_fields(REPORT)
    assert f["revenue"]["value"] == 1234.56e8
    assert f["net_income"]["value"] == 123.46e8
    assert abs(f["gross_margin"]["value"] - 0.40) < 1e-9
    assert abs(f["net_margin"]["value"] - 0.10) < 1e-9
    assert abs(f["operating_margin"]["value"] - 0.125) < 1e-9


def test_usd_billion_table_header():
    md = """## 4. 财务分析

| 指标 | 金额（亿美元） |
|---|---:|
| 营业收入 | 4,161.61 |
| 净利润 | 1,120.10 |
| 毛利率 | 46.91% |
| 净利率 | 26.92% |
| 营业利润率 | 31.97% |
"""
    f = extract_fields(md)
    assert abs(f["revenue"]["value"] - 4.16161e11) < 1
    assert abs(f["net_income"]["value"] - 1.12010e11) < 1


def test_relative_error():
    assert relative_error(105, 100) == 0.05
    assert relative_error(None, 100) is None


def test_multi_period_prefers_target_period():
    md = """## 4. 财务分析

### 2026年半年度（period_end = 20260630）

| 指标 | 数值 |
|---|---:|
| 营业收入 | 922.78 亿元 |
| 归母净利润 | 445.17 亿元 |

**补充：2025年度数据**

| 指标 | 数值 |
|---|---:|
| 营业总收入 | 1,720.54 亿元 |
| 归母净利润 | 823.20 亿元 |
"""
    f = extract_fields(md, period_end="20260630")
    assert abs(f["revenue"]["value"] - 922.78e8) < 1
    assert abs(f["net_income"]["value"] - 445.17e8) < 1


def test_is_substantive():
    assert not is_substantive("数据缺失")
    assert not is_substantive("| 指标 | 数值 |\n|---|---|\n| 毛利率 | 数据缺失 |")
    assert is_substantive("公司主营工业设备与相关服务，业务覆盖国内与海外市场，收入保持增长，订单储备稳定，"
                          "并通过渠道扩张与售后服务提升客户黏性，长期替换需求稳定。")
