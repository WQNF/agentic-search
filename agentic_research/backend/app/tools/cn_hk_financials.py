#A股/港股数据获取工具 cn_hk_financials.py

import logging
import math
from typing import Dict, Optional
import requests
import akshare as ak
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from app.Schemas.entity import Entity
from app.core.metrics import track_tool
from app.services.snapshot_store import get_or_fetch

logger = logging.getLogger(__name__)


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x

class CNHKDataError(Exception):
    """A股/港股数据获取异常"""
    pass

# 通用重试装饰器
def retry_akshare(func):
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError, KeyError, ValueError)),
        reraise=True
    )(func)

SINA_QUOTE_URL = "https://hq.sinajs.cn/list="
SINA_HEADERS = {"Referer": "https://finance.sina.com.cn"}


def _sina_quote(sina_code: str) -> Optional[list]:
    """调用新浪行情接口，返回解析后的字段列表"""
    try:
        resp = requests.get(f"{SINA_QUOTE_URL}{sina_code}", headers=SINA_HEADERS, timeout=10)
        if resp.status_code != 200 or not resp.text:
            return None
        text = resp.text
        if '"' not in text:
            return None
        data = text.split('"')[1]
        return data.split(',') if data else None
    except Exception:
        return None


@track_tool("get_cn_company_profile")
def get_cn_company_profile(entity: Entity) -> Dict:
    """获取A股公司概况（通过新浪财经）"""
    if entity.market != "CN":
        raise CNHKDataError("仅支持A股")
    code = entity.symbol.split('.')[0]
    if entity.symbol.endswith('.SH'):
        sina_code = f"sh{code}"
    else:
        sina_code = f"sz{code}"
    fields = _sina_quote(sina_code)
    if not fields:
        raise CNHKDataError(f"获取A股公司概况失败：无数据")
    return {
        "name": fields[0] if len(fields) > 0 else entity.company_name,
        "ticker": entity.symbol,
        "exchange": entity.exchange,
        "industry": "",
        "sector": "",
        "website": "",
        "description": "",
        "employees": None,
        "address": "",
        "city": "",
        "state": "",
        "country": "中国",
        "phone": "",
        "ceo": None,
        "ipo_date": "",
        "sic_code": "",
        "sic_description": "",
    }

@track_tool("get_hk_company_profile")
@retry_akshare
def get_hk_company_profile(entity: Entity) -> Dict:
    """获取港股公司概况"""
    if entity.market != "HK":
        raise CNHKDataError("仅支持港股")
    code = entity.symbol.split('.')[0].zfill(5)
    try:
        df = ak.stock_hk_company_profile_em(symbol=code)
        # df 可能包含多个字段
        info = df.to_dict(orient='records')[0] if not df.empty else {}
        return {
            "name": entity.company_name,
            "ticker": entity.symbol,
            "exchange": "HKEX",
            "industry": info.get("行业", ""),
            "sector": "",
            "website": info.get("公司网址", ""),
            "description": info.get("公司介绍", ""),
            "employees": None,
            "address": info.get("办公地址", ""),
            "city": "",
            "state": "",
            "country": "中国香港",
            "phone": info.get("联系电话", ""),
            "ceo": None,
            "ipo_date": info.get("上市日期", ""),
            "sic_code": "",
            "sic_description": "",
        }
    except Exception as e:
        logger.error(f"获取港股公司概况失败: {e}")
        raise CNHKDataError(f"获取港股公司概况失败: {e}")

def _fetch_cn_financial_ths(entity: Entity) -> Dict:
    """A 股财务摘要（同花顺）。"""
    code = entity.symbol.split('.')[0]
    df = ak.stock_financial_abstract(symbol=code)
    if df.empty:
        raise CNHKDataError(f"A股财务摘要为空: {code}")
    # 日期列（排除 选项、指标），第一列是最新一期
    date_cols = [c for c in df.columns if c not in ('选项', '指标')]
    if not date_cols:
        raise CNHKDataError(f"A股财务摘要缺少日期列: {code}")
    latest_col = date_cols[0]
    result = {"period_end": str(latest_col), "company": entity.company_name, "_source_detail": "akshare_ths"}
    for _, row in df.iterrows():
        metric_name = str(row['指标'])
        val = row[latest_col]
        try:
            result[metric_name] = float(val)
        except (ValueError, TypeError):
            result[metric_name] = val
    return result


