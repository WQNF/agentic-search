"""工具调用 / LLM token / 节点耗时埋点。

设计原则：
  - 工具成功率按“语义成功”判定，而不是“没抛异常”。例如 akshare 工具吞掉异常返回 {}，
    必须判失败；
  - 记录 provider，而不是工具名。当前 .env 用 serper / eastmoney，Tavily / yfinance /
    Finnhub 可能是 0 次调用（N/A），不能报成 0%；
  - LLM token 从 callback 的真实 usage 读取，不用估算。
"""
from __future__ import annotations

import functools
import math
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional

import app.graph.nodes as nodes
from app.core.config import settings

from .config import DEFAULT_PRICES

CURRENT: Optional["Collector"] = None


def _is_num(x) -> bool:
    try:
        v = float(x)
        return not (math.isnan(v) or math.isinf(v))
    except (TypeError, ValueError):
        return False


def _summary(obj: Any) -> str:
    try:
        s = str(obj)
    except Exception:
        s = "<unprintable>"
    return s[:300]


def _semantic_ok(tool: str, result: Any) -> bool:
    if result is None:
        return False
    if tool == "search_web":
        return isinstance(result, list) and any(
            isinstance(x, dict) and (x.get("title") or x.get("url")) for x in result
        )
    if tool == "get_company_profile":
        if not isinstance(result, dict):
            return False
        core = ["name", "ticker", "industry", "description", "website", "employees", "country"]
        return sum(1 for k in core if result.get(k) not in (None, "", [], {})) >= 3
    if tool == "get_stock_quote":
        return isinstance(result, dict) and _is_num(result.get("price")) and float(result["price"]) > 0
    if tool in ("extract_financial_data",):
        if not isinstance(result, dict):
            return False
        parts = [result.get(k) for k in ("income_statement", "balance_sheet", "cash_flow")]
        return sum(1 for p in parts if isinstance(p, dict) and len(p) >= 3) >= 2
    if tool in ("get_cn_financial_abstract", "get_hk_financial_abstract"):
        if not isinstance(result, dict) or len(result) < 3:
            return False
        return any(_is_num(v) for k, v in result.items() if k not in ("fiscal_year", "company"))
    return True


_TOOL_PROVIDER = {
    "search_web": lambda: f"search:{settings.search_provider}",
    "get_company_profile": lambda: "profile:sec_or_yahoo",
    "get_stock_quote": lambda: f"quote:{settings.stock_quote_provider}",
    "extract_financial_data": lambda: "sec_edgar",
    "get_cn_financial_abstract": lambda: "akshare_cn",
    "get_hk_financial_abstract": lambda: "akshare_hk",
}

_PROVIDER_METRIC = {
    "search:tavily": "tavily",
    "search:serper": "serper",
    "sec_edgar": "sec_edgar",
    "akshare_cn": "akshare",
    "akshare_hk": "akshare",
    "quote:yfinance": "yfinance",
    "quote:finnhub": "finnhub",
    "quote:eastmoney": "eastmoney",
    "quote:akshare": "akshare",
    "profile:sec_or_yahoo": "company_profile",
}


