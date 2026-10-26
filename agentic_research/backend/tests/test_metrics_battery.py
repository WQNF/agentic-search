#d:\Python_exercise\项目2-智慧投研Agent\③项目代码\agentic_research\backend\tests\test_metrics_battery.py
"""
[DEPRECATED / 口径有误] 本文件保留用于对照，不要再用它产出验收数字。

已知问题（详见 docs/测试方案_定量指标_v2.md 第 3 节）：
  - section 判定是关键词包含，不是标题结构；
  - 150 字段从未真正测量（只比较 5 只 × 25 键）；
  - None==None 判对；CN/HK 键名没有映射；
  - 工具成功率分母翻倍，SEC/akshare 调用没有日志；
  - 成本是估算，不是真实 token；
  - 故障用例把 final_status=failed 判成降级成功；
  - 缺少 17–21 项。

新工装：tests/eval/（python -m tests.eval.cli all --sample core --gt --force）
=============================================================================
项目2-智慧投研Agent —— 综合指标测试套件
=============================================================================
测试项：
  1.  30 家端到端成功数/率          (5 个 section 非空且无 fatal error / 30)
  2.  无效代码降级成功数/率          (3 个无效代码/停牌代码)
  3.  报告完整率                     (5 个 section 非空 / 30)
  4.  150 字段准确数/率             (与官方数据一致 / 有效字段, 容差 1%)
  5.  分市场字段准确率               (A股 / 港股 / 美股 分别)
  6.  单 LLM 基线字段准确率          (同 prompt、无工具)
  7.  工具成功率(总)                 (成功调用 / 总调用)
  8.  Tavily 成功率
  9.  SEC EDGAR 成功率
  10. akshare 成功率
  11. yfinance 成功率
  12. Finnhub 成功率
  13. P50 延迟                      (端到端)
  14. P95 延迟                      (端到端)
  15. 单份成本                      (token×单价 / 30)
  16. 故障降级成功数/率             (10 类故障注入)

运行方式：
    cd backend
    # 完整测试（需要真实 API）
    python -m pytest tests/test_metrics_battery.py -v -s

    # 仅运行故障注入测试（不需要外部 API）
    python -m pytest tests/test_metrics_battery.py -v -s -k "fault"

    # 仅运行端到端测试
    python -m pytest tests/test_metrics_battery.py -v -s -k "e2e"
=============================================================================
"""

import asyncio
import json
import logging
import math
import os
import re
import statistics
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── 路径设置 ──────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.graph.workflow import build_graph
from app.graph.state import ResearchState
from app.Schemas.entity import Entity
from app.tools.metrics import normalize_financial_data, calculate_metrics
from app.core.config import settings

logger = logging.getLogger(__name__)

# 模块级初始化：跨 test 共享的结果存储
pytest._e2e_results = []
pytest._baseline_accuracy = None
_pytest_total_param = 0  # 用于显示进度


# ── 可视化输出辅助 ──
def _print_header(text: str, width: int = 70):
    """打印醒目的标题分隔符"""
    print(f"\n{'━' * width}")
    print(f"  {text}")
    print(f"{'━' * width}")


def _print_step(icon: str, label: str, status: str, detail: str = ""):
    """打印单个步骤状态，icon 用 emoji 指示状态"""
    d = f"  → {detail}" if detail else ""
    print(f"  {icon} {label:<20s} │ {status:<14s}{d}")


def _print_done(query: str, duration_s: float, is_success: bool):
    """打印单只股票完成总结"""
    verdict = "✅ 成功" if is_success else "❌ 失败"
    print(f"\n  {'─' * 60}")
    print(f"  {verdict} │ {query} │ 耗时 {duration_s:.1f}s")
    print(f"  {'─' * 60}")


# ── 磁盘持久化（跨 session 保留 e2e 结果） ──
E2E_RESULTS_FILE = Path(__file__).parent / "_e2e_results.json"


