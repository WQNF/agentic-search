# backend/app/tools/search.py
import logging
import os
from typing import List, Dict, Optional
from curl_cffi import requests as cffi_requests
from tavily import TavilyClient
from app.core.config import settings
from app.core.metrics import track_tool

logger = logging.getLogger(__name__)

class SearchError(Exception):
    """搜索工具异常"""
    pass

@track_tool("search_web")
def search_web(query: str, max_results: int = 5) -> List[Dict]:
    """
    搜索网页，返回结构化结果列表。
    每个结果包含：title, url, content, score
    """
    if settings.search_provider == "tavily":
        return _search_tavily(query, max_results)
    elif settings.search_provider == "serper":
        return _search_serper(query, max_results)
    else:
        raise SearchError(f"不支持的搜索提供商: {settings.search_provider}")

def _search_tavily(query: str, max_results: int) -> List[Dict]:
    if not settings.tavily_api_key:
        raise SearchError("Tavily API key 未配置")
    try:
        client = TavilyClient(api_key=settings.tavily_api_key)
        response = client.search(query=query, max_results=max_results)
        results = []
        for item in response.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", ""),
                "score": item.get("score", 0.0)
            })
        return results
    except Exception as e:
        logger.error(f"Tavily 搜索失败: {e}")
        raise SearchError(f"Tavily 搜索失败: {e}")

def _search_serper(query: str, max_results: int) -> List[Dict]:
    if not settings.serper_api_key:
        raise SearchError("Serper API key 未配置")
    url = "https://google.serper.dev/search"
    headers = {
        "X-API-KEY": settings.serper_api_key,
        "Content-Type": "application/json"
    }
    payload = {
        "q": query,
        "num": max_results
    }
    try:
        proxy = settings.https_proxy or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        proxies = {"http": proxy, "https": proxy} if proxy else None
        response = cffi_requests.post(
            url, json=payload, headers=headers,
            impersonate="chrome120", proxies=proxies, timeout=15
        )
        response.raise_for_status()
        data = response.json()
        results = []
        for item in data.get("organic", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "content": item.get("snippet", ""),
                "score": None  # Serper 不提供 score
            })
        return results
    except Exception as e:
        logger.error(f"Serper 搜索失败: {e}")
        raise SearchError(f"Serper 搜索失败: {e}")