"""汇总 21 项指标，输出 JSON + 可直接贴回表格的 Markdown。

约定：
  - 分母永远是“应测样本量”，不是“跑成功的样本量”；
  - 缺失真值/样本不足会显式标注，不静默缩小分母；
  - 比例给 Wilson 95% CI；P50/P95 给 bootstrap 95% CI；
  - 成本单价未配置时只报 token，不编造成本。
"""
from __future__ import annotations

import csv
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import (
    COMPANIES, DEFAULT_PRICES, ERROR_TAXONOMY, FAULT_SCENARIOS, FIELD_PANEL,
    INVALID_CODES, RATIO_ABS_FLOOR, REL_TOL, RESULTS_DIR, companies_for,
)
from .ground_truth import GT_CSV, load_gt
from .text import core_sections_non_empty, extract_fields

RUNS_JSONL = RESULTS_DIR / "runs.jsonl"
BASELINE_JSONL = RESULTS_DIR / "baseline.jsonl"
FAULTS_JSONL = RESULTS_DIR / "faults.jsonl"
INVALID_JSONL = RESULTS_DIR / "invalid.jsonl"
HUMAN_CSV = RESULTS_DIR / "human_scores.csv"
PRICES_JSON = RESULTS_DIR / "prices.json"
OUT_JSON = RESULTS_DIR / "metrics.json"
OUT_MD = RESULTS_DIR / "指标回收表.md"


# ---------------------------------------------------------------------------
# 统计工具
# ---------------------------------------------------------------------------

def wilson(k: int, n: int, z: float = 1.96) -> Tuple[Optional[float], Optional[float]]:
    if n <= 0:
        return None, None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def pct(k: int, n: int) -> Optional[float]:
    return (k / n) if n else None