def _load_e2e_results() -> List[Dict]:
    """从磁盘加载之前 session 保存的 e2e 结果"""
    if E2E_RESULTS_FILE.exists():
        with open(E2E_RESULTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_e2e_result(task_dict: Dict):
    """追加一条 e2e 结果到磁盘文件"""
    all_results = _load_e2e_results()
    all_results.append(task_dict)
    with open(E2E_RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

# ═══════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════

# LLM 价格 (RMB / 1M tokens) — DeepSeek V4-Flash 参考定价
PRICE_PER_1M_INPUT_TOKENS = 1.0   # ¥1 / 百万输入 token
PRICE_PER_1M_OUTPUT_TOKENS = 2.0  # ¥2 / 百万输出 token
# 粗略估计每次 LLM 调用约消耗 8000 input + 3000 output tokens
EST_INPUT_TOKENS_PER_CALL = 8000
EST_OUTPUT_TOKENS_PER_CALL = 3000

# 报告核心 5 个 section 的标题关键词
CORE_SECTIONS = [
    "公司概览",
    "业务与行业",
    "近期新闻与舆情",
    "财务分析",
    "风险提示",
]

# ── 30 只测试股票（美股 10 / A股 10 / 港股 10） ──
TEST_STOCKS_US = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",
    "TSLA", "NVDA", "JPM", "V", "WMT",
]
TEST_STOCKS_CN = [
    "600519.SH", "000858.SZ", "601318.SH", "000333.SZ", "600036.SH",
    "002415.SZ", "600276.SH", "000651.SZ", "601166.SH", "002714.SZ",
]
TEST_STOCKS_HK = [
    "00700.HK", "09988.HK", "00388.HK", "00941.HK", "02318.HK",
    "01299.HK", "01810.HK", "02020.HK", "01093.HK", "09618.HK",
]
ALL_TEST_STOCKS = TEST_STOCKS_US + TEST_STOCKS_CN + TEST_STOCKS_HK

# ── 3 只无效/停牌代码 ──
INVALID_CODES = ["ZZZZZZ", "999999.SH", "88888.HK"]

# ── 150 字段定义（报告 + 指标中需要验证的字段） ──
EXPECTED_METRIC_FIELDS = [
    # 盈利能力
    "gross_margin", "net_margin", "operating_margin", "ebitda_margin",
    "roe", "roa",
    # 成长能力
    "revenue_growth_yoy", "net_income_growth_yoy", "eps_growth_yoy",
    # 现金流
    "operating_cash_flow", "capital_expenditure", "free_cash_flow",
    # 偿债能力
    "debt_to_asset_ratio", "current_ratio", "quick_ratio",
    # 运营效率
    "receivables_turnover", "inventory_turnover", "asset_turnover",
    # 市场估值
    "market_cap", "pe", "pb", "ps", "ev_to_ebitda",
    # 股东回报
    "eps", "dividend_yield",
    # 原始字段
    "revenue", "net_income", "gross_profit", "operating_income",
    "ebitda", "total_assets", "total_liabilities", "total_equity",
    "current_assets", "current_liabilities", "inventory",
    "accounts_receivable", "cash_and_equivalents",
    "short_term_investments", "long_term_debt", "short_term_debt",
    "shares_outstanding", "price",
]
# 扩展 profile 字段 (约 18 个)
EXPECTED_PROFILE_FIELDS = [
    "name", "ticker", "exchange", "industry", "sector", "website",
    "description", "employees", "address", "city", "state", "country",
    "ceo", "ipo_date", "sic_code", "sic_description",
]
# 新闻字段 (3 * 5 = 15)
# 报告段落字段 (约 30 个关键数据点)
# 行情字段 (约 12 个)
EXPECTED_QUOTE_FIELDS = [
    "symbol", "price", "currency", "market_cap", "previous_close",
    "open", "day_high", "day_low", "volume", "change", "change_percent",
    "timestamp",
]

TOTAL_150_FIELDS = (
    EXPECTED_METRIC_FIELDS
    + EXPECTED_PROFILE_FIELDS
    + EXPECTED_QUOTE_FIELDS
    # 加上报告中的关键字段
    + ["report_has_公司概览", "report_has_业务与行业", "report_has_近期新闻与舆情",
       "report_has_财务分析", "report_has_风险提示", "report_has_投资结论",
       "report_has_附录"]
)
# 确保总字段数接近 150
assert len(TOTAL_150_FIELDS) <= 160, f"字段定义过多: {len(TOTAL_150_FIELDS)}"

# ═══════════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════════

@dataclass
class TaskResult:
    """单次任务结果"""
    query: str
    run_id: str
    duration_ms: int
    success: bool                                # 5 section 非空 & 无 fatal
    entity_status: str = "pending"
    financial_status: str = "pending"
    report_status: str = "pending"
    final_status: str = "pending"
    sections_found: List[str] = field(default_factory=list)
    sections_missing: List[str] = field(default_factory=list)
    metric_count: int = 0
    report_markdown: str = ""
    errors: List[Dict] = field(default_factory=list)
    fatal_error: bool = False
    # 工具调用统计
    tool_calls: Dict[str, int] = field(default_factory=dict)       # tool -> total
    tool_successes: Dict[str, int] = field(default_factory=dict)   # tool -> success
    # LLM token 估算
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0

    @property
    def core_sections_non_empty(self) -> bool:
        return len(self.sections_missing) == 0  # no missing sections

    @property
    def is_end_to_end_success(self) -> bool:
        return self.core_sections_non_empty and not self.fatal_error

    @property
    def estimated_cost_rmb(self) -> float:
        in_cost = (self.estimated_input_tokens / 1_000_000) * PRICE_PER_1M_INPUT_TOKENS
        out_cost = (self.estimated_output_tokens / 1_000_000) * PRICE_PER_1M_OUTPUT_TOKENS
        return in_cost + out_cost

@dataclass
class MetricsReport:
    """聚合指标报告"""
    # Test 1
    e2e_success_count: int = 0
    e2e_total: int = 0
    e2e_success_rate: float = 0.0

    # Test 2
    degradation_success_count: int = 0
    degradation_total: int = 0
    degradation_rate: float = 0.0

    # Test 3
    report_complete_count: int = 0
    report_complete_rate: float = 0.0

    # Test 4 & 5
    field_accuracy: float = 0.0
    field_accuracy_cn: float = 0.0
    field_accuracy_hk: float = 0.0
    field_accuracy_us: float = 0.0

    # Test 7-12
    tool_success_rates: Dict[str, Tuple[int, int, float]] = field(default_factory=dict)

    # Test 13-14
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    latencies: List[int] = field(default_factory=list)

    # Test 15
    avg_cost_per_report: float = 0.0

    # Test 16
    fault_degradation_count: int = 0
    fault_degradation_total: int = 0
    fault_degradation_rate: float = 0.0

    # 明细
    task_results: List[TaskResult] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "=== 1. 30家端到端测试 ===": {
                "成功数": self.e2e_success_count,
                "总数": self.e2e_total,
                "成功率": f"{self.e2e_success_rate:.2%}",
            },
            "=== 2. 无效代码降级测试 ===": {
                "降级成功数": self.degradation_success_count,
                "总数": self.degradation_total,
                "降级成功率": f"{self.degradation_rate:.2%}",
            },
            "=== 3. 报告完整率 ===": {
                "5 section 非空数": self.report_complete_count,
                "完整率": f"{self.report_complete_rate:.2%}",
            },
            "=== 4. 150字段总体准确率 ===": {
                "准确率": f"{self.field_accuracy:.2%}",
            },
            "=== 5. 分市场字段准确率 ===": {
                "A股": f"{self.field_accuracy_cn:.2%}",
                "港股": f"{self.field_accuracy_hk:.2%}",
                "美股": f"{self.field_accuracy_us:.2%}",
            },
            "=== 6. 单LLM基线字段准确率 ===": "见 baseline_accuracy 字段",
            "=== 7-12. 工具成功率 ===": {
                tool: f"{rate:.2%} ({success}/{total})"
                for tool, (success, total, rate) in self.tool_success_rates.items()
            },
            "=== 13-14. 延迟 ===": {
                "P50(ms)": self.p50_latency_ms,
                "P95(ms)": self.p95_latency_ms,
                "样本数": len(self.latencies),
            },
            "=== 15. 单份成本 ===": {
                "平均成本(¥)": f"{self.avg_cost_per_report:.6f}",
            },
            "=== 16. 故障降级测试 ===": {
                "降级成功数": self.fault_degradation_count,
                "总数": self.fault_degradation_total,
                "降级成功率": f"{self.fault_degradation_rate:.2%}",
            },
        }

# ═══════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════

def _initial_state(run_id: str, query: str) -> ResearchState:
    return {
        "query": query,
        "run_id": run_id,
        "user_id": "test_user",
        "entity": None,
        "entity_status": "pending",
        "profile": None,
        "news": [],
        "profile_status": "pending",
        "financial_raw": None,
        "metrics": None,
        "financial_status": "pending",
        "report_markdown": "",
        "report_status": "pending",
        "review": None,
        "final_status": "pending",
        "steps": [],
        "errors": [],
    }

def check_core_sections(report_md: str) -> Tuple[List[str], List[str]]:
    """返回 (found_sections, missing_sections)"""
    found = []
    missing = []
    for section in CORE_SECTIONS:
        if section in report_md and len(report_md.split(section, 1)[-1].strip()) > 10:
            found.append(section)
        else:
            missing.append(section)
    return found, missing

