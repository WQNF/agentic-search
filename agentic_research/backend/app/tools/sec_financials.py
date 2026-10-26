#SEC 财报工具 sec_financials.py

# backend/app/tools/sec_financials.py
import logging
import re
from typing import List, Dict, Optional, Union

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from edgar import Company, Filing, set_identity
from app.Schemas.entity import Entity
from app.core.config import settings
from app.core.metrics import track_tool
from app.services.snapshot_store import get_or_fetch

logger = logging.getLogger(__name__)

class SECFinancialsError(Exception):
    """SEC 财报获取异常"""
    pass

# 重试装饰器：针对网络错误、解析错误等
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
    reraise=True
)
def _get_company(entity: Entity) -> Company:
    """获取 edgar Company 对象，带重试"""
    try:
        set_identity(settings.sec_user_agent)
        if entity.cik:
            company = Company(entity.cik)
        else:
            company = Company(entity.symbol)
        return company
    except Exception as e:
        logger.error(f"获取 Company 失败: {e}")
        raise SECFinancialsError(f"无法初始化 Company: {e}")

@track_tool("fetch_sec_filings")
def fetch_sec_filings(entity: Entity, form: str = "10-K", limit: int = 5) -> List[Dict]:
    """
    获取 SEC filings 列表。
    
    参数:
        entity: 标准化实体（美股，需含 cik 或 symbol）
        form: 报表类型，如 "10-K", "10-Q"
        limit: 返回最近的数量
    
    返回:
        list of dict，每个 dict 包含:
        - accession_number: 唯一标识
        - filing_date: 提交日期
        - form: 报表类型
        - primary_document: 主文档 URL
        - period_of_report: 报告期
    """
    try:
        company = _get_company(entity)
        filings = company.get_filings(form=form).head(limit)
        results = []
        for f in filings:
            results.append({
                "accession_number": f.accession_number,
                "filing_date": str(f.filing_date),
                "form": f.form,
                "primary_document": f.primary_document,
                "period_of_report": str(f.period_of_report) if hasattr(f, 'period_of_report') else ""
            })
        return results
    except Exception as e:
        logger.error(f"获取 SEC filings 失败: {e}")
        raise SECFinancialsError(f"获取 filings 失败: {e}")

