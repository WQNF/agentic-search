"""真值（ground truth）构建与读取。

原则：
  1) 期间对齐：真值取“系统实际使用的那一期”同一期间的官方/独立数据；
  2) 币种/单位：金额统一为报告币种的基础单位（元/美元/港元），比率用小数；
  3) 来源可追溯：每行记录 source / source_url / period_end / source_tier；
  4) 不把供应商数据伪装成“官方”：source_tier=vendor 的字段只能用于预跑，
     最终 1% 准确率必须用 official_filing 或 vendor_crosschecked。

用法：
  python -m tests.eval.cli gt --sample core
  python -m tests.eval.cli gt --sample pilot
"""
from __future__ import annotations

import csv
import json
import math
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from .config import BACKEND_DIR, COMPANIES, FIELD_PANEL, RESULTS_DIR

SEC_UA = "research-agent contact@example.com"
GT_CSV = RESULTS_DIR / "ground_truth.csv"
GT_META = RESULTS_DIR / "ground_truth.meta.json"


# ---------------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------------

def _safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b in (None, 0):
        return None
    try:
        return float(a) / float(b)
    except Exception:
        return None


def _num(x) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


@dataclass
class GTRow:
    symbol: str
    market: str
    field: str
    value: Optional[float]
    currency: str
    period_end: str
    fiscal_year: str
    source: str
    source_url: str
    source_tier: str
    verified: bool = False
    notes: str = ""


# ---------------------------------------------------------------------------
# 美股：SEC companyfacts（官方 XBRL）
# ---------------------------------------------------------------------------

def _sec_ticker_map() -> Dict[str, Dict]:
    path = BACKEND_DIR / "data" / "sec_company_tickers.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for item in data.values():
        out[str(item["ticker"]).upper()] = item
    return out


def _sec_get(url: str) -> Dict:
    r = requests.get(url, headers={"User-Agent": SEC_UA}, timeout=30)
    r.raise_for_status()
    return r.json()


def build_us(company: Dict) -> List[GTRow]:
    ticker = company["symbol"].upper()
    info = _sec_ticker_map().get(ticker)
    if not info:
        return [GTRow(company["symbol"], "US", f, None, "USD", "", "", "SEC", "", "official_filing",
                      False, "ticker_not_found_in_sec_map") for f in FIELD_PANEL]
    cik = int(info["cik_str"])
    cik10 = f"{cik:010d}"

    subs = _sec_get(f"https://data.sec.gov/submissions/CIK{cik10}.json")
    recent = subs.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    report_dates = recent.get("reportDate", [])
    filing_dates = recent.get("filingDate", [])

    idx = None
    for i, form in enumerate(forms):
        if form == "10-K":
            idx = i
            break
    if idx is None:
        return [GTRow(company["symbol"], "US", f, None, "USD", "", "", "SEC", "", "official_filing",
                      False, "no_10k") for f in FIELD_PANEL]
    accn = accns[idx]
    period = report_dates[idx] or ""
    filed = filing_dates[idx] or ""

    facts = _sec_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json")
    gaap = facts.get("facts", {}).get("us-gaap", {})

    def pick(tags: List[str], balance: bool) -> Tuple[Optional[float], str]:
        for tag in tags:
            node = gaap.get(tag)
            if not node:
                continue
            for unit, entries in node.get("units", {}).items():
                # 先按 accession 精确取
                cand = [e for e in entries if e.get("accn") == accn]
                if not cand:
                    # 回退：同期间 + 10-K
                    cand = [e for e in entries if e.get("form") == "10-K" and e.get("end") == period]
                if not cand:
                    continue
                # 流量项要 start；余额项不要 start
                if balance:
                    cand = [e for e in cand if not e.get("start")]
                else:
                    cand = [e for e in cand if e.get("start")]
                if not cand:
                    continue
                cand.sort(key=lambda e: (e.get("filed", ""), e.get("end", "")))
                return _num(cand[-1].get("val")), tag
        return None, ""

    revenue, _ = pick([
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues", "SalesRevenueNet",
    ], balance=False)
    net_income, _ = pick(["NetIncomeLoss", "ProfitLoss"], balance=False)
    gross_profit, _ = pick(["GrossProfit"], balance=False)
    if gross_profit is None and revenue is not None:
        # 很多公司不在 XBRL 里单列 GrossProfit，用收入 - 营业成本反推
        cost_of_revenue, _ = pick([
            "CostOfRevenue",
            "CostOfGoodsAndServicesSold",
            "CostOfGoodsSold",
            "CostOfRevenueExcludingDepreciationDepletionAndAmortization",
        ], balance=False)
        if cost_of_revenue is not None:
            gross_profit = revenue - cost_of_revenue
    operating_income, _ = pick(["OperatingIncomeLoss"], balance=False)
    equity, _ = pick([
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ], balance=True)
    assets, _ = pick(["Assets"], balance=True)

    # 成本由毛利反推，避免不同公司成本口径差异
    cost = (revenue - gross_profit) if (revenue is not None and gross_profit is not None) else None

    values = {
        "revenue": revenue,
        "net_income": net_income,
        "gross_margin": _safe_div(gross_profit, revenue),
        "net_margin": _safe_div(net_income, revenue),
        "operating_margin": _safe_div(operating_income, revenue),
    }
    fy = period[:4] if period else ""
    src = f"SEC 10-K {accn} (filed {filed})"
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}/"
    return [
        GTRow(company["symbol"], "US", f, values[f], "USD", period, fy, src, url,
              "official_filing", False,
              "" if values[f] is not None else "not_in_xbrl")
        for f in FIELD_PANEL
    ]


