# backend/app/tools/company_profile.py
import logging
import os
from typing import Dict, Optional
from curl_cffi import requests as cffi_requests
from app.Schemas.entity import Entity
from app.core.config import settings
from app.core.metrics import track_tool
from app.services.snapshot_store import get_or_fetch

logger = logging.getLogger(__name__)

class CompanyProfileError(Exception):
    """公司概况获取异常"""
    pass

def _fetch_company_profile_live(entity: Entity) -> Dict:
    """实时获取公司概况（US SEC/Yahoo；CN 新浪；HK 东财）。"""
    if entity.market == "US":
        # 尝试从 SEC 获取基本信息
        try:
            profile = _get_sec_profile(entity)
            if profile:
                return profile
        except Exception as e:
            logger.warning(f"SEC 概况获取失败，回退到 yfinance: {e}")
        # 回退到 yfinance
        return _get_yfinance_profile(entity)
    elif entity.market == "CN":
        from app.tools.cn_hk_financials import get_cn_company_profile
        return get_cn_company_profile(entity)
    elif entity.market == "HK":
        from app.tools.cn_hk_financials import get_hk_company_profile
        return get_hk_company_profile(entity)
    else:
        raise CompanyProfileError(f"暂不支持市场: {entity.market}")


@track_tool("get_company_profile")
def get_company_profile(entity: Entity) -> Dict:
    """获取公司概况：快照优先（30 天），实时失败回退旧快照。"""
    return get_or_fetch(
        "profiles", f"{entity.market}:{entity.symbol}",
        lambda: _fetch_company_profile_live(entity),
        source="sec_or_sina_or_eastmoney",
        prefer_snapshot=True, max_age_days=30,
        validate=lambda p: isinstance(p, dict) and sum(
            1 for k in ("name", "ticker", "industry", "description", "website", "employees", "country")
            if p.get(k) not in (None, "", [], {})
        ) >= 3,
    )

def _get_sec_profile(entity: Entity) -> Optional[Dict]:
    """从 SEC EDGAR submissions API 获取公司基本信息"""
    if not entity.cik:
        return None
    url = f"https://data.sec.gov/submissions/CIK{entity.cik}.json"
    headers = {"User-Agent": settings.sec_user_agent}
    try:
        proxy = settings.https_proxy or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        proxies = {"http": proxy, "https": proxy} if proxy else None
        response = cffi_requests.get(
            url, headers=headers,
            impersonate="chrome120", proxies=proxies, timeout=15
        )
        response.raise_for_status()
        data = response.json()
        # 公司名称从 entity 中已有
        # 行业信息从 SIC 描述提取
        sic_description = data.get("sicDescription", "")
        # 地址
        addresses = data.get("addresses", {})
        business_address = addresses.get("business", {})
        mailing_address = addresses.get("mailing", {})
        # 使用 business 优先
        address = business_address or mailing_address or {}
        
        profile = {
            "name": entity.company_name,
            "ticker": entity.symbol,
            "exchange": entity.exchange or "",
            "industry": sic_description,
            "sector": "",  # SEC 没有 sector，留空
            "website": "",  # SEC 没有 website
            "description": "",  # SEC 没有 description
            "employees": None,
            "address": address.get("street1", ""),
            "city": address.get("city", ""),
            "state": address.get("stateOrCountry", ""),
            "country": address.get("stateOrCountry", ""),
            "phone": business_address.get("phone", ""),
            "ceo": None,
            "ipo_date": None,
            "sic_code": data.get("sic", ""),
            "sic_description": sic_description,
        }
        return profile
    except Exception as e:
        logger.warning(f"SEC profile 请求失败: {e}")
        return None

def _get_yfinance_profile(entity: Entity) -> Dict:
    """使用 curl_cffi 直连 Yahoo Finance API 获取公司概况，走代理"""
    ticker_symbol = entity.symbol
    proxy = settings.https_proxy or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    
    try:
        # 使用 Yahoo Finance chart API（比 quoteSummary 限流更宽松）
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker_symbol}?interval=1d&range=5d"
        response = cffi_requests.get(
            url, impersonate="chrome120", proxies=proxies, timeout=15
        )
        response.raise_for_status()
        data = response.json()
        result = data["chart"]["result"][0]
        meta = result["meta"]
        
        profile = {
            "name": meta.get("longName", entity.company_name),
            "ticker": entity.symbol,
            "exchange": meta.get("exchangeName", entity.exchange or ""),
            "industry": "",
            "sector": "",
            "website": "",
            "description": "",
            "employees": None,
            "address": "",
            "city": "",
            "state": "",
            "country": "",
            "phone": "",
            "ceo": None,
            "ipo_date": None,
            "sic_code": "",
            "sic_description": "",
        }
        return profile
    except Exception as e:
        logger.error(f"yfinance profile 失败: {e}")
        raise CompanyProfileError(f"无法获取公司概况: {e}")