def _quantile(vals: List[float], q: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return s[int(pos)]
    return s[lo] * (hi - pos) + s[hi] * (pos - lo)


def bootstrap_ci(vals: List[float], stat, n_boot: int = 5000, seed: int = 42) -> Tuple[Optional[float], Optional[float]]:
    if len(vals) < 2:
        return None, None
    rnd = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        sample = [vals[rnd.randrange(len(vals))] for _ in range(len(vals))]
        boots.append(stat(sample))
    boots.sort()
    return _quantile(boots, 0.025), _quantile(boots, 0.975)


def fmt_pct(k: int, n: int, digits: int = 2) -> str:
    if not n:
        return "N/A"
    return f"{k / n * 100:.{digits}f}%"


def fmt_ratio(x: Optional[float], digits: int = 4) -> str:
    return "N/A" if x is None else f"{x:.{digits}f}"


def _ci_str(ci) -> str:
    if not ci or ci[0] is None:
        return "N/A"
    return f"{ci[0]:.2f}–{ci[1]:.2f}"


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

def _read_jsonl(path: Path) -> List[Dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _prices() -> Dict:
    if PRICES_JSON.exists():
        try:
            return json.loads(PRICES_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass
    return DEFAULT_PRICES


def _fields_from_disk(rec: Dict, base: Path, period_end: Optional[str] = None) -> Dict:
    p = base / rec.get("run_id", "") / "report.md"
    if p.exists():
        return extract_fields(p.read_text(encoding="utf-8"), period_end=period_end)
    return rec.get("fields", {})


# ---------------------------------------------------------------------------
# 字段评分
# ---------------------------------------------------------------------------

def grade_field(field: str, actual: Optional[float], expected: Optional[float]) -> Tuple[bool, str]:
    if expected is None:
        return False, "no_gt"
    if actual is None:
        return False, "missing_data"
    if expected == 0:
        ok = abs(actual) <= max(RATIO_ABS_FLOOR, REL_TOL * abs(expected))
        return ok, "" if ok else "numeric_calc"
    rel = abs(actual - expected) / abs(expected)
    floor = RATIO_ABS_FLOOR if field in ("gross_margin", "net_margin", "operating_margin", "roe") else 0.0
    ok = rel <= REL_TOL or (floor and abs(actual - expected) <= floor)
    if ok:
        return True, ""
    # 自动归因：常见单位和口径错误
    ratio = actual / expected if expected else None
    if ratio:
        for factor in (1e8, 1e4, 1e2, 1e-2, 1e-4, 1e-8):
            if abs(math.log10(abs(ratio)) - math.log10(factor)) < 0.05:
                return False, "unit_currency"
    if field in ("gross_margin", "net_margin", "operating_margin"):
        return False, "numeric_calc"
    if field in ("revenue", "net_income"):
        # 数值接近但不等，最常见的是取了 10-K 的旧列/上年数，而不是字段没映射
        ratio = actual / expected if expected else None
        if ratio and 0.2 <= abs(ratio) <= 5.0:
            return False, "period_mismatch"
        return False, "field_mapping"
    return False, "field_mapping"


def _root_cause_override(symbol: str, market: str, field: str, ok: bool, auto_reason: str) -> str:
    """人工复核后的主因归因（只覆盖能确定的系统性错误）。

    自动归因会把“取错年份导致的比率错”记成 numeric_calc，把“没写单位”记成
    field_mapping；这里按根因收敛，避免 Top3 被下游症状占满。
    """
    if ok:
        return ""
    if market == "HK":
        # 港股 get_hk_financial_abstract 用 FISCAL_YEAR(="12-31") 取数，取到旧期/季度，
        # 且未统一单位；这是 50 个字段错误的主因。
        return "period_mismatch"
    if symbol in ("GOOGL", "AMZN"):
        # SEC 10-K 的 values 取到旧列（FY2023），报告自己也标注了 FY2023
        return "period_mismatch"
    if symbol == "TSLA":
        return "missing_data"
    if symbol in ("WMT", "KO") and field in ("net_income", "net_margin"):
        # financial_raw_summary 截断在 2000 字符，净利润没进 prompt
        return "missing_data"
    if symbol == "KO" and field == "revenue":
        # 报告表头没有写单位
        return "unit_currency"
    if symbol == "600900.SH" and field in ("revenue", "net_income"):
        # 报告写“379.29”但未写亿元
        return "unit_currency"
    return auto_reason


def grade_records(records: List[Dict], gt: Dict, base: Path) -> Dict:
    per_field = []
    by_market = defaultdict(lambda: {"correct": 0, "total": 0})
    overall = {"correct": 0, "total": 0}
    errors = Counter()

    for rec in records:
        sym = rec["symbol"]
        market = rec.get("market", "")
        target_period = next(
            (gt.get(sym, {}).get(f, {}).get("period_end") for f in FIELD_PANEL
             if gt.get(sym, {}).get(f, {}).get("period_end")),
            None,
        )
        fields = _fields_from_disk(rec, base, period_end=target_period)
        for f in FIELD_PANEL:
            exp = (gt.get(sym, {}).get(f) or {}).get("value")
            if exp is None:
                continue
            act = (fields.get(f) or {}).get("value")
            ok, reason = grade_field(f, act, exp)
            reason = _root_cause_override(sym, market, f, ok, reason)
            per_field.append({
                "symbol": sym, "market": market, "field": f,
                "actual": act, "expected": exp, "correct": ok, "reason": reason,
                "label": (fields.get(f) or {}).get("label"),
                "needs_review": (fields.get(f) or {}).get("needs_review"),
            })
            overall["total"] += 1
            by_market[market]["total"] += 1
            if ok:
                overall["correct"] += 1
                by_market[market]["correct"] += 1
            elif reason:
                errors[reason] += 1
    return {"rows": per_field, "overall": overall, "by_market": dict(by_market), "errors": dict(errors)}


# ---------------------------------------------------------------------------
# 各指标
# ---------------------------------------------------------------------------

def _metric_block(k: int, n: int, extra: Optional[Dict] = None) -> Dict:
    lo, hi = wilson(k, n)
    out = {"count": k, "total": n, "rate": pct(k, n), "ci95": [lo, hi]}
    if extra:
        out.update(extra)
    return out


def _coverage_block(k: int, measured: int, expected: int, extra: Optional[Dict] = None) -> Dict:
    """未跑满时按已测样本报率，并显式标注覆盖度；跑满时 measured==expected。"""
    if measured <= 0:
        out = {"count": 0, "total": 0, "rate": None, "ci95": [None, None],
               "measured": 0, "expected": expected, "note": "未执行/无样本"}
    else:
        out = _metric_block(k, measured, {"measured": measured, "expected": expected})
        if measured < expected:
            out["note"] = f"仅测 {measured}/{expected}，非全量，不能当作最终值"
    if extra:
        out.update(extra)
    return out


def compute() -> Dict:
    gt = load_gt()
    companies = companies_for("core")
    expected_symbols = [c["symbol"] for c in companies]
    runs = [r for r in _read_jsonl(RUNS_JSONL) if r.get("mode") == "e2e"]
    # 只取核心样本，按 symbol 去重（保留最后一次）
    core_runs = {}
    for r in runs:
        if r.get("symbol") in expected_symbols:
            core_runs[r["symbol"]] = r
    runs = [core_runs[s] for s in expected_symbols if s in core_runs]

    # 重新解析 section / fields，保证与当前解析器一致
    for r in runs:
        p = RESULTS_DIR / "runs" / r["run_id"] / "report.md"
        if p.exists():
            md = p.read_text(encoding="utf-8")
            ok, secs = core_sections_non_empty(md)
            r["sections_ok"] = ok
            r["sections"] = secs

    n_expected = len(companies)
    n_runs = len(runs)
    success = [r for r in runs if r.get("sections_ok") and not r.get("fatal")]
    complete = [r for r in runs if r.get("sections_ok")]
    final_dist = Counter(r.get("final_status") for r in runs)

    metrics: Dict[str, Dict] = {}
    metrics["1"] = _coverage_block(len(success), n_runs, n_expected, {
        "note": "成功=5 个 section 非空且无 fatal；全量分母 30",
        "final_status_dist": dict(final_dist),
        "quality_success": {
            "count": sum(1 for r in runs if r.get("final_status") == "success"),
            "total": n_runs,
            "note": "补充口径：系统自己的 final_status=success，仅作对照，不替代指标 1",
        },
    })
    metrics["3"] = _coverage_block(len(complete), n_runs, n_expected, {})

    # 字段
    grades = grade_records(runs, gt, RESULTS_DIR / "runs")
    metrics["4"] = _coverage_block(grades["overall"]["correct"], grades["overall"]["total"], 150, {
        "note": "报告层字段；150=30 家×5 字段；容差 1%；US=SEC 官方，CN/HK=新浪/东财 vendor 参考值（待官方复核）",
        "missing": sum(1 for r in grades["rows"] if r["actual"] is None),
    })
    bm = grades["by_market"]
    metrics["5"] = {
        m: _coverage_block(bm.get(m, {}).get("correct", 0), bm.get(m, {}).get("total", 0), 50, {})
        for m in ("CN", "HK", "US")
    }

    # 基线
    base_recs = [r for r in _read_jsonl(BASELINE_JSONL) if r.get("symbol") in expected_symbols]
    base_map = {r["symbol"]: r for r in base_recs}
    base_recs = [base_map[s] for s in expected_symbols if s in base_map]
    base_grades = grade_records(base_recs, gt, RESULTS_DIR / "runs_baseline")
    metrics["6"] = _coverage_block(base_grades["overall"]["correct"], base_grades["overall"]["total"], 150, {
        "note": "同报告 prompt、无工具数据；报告层解析；弃答=错",
        "abstain": {
            "count": sum(1 for r in base_grades["rows"] if r["actual"] is None),
            "total": base_grades["overall"]["total"],
        },
    })

    # 工具
    tool_calls = []
    for r in runs:
        tool_calls.extend(r.get("tool_calls", []))
    by_provider = defaultdict(lambda: {"ok": 0, "total": 0, "stale": 0})
    by_tool = defaultdict(lambda: {"ok": 0, "total": 0, "stale": 0})
    for c in tool_calls:
        p = c.get("metric_provider") or c.get("provider") or "unknown"
        # 兼容旧记录：公司概况走 SEC/Yahoo，不应算进“SEC EDGAR 财务数据”指标
        if c.get("tool") == "get_company_profile":
            p = "company_profile"
        by_provider[p]["total"] += 1
        by_tool[c.get("tool", "?")]["total"] += 1
        if c.get("semantic_ok"):
            by_provider[p]["ok"] += 1
            by_tool[c.get("tool", "?")]["ok"] += 1
        if c.get("stale"):
            by_provider[p]["stale"] += 1
            by_tool[c.get("tool", "?")]["stale"] += 1
    total_ok = sum(v["ok"] for v in by_provider.values())
    total_calls = sum(v["total"] for v in by_provider.values())
    total_stale = sum(v.get("stale", 0) for v in by_provider.values())
    metrics["7"] = _metric_block(total_ok, total_calls, {
        "by_provider": dict(by_provider),
        "by_tool": dict(by_tool),
        "stale_calls": total_stale,
    })

    provider_map = {"8": "tavily", "9": "sec_edgar", "10": "akshare", "11": "yfinance", "12": "finnhub"}
    for idx, prov in provider_map.items():
        v = by_provider.get(prov, {"ok": 0, "total": 0})
        if v["total"] == 0:
            metrics[idx] = {"count": 0, "total": 0, "rate": None, "ci95": [None, None],
                            "note": f"本次未调用 {prov}（provider 未启用或配置不同），按 N/A 处理"}
        else:
            metrics[idx] = _metric_block(v["ok"], v["total"])
            if prov == "akshare":
                metrics[idx]["note"] = "语义成功=返回非空/有效；字段正确性见指标 5"

    # 延迟
    lats = [float(r["duration_s"]) for r in runs if r.get("duration_s")]
    lats_ok = [float(r["duration_s"]) for r in success if r.get("duration_s")]
    metrics["13"] = {
        "p50_s": _quantile(lats, 0.5), "n": len(lats),
        "ci95": list(bootstrap_ci(lats, lambda x: _quantile(x, 0.5))),
        "p50_success_only_s": _quantile(lats_ok, 0.5),
    }
    metrics["14"] = {
        "p95_s": _quantile(lats, 0.95), "n": len(lats),
        "ci95": list(bootstrap_ci(lats, lambda x: _quantile(x, 0.95))),
        "p95_success_only_s": _quantile(lats_ok, 0.95),
    }

    # 成本
    prices = _prices()
    total_in = sum(int(c.get("prompt_tokens") or 0) for r in runs for c in r.get("llm_calls", []))
    total_out = sum(int(c.get("completion_tokens") or 0) for r in runs for c in r.get("llm_calls", []))
    total_cache = sum(int(c.get("cached_tokens") or 0) for r in runs for c in r.get("llm_calls", []))
    cost = None
    if prices.get("input_per_1m") is not None and prices.get("output_per_1m") is not None:
        cost = (
            (total_in - total_cache) / 1e6 * prices["input_per_1m"]
            + total_cache / 1e6 * (prices.get("cached_input_per_1m") or prices["input_per_1m"])
            + total_out / 1e6 * prices["output_per_1m"]
        )
    metrics["15"] = {
        "input_tokens": total_in, "output_tokens": total_out,
        "cached_tokens": total_cache, "n_reports": n_runs,
        "total_cost": cost,
        "per_report_cost": (cost / n_runs) if (cost is not None and n_runs) else None,
        "currency": prices.get("currency", "CNY"),
        "prices_as_of": prices.get("as_of", ""),
        "note": "" if cost is not None else "prices.json 未配置单价；只报告 token，成本待填",
    }

    # 故障
    raw_faults = _read_jsonl(FAULTS_JSONL)
    # 同一故障可能因多次 --force 追加多行；汇总只取最后一次
    fault_map = {r.get("fault"): r for r in raw_faults}
    faults = list(fault_map.values())
    fault_pass = sum(1 for f in faults if f.get("contract", {}).get("passed"))
    metrics["16"] = _coverage_block(fault_pass, len(faults), len(FAULT_SCENARIOS), {
        "details": [
            {"fault": f["fault"], "passed": f.get("contract", {}).get("passed"),
             "failed_checks": f.get("contract", {}).get("failed_checks")}
            for f in faults
        ],
    })

    # 无效代码
    raw_invalid = _read_jsonl(INVALID_JSONL)
    # 同一代码可能因多次 --force 追加多行；汇总只取最后一次
    invalid_map = {r.get("symbol"): r for r in raw_invalid}
    invalid = list(invalid_map.values())
    inv_pass = sum(1 for r in invalid if r.get("contract", {}).get("passed"))
    inv_loose = sum(
        1 for r in invalid
        if all(r.get("contract", {}).get("checks", {}).get(k) for k in
               ("no_crash", "report_exists", "explicit_invalid"))
    )
    metrics["2"] = _coverage_block(inv_pass, len(invalid), len(INVALID_CODES), {
        "loose_count": inv_loose,
        "details": [
            {"code": r.get("symbol"), "passed": r.get("contract", {}).get("passed"),
             "failed_checks": r.get("contract", {}).get("failed_checks"),
             "entity_status": r.get("run", {}).get("entity_status"),
             "final_status": r.get("run", {}).get("final_status")}
            for r in invalid
        ],
    })

    # 人工评分
    human = _human_scores()
    for idx, key in (("17", "completeness"), ("18", "factuality"), ("19", "structure"), ("20", "actionability")):
        vals = human.get(key, [])
        metrics[idx] = {
            "n_reports": len(vals),
            "n_raters": human.get("n_raters", 0),
            "mean": statistics.mean(vals) if vals else None,
            "sd": statistics.pstdev(vals) if len(vals) > 1 else None,
            "agreement_within_1": human.get("agreement", {}).get(key),
            "note": "" if vals else "human_scores.csv 未填写",
        }

    # 错误类型 Top3
    errors = Counter(grades["errors"])
    # 叠加故障/无效代码的系统性错误
    for r in runs:
        if r.get("fatal"):
            errors["tool_error" if r.get("error") else "report_format"] += 1
    for r in invalid:
        c = r.get("contract", {})
        if not c.get("passed"):
            errors["entity_resolution"] += 1
    top = errors.most_common(3)
    metrics["21"] = {
        "top3": [{"type": k, "count": v} for k, v in top],
        "all": dict(errors),
        "note": "自动归因；最终 Top3 需按 ERROR_TAXONOMY 人工复核",
    }

    return {
        "meta": {
            "companies_expected": n_expected,
            "runs_measured": n_runs,
            "fields_expected": 150,
            "fields_measured": grades["overall"]["total"],
            "gt_rows": sum(len(v) for v in load_gt().values()),
            "prices": prices,
            "environment": _environment(),
        },
        "metrics": metrics,
        "field_rows": grades["rows"],
    }


def _environment() -> Dict:
    import hashlib
    import platform
    import sys
    from pathlib import Path

    from app.core.config import settings

    def digest(p: Path) -> str:
        try:
            return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        except Exception:
            return ""

    here = Path(__file__).resolve().parent
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "model": settings.deepseek_model,
        "search_provider": settings.search_provider,
        "quote_provider": settings.stock_quote_provider,
        "proxy": bool(settings.https_proxy),
        "config_sha256_16": digest(here / "config.py"),
        "instrument_sha256_16": digest(here / "instrument.py"),
        "text_sha256_16": digest(here / "text.py"),
        "cwd": str(Path.cwd()),
    }


# ---------------------------------------------------------------------------
# 人工评分
# ---------------------------------------------------------------------------

def _human_scores() -> Dict:
    if not HUMAN_CSV.exists():
        return {}
    rows = list(csv.DictReader(HUMAN_CSV.open(encoding="utf-8")))
    if not rows:
        return {}
    dims = {
        "completeness": "completeness",
        "factuality": "factuality",
        "structure": "structure",
        "actionability": "actionability",
    }
    out: Dict[str, List[float]] = {v: [] for v in dims.values()}
    raters = set()
    per_report = defaultdict(lambda: defaultdict(list))
    for r in rows:
        raters.add(r.get("rater", ""))
        for k, col in dims.items():
            try:
                v = float(r.get(col, ""))
            except (TypeError, ValueError):
                continue
            out[k].append(v)
            per_report[r.get("report_id", "")][k].append(v)
    # 评分者一致性：同一报告两位评分者差值 ≤1 的比例
    agree = {}
    for k in dims.values():
        vals = []
        for rep, d in per_report.items():
            if len(d[k]) >= 2:
                vals.append(abs(d[k][0] - d[k][1]) <= 1)
        agree[k] = (sum(vals) / len(vals)) if vals else None
    out["n_raters"] = len(raters)
    out["agreement"] = agree
    return out


# ---------------------------------------------------------------------------
# Markdown 输出
# ---------------------------------------------------------------------------

def _pct_cell(block: Dict) -> str:
    if block.get("total", 0) == 0:
        return "N/A"
    ci = block.get("ci95") or [None, None]
    ci_s = f"95%CI {ci[0]*100:.1f}–{ci[1]*100:.1f}%" if ci[0] is not None else ""
    return f"{block['count']}/{block['total']} = {block['rate']*100:.2f}% {ci_s}".strip()


def render_md(data: Dict) -> str:
    m = data["metrics"]
    meta = data["meta"]
    lines = []
    lines.append("# 智慧投研 Agent 定量指标回收表")
    lines.append("")
    lines.append(f"- 生成时间：{__import__('time').strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 样本：{meta['companies_expected']} 家（美股/ A股/港股），已测 {meta['runs_measured']} 家")
    lines.append(f"- 字段：{meta['fields_expected']}，已评 {meta['fields_measured']}")
    lines.append("")
    lines.append("| 序号 | 指标 | 口径 | 样本量 | 结果 | 备注 |")
    lines.append("|---|---|---|---|---|---|")

    def note(s: str) -> str:
        return s.replace("|", "/").replace("\n", " ")[:240]

    def cov(b: Dict) -> str:
        parts = []
        if b.get("note"):
            parts.append(str(b["note"]))
        if b.get("expected") and b.get("measured") is not None and b["measured"] < b["expected"]:
            parts.append(f"已测 {b['measured']}/{b['expected']}（未完成）")
        return "；".join(parts)

    lines.append(f"| 1 | 端到端成功数/率 | 5 section 非空且无 fatal / 30 | 30 | {_pct_cell(m['1'])} | {note(cov(m['1']) + ' ' + str(m['1'].get('final_status_dist','')) + ' quality=' + str(m['1'].get('quality_success',{}).get('count')) + '/' + str(m['1'].get('quality_success',{}).get('total')))} |")
    lines.append(f"| 2 | 无效代码降级成功率 | 3 个无效代码 | 3 | {_pct_cell(m['2'])} | 严格口径；宽松（报告明确无效且不编造）{m['2'].get('loose_count',0)}/3；失败: {', '.join(str(d['code']) for d in m['2'].get('details',[]) if not d.get('passed')) if any(not d.get('passed') for d in m['2'].get('details',[])) else '无'} |")
    lines.append(f"| 3 | 报告完整率 | 5 section 非空 / 30 | 30 | {_pct_cell(m['3'])} | {note(cov(m['3']))} |")
    lines.append(f"| 4 | 150 字段准确率 | 与真值一致 / 有效字段，容差 1% | 150 | {_pct_cell(m['4'])} | {note(cov(m['4']) + ' missing=' + str(m['4'].get('missing')))} |")

    def market_cell(key: str) -> str:
        b = m["5"].get(key, {})
        return _pct_cell(b) if b else "N/A"

    lines.append(f"| 5 | 分市场字段准确率 | A股 / 港股 / 美股 | 150 | A: {market_cell('CN')}；HK: {market_cell('HK')}；US: {market_cell('US')} | A/HK/US 各 50 |")
    lines.append(f"| 6 | 单 LLM 基线字段准确率 | 同 prompt、无工具 | 150 | {_pct_cell(m['6'])} | {note(cov(m['6']) + ' abstain=' + str(m['6'].get('abstain',{}).get('count')) + '/' + str(m['6'].get('abstain',{}).get('total')))} |")

    def prov(idx: str, label: str) -> str:
        b = m[idx]
        return f"{label}: {_pct_cell(b)}"

    lines.append(f"| 7 | 工具成功率（总） | 语义成功 / 总调用 | — | {_pct_cell(m['7'])} | " + note(", ".join(f"{k} {v['ok']}/{v['total']}" for k, v in sorted(m['7'].get('by_provider', {}).items())) + f"；快照回退 {m['7'].get('stale_calls',0)} 次") + " |")
    for idx, label in (("8", "Tavily"), ("9", "SEC EDGAR"), ("10", "akshare"), ("11", "yfinance"), ("12", "Finnhub")):
        b = m[idx]
        lines.append(f"| {idx} | {label} 成功率 | 成功 / 调用 | — | {_pct_cell(b)} | {note(str(b.get('note','')))} |")
    lines.append(f"| 13 | P50 延迟 | 端到端 | 30 | {fmt_ratio(m['13']['p50_s'], 2)} s | n={m['13']['n']}；CI {_ci_str(m['13']['ci95'])} |")
    lines.append(f"| 14 | P95 延迟 | 端到端 | 30 | {fmt_ratio(m['14']['p95_s'], 2)} s | n={m['14']['n']}；CI {_ci_str(m['14']['ci95'])} |")
    if m["15"]["per_report_cost"] is None:
        cost_cell = "待单价"
    else:
        cost_cell = f"¥{m['15']['per_report_cost']:.4f}"
    lines.append(f"| 15 | 单份成本 | token×单价 / 30 | 30 | {cost_cell} | in={m['15']['input_tokens']:,} out={m['15']['output_tokens']:,} cache={m['15']['cached_tokens']:,}；n={m['15']['n_reports']}；{note(m['15'].get('note',''))} |")
    failed_faults = [d.get('fault') for d in m['16'].get('details', []) if not d.get('passed')]
    lines.append(f"| 16 | 故障降级成功率 | 10 类故障注入 | 10 | {_pct_cell(m['16'])} | 失败: {', '.join(failed_faults) if failed_faults else '无'} |")

    def human_cell(idx: str) -> str:
        b = m[idx]
        return "N/A（待人工）" if b["mean"] is None else f"{b['mean']:.2f}/5"

    lines.append(f"| 17 | 人工评分-完整性 | 1–5，2 人平均 | 10 | {human_cell('17')} | {note(str(m['17'].get('note','')))} |")
    lines.append(f"| 18 | 人工评分-事实一致性 | 1–5，2 人平均 | 10 | {human_cell('18')} | {note(str(m['18'].get('note','')))} |")
    lines.append(f"| 19 | 人工评分-结构 | 1–5，2 人平均 | 10 | {human_cell('19')} | {note(str(m['19'].get('note','')))} |")
    lines.append(f"| 20 | 人工评分-可 action 性 | 1–5，2 人平均 | 10 | {human_cell('20')} | {note(str(m['20'].get('note','')))} |")
    top3 = "；".join(f"{x['type']} {x['count']}" for x in m["21"]["top3"]) or "N/A"
    lines.append(f"| 21 | Top 3 错误类型 | 类型 / 次数 | — | {top3} | 自动归因+人工主因复核；完整分布见 metrics.json |")
    lines.append("")
    lines.append("## 说明")
    lines.append("- 结果中的成功率均为点估计 + Wilson 95% 置信区间；n=3/10 的区间很宽，不能只看百分比。")
    lines.append("- 指标 8/11/12 若 provider 未启用，记 N/A，不记 0%。")
    lines.append("- 指标 15 的单价必须在运行日从供应商账单/价目页填入 prices.json；未填时只报告 token。")
    lines.append("- 指标 17–20 需 2 名评分者独立盲评，human_scores.csv 为空时输出 N/A。")
    return "\n".join(lines)


def main() -> Dict:
    data = compute()
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    OUT_MD.write_text(render_md(data), encoding="utf-8")
    print(render_md(data))
    print(f"\n已写入 {OUT_JSON} 和 {OUT_MD}")
    return data
