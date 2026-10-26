"""评测工装配置：样本、字段、路径、价格、标签。

所有口径只在这里定义一次，runner / baseline / aggregate 共用。
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
EVAL_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVAL_DIR.parent.parent
PROJECT_DIR = BACKEND_DIR.parent
RESULTS_DIR = EVAL_DIR / "results"
RUNS_DIR = RESULTS_DIR / "runs"
FAULT_RUNS_DIR = RESULTS_DIR / "runs_faults"
BASELINE_RUNS_DIR = RESULTS_DIR / "runs_baseline"

# 应用产物目录。评测时改到 results/ 下，避免和线上 runs/ 混在一起。
APP_RUNS_DIR = RESULTS_DIR / "app_runs"

# EDGAR 缓存必须可写；默认 ~/.edgar 在本机不可写。
os.environ.setdefault(
    "EDGAR_LOCAL_DATA_DIR", str(PROJECT_DIR / ".edgar")
)
os.environ.setdefault("EDGAR_IDENTITY", "research-agent contact@example.com")

# ---------------------------------------------------------------------------
# 样本
# ---------------------------------------------------------------------------
# 30 家 = 美股 10 / A股 10 / 港股 10。
# 选择标准：
#   1) 大型非金融公司（银行/保险没有可比的“毛利”，会把 150 字段口径污染）；
#   2) 三地都使用标准利润表/资产负债表；
#   3) 近三年没有长期停牌或退市。
# 如需与旧结果对比，可把 OLD_COMPANIES 传给 run --sample old。
COMPANIES = [
    # US
    {"symbol": "AAPL", "market": "US", "name": "Apple Inc.", "currency": "USD"},
    {"symbol": "MSFT", "market": "US", "name": "Microsoft Corp.", "currency": "USD"},
    {"symbol": "GOOGL", "market": "US", "name": "Alphabet Inc.", "currency": "USD"},
    {"symbol": "AMZN", "market": "US", "name": "Amazon.com Inc.", "currency": "USD"},
    {"symbol": "META", "market": "US", "name": "Meta Platforms Inc.", "currency": "USD"},
    {"symbol": "NVDA", "market": "US", "name": "NVIDIA Corp.", "currency": "USD"},
    {"symbol": "TSLA", "market": "US", "name": "Tesla Inc.", "currency": "USD"},
    {"symbol": "WMT", "market": "US", "name": "Walmart Inc.", "currency": "USD"},
    {"symbol": "KO", "market": "US", "name": "Coca-Cola Co.", "currency": "USD"},
    {"symbol": "PG", "market": "US", "name": "Procter & Gamble Co.", "currency": "USD"},
    # CN
    {"symbol": "600519.SH", "market": "CN", "name": "贵州茅台", "currency": "CNY"},
    {"symbol": "000858.SZ", "market": "CN", "name": "五粮液", "currency": "CNY"},
    {"symbol": "000333.SZ", "market": "CN", "name": "美的集团", "currency": "CNY"},
    {"symbol": "002415.SZ", "market": "CN", "name": "海康威视", "currency": "CNY"},
    {"symbol": "600276.SH", "market": "CN", "name": "恒瑞医药", "currency": "CNY"},
    {"symbol": "000651.SZ", "market": "CN", "name": "格力电器", "currency": "CNY"},
    {"symbol": "002714.SZ", "market": "CN", "name": "牧原股份", "currency": "CNY"},
    {"symbol": "002594.SZ", "market": "CN", "name": "比亚迪", "currency": "CNY"},
    {"symbol": "600887.SH", "market": "CN", "name": "伊利股份", "currency": "CNY"},
    {"symbol": "600900.SH", "market": "CN", "name": "长江电力", "currency": "CNY"},
    # HK
    {"symbol": "00700.HK", "market": "HK", "name": "腾讯控股", "currency": "CNY"},
    {"symbol": "09988.HK", "market": "HK", "name": "阿里巴巴-W", "currency": "CNY"},
    {"symbol": "00941.HK", "market": "HK", "name": "中国移动", "currency": "CNY"},
    {"symbol": "01810.HK", "market": "HK", "name": "小米集团-W", "currency": "CNY"},
    {"symbol": "02020.HK", "market": "HK", "name": "安踏体育", "currency": "CNY"},
    {"symbol": "01093.HK", "market": "HK", "name": "石药集团", "currency": "CNY"},
    {"symbol": "09618.HK", "market": "HK", "name": "京东集团-SW", "currency": "CNY"},
    {"symbol": "01024.HK", "market": "HK", "name": "快手-W", "currency": "CNY"},
    {"symbol": "00981.HK", "market": "HK", "name": "中芯国际", "currency": "USD"},
    {"symbol": "09888.HK", "market": "HK", "name": "百度集团-SW", "currency": "CNY"},
]

# 旧测试使用的 30 家（用于复现/对照；含 5 家金融，字段口径需额外处理）
OLD_COMPANIES = [
    {"symbol": s, "market": "US", "name": s, "currency": "USD"}
    for s in ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA", "JPM", "V", "WMT"]
] + [
    {"symbol": s, "market": "CN", "name": s, "currency": "CNY"}
    for s in ["600519.SH", "000858.SZ", "601318.SH", "000333.SZ", "600036.SH",
              "002415.SZ", "600276.SH", "000651.SZ", "601166.SH", "002714.SZ"]
] + [
    {"symbol": s, "market": "HK", "name": s, "currency": "CNY"}
    for s in ["00700.HK", "09988.HK", "00388.HK", "00941.HK", "02318.HK",
              "01299.HK", "01810.HK", "02020.HK", "01093.HK", "09618.HK"]
]

PILOT_COMPANIES = [COMPANIES[0], COMPANIES[10], COMPANIES[20]]  # AAPL / 600519 / 00700

# ---------------------------------------------------------------------------
# 150 字段 = 30 家 × 5 个字段
# ---------------------------------------------------------------------------
# 选择理由：
#   - 均为报告 prompt 明确要求披露的指标，报告层可解析；
#   - 三地均可由标准报表得到；
#   - 不依赖当日股价，避免真值漂移。
# 定义（必须与真值脚本、评分脚本完全一致）：
#   revenue          = 营业收入（合并口径）
#   net_income       = 归属于母公司股东的净利润（美股 NetIncomeLoss，A股 归母净利润，港股 股东应占溢利）
#   gross_margin     = (revenue - 营业成本) / revenue
#   net_margin       = net_income / revenue
#   operating_margin = 营业利润 / revenue
#
# 不把 ROE 放进 150 字段：官方 ROE 存在加权平均/期末权益/年化等多种口径，
# 1% 容差下无法唯一判定；ROE 当前不进入 21 项（可选诊断字段）。
FIELD_PANEL = ["revenue", "net_income", "gross_margin", "net_margin", "operating_margin"]

FIELD_LABELS = {
    "revenue": [
        "营业总收入", "营业收入", "主营营业收入", "营业额", "营运收入", "总营业收入", "营收",
        "净销售额", "总营收", "销售收入", "营业总营收",
        "Total revenues", "Total revenue", "Net sales", "Revenue",
    ],
    "net_income": [
        "归属于母公司股东的净利润", "归属于母公司所有者的净利润", "归母净利润",
        "股东应占溢利", "本公司拥有人应占溢利", "净收入",
        "Net income attributable to", "Net income", "Net profit",
        "净利润",
    ],
    "gross_margin": ["毛利率", "Gross margin", "Gross Margin"],
    "net_margin": ["销售净利率", "净利率", "Net profit margin", "Net margin"],
    "operating_margin": ["营业利润率", "经营利润率", "Operating margin", "Operating Margin"],
    "roe": ["净资产收益率", "加权平均净资产收益率", "ROE", "Return on equity"],
}

SECTION_ALIASES = {
    "公司概览": ["公司概览", "公司概况", "公司简介"],
    "业务与行业": ["业务与行业", "业务概况", "行业分析", "业务分析"],
    "近期新闻与舆情": ["近期新闻与舆情", "新闻与舆情", "近期新闻", "新闻动态"],
    "财务分析": ["财务分析", "财务表现", "财务状况"],
    "风险提示": ["风险提示", "风险因素", "风险分析"],
}

# ---------------------------------------------------------------------------
# 成本：必须按运行当日供应商价目填写。默认值仅作为占位，aggregate 会检查。
# 单位：人民币 / 百万 token。
# ---------------------------------------------------------------------------
DEFAULT_PRICES = {
    "model": "deepseek-flash",
    "currency": "CNY",
    "input_per_1m": None,
    "output_per_1m": None,
    "cached_input_per_1m": None,
    "source": "请按 DeepSeek 账单/价目页填写，并在 prices.json 记录抓取日期",
    "as_of": "",
}

# 容差：1% 相对误差；比率再加 0.0005 绝对下限（0.05 个百分点）。
REL_TOL = 0.01
RATIO_ABS_FLOOR = 0.0005

# 端到端超时（秒）。超时记为失败/截尾，不静默丢弃。
RUN_TIMEOUT_S = 600

# 无效代码样本
INVALID_CODES = [
    {"code": "ZZZZZZ", "kind": "格式无效/不存在", "market": "US"},
    {"code": "999999.SH", "kind": "格式有效但不存在", "market": "CN"},
    {"code": "88888.HK", "kind": "格式有效但不存在", "market": "HK"},
]

# 故障注入 10 类。真实执行时每类 1 次。
FAULT_SCENARIOS = [
    {"id": "entity_timeout", "desc": "实体解析超时", "market": "US"},
    {"id": "entity_invalid", "desc": "实体解析返回异常数据", "market": "US"},
    {"id": "search_down", "desc": "搜索 API 不可用", "market": "US"},
    {"id": "search_rate_limit", "desc": "搜索 API 限流", "market": "US"},
    {"id": "profile_down", "desc": "公司概况 API 不可用", "market": "US"},
    {"id": "sec_down", "desc": "SEC EDGAR 不可用", "market": "US"},
    {"id": "akshare_down", "desc": "akshare 不可用", "market": "CN"},
    {"id": "quote_down", "desc": "行情 API 不可用", "market": "US"},
    {"id": "llm_timeout", "desc": "LLM 调用超时", "market": "US"},
    {"id": "llm_invalid", "desc": "LLM 返回无效 JSON", "market": "US"},
]

# 错误分类（21 项）。每个错误字段/失败任务必须落到唯一主类。
ERROR_TAXONOMY = [
    "tool_timeout",
    "tool_error",
    "entity_resolution",
    "field_mapping",
    "period_mismatch",
    "unit_currency",
    "missing_data",
    "llm_hallucination",
    "report_format",
    "numeric_calc",
]


def companies_for(sample: str = "core"):
    if sample == "old":
        return OLD_COMPANIES
    if sample == "pilot":
        return PILOT_COMPANIES
    return COMPANIES


def ensure_dirs() -> None:
    for p in [RESULTS_DIR, RUNS_DIR, FAULT_RUNS_DIR, BASELINE_RUNS_DIR, APP_RUNS_DIR]:
        p.mkdir(parents=True, exist_ok=True)
