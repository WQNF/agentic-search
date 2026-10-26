# backend/app/tools/stock_quote.py
import logging
from typing import Dict, Optional
import yfinance as yf
import httpx
from curl_cffi import requests as cffi_requests
from app.Schemas.entity import Entity
from app.core.config import settings
from app.core.metrics import track_tool
from app.services.snapshot_store import get_or_fetch

logger = logging.getLogger(__name__)

# 美股市场代码 → 东方财富 secid 前缀
_MARKET_PREFIX = {
    "NASDAQ": "105",
    "NYSE": "106",
    "AMEX": "107",
}

class StockQuoteError(Exception):
    """股票行情获取异常"""
    pass

def _fetch_stock_quote_live(entity: Entity) -> Dict:
    """实时行情：CN/HK 走新浪，US 按 provider。"""
    if entity.market == "CN" and settings.stock_quote_provider in ("eastmoney", "akshare"):
        # A 股走新浪行情（eastmoney 的 secid 构造原本只覆盖美股）
        from app.tools.cn_hk_financials import get_cn_quote
        return get_cn_quote(entity)
    if entity.market == "HK" and settings.stock_quote_provider in ("eastmoney", "akshare"):
        # 港股走新浪行情
        from app.tools.cn_hk_financials import get_hk_quote
        return get_hk_quote(entity)
    if settings.stock_quote_provider == "yfinance":
        return _get_quote_yfinance(entity)
    elif settings.stock_quote_provider == "finnhub":
        return _get_quote_finnhub(entity)
    elif settings.stock_quote_provider in ("akshare", "eastmoney"):
        return _get_quote_eastmoney(entity)
    else:
        raise StockQuoteError(f"不支持的行情提供商: {settings.stock_quote_provider}")


@track_tool("get_stock_quote")
def get_stock_quote(entity: Entity) -> Dict:
    """行情：实时优先；实时失败回退最多 1 天前的快照，并标记 stale。"""
    return get_or_fetch(
        "quotes", f"{entity.market}:{entity.symbol}",
        lambda: _fetch_stock_quote_live(entity),
        source=f"quote:{settings.stock_quote_provider}",
        prefer_snapshot=False, max_age_days=0.02,
        fallback_max_age_days=1,
        validate=lambda p: isinstance(p, dict) and p.get("price") is not None,
    )

def _get_quote_yfinance(entity: Entity) -> Dict:
    try:
        import os
        import requests
        import json
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        if proxy:
            session.proxies = {"http": proxy, "https": proxy}
        # 直接调 Yahoo Finance API，绕过 yfinance 内部 session 问题
        symbol = entity.symbol
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
        resp = session.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        result = data["chart"]["result"][0]
        meta = result["meta"]
        quotes = result["indicators"]["quote"][0]
        timestamp = result["timestamp"]
        if not timestamp:
            raise StockQuoteError(f"无行情数据: {symbol}")
        price = meta.get("regularMarketPrice")
        previous_close = meta.get("previousClose", price)
        change = price - previous_close if price and previous_close else 0
        change_percent = (change / previous_close * 100) if previous_close else 0.0
        return {
            "symbol": symbol,
            "price": price,
            "currency": meta.get("currency", "USD"),
            "market_cap": meta.get("marketCap", None),
            "previous_close": previous_close,
            "open": quotes["open"][-1] if quotes.get("open") else None,
            "day_high": meta.get("regularMarketDayHigh"),
            "day_low": meta.get("regularMarketDayLow"),
            "volume": quotes["volume"][-1] if quotes.get("volume") else None,
            "change": change,
            "change_percent": change_percent,
            "timestamp": None
        }
    except Exception as e:
        logger.error(f"yfinance 行情失败: {e}")
        raise StockQuoteError(f"无法获取行情: {e}")

def _get_quote_finnhub(entity: Entity) -> Dict:
    if not settings.finnhub_api_key:
        raise StockQuoteError("Finnhub API key 未配置")
    symbol = entity.symbol
    base_url = "https://finnhub.io/api/v1"
    try:
        # 获取报价
        quote_resp = httpx.get(f"{base_url}/quote", params={"symbol": symbol, "token": settings.finnhub_api_key})
        quote_resp.raise_for_status()
        quote = quote_resp.json()
        # 获取公司信息（市值）
        profile_resp = httpx.get(f"{base_url}/stock/profile2", params={"symbol": symbol, "token": settings.finnhub_api_key})
        profile_resp.raise_for_status()
        profile = profile_resp.json()
        return {
            "symbol": symbol,
            "price": quote.get("c", None),
            "currency": "USD",
            "market_cap": profile.get("marketCapitalization", None),
            "previous_close": quote.get("pc", None),
            "open": quote.get("o", None),
            "day_high": quote.get("h", None),
            "day_low": quote.get("l", None),
            "volume": None,  # Finnhub quote 不提供 volume
            "change": quote.get("dp", None),  # 注意 dp 是百分比变化
            "change_percent": quote.get("dp", None),
            "timestamp": None
        }
    except Exception as e:
        logger.error(f"Finnhub 行情失败: {e}")
        raise StockQuoteError(f"无法获取行情: {e}")

def _get_quote_eastmoney(entity: Entity) -> Dict:
    """通过 curl_cffi 直连东方财富 API 获取美股实时行情
    
    使用 curl_cffi 模拟浏览器 TLS 指纹，绕过东方财富 WAF。
    国内直连，无需代理。
    """
    market = (entity.market or "").upper()
    prefix = _MARKET_PREFIX.get(market, "105")  # 默认 NASDAQ
    secid = f"{prefix}.{entity.symbol}"
    
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "secid": secid,
        "fields": "f43,f44,f45,f46,f47,f48,f50,f57,f58,f60,f116,f117,f167,f168,f169,f170",
    }
    
    try:
        r = cffi_requests.get(url, params=params, impersonate="chrome120", timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("rc") != 0 or not data.get("data"):
            raise StockQuoteError(f"东方财富返回异常: {data}")
        
        d = data["data"]
        # 字段映射 & 缩放：价格类字段 ÷1000，涨跌幅 ÷100，换手率 ÷100
        price = d.get("f43", 0) / 1000
        previous_close = d.get("f60", 0) / 1000
        open_price = d.get("f46", 0) / 1000
        day_high = d.get("f44", 0) / 1000
        day_low = d.get("f45", 0) / 1000
        change = d.get("f169", 0) / 1000
        change_percent = d.get("f170", 0) / 100
        volume = d.get("f47", 0)
        market_cap = d.get("f116")
        turnover = d.get("f168", 0) / 100 if d.get("f168") else None
        
        return {
            "symbol": entity.symbol,
            "price": price,
            "currency": "USD",
            "market_cap": market_cap,
            "previous_close": previous_close,
            "open": open_price,
            "day_high": day_high,
            "day_low": day_low,
            "volume": volume,
            "change": change,
            "change_percent": change_percent,
            "timestamp": None
        }
    except StockQuoteError:
        raise
    except Exception as e:
        logger.error(f"东方财富行情失败: {e}")
        raise StockQuoteError(f"无法获取行情: {e}")