def has_fatal_error(result: Dict, report_md: str) -> bool:
    """判断是否存在致命错误"""
    # 如果实体解析失败且报告完全为空，认为是 fatal
    if result.get("entity_status") == "failed" and len(report_md.strip()) < 50:
        return True
    # 如果 final_status 是 failed
    if result.get("final_status") == "failed":
        return True
    # 如果有 error 级别的错误
    for err in result.get("errors", []):
        if isinstance(err, dict) and err.get("node") == "entity_resolver":
            return True
    return False

def estimate_llm_tokens(
    task_result: Dict, num_llm_calls: int = 4
) -> Tuple[int, int]:
    """估算 input / output token 数"""
    # 工作流有 4 个 LLM 调用：
    #   entity_resolver (小), info_collector (中), financial_analyzer (小),
    #   report_writer (大), quality_reviewer (小)
    report_len = len(task_result.get("report_markdown", ""))
    # 粗略估计：report 中的每个中文字符 ≈ 1.5 tokens
    est_output = max(report_len * 1.5, 500)
    est_input = num_llm_calls * EST_INPUT_TOKENS_PER_CALL
    return int(est_input), int(est_output)

def percentile(data: List[float], p: float) -> float:
    """计算百分位数"""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * p / 100.0
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_data[int(k)]
    d0 = sorted_data[f] * (c - k)
    d1 = sorted_data[c] * (k - f)
    return d0 + d1

def parse_log_tool_calls(run_id: str) -> Dict[str, Tuple[int, int]]:
    """从 logs.jsonl 中提取工具调用统计 → {tool: (success, total)}"""
    log_path = Path("runs") / run_id / "logs.jsonl"
    if not log_path.exists():
        return {}
    tool_stats = defaultdict(lambda: [0, 0])  # [success, total]
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            extra = entry.get("extra", {})
            tool = extra.get("tool", "")
            event = entry.get("event", "")
            if tool and event:
                tool_stats[tool][1] += 1
                if event in ("tool_success", "success"):
                    tool_stats[tool][0] += 1
    return {k: (v[0], v[1]) for k, v in tool_stats.items()}

def normalize_ground_truth_value(val: Any) -> Optional[float]:
    """将 ground truth 值标准化为 float"""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None

def check_field_accuracy(
    actual: Optional[float], expected: Optional[float], tolerance: float = 0.01
) -> bool:
    """检查字段准确度，容差 1%"""
    if actual is None and expected is None:
        return True  # 双空 → 一致
    if actual is None or expected is None:
        return False  # 一空一非空 → 不一致
    if expected == 0:
        return abs(actual) < 1e-9
    return abs((actual - expected) / expected) <= tolerance

# ═══════════════════════════════════════════════════════════
# Test Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def graph():
    """构建无 checkpointer 的图（测试用内存执行）"""
    return build_graph(checkpointer=None)


GROUND_TRUTH_PATH = Path(__file__).parent / "ground_truth.json"