def _fetch_cn_financial_sina(entity: Entity) -> Dict:
    """A 股财务摘要（新浪财务报表），作为同花顺失败时的独立备源。"""
    code = entity.symbol.split('.')[0]
    stock = f"sh{code}" if entity.symbol.endswith(".SH") else f"sz{code}"
    inc = ak.stock_financial_report_sina(stock=stock, symbol="利润表")
    bal = ak.stock_financial_report_sina(stock=stock, symbol="资产负债表")
    if inc is None or inc.empty or bal is None or bal.empty:
        raise CNHKDataError(f"新浪A股报表为空: {code}")

    def latest(df):
        if "报告日" not in df.columns:
            raise CNHKDataError(f"新浪A股报表缺少报告日: {code}")
        return df.sort_values("报告日").iloc[-1]

    ri, rb = latest(inc), latest(bal)

    def cell(row, names):
        for n in names:
            if n in row.index:
                v = _num(row[n])
                if v is not None:
                    return v
        return None

    revenue = cell(ri, ["营业总收入", "营业收入"])
    cost = cell(ri, ["营业成本"])
    net_income = cell(ri, ["归属于母公司所有者的净利润", "归属于母公司股东的净利润", "净利润"])
    operating_income = cell(ri, ["营业利润"])
    total_assets = cell(rb, ["资产总计", "总资产"])
    total_liabilities = cell(rb, ["负债合计", "总负债"])
    total_equity = cell(rb, ["归属于母公司股东权益合计", "所有者权益(或股东权益)合计"])
    ocf = cell(ri, ["经营活动产生的现金流量净额", "经营现金流量净额"])
    eps = cell(ri, ["基本每股收益"])

    result = {"period_end": str(ri["报告日"]), "company": entity.company_name, "_source_detail": "sina"}
    if revenue is not None:
        result["营业总收入"] = revenue
    if revenue is not None and cost is not None:
        result["毛利"] = revenue - cost
    if net_income is not None:
        result["归母净利润"] = net_income
    if operating_income is not None:
        result["营业利润"] = operating_income
    if total_assets is not None:
        result["总资产"] = total_assets
    if total_liabilities is not None:
        result["负债合计"] = total_liabilities
    if total_equity is not None:
        result["归属于母公司股东权益合计"] = total_equity
    if ocf is not None:
        result["经营活动现金流量净额"] = ocf
    if eps is not None:
        result["基本每股收益"] = eps
    return result


def _fetch_cn_financial_live(entity: Entity) -> Dict:
    """实时链路：同花顺 → 新浪 → 抛错（由 snapshot_store 回退旧快照）。"""
    errors = []
    for name, fn in (("ths", _fetch_cn_financial_ths), ("sina", _fetch_cn_financial_sina)):
        try:
            return fn(entity)
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {e}")
            logger.warning(f"A股财务实时源 {name} 失败: {e}")
    raise CNHKDataError(f"A股财务实时获取失败（{' | '.join(errors)}）")


@track_tool("get_cn_financial_abstract")
@retry_akshare
def get_cn_financial_abstract(entity: Entity) -> Dict:
    """A 股财务摘要：快照优先（30 天），实时失败回退旧快照。"""
    return get_or_fetch(
        "financials_cn", entity.symbol,
        lambda: _fetch_cn_financial_live(entity),
        source="akshare_ths_or_sina",
        prefer_snapshot=True, max_age_days=30,
        validate=lambda p: isinstance(p, dict) and len(p) >= 3,
    )


