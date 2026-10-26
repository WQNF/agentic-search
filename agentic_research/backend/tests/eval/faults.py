"""10 类故障注入与降级契约。

与旧测试的关键区别：
  - 不再把“跑完但 final_status=failed”当作降级成功；
  - 每类故障有明确的契约（不崩溃 / 报告存在 / 明确说明缺失 / 不编造财务数字）；
  - 修对了 patch 目标：nodes 里是 `from app.core.llm import get_llm`，必须 patch
    app.graph.nodes.get_llm，而不是 app.core.llm.get_llm。
"""
from __future__ import annotations

import json
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

from .config import FAULT_RUNS_DIR, RESULTS_DIR, companies_for
from .runner import run_one
from .text import core_sections_non_empty

JSONL = RESULTS_DIR / "faults.jsonl"

_EXPLICIT = ("无法", "失败", "缺失", "未能", "不支持", "异常", "不可用", "超时")


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeAgent:
    def __init__(self, behavior):
        self._behavior = behavior

    async def ainvoke(self, *a, **kw):
        return self._behavior()


def _fault_patches(fault_id: str):
    """返回 (ExitStack 已 enter, 说明)。调用方负责 close。"""
    import app.graph.nodes as nodes

    stack = ExitStack()

    if fault_id == "entity_timeout":
        stack.enter_context(patch.object(nodes.entity_resolver, "resolve",
                                         MagicMock(side_effect=TimeoutError("entity resolution timeout"))))
    elif fault_id == "entity_invalid":
        stack.enter_context(patch.object(nodes.entity_resolver, "resolve",
                                         MagicMock(side_effect=ValueError("invalid entity payload"))))
    elif fault_id == "search_down":
        stack.enter_context(patch.object(nodes, "search_web",
                                         MagicMock(side_effect=RuntimeError("search provider down"))))
    elif fault_id == "search_rate_limit":
        state = {"n": 0}

        def _rate_limited(*a, **kw):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("search rate limit exceeded")
            return [{"title": "fallback", "url": "https://example.com", "content": "fallback", "score": 0.1}]

        stack.enter_context(patch.object(nodes, "search_web", _rate_limited))
    elif fault_id == "profile_down":
        stack.enter_context(patch.object(nodes, "get_company_profile",
                                         MagicMock(side_effect=RuntimeError("profile API down"))))
    elif fault_id == "sec_down":
        stack.enter_context(patch.object(nodes, "extract_financial_data",
                                         MagicMock(side_effect=RuntimeError("SEC EDGAR down"))))
    elif fault_id == "akshare_down":
        stack.enter_context(patch.object(nodes, "get_cn_financial_abstract",
                                         MagicMock(side_effect=RuntimeError("akshare down"))))
        stack.enter_context(patch.object(nodes, "get_hk_financial_abstract",
                                         MagicMock(side_effect=RuntimeError("akshare down"))))
    elif fault_id == "quote_down":
        stack.enter_context(patch.object(nodes, "get_stock_quote",
                                         MagicMock(side_effect=RuntimeError("quote API down"))))
    elif fault_id == "llm_timeout":
        def _raise(*a, **kw):
            raise TimeoutError("LLM timeout")

        from langchain_core.runnables import RunnableLambda
        stack.enter_context(patch.object(nodes, "get_llm", lambda *a, **kw: RunnableLambda(_raise)))
        stack.enter_context(patch.object(nodes, "create_react_agent",
                                         lambda *a, **kw: _FakeAgent(_raise)))
    elif fault_id == "llm_invalid":
        stack.enter_context(patch.object(
            nodes, "create_react_agent",
            lambda *a, **kw: _FakeAgent(lambda: {"messages": [_FakeMessage("not a json at all ~~~")]}),
        ))
    else:
        raise ValueError(f"unknown fault {fault_id}")
    return stack


def contract(fault_id: str, rec: Dict) -> Dict:
    report = ""
    # run_one 已把报告写盘
    if rec.get("run_id"):
        p = Path(RESULTS_DIR) / "runs" / rec["run_id"] / "report.md"
        if p.exists():
            report = p.read_text(encoding="utf-8")
    sections_ok, _ = core_sections_non_empty(report)
    explicit = any(k in report for k in _EXPLICIT)
    no_crash = rec.get("error") in ("", None)
    report_exists = len(report.strip()) >= 100
    financial_not_success = rec.get("financial_status") != "success"
    allowed_final = rec.get("final_status") in ("success", "partial_success", "failed")

    if fault_id in ("entity_timeout", "entity_invalid"):
        checks = {
            "no_crash": no_crash,
            "report_exists": report_exists,
            "entity_failed": rec.get("entity_status") == "failed",
            "explicit": explicit,
            "no_financial_fabrication": financial_not_success,
        }
    elif fault_id in ("sec_down", "akshare_down"):
        checks = {
            "no_crash": no_crash,
            "report_exists": report_exists,
            "sections_ok": sections_ok,
            "explicit": explicit,
            "financial_not_success": financial_not_success,
        }
    elif fault_id in ("llm_timeout", "llm_invalid"):
        checks = {
            "no_crash": no_crash,
            "report_exists": report_exists,
            "sections_ok": sections_ok,
            "explicit": explicit,
            "final_allowed": allowed_final,
        }
    else:  # search/profile/quote
        checks = {
            "no_crash": no_crash,
            "report_exists": report_exists,
            "sections_ok": sections_ok,
            "explicit": explicit,
            "final_allowed": allowed_final,
        }
    return {
        "fault": fault_id,
        "checks": checks,
        "passed": all(checks.values()),
        "failed_checks": [k for k, v in checks.items() if not v],
        "sections_ok": sections_ok,
        "explicit": explicit,
        "report_chars": len(report),
    }


def load_done() -> Dict[str, Dict]:
    if not JSONL.exists():
        return {}
    out = {}
    for line in JSONL.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        out[r["fault"]] = r
    return out


async def run_all(force: bool = False, only: Optional[List[str]] = None) -> List[Dict]:
    from app.graph.workflow import build_graph

    FAULT_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    done = load_done()
    out = []
    scenarios = [
        ("entity_timeout", "AAPL"),
        ("entity_invalid", "AAPL"),
        ("search_down", "AAPL"),
        ("search_rate_limit", "AAPL"),
        ("profile_down", "AAPL"),
        ("sec_down", "AAPL"),
        ("akshare_down", "600519.SH"),
        ("quote_down", "AAPL"),
        ("llm_timeout", "AAPL"),
        ("llm_invalid", "AAPL"),
    ]
    for fid, symbol in scenarios:
        if only and fid not in only:
            continue
        if not force and fid in done:
            print(f"skip fault {fid}")
            out.append(done[fid])
            continue
        company = next(c for c in companies_for("core") if c["symbol"] == symbol)
        print(f"fault {fid} on {symbol} ...", flush=True)
        stack = _fault_patches(fid)
        try:
            # 每个故障单独构图，避免上一次 patch 的缓存
            graph = build_graph(checkpointer=None)
            rec = await run_one(graph, company, mode=f"fault:{fid}")
        finally:
            stack.close()
        c = contract(fid, rec)
        row = {"fault": fid, "symbol": symbol, "run": rec, "contract": c}
        with JSONL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        out.append(row)
        print(f"    {c['passed']} failed={c['failed_checks']} status={rec.get('final_status')} "
              f"report={c['report_chars']}")
    return out