# ---------------------------------------------------------------------------
# A 股：新浪财务报表（与系统使用的同花顺摘要互为独立来源）
# ---------------------------------------------------------------------------

def _sina_stock(symbol: str) -> str:
    code = symbol.split(".")[0]
    if symbol.endswith(".SH"):
        return f"sh{code}"
    if symbol.endswith(".SZ"):
        return f"sz{code}"
    return f"bj{code}"


def build_cn(company: Dict) -> List[GTRow]:
    import akshare as ak
    import pandas as pd

    symbol = company["symbol"]
    stock = _sina_stock(symbol)
    try:
        inc = ak.stock_financial_report_sina(stock=stock, symbol="利润表")
        bal = ak.stock_financial_report_sina(stock=stock, symbol="资产负债表")
    except Exception as e:
        return [GTRow(symbol, "CN", f, None, "CNY", "", "", "sina", "akshare:stock_financial_report_sina",
                      "vendor", False, f"fetch_error:{e}") for f in FIELD_PANEL]

    inc = inc.sort_values("报告日")
    bal = bal.sort_values("报告日")
    ri = inc.iloc[-1]
    rb = bal.iloc[-1]
    period = str(int(ri["报告日"])) if not pd.isna(ri["报告日"]) else ""
    if isinstance(ri["报告日"], str):
        period = ri["报告日"]

    def cell(df_row, candidates: List[str]) -> Optional[float]:
        for c in candidates:
            if c in df_row.index:
                v = _num(df_row[c])
                if v is not None:
                    return v
        return None

    revenue = cell(ri, ["营业总收入", "营业收入"])
    net_income = cell(ri, ["归属于母公司所有者的净利润", "归属于母公司股东的净利润", "净利润"])
    cost = cell(ri, ["营业成本"])
    operating_income = cell(ri, ["营业利润"])
    equity = cell(rb, ["归属于母公司股东权益合计", "归属于母公司所有者权益合计", "所有者权益(或股东权益)合计"])

    gross_profit = (revenue - cost) if (revenue is not None and cost is not None) else None
    values = {
        "revenue": revenue,
        "net_income": net_income,
        "gross_margin": _safe_div(gross_profit, revenue),
        "net_margin": _safe_div(net_income, revenue),
        "operating_margin": _safe_div(operating_income, revenue),
    }
    src = f"新浪财经 财务报表 报告日={period}"
    url = "https://money.finance.sina.com.cn/corp/go.php/vFD_FinanceSummary/stockid/{}.phtml".format(
        symbol.split(".")[0]
    )
    return [
        GTRow(symbol, "CN", f, values[f], "CNY", period, period[:4], src, url,
              "vendor", False,
              "" if values[f] is not None else "not_in_statement")
        for f in FIELD_PANEL
    ]


# ---------------------------------------------------------------------------
# 港股：东方财富港股财务报表（与系统同源，先标记 vendor；需人工/官方复核）
# ---------------------------------------------------------------------------