def _hk_latest_statement(code: str, statement: str) -> tuple:
    """取港股某张报表的最新一期，返回 ({科目: 金额}, period_end)。

    关键：东财的 FISCAL_YEAR 列是 "12-31"（不是年份），必须按 REPORT_DATE 取最新，
    不能按 FISCAL_YEAR 过滤，否则会取到 2001 年这种最旧一期。
    """
    df = ak.stock_financial_hk_report_em(stock=code, symbol=statement, indicator="年度")
    if df is None or df.empty or "REPORT_DATE" not in df.columns:
        return {}, ""
    df = df.dropna(subset=["REPORT_DATE"])
    if df.empty:
        return {}, ""
    latest = df["REPORT_DATE"].max()
    latest_rows = df[df["REPORT_DATE"] == latest]
    out = {}
    for _, row in latest_rows.iterrows():
        name = str(row.get("STD_ITEM_NAME", "")).strip()
        if name and name.lower() != "nan":
            out[name] = row.get("AMOUNT")
    return out, str(latest)[:10]


def _fetch_hk_financial_live(entity: Entity) -> Dict:
    """港股财务实时链路：利润表 + 资产负债表 + 现金流量表，统一取最新一期。"""
    code = entity.symbol.split('.')[0].zfill(5)
    result = {"company": entity.company_name}
    periods = []
    for statement in ("利润表", "资产负债表", "现金流量表"):
        try:
            data, period = _hk_latest_statement(code, statement)
        except Exception as e:
            logger.warning(f"港股 {statement} 获取失败: {e}")
            continue
        if period:
            periods.append(period)
        for k, v in data.items():
            result.setdefault(k, v)
    if not any(k in result for k in ("营业额", "毛利", "股东应占溢利")):
        raise CNHKDataError(f"港股财务摘要为空: {code}")
    if periods:
        result["period_end"] = max(periods)
    result["_source_detail"] = "akshare_eastmoney_hk"
    return result


@track_tool("get_hk_financial_abstract")
@retry_akshare
def get_hk_financial_abstract(entity: Entity) -> Dict:
    """港股财务摘要：快照优先（30 天），实时失败回退旧快照。"""
    return get_or_fetch(
        "financials_hk", entity.symbol,
        lambda: _fetch_hk_financial_live(entity),
        source="akshare_eastmoney_hk",
        prefer_snapshot=True, max_age_days=30,
        validate=lambda p: isinstance(p, dict) and len(p) >= 3,
    )

@track_tool("get_cn_quote")
@retry_akshare
def get_cn_quote(entity: Entity) -> Dict:
    """获取A股实时行情（通过新浪财经）"""
    code = entity.symbol.split('.')[0]
    if entity.symbol.endswith('.SH'):
        sina_code = f"sh{code}"
    else:
        sina_code = f"sz{code}"
    fields = _sina_quote(sina_code)
    if not fields or len(fields) < 6:
        raise CNHKDataError("无行情数据")
    return {
        "symbol": entity.symbol,
        "name": fields[0],
        "price": float(fields[3]) if len(fields) > 3 else None,
        "currency": "CNY",
        "open": float(fields[1]) if len(fields) > 1 else None,
        "previous_close": float(fields[2]) if len(fields) > 2 else None,
        "day_high": float(fields[4]) if len(fields) > 4 else None,
        "day_low": float(fields[5]) if len(fields) > 5 else None,
        "volume": float(fields[8]) if len(fields) > 8 else None,
        "change": None,
        "change_percent": None,
        "market_cap": None,
        "timestamp": None,
    }

@track_tool("get_hk_quote")
@retry_akshare
def get_hk_quote(entity: Entity) -> Dict:
    """获取港股实时行情（通过新浪财经）"""
    code = entity.symbol.split('.')[0].zfill(5)
    fields = _sina_quote(f"hk{code}")
    if not fields or len(fields) < 19:
        raise CNHKDataError("无行情数据")
    return {
        "symbol": entity.symbol,
        "name": fields[1],
        "price": float(fields[6]),
        "currency": "HKD",
        "open": float(fields[2]),
        "previous_close": float(fields[3]),
        "day_high": float(fields[4]),
        "day_low": float(fields[5]),
        "volume": float(fields[11]) if len(fields) > 11 else None,
        "change": float(fields[7]),
        "change_percent": float(fields[8]),
        "market_cap": None,
        "timestamp": f"{fields[17]} {fields[18]}" if len(fields) > 18 else None,
    }