@pytest.fixture
def ground_truth() -> Dict:
    """加载 ground truth 数据（模块级，所有测试类共享）"""
    if GROUND_TRUTH_PATH.exists():
        with open(GROUND_TRUTH_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

# ═══════════════════════════════════════════════════════════
# Test 1 & 3: 30 家端到端测试 + 报告完整率
# ═══════════════════════════════════════════════════════════

class TestEndToEnd:
    """端到端测试：30 只真实股票"""

    @pytest.mark.slow
    @pytest.mark.e2e
    @pytest.mark.parametrize("query", ALL_TEST_STOCKS)
    async def test_single_stock_e2e(self, graph, query):
        """逐只股票端到端测试（pytest 参数化）"""
        global _pytest_total_param
        _pytest_total_param += 1
        idx = _pytest_total_param
        total = len(ALL_TEST_STOCKS)

        _print_header(f"[{idx}/{total}] 🏷 {query} — 开始端到端测试")
        run_id = str(uuid.uuid4())
        start = time.time()

        # ── 执行工作流 ──
        print(f"  ⏳ 工作流执行中... (run_id={run_id[:8]}...)")
        workflow_start = time.time()
        result = await graph.ainvoke(_initial_state(run_id, query))
        workflow_duration = time.time() - workflow_start
        duration_ms = int((time.time() - start) * 1000)

        # ── 逐步输出每个节点的状态 ──
        entity_status = result.get("entity_status", "?")
        entity_info = result.get("entity")
        entity_icon = "✅" if entity_status == "success" else ("⚠️" if entity_status == "partial" else "❌")
        entity_detail = f"{entity_info.get('name','?')} ({entity_info.get('market','?')})" if entity_info and isinstance(entity_info, dict) else ""
        _print_step(entity_icon, "① 实体解析", entity_status, entity_detail)

        profile_status = result.get("profile_status", "?")
        p_icon = "✅" if profile_status == "success" else ("⚠️" if profile_status == "partial" else "❌")
        profile = result.get("profile")
        p_detail = f"{len(profile)}字段" if profile and isinstance(profile, dict) else ""
        _print_step(p_icon, "② 概况+新闻", profile_status, p_detail)

        fin_status = result.get("financial_status", "?")
        fin_icon = "✅" if fin_status == "success" else ("⚠️" if fin_status == "partial" else "❌")
        metrics = result.get("metrics") or {}
        _print_step(fin_icon, "③ 财务分析", fin_status, f"{len(metrics)}指标")

        report_status = result.get("report_status", "?")
        r_icon = "✅" if report_status == "success" else ("⚠️" if report_status == "partial" else "❌")
        report_md = result.get("report_markdown", "")
        _print_step(r_icon, "④ 报告撰写", report_status, f"{len(report_md)}字符")

        final_status = result.get("final_status", "?")
        f_icon = "✅" if final_status == "success" else ("⚠️" if final_status in ("partial_success", "partial") else "❌")
        _print_step(f_icon, "⑤ 质量审核", final_status)

        # ── Section 检查 ──
        found, missing = check_core_sections(report_md)
        fatal = has_fatal_error(result, report_md)
        is_success = len(missing) == 0 and not fatal

        print(f"\n  📋 Section 检查: 找到 {len(found)}/5 | 缺失 {len(missing)}")
        for sec in CORE_SECTIONS:
            mark = "✅" if sec in found else "  ❌"
            print(f"    {mark} {sec}")

        if result.get("errors"):
            print(f"  ⚠️ 错误 ({len(result['errors'])}):")
            for err in result["errors"][:3]:
                print(f"    · {str(err)[:100]}")

        # ── 输出最终结果 ──
        _print_done(query, duration_ms / 1000, is_success)

        # 记录指标
        task = TaskResult(
            query=query, run_id=run_id, duration_ms=duration_ms,
            success=is_success, entity_status=entity_status,
            financial_status=fin_status,
            report_status=report_status,
            final_status=final_status,
            sections_found=found, sections_missing=missing,
            metric_count=len(metrics),
            report_markdown=report_md,
            errors=result.get("errors", []),
            fatal_error=fatal,
        )

        pytest._e2e_results.append(task)
        _save_e2e_result({
            "query": query, "run_id": run_id, "duration_ms": duration_ms,
            "success": is_success, "entity_status": entity_status,
            "financial_status": fin_status, "report_status": report_status,
            "final_status": final_status, "sections_found": found,
            "sections_missing": missing, "metric_count": len(metrics),
            "fatal_error": fatal,
            "estimated_cost_rmb": task.estimated_cost_rmb,
        })
        print(f"  💾 结果已写入 {E2E_RESULTS_FILE.name}")

        assert is_success, (
            f"{query}: sections_missing={missing}, fatal={fatal}, "
            f"entity_status={task.entity_status}, report_len={len(report_md)}"
        )

    async def test_aggregate_e2e_results(self):
        """聚合 30 家端到端结果"""
        results: List[TaskResult] = getattr(pytest, "_e2e_results", [])
        if not results:
            pytest.skip("没有端到端测试结果")

        success = [r for r in results if r.is_end_to_end_success]
        complete = [r for r in results if r.core_sections_non_empty]

        print("\n" + "=" * 60)
        print("📊 测试 1: 30 家端到端成功数/率")
        print(f"   成功数: {len(success)} / {len(results)}")
        print(f"   成功率: {len(success) / max(len(results), 1):.2%}")

        print("\n📊 测试 3: 报告完整率 (5 section 非空)")
        print(f"   完整数: {len(complete)} / {len(results)}")
        print(f"   完整率: {len(complete) / max(len(results), 1):.2%}")

        # 详细的 section 缺失分析
        missing_stats = defaultdict(int)
        for r in results:
            for s in r.sections_missing:
                missing_stats[s] += 1
        print("\n   Section 缺失分布:")
        for sec, count in sorted(missing_stats.items(), key=lambda x: -x[1]):
            print(f"     {sec}: {count} / {len(results)}")

        assert len(success) >= 20, f"端到端成功率过低: {len(success)}/{len(results)}"

# ═══════════════════════════════════════════════════════════
# Test 2: 无效代码降级测试
# ═══════════════════════════════════════════════════════════

class TestInvalidCodeDegradation:
    """无效代码降级测试"""

    @pytest.mark.parametrize("invalid_code", INVALID_CODES)
    async def test_invalid_code_degradation(self, graph, invalid_code):
        """输入无效代码 → 系统应优雅降级，不应崩溃"""
        _print_header(f"🧪 无效代码降级测试: {invalid_code}")
        run_id = str(uuid.uuid4())
        start = time.time()

        print(f"  ⏳ 执行中...")
        try:
            result = await graph.ainvoke(_initial_state(run_id, invalid_code))
        except Exception as e:
            print(f"  ❌ 崩溃: {type(e).__name__}: {e}")
            pytest.fail(f"输入 {invalid_code} 导致系统崩溃: {e}")

        duration_ms = int((time.time() - start) * 1000)
        report_md = result.get("report_markdown", "")
        final_status = result.get("final_status", "?")
        entity_status = result.get("entity_status", "?")

        # 逐步输出
        e_icon = "✅" if entity_status == "failed" else "⚠️"
        _print_step(e_icon, "实体解析", entity_status)
        _print_step("📝" if len(report_md) > 20 else "❌", "降级报告", "已生成" if len(report_md.strip()) > 20 else "为空", f"{len(report_md)}字符")
        _print_step("🏁", "最终状态", final_status)

        entity_ok = entity_status in ("failed", "partial")
        report_ok = len(report_md.strip()) > 20
        final_ok = final_status in ("failed", "partial_success")
        degradation_ok = entity_ok and report_ok and final_ok

        # 存入磁盘供汇总
        deg_path = Path(__file__).parent / "_degradation_results.json"
        deg_results = []
        if deg_path.exists():
            deg_results = json.loads(deg_path.read_text(encoding="utf-8"))
        deg_results.append({
            "code": invalid_code, "degradation_ok": degradation_ok,
            "entity_status": result.get("entity_status"),
            "final_status": result.get("final_status"),
        })
        deg_path.write_text(json.dumps(deg_results, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"\n  {'─' * 50}")
        print(f"  {'✅ 降级成功' if degradation_ok else '❌ 降级失败'} │ {invalid_code} │ {duration_ms/1000:.1f}s")
        print(f"  {'─' * 50}")

        assert degradation_ok, (
            f"降级失败: entity_status={entity_status}, "
            f"report_len={len(report_md)}, final_status={final_status}"
        )

# ═══════════════════════════════════════════════════════════
# Test 4 & 5: 字段准确率 (需 ground truth 数据)
# ═══════════════════════════════════════════════════════════

class TestFieldAccuracy:
    """
    字段准确率测试

    注意：完整测试需要 ground truth 数据文件 (tests/ground_truth.json)。
    此测试框架检测 ground truth 文件是否存在，若不存在则跳过数值验证，
    但仍会验证字段存在性（非空率）。
    """

    def _build_all_fields(self, result: Dict) -> Dict:
        """将 result 中的 metrics + profile + financial_raw合并为一个完整字段字典"""
        all_fields = {}
        metrics = result.get("metrics") or {}
        all_fields.update(metrics)
        profile = result.get("profile") or {}
        if isinstance(profile, dict):
            for k, v in profile.items():
                all_fields.setdefault(k, v)
        fr = result.get("financial_raw")
        if isinstance(fr, dict):
            if "standardized" in fr:
                for k, v in (fr["standardized"] or {}).items():
                    all_fields.setdefault(k, v)
            for k, v in fr.items():
                if isinstance(v, (int, float)) and k not in all_fields:
                    all_fields[k] = v
        return all_fields

    def _compute_field_accuracy(
        self, all_fields: Dict, ground: Dict, tolerance: float = 0.01
    ) -> Tuple[int, int, List[str]]:
        correct = 0
        total_compared = 0
        incorrect_fields = []

        for field, expected_val in ground.items():
            actual_val = all_fields.get(field)
            expected = normalize_ground_truth_value(expected_val)
            if expected is None:
                continue
            total_compared += 1
            actual = normalize_ground_truth_value(actual_val)
            if check_field_accuracy(actual, expected, tolerance):
                correct += 1
            else:
                incorrect_fields.append(
                    f"{field}: actual={actual}, expected={expected}"
                )

        return correct, total_compared, incorrect_fields

    async def test_field_accuracy_aggregate(self, graph, ground_truth):
        """
        对 ground truth 中定义的股票逐一测试，汇总字段准确率。
        仅运行 ground truth 中存在的股票。
        """
        stocks = list(ground_truth.keys())
        if not stocks:
            pytest.skip("ground_truth.json 为空或不存在，跳过字段准确率测试")

        all_correct = 0
        all_compared = 0
        market_correct = {"US": 0, "CN": 0, "HK": 0}
        market_compared = {"US": 0, "CN": 0, "HK": 0}
        all_incorrect = []

        for query in stocks[:5]:  # 限制测试数量以提高速度
            run_id = str(uuid.uuid4())
            result = await graph.ainvoke(_initial_state(run_id, query))
            all_fields = self._build_all_fields(result)
            gt = ground_truth.get(query, {})

            if query.endswith(".SH") or query.endswith(".SZ"):
                market = "CN"
            elif query.endswith(".HK"):
                market = "HK"
            else:
                market = "US"

            correct, compared, incorrect = self._compute_field_accuracy(
                all_fields, gt
            )
            print(f"  📊 all_fields 键数: {len(all_fields)}, gt 键数: {len(gt)}")
            all_correct += correct
            all_compared += compared
            market_correct[market] += correct
            market_compared[market] += compared
            all_incorrect.extend(incorrect)

            print(f"\n   [{query}] {correct}/{compared} 字段准确")

        overall = all_correct / max(all_compared, 1)
        cn_acc = market_correct["CN"] / max(market_compared["CN"], 1)
        hk_acc = market_correct["HK"] / max(market_compared["HK"], 1)
        us_acc = market_correct["US"] / max(market_compared["US"], 1)

        print("\n" + "=" * 60)
        print("📊 测试 4: 150 字段总体准确率")
        print(f"   正确: {all_correct}/{all_compared} = {overall:.2%}")
        print("\n📊 测试 5: 分市场字段准确率")
        print(f"   A股: {market_correct['CN']}/{market_compared['CN']} = {cn_acc:.2%}")
        print(f"   港股: {market_correct['HK']}/{market_compared['HK']} = {hk_acc:.2%}")
        print(f"   美股: {market_correct['US']}/{market_compared['US']} = {us_acc:.2%}")

        if all_incorrect:
            print(f"\n   ❌ 不准确字段 (前 10):")
            for f in all_incorrect[:10]:
                print(f"      {f}")

        # 存入磁盘供汇总报告读取
        acc_path = Path(__file__).parent / "_field_accuracy.json"
        acc_path.write_text(json.dumps({
            "overall": overall, "total_correct": all_correct,
            "total_compared": all_compared,
            "cn": cn_acc, "hk": hk_acc, "us": us_acc,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n   字段准确率已保存: {acc_path}")

        assert overall >= 0.30, f"总体字段准确率 {overall:.2%} 低于 30%"

# ═══════════════════════════════════════════════════════════
# Test 6: 单 LLM 基线字段准确率 (无工具)
# ═══════════════════════════════════════════════════════════

class TestLLMBaseline:
    """
    单 LLM 基线测试：使用与 report_writer 相同的 prompt，
    但不提供工具数据，仅靠模型自身知识生成报告，然后比较字段准确率。
    """

    async def test_baseline_without_tools(self, ground_truth):
        """直接调用 LLM 生成报告（不经过工作流），比较准确率"""
        from app.core.llm import get_llm
        from app.prompts.report_prompt import REPORT_SYSTEM_PROMPT, REPORT_HUMAN_TEMPLATE
        from langchain_core.prompts import ChatPromptTemplate

        gt = ground_truth if ground_truth else {}
        stocks = list(gt.keys())
        if not stocks:
            pytest.skip("ground_truth.json 不存在，跳过 LLM 基线测试")

        llm = get_llm(temperature=0.3, max_tokens=16000)
        prompt = ChatPromptTemplate.from_messages([
            ("system", REPORT_SYSTEM_PROMPT),
            ("human", REPORT_HUMAN_TEMPLATE),
        ])
        chain = prompt | llm

        all_correct = 0
        all_compared = 0

        for query in stocks[:3]:  # 限制 3 只
            # 构造"空"数据上下文
            entity_info = json.dumps({"company_name": query, "symbol": query}, ensure_ascii=False)
            empty_data = json.dumps({}, ensure_ascii=False)

            llm_start = time.time()
            try:
                report = await chain.ainvoke({
                    "entity_info": entity_info,
                    "profile": empty_data,
                    "news": "[]",
                    "financial_raw_summary": empty_data,
                    "metrics": empty_data,
                })
            except Exception:
                continue

            report_content = report.content if hasattr(report, 'content') else str(report)
            print(f"\n   [{query}] LLM 基线报告长度: {len(report_content)}")

            # 基线比较：检查 LLM 凭记忆输出的关键指标是否接近 ground truth
            stock_gt = gt.get(query, {})
            for field, expected_val in stock_gt.items():
                expected = normalize_ground_truth_value(expected_val)
                if expected is None:
                    continue
                # 在报告中搜索数值（简单启发式）
                pattern = rf'{re.escape(field)}.*?([\d,.]+)'
                match = re.search(pattern, report_content, re.IGNORECASE)
                if match:
                    try:
                        actual = float(match.group(1).replace(",", ""))
                        all_compared += 1
                        if check_field_accuracy(actual, expected):
                            all_correct += 1
                    except ValueError:
                        pass

        baseline_acc = all_correct / max(all_compared, 1)
        print("\n" + "=" * 60)
        print("📊 测试 6: 单 LLM 基线字段准确率")
        print(f"   正确: {all_correct}/{all_compared} = {baseline_acc:.2%}")

        # 存入磁盘供汇总报告读取
        base_path = Path(__file__).parent / "_baseline.json"
        base_path.write_text(json.dumps({
            "baseline_accuracy": baseline_acc,
            "correct": all_correct, "compared": all_compared,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   LLM基线已保存: {base_path}")

        pytest._baseline_accuracy = baseline_acc

# ═══════════════════════════════════════════════════════════
# Test 7-12: 工具成功率
# ═══════════════════════════════════════════════════════════

class TestToolSuccessRates:
    """
    工具成功率测试。
    需要先运行端到端测试，从 logs.jsonl 中统计工具调用。
    """

    async def test_tool_success_rates(self):
        """汇总所有 logs 中的工具调用统计"""
        runs_dir = Path("runs")
        if not runs_dir.exists():
            pytest.skip("runs 目录不存在，跳过工具成功率测试")

        global_stats = defaultdict(lambda: [0, 0])  # [success, total]

        for run_dir in runs_dir.iterdir():
            if not run_dir.is_dir():
                continue
            stats = parse_log_tool_calls(run_dir.name)
            for tool, (success, total) in stats.items():
                global_stats[tool][0] += success
                global_stats[tool][1] += total

        if not global_stats:
            pytest.skip("没有找到工具调用日志")

        total_success = sum(v[0] for v in global_stats.values())
        total_calls = sum(v[1] for v in global_stats.values())

        print("\n" + "=" * 60)
        print("📊 测试 7: 工具成功率(总)")
        print(f"   总成功: {total_success} / {total_calls} = "
              f"{total_success / max(total_calls, 1):.2%}")

        # 工具名映射
        tool_display = [
            ("search_web", "8. Tavily 成功率"),
            ("fetch_sec_filings", "9. SEC EDGAR 成功率"),
            ("get_cn_financial_abstract", "10. akshare 成功率"),
            ("get_hk_financial_abstract", "10. akshare 成功率"),
            ("get_cn_quote", "10. akshare 成功率"),
            ("get_hk_quote", "10. akshare 成功率"),
            ("get_cn_company_profile", "10. akshare 成功率"),
            ("get_hk_company_profile", "10. akshare 成功率"),
            ("get_stock_quote", "11. yfinance / Finnhub / akshare 成功率"),
            ("get_company_profile", "工具综合"),
        ]

        # 汇总 akshare 相关
        akshare_success = 0
        akshare_total = 0
        akshare_tools = [
            "get_cn_financial_abstract", "get_hk_financial_abstract",
            "get_cn_quote", "get_hk_quote",
            "get_cn_company_profile", "get_hk_company_profile",
        ]

        for tool, (s, t) in sorted(global_stats.items()):
            rate = s / max(t, 1)
            print(f"   {tool}: {s}/{t} = {rate:.2%}")

            if tool in akshare_tools:
                akshare_success += s
                akshare_total += t

        # 分工具输出
        for tool_name, label in tool_display:
            if tool_name in global_stats:
                s, t = global_stats[tool_name]
                print(f"   [{label}] -> {s}/{t} = {s / max(t, 1):.2%}")

        if akshare_total > 0:
            print(f"\n📊 测试 10: akshare 综合成功率")
            print(f"   {akshare_success}/{akshare_total} = "
                  f"{akshare_success / max(akshare_total, 1):.2%}")

        # 检查 quote tool 对应的 provider
        print(f"\n📊 测试 11: yfinance 成功率 (provider={settings.stock_quote_provider})")
        print(f"📊 测试 12: Finnhub 成功率 (provider={settings.stock_quote_provider})")
        print("   (注: 当前 stock_quote 的 provider 由 config 决定，"
              "需根据实际 provider 判断)")

        assert total_success / max(total_calls, 1) >= 0.5, "工具总成功率低于 50%"

# ═══════════════════════════════════════════════════════════
# Test 13-15: 延迟 & 成本
# ═══════════════════════════════════════════════════════════

class TestLatencyAndCost:
    """延迟与成本分析"""

    async def test_latency_and_cost(self):
        """基于端到端测试结果计算 P50/P95 延迟和单份成本"""
        results: List[TaskResult] = getattr(pytest, "_e2e_results", [])
        if not results:
            pytest.skip("没有端到端测试结果")

        latencies = [r.duration_ms for r in results if r.duration_ms > 0]

        if latencies:
            p50 = percentile(latencies, 50)
            p95 = percentile(latencies, 95)

            print("\n" + "=" * 60)
            print("📊 测试 13-14: 端到端延迟")
            print(f"   样本数: {len(latencies)}")
            print(f"   P50: {p50:.0f} ms ({p50 / 1000:.1f} s)")
            print(f"   P95: {p95:.0f} ms ({p95 / 1000:.1f} s)")
            print(f"   Min: {min(latencies):.0f} ms")
            print(f"   Max: {max(latencies):.0f} ms")
            print(f"   Mean: {statistics.mean(latencies):.0f} ms")

        # 成本计算
        total_input = sum(r.estimated_input_tokens for r in results)
        total_output = sum(r.estimated_output_tokens for r in results)
        in_cost = (total_input / 1_000_000) * PRICE_PER_1M_INPUT_TOKENS
        out_cost = (total_output / 1_000_000) * PRICE_PER_1M_OUTPUT_TOKENS
        total_cost = in_cost + out_cost
        avg_cost = total_cost / max(len(results), 1)

        print(f"\n📊 测试 15: 单份成本")
        print(f"   总输入 tokens (est): {total_input:,}")
        print(f"   总输出 tokens (est): {total_output:,}")
        print(f"   总成本: ¥{total_cost:.4f}")
        print(f"   平均每份: ¥{avg_cost:.4f}")

        assert avg_cost < 10.0, f"单份成本过高: ¥{avg_cost:.4f}"

# ═══════════════════════════════════════════════════════════
# Test 16: 10 类故障注入 & 降级测试
# ═══════════════════════════════════════════════════════════

class TestFaultInjection:
    """
    10 类故障注入测试，验证系统在各种异常下的降级能力。
    每类故障注入后，系统应优雅降级而非崩溃。
    """

    # ── 故障场景定义 ──
    FAULT_SCENARIOS = [
        ("entity_timeout", "实体解析超时"),
        ("entity_invalid_response", "实体解析返回异常数据"),
        ("search_api_down", "搜索 API (Tavily) 不可用"),
        ("search_rate_limit", "搜索 API 限流"),
        ("profile_api_down", "公司概况 API 不可用"),
        ("sec_edgar_down", "SEC EDGAR 不可用"),
        ("akshare_down", "akshare 不可用"),
        ("yfinance_down", "yfinance 不可用"),
        ("llm_timeout", "LLM 调用超时"),
        ("llm_invalid_response", "LLM 返回无效 JSON"),
    ]

    @pytest.mark.fault
    @pytest.mark.parametrize("fault_name,description", FAULT_SCENARIOS)
    async def test_fault_degradation(
        self, graph, monkeypatch, fault_name, description
    ):
        """各类故障注入后系统应优雅降级"""
        run_id = str(uuid.uuid4())

        # ── 根据故障类型注入 mock ──
        self._inject_fault(monkeypatch, fault_name)

        try:
            result = await graph.ainvoke(_initial_state(run_id, "AAPL"))
        except Exception as e:
            # 对于致命异常（如未捕获的崩溃），判定为降级失败
            print(f"\n   [{fault_name}] ❌ 崩溃: {type(e).__name__}: {e}")
            pytest.fail(f"故障注入 [{fault_name}] 导致系统崩溃: {e}")

        report_md = result.get("report_markdown", "")
        final_status = result.get("final_status", "?")

        # 降级成功的判定：
        #   - 没有崩溃（已在 try/except 中验证）
        #   - 至少生成了降级报告（非空）
        #   - final_status 不应是 pending（说明流程至少跑完了）
        has_report = len(report_md.strip()) > 50
        flow_complete = final_status != "pending"

        degradation_ok = has_report and flow_complete

        # 存入磁盘供汇总
        f_path = Path(__file__).parent / "_fault_results.json"
        fault_list = []
        if f_path.exists():
            fault_list = json.loads(f_path.read_text(encoding="utf-8"))
        fault_list.append({
            "fault": fault_name, "description": description,
            "degradation_ok": degradation_ok,
            "report_len": len(report_md), "final_status": final_status,
        })
        f_path.write_text(json.dumps(fault_list, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"\n   [{fault_name}] {description}: "
              f"report_len={len(report_md)}, final={final_status}, "
              f"degradation={'✅' if degradation_ok else '❌'}")

        assert degradation_ok, (
            f"故障 [{fault_name}] 降级失败: "
            f"report_len={len(report_md)}, final_status={final_status}"
        )

    def _inject_fault(self, monkeypatch, fault_name: str):
        """注入各类故障"""

        # 1. 实体解析超时 / 异常响应
        if fault_name == "entity_timeout":
            monkeypatch.setattr(
                "app.graph.nodes.entity_resolver.resolve",
                MagicMock(side_effect=TimeoutError("Entity resolution timed out")),
            )
        elif fault_name == "entity_invalid_response":
            monkeypatch.setattr(
                "app.graph.nodes.entity_resolver.resolve",
                MagicMock(side_effect=ValueError("Invalid entity response")),
            )

        # 2. 搜索 API 故障
        elif fault_name == "search_api_down":
            monkeypatch.setattr(
                "app.graph.nodes.search_web",
                MagicMock(side_effect=Exception("Tavily API connection refused")),
            )
        elif fault_name == "search_rate_limit":
            # 模拟限流后第一次失败、第二次成功
            call_count = [0]

            def rate_limited_search(query, max_results=5):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise Exception("Tavily API rate limit exceeded")
                return [{"title": "Fallback", "url": "", "content": "", "score": 0}]

            monkeypatch.setattr(
                "app.graph.nodes.search_web",
                MagicMock(side_effect=rate_limited_search),
            )

        # 3. 公司概况 API 故障
        elif fault_name == "profile_api_down":
            monkeypatch.setattr(
                "app.graph.nodes.get_company_profile",
                MagicMock(side_effect=Exception("Profile API unavailable")),
            )

        # 4-5. 财报数据源故障
        elif fault_name == "sec_edgar_down":
            monkeypatch.setattr(
                "app.graph.nodes.extract_financial_data",
                MagicMock(side_effect=Exception("SEC EDGAR service unavailable")),
            )
        elif fault_name == "akshare_down":
            monkeypatch.setattr(
                "app.graph.nodes.get_cn_financial_abstract",
                MagicMock(side_effect=Exception("akshare service unavailable")),
            )
            monkeypatch.setattr(
                "app.graph.nodes.get_hk_financial_abstract",
                MagicMock(side_effect=Exception("akshare service unavailable")),
            )
        elif fault_name == "yfinance_down":
            monkeypatch.setattr(
                "app.graph.nodes.get_stock_quote",
                MagicMock(side_effect=Exception("yfinance API unavailable")),
            )

        # 6-7. LLM 故障
        elif fault_name == "llm_timeout":
            # Mock LLM 使其抛出超时异常
            original_get_llm = None
            try:
                from app.core.llm import get_llm as _get_llm
                original_get_llm = _get_llm
            except ImportError:
                pass

            if original_get_llm:
                def _timeout_llm(*args, **kwargs):
                    llm = original_get_llm(*args, **kwargs)
                    # 让 ainvoke 抛出超时
                    original_ainvoke = llm.ainvoke

                    async def timeout_ainvoke(*a, **kw):
                        raise TimeoutError("LLM request timed out after 60s")

                    llm.ainvoke = timeout_ainvoke
                    return llm

                monkeypatch.setattr(
                    "app.core.llm.get_llm", _timeout_llm
                )
        elif fault_name == "llm_invalid_response":
            # info_collector 中的 agent 返回空或无效 JSON
            class FakeInvalidMessage:
                content = "Invalid response ~~~ not json at all ~~~"

            # 替换 info_collector 的 Agent
            original_create = None
            try:
                from langgraph.prebuilt import create_react_agent
                original_create = create_react_agent
            except ImportError:
                pass

            if original_create:
                def _invalid_agent(*args, **kwargs):
                    original = original_create(*args, **kwargs)
                    original.ainvoke = AsyncMock(return_value={
                        "messages": [FakeInvalidMessage()]
                    })
                    return original

                monkeypatch.setattr(
                    "langgraph.prebuilt.create_react_agent", _invalid_agent
                )

    async def test_fault_degradation_aggregate(self):
        """汇总 10 类故障注入测试结果"""
        f_path = Path(__file__).parent / "_fault_results.json"
        results = json.loads(f_path.read_text(encoding="utf-8")) if f_path.exists() else []
        ok = sum(1 for r in results if r.get("degradation_ok"))

        print("\n" + "=" * 60)
        print("📊 测试 16: 故障降级汇总")
        for r in results:
            s = "✅" if r.get("degradation_ok") else "❌"
            print(f"   [{r.get('fault')}] {r.get('description')}: {s}")
        print(f"\n   成功: {ok}/{len(results)} = {ok / max(len(results), 1):.2%}")

        f_path.write_text(json.dumps({
            "degradation_ok_count": ok, "total": len(results),
            "rate": ok / max(len(results), 1),
            "details": results,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

# ═══════════════════════════════════════════════════════════
# 综合汇总报告
# ═══════════════════════════════════════════════════════════

class TestAggregateReport:
    """汇总所有测试指标并输出最终报告（从磁盘文件汇总全部16项）"""

    async def test_generate_final_report(self):
        """生成综合测试报告 JSON"""
        test_dir = Path(__file__).parent

        # ── 1. 读取 e2e 结果 ──
        results = self._load_e2e(test_dir)

        # ── 2. 读取无效代码降级 ──
        deg = self._load_json(test_dir / "_degradation_results.json", [])
        deg_ok = sum(1 for r in deg if r.get("degradation_ok"))
        deg_total = len(deg)

        # ── 3. 读取字段准确率 ──
        fa = self._load_json(test_dir / "_field_accuracy.json", {})

        # ── 4. 读取 LLM 基线 ──
        bl = self._load_json(test_dir / "_baseline.json", {})

        # ── 5. 读取故障降级 ──
        fault = self._load_json(test_dir / "_fault_results.json", {})

        # ── 6. 实时计算工具成功率（从 runs/ 日志） ──
        tool_rates = self._compute_tool_rates()

        # ── 7. 延迟 ──
        latencies = [r.duration_ms for r in results if r.duration_ms > 0]
        p50 = percentile(latencies, 50) if latencies else 0
        p95 = percentile(latencies, 95) if latencies else 0

        # ── 8. 成本（根据报告长度估算 token，反序列化后 estimated_input_tokens 为0） ──
        total_in = 0; total_out = 0
        for r in results:
            in_t, out_t = estimate_llm_tokens({"report_markdown": getattr(r, "report_markdown", "")}, 4)
            total_in += in_t
            total_out += out_t
        in_cost = (total_in / 1_000_000) * PRICE_PER_1M_INPUT_TOKENS
        out_cost = (total_out / 1_000_000) * PRICE_PER_1M_OUTPUT_TOKENS
        avg_cost = (in_cost + out_cost) / max(len(results), 1)

        # ── 组装输出 ──
        output = {
            "=== 1. 30家端到端测试 ===": {
                "成功数": sum(1 for r in results if getattr(r, "is_end_to_end_success", False)),
                "总数": len(results),
                "成功率": f"{sum(1 for r in results if getattr(r, 'is_end_to_end_success', False)) / max(len(results), 1):.2%}",
            },
            "=== 2. 无效代码降级测试 ===": {
                "降级成功数": deg_ok, "总数": deg_total,
                "降级成功率": f"{deg_ok / max(deg_total, 1):.2%}",
            },
            "=== 3. 报告完整率 ===": {
                "5 section 非空数": sum(1 for r in results if getattr(r, "core_sections_non_empty", False)),
                "完整率": f"{sum(1 for r in results if getattr(r, 'core_sections_non_empty', False)) / max(len(results), 1):.2%}",
            },
            "=== 4. 150字段总体准确率 ===": {
                "准确率": f"{fa.get('overall', 0):.2%}",
                "正确/比较": f"{fa.get('total_correct', 0)}/{fa.get('total_compared', 0)}",
            },
            "=== 5. 分市场字段准确率 ===": {
                "A股": f"{fa.get('cn', 0):.2%}",
                "港股": f"{fa.get('hk', 0):.2%}",
                "美股": f"{fa.get('us', 0):.2%}",
            },
            "=== 6. 单LLM基线字段准确率 ===": {
                "准确率": f"{bl.get('baseline_accuracy', 0):.2%}",
                "正确/比较": f"{bl.get('correct', 0)}/{bl.get('compared', 0)}",
            },
            "=== 7-12. 工具成功率 ===": tool_rates,
            "=== 13-14. 延迟 ===": {
                "P50(ms)": p50, "P95(ms)": p95,
                "样本数": len(latencies),
            },
            "=== 15. 单份成本 ===": {
                "总输入tokens(est)": f"{total_in:,}",
                "总输出tokens(est)": f"{total_out:,}",
                "总成本(¥)": f"{in_cost + out_cost:.4f}",
                "平均每份(¥)": f"{avg_cost:.6f}",
            },
            "=== 16. 故障降级测试 ===": {
                "降级成功数": fault.get("degradation_ok_count", 0),
                "总数": fault.get("total", 0),
                "降级成功率": f"{fault.get('rate', 0):.2%}",
            },
        }

        # ── 写入 JSON ──
        report_path = test_dir / "metrics_report.json"
        report_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

        # ── 打印 ──
        print("\n" + "=" * 60)
        print("📊 综合测试报告（16 项全量）")
        print("=" * 60)
        for section, data in output.items():
            print(f"\n{section}")
            if isinstance(data, dict):
                for k, v in data.items():
                    print(f"   {k}: {v}")
            else:
                print(f"   {data}")
        print(f"\n📄 报告已保存至: {report_path}")
        print("=" * 60)

    @staticmethod
    def _load_json(path: Path, default):
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

    @staticmethod
    def _load_e2e(test_dir: Path) -> List:
        memory: List = getattr(pytest, "_e2e_results", [])
        if memory:
            return memory
        e2e_file = test_dir / "_e2e_results.json"
        if not e2e_file.exists():
            return []
        print(f"  从磁盘回读 {e2e_file.name} ...")
        raw = json.loads(e2e_file.read_text(encoding="utf-8"))
        return [TaskResult(
            query=r.get("query", ""), run_id=r.get("run_id", ""),
            duration_ms=r.get("duration_ms", 0), success=r.get("success", False),
            entity_status=r.get("entity_status", "?"),
            financial_status=r.get("financial_status", "?"),
            report_status=r.get("report_status", "?"),
            final_status=r.get("final_status", "?"),
            sections_found=r.get("sections_found", []),
            sections_missing=r.get("sections_missing", []),
            metric_count=r.get("metric_count", 0),
            fatal_error=r.get("fatal_error", False),
            report_markdown=r.get("report_markdown", ""),
        ) for r in raw]

    @staticmethod
    def _compute_tool_rates() -> Dict:
        runs_dir = Path("runs")
        if not runs_dir.exists():
            return {}
        stats = defaultdict(lambda: [0, 0])
        for run_dir in runs_dir.iterdir():
            log = run_dir / "logs.jsonl"
            if not log.exists():
                continue
            for line in log.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                extra = entry.get("extra", {})
                event = entry.get("event", "")
                tool = extra.get("tool", "")
                msg = str(entry.get("message", "")).lower()
                for t_name in ["tavily", "search_web", "sec_edgar", "akshare", "yfinance", "finnhub"]:
                    if t_name in tool.lower() or t_name in msg:
                        stats[t_name][1] += 1
                        if event in ("success", "tool_success") or "success" in msg:
                            stats[t_name][0] += 1
        result = {}
        akshare_succ = 0; akshare_total = 0
        for tool, (s, t) in sorted(stats.items()):
            rate = s / max(t, 1)
            result[tool] = f"{s}/{t} = {rate:.2%}"
            if "akshare" in tool:
                akshare_succ += s; akshare_total += t
        if akshare_total > 0:
            result["akshare(汇总)"] = f"{akshare_succ}/{akshare_total} = {akshare_succ / akshare_total:.2%}"
        total_s = sum(v[0] for v in stats.values())
        total_t = sum(v[1] for v in stats.values())
        if total_t > 0:
            result["总工具成功率"] = f"{total_s}/{total_t} = {total_s / total_t:.2%}"
        return result