class Collector:
    def __init__(self, run_id: str = ""):
        self.run_id = run_id
        self.tool_calls: List[Dict] = []
        self.llm_calls: List[Dict] = []
        self.errors: List[Dict] = []
        self._orig: Dict[str, Callable] = {}
        self._llm_start: Dict[str, float] = {}

    # -- 工具包装 -------------------------------------------------------
    def _wrap(self, name: str, fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            provider = _TOOL_PROVIDER.get(name, lambda: "unknown")()
            status, err_type, err, result = "success", "", "", None
            try:
                result = fn(*args, **kwargs)
                return result
            except Exception as e:
                status = "error"
                err_type = type(e).__name__
                err = _summary(e)
                raise
            finally:
                dur = (time.perf_counter() - start) * 1000
                ok = status == "success" and _semantic_ok(name, result)
                stale = bool(result.get("_data_meta", {}).get("stale")) if isinstance(result, dict) else False
                self.tool_calls.append({
                    "run_id": self.run_id,
                    "tool": name,
                    "provider": provider,
                    "metric_provider": _PROVIDER_METRIC.get(provider, provider),
                    "status": status,
                    "semantic_ok": ok,
                    "stale": stale,
                    "error_type": err_type,
                    "error": err,
                    "duration_ms": round(dur, 1),
                    "result_summary": _summary(result),
                })
        return wrapper

    def patch_tools(self):
        for name in list(_TOOL_PROVIDER):
            fn = getattr(nodes, name, None)
            if fn is None:
                continue
            self._orig[name] = fn
            setattr(nodes, name, self._wrap(name, fn))

    def restore_tools(self):
        for name, fn in self._orig.items():
            setattr(nodes, name, fn)
        self._orig.clear()

    # -- LLM callback ---------------------------------------------------
    def make_handler(self):
        collector = self
        from langchain_core.callbacks import BaseCallbackHandler

        class UsageHandler(BaseCallbackHandler):
            def on_llm_start(self, serialized, prompts, **kwargs):
                collector._llm_start[str(id(serialized)) + str(time.perf_counter())] = time.perf_counter()

            def on_llm_end(self, response, **kwargs):
                usage = {}
                try:
                    llm_output = getattr(response, "llm_output", None) or {}
                    usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
                except Exception:
                    usage = {}
                if not usage:
                    try:
                        msg = response.generations[0][0].message
                        um = getattr(msg, "usage_metadata", None) or {}
                        usage = {
                            "prompt_tokens": um.get("input_tokens"),
                            "completion_tokens": um.get("output_tokens"),
                            "total_tokens": um.get("total_tokens"),
                            "prompt_tokens_details": {"cached_tokens": (um.get("input_token_details") or {}).get("cache_read")},
                            "completion_tokens_details": {"reasoning_tokens": (um.get("output_token_details") or {}).get("reasoning")},
                        }
                    except Exception:
                        usage = {}
                model = ""
                try:
                    model = (getattr(response, "llm_output", None) or {}).get("model_name", "")
                except Exception:
                    pass
                rec = {
                    "run_id": collector.run_id,
                    "model": model,
                    "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                    "completion_tokens": int(usage.get("completion_tokens") or 0),
                    "cached_tokens": int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
                    "reasoning_tokens": int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0),
                }
                rec["total_tokens"] = rec["prompt_tokens"] + rec["completion_tokens"]
                collector.llm_calls.append(rec)

            def on_llm_error(self, error, **kwargs):
                collector.errors.append({"run_id": collector.run_id, "kind": "llm_error",
                                         "error_type": type(error).__name__, "error": _summary(error)})

        return UsageHandler()


@contextmanager
def collecting(run_id: str):
    global CURRENT
    c = Collector(run_id)
    c.patch_tools()
    CURRENT = c
    try:
        yield c
    finally:
        c.restore_tools()
        CURRENT = None


def estimate_cost(llm_calls: List[Dict], prices: Dict = DEFAULT_PRICES) -> Dict:
    """按真实 token 计费；价格缺失时返回 tokens 和 None，不编造成本。"""
    pin = prices.get("input_per_1m")
    pout = prices.get("output_per_1m")
    pcache = prices.get("cached_input_per_1m")
    total_in = sum(c["prompt_tokens"] for c in llm_calls)
    total_out = sum(c["completion_tokens"] for c in llm_calls)
    total_cache = sum(c.get("cached_tokens", 0) for c in llm_calls)
    if pin is None or pout is None:
        return {
            "currency": prices.get("currency", "CNY"),
            "input_tokens": total_in,
            "output_tokens": total_out,
            "cached_tokens": total_cache,
            "cost": None,
            "note": "prices.json 未配置单价，只报告 token",
        }
    cache_price = pcache if pcache is not None else pin
    cost = (
        (total_in - total_cache) / 1_000_000 * pin
        + total_cache / 1_000_000 * cache_price
        + total_out / 1_000_000 * pout
    )
    return {
        "currency": prices.get("currency", "CNY"),
        "input_tokens": total_in,
        "output_tokens": total_out,
        "cached_tokens": total_cache,
        "cost": cost,
    }