def _fetch_us_financials_live(entity: Entity, filing_accession: Optional[str] = None) -> Dict:
    """从 SEC companyfacts 提取最新 10-K（或指定 accession）的三张报表。

    为什么不再用 edgartools 的 statement：
      - 它的 values 是多列字典，旧代码取 next(iter(...)) 会拿到旧列（GOOGL 拿到 FY2023）；
      - 它可能抓到 10-K/A，修订版没有完整 XBRL（TSLA）；
      - companyfacts 是按 accession/期间精确取数，和“同期真值”口径一致。

    返回的 label 键保持 normalize_financial_data(source="us") 能识别的名称，
    例如 Revenues / GrossProfit / OperatingIncomeLoss / NetIncomeLoss / Assets。
    """
    try:
        cik = str(entity.cik or "").strip()
        if not cik:
            raise SECFinancialsError("美股实体缺少 CIK，无法读取 companyfacts")
        cik10 = cik.zfill(10)
        ua = settings.sec_user_agent or "research-agent contact@example.com"

        def _get_json(url: str) -> Dict:
            resp = requests.get(url, headers={"User-Agent": ua}, timeout=30)
            resp.raise_for_status()
            return resp.json()

        def _num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        # 1) 找最新 10-K（排除 10-K/A），或指定 accession
        subs = _get_json(f"https://data.sec.gov/submissions/CIK{cik10}.json")
        recent = subs.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accns = recent.get("accessionNumber", [])
        periods = recent.get("reportDate", [])
        fileds = recent.get("filingDate", [])

        chosen = None
        for i, form in enumerate(forms):
            if form != "10-K":
                continue
            if filing_accession and accns[i] != filing_accession:
                continue
            chosen = {
                "accession": accns[i],
                "period": periods[i] or "",
                "filed": fileds[i] or "",
            }
            break
        if chosen is None:
            # 指定 accession 可能不在 recent 列表里：回退到最新 10-K
            for i, form in enumerate(forms):
                if form == "10-K":
                    chosen = {"accession": accns[i], "period": periods[i] or "", "filed": fileds[i] or ""}
                    break
        if chosen is None:
            raise SECFinancialsError("没有找到 10-K 文件")

        accn = chosen["accession"]
        period = chosen["period"][:10]

        # 2) companyfacts 取数
        facts = _get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json")
        gaap = facts.get("facts", {}).get("us-gaap", {})

        def _pick(tags: List[str], flow: bool):
            for tag in tags:
                node = gaap.get(tag)
                if not node:
                    continue
                for _unit, entries in node.get("units", {}).items():
                    cand = [e for e in entries if e.get("accn") == accn]
                    if not cand:
                        cand = [e for e in entries if e.get("form") == "10-K" and e.get("end") == period]
                    if flow:
                        cand = [e for e in cand if e.get("start")]
                    else:
                        cand = [e for e in cand if not e.get("start")]
                    if not cand:
                        continue
                    cand.sort(key=lambda e: e.get("end", ""))
                    return _num(cand[-1].get("val")), tag
            return None, ""

        revenue, _ = _pick([
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "Revenues", "SalesRevenueNet",
        ], flow=True)
        gross_profit, _ = _pick(["GrossProfit"], flow=True)
        cost, _ = _pick(["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"], flow=True)
        if gross_profit is None and revenue is not None and cost is not None:
            gross_profit = revenue - cost
        operating_income, _ = _pick(["OperatingIncomeLoss"], flow=True)
        net_income, _ = _pick(["NetIncomeLoss", "ProfitLoss"], flow=True)
        eps, _ = _pick(["EarningsPerShareBasic", "EarningsPerShareDiluted"], flow=True)
        ocf, _ = _pick(["NetCashProvidedByUsedInOperatingActivities"], flow=True)
        capex, _ = _pick(["PaymentsToAcquirePropertyPlantAndEquipment"], flow=True)

        assets, _ = _pick(["Assets"], flow=False)
        liabilities, _ = _pick(["Liabilities"], flow=False)
        equity, _ = _pick(["StockholdersEquity",
                           "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"], flow=False)
        current_assets, _ = _pick(["AssetsCurrent"], flow=False)
        current_liabilities, _ = _pick(["LiabilitiesCurrent"], flow=False)
        inventory, _ = _pick(["InventoryNet"], flow=False)
        receivables, _ = _pick(["AccountsReceivableNetCurrent"], flow=False)
        cash, _ = _pick(["CashAndCashEquivalentsAtCarryingValue"], flow=False)
        st_invest, _ = _pick(["ShortTermInvestments"], flow=False)
        lt_debt, _ = _pick(["LongTermDebtNoncurrent", "LongTermDebt"], flow=False)
        st_debt, _ = _pick(["ShortTermBorrowings", "DebtCurrent"], flow=False)
        shares, _ = _pick(["CommonStockSharesOutstanding",
                           "WeightedAverageNumberOfSharesOutstandingBasic"], flow=False)

        income_stmt = {
            "Revenues": revenue,
            "GrossProfit": gross_profit,
            "OperatingIncomeLoss": operating_income,
            "NetIncomeLoss": net_income,
            "EarningsPerShareBasic": eps,
            "CostOfRevenue": cost,
        }
        balance_sheet = {
            "Assets": assets,
            "Liabilities": liabilities,
            "StockholdersEquity": equity,
            "AssetsCurrent": current_assets,
            "LiabilitiesCurrent": current_liabilities,
            "InventoryNet": inventory,
            "AccountsReceivableNetCurrent": receivables,
            "CashAndCashEquivalentsAtCarryingValue": cash,
            "ShortTermInvestments": st_invest,
            "LongTermDebtNoncurrent": lt_debt,
            "ShortTermBorrowings": st_debt,
            "CommonStockSharesOutstanding": shares,
        }
        cash_flow = {
            "NetCashProvidedByUsedInOperatingActivities": ocf,
            "PaymentsToAcquirePropertyPlantAndEquipment": capex,
        }
        # 去掉 None，避免报告层把空字段当 0
        income_stmt = {k: v for k, v in income_stmt.items() if v is not None}
        balance_sheet = {k: v for k, v in balance_sheet.items() if v is not None}
        cash_flow = {k: v for k, v in cash_flow.items() if v is not None}

        if revenue is None and net_income is None:
            raise SECFinancialsError(f"companyfacts 缺少核心字段: {entity.symbol}")

        return {
            "balance_sheet": balance_sheet,
            "income_statement": income_stmt,
            "cash_flow": cash_flow,
            "filing_info": {
                "accession_number": accn,
                "filing_date": chosen["filed"],
                "period_of_report": period,
            },
        }
    except SECFinancialsError:
        raise
    except Exception as e:
        logger.error(f"提取财务数据失败: {e}")
        raise SECFinancialsError(f"提取财务数据失败: {e}")


@track_tool("extract_financial_data")
def extract_financial_data(entity: Entity, filing_accession: Optional[str] = None) -> Dict:
    """US 财务：快照优先（30 天），实时失败回退旧快照。"""
    key = f"{entity.symbol}:{filing_accession}" if filing_accession else entity.symbol
    return get_or_fetch(
        "financials_us", key,
        lambda: _fetch_us_financials_live(entity, filing_accession),
        source="sec_companyfacts",
        prefer_snapshot=True, max_age_days=30,
        validate=lambda p: isinstance(p, dict)
        and (bool(p.get("income_statement")) or bool(p.get("balance_sheet"))),
    )