def build_hk(company: Dict) -> List[GTRow]:
    import akshare as ak
    import pandas as pd

    symbol = company["symbol"]
    code = symbol.split(".")[0]
    try:
        inc = ak.stock_financial_hk_report_em(stock=code, symbol="利润表", indicator="年度")
        bal = ak.stock_financial_hk_report_em(stock=code, symbol="资产负债表", indicator="年度")
    except Exception as e:
        return [GTRow(symbol, "HK", f, None, company.get("currency", "HKD"), "", "",
                      "eastmoney_hk", "akshare:stock_financial_hk_report_em", "vendor", False,
                      f"fetch_error:{e}") for f in FIELD_PANEL]

    # 关键：按 REPORT_DATE 取最新一期，不能用 FISCAL_YEAR（该列是 "12-31"）
    inc = inc.dropna(subset=["REPORT_DATE"]).sort_values("REPORT_DATE")
    bal = bal.dropna(subset=["REPORT_DATE"]).sort_values("REPORT_DATE")
    latest = inc["REPORT_DATE"].max()
    inc_l = inc[inc["REPORT_DATE"] == latest]
    bal_l = bal[bal["REPORT_DATE"] == latest]
    period = str(pd.Timestamp(latest).date())

    def item(df, names: List[str]) -> Optional[float]:
        for n in names:
            hit = df[df["STD_ITEM_NAME"].astype(str).str.strip() == n]
            if not hit.empty:
                v = _num(hit.iloc[0]["AMOUNT"])
                if v is not None:
                    return v
        return None

    revenue = item(inc_l, ["营业额", "营运收入"])
    gross_profit = item(inc_l, ["毛利"])
    operating_income = item(inc_l, ["经营溢利"])
    net_income = item(inc_l, ["股东应占溢利", "本公司拥有人应占溢利"])
    equity = item(bal_l, ["股东权益", "本公司拥有人应占权益"])

    values = {
        "revenue": revenue,
        "net_income": net_income,
        "gross_margin": _safe_div(gross_profit, revenue),
        "net_margin": _safe_div(net_income, revenue),
        "operating_margin": _safe_div(operating_income, revenue),
    }
    src = f"东方财富港股报表 REPORT_DATE={period}"
    url = f"https://emweb.securities.eastmoney.com/PC_HKF10/FinancialAnalysis/index?type=web&code={code}"
    return [
        GTRow(symbol, "HK", f, values[f], company.get("currency", "HKD"), period, period[:4],
              src, url, "vendor", False,
              "" if values[f] is not None else "not_in_statement")
        for f in FIELD_PANEL
    ]


# ---------------------------------------------------------------------------
# 构建 / 读取 / 校验
# ---------------------------------------------------------------------------

BUILDERS = {"US": build_us, "CN": build_cn, "HK": build_hk}


def build(companies: List[Dict], force: bool = False) -> Dict:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if GT_CSV.exists() and not force:
        raise SystemExit(f"{GT_CSV} 已存在；加 --force 覆盖")
    rows: List[GTRow] = []
    for c in companies:
        builder = BUILDERS.get(c["market"])
        if not builder:
            continue
        try:
            got = builder(c)
        except Exception as e:
            got = [GTRow(c["symbol"], c["market"], f, None, c.get("currency", ""), "", "",
                         "error", "", "missing", False, f"builder_error:{e}") for f in FIELD_PANEL]
        rows.extend(got)
        print(f"[gt] {c['symbol']:12s} {len([r for r in got if r.value is not None])}/5")
        time.sleep(0.3)

    with GT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        w.writeheader()
        for r in rows:
            w.writerow(asdict(r))

    meta = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "companies": len(companies),
        "rows": len(rows),
        "fields": FIELD_PANEL,
        "by_market": {},
    }
    for m in ("US", "CN", "HK"):
        mr = [r for r in rows if r.market == m]
        meta["by_market"][m] = {
            "fields": len(mr),
            "non_null": len([r for r in mr if r.value is not None]),
            "tiers": sorted({r.source_tier for r in mr}),
        }
    GT_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return meta


def load_gt() -> Dict[str, Dict[str, Dict]]:
    if not GT_CSV.exists():
        return {}
    out: Dict[str, Dict[str, Dict]] = {}
    with GT_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            v = row["value"]
            out.setdefault(row["symbol"], {})[row["field"]] = {
                "value": float(v) if v not in ("", "None") else None,
                "currency": row["currency"],
                "period_end": row["period_end"],
                "source": row["source"],
                "source_tier": row["source_tier"],
                "verified": row["verified"].lower() == "true",
            }
    return out


def validate_gt(companies: List[Dict]) -> List[str]:
    gt = load_gt()
    problems = []
    for c in companies:
        if c["symbol"] not in gt:
            problems.append(f"{c['symbol']}: 无真值")
            continue
        for f in FIELD_PANEL:
            rec = gt[c["symbol"]].get(f)
            if not rec or rec["value"] is None:
                problems.append(f"{c['symbol']}.{f}: 真值缺失")
    return problems
