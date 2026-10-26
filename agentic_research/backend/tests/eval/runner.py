"""端到端运行器：按与 task_runner 相同的图执行，记录真实延迟/工具/token/报告。"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from .config import (
    APP_RUNS_DIR, COMPANIES, DEFAULT_PRICES, RESULTS_DIR, RUN_TIMEOUT_S, RUNS_DIR,
    companies_for, ensure_dirs,
)
from .instrument import collecting, estimate_cost
from .text import core_sections_non_empty, extract_fields

JSONL = RESULTS_DIR / "runs.jsonl"


def initial_state(query: str, run_id: str) -> Dict:
    return {
        "query": query, "run_id": run_id, "user_id": "eval",
        "entity": None, "entity_status": "pending",
        "profile": None, "news": [], "profile_status": "pending",
        "financial_raw": None, "metrics": None, "financial_status": "pending",
        "report_markdown": "", "report_status": "pending",
        "review": None, "final_status": "pending",
        "steps": [], "errors": [],
    }


def _redirect_app_runs():
    """把节点产物写到评测目录，避免污染线上 runs/。"""
    import app.utils.artifact as artifact
    import app.utils.run_logger as run_logger
    artifact.RUNS_DIR = APP_RUNS_DIR
    run_logger.RUNS_DIR = APP_RUNS_DIR
    APP_RUNS_DIR.mkdir(parents=True, exist_ok=True)


def load_done() -> Dict[str, Dict]:
    if not JSONL.exists():
        return {}
    out = {}
    for line in JSONL.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("mode") == "e2e":
            out[rec["symbol"]] = rec
    return out


def append_jsonl(rec: Dict):
    with JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


async def run_one(graph, company: Dict, mode: str = "e2e", query: Optional[str] = None,
                  timeout_s: int = RUN_TIMEOUT_S) -> Dict:
    from app.graph.workflow import build_graph  # noqa: F401
    from langchain_core.callbacks import BaseCallbackHandler  # noqa: F401

    ensure_dirs()
    _redirect_app_runs()
    query = query or company["symbol"]
    run_id = str(uuid.uuid4())
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    t0 = time.perf_counter()
    result: Dict = {}
    error = ""
    with collecting(run_id) as col:
        handler = col.make_handler()
        try:
            result = await asyncio.wait_for(
                graph.ainvoke(
                    initial_state(query, run_id),
                    config={"callbacks": [handler], "recursion_limit": 50},
                ),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            error = f"timeout>{timeout_s}s"
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
        duration_s = time.perf_counter() - t0
        tool_calls = list(col.tool_calls)
        llm_calls = list(col.llm_calls)
        collector_errors = list(col.errors)

    report = (result or {}).get("report_markdown", "") or ""
    sections_ok, sections = core_sections_non_empty(report)
    metrics = (result or {}).get("metrics") or {}
    raw = (result or {}).get("financial_raw") or {}
    period_hint = ""
    if isinstance(raw, dict):
        period_hint = str(
            (raw.get("_data_meta") or {}).get("period_end")
            or (raw.get("filing_info") or {}).get("period_of_report")
            or raw.get("period_end")
            or ""
        )
    fields = extract_fields(report, period_end=period_hint or None)

    std = {}
    period = ""
    if raw:
        try:
            from app.tools.metrics import normalize_financial_data
            market = (result or {}).get("entity", {}).get("market", company["market"])
            source = {"US": "us", "CN": "cn", "HK": "hk"}.get(market, "us")
            std = normalize_financial_data(raw, source=source)
            if isinstance(raw, dict):
                period = str((raw.get("filing_info") or {}).get("period_of_report") or "")
        except Exception as e:
            collector_errors.append({"kind": "normalize_error", "error": str(e)})

    fatal = bool(error)
    if not fatal:
        if len(report.strip()) < 100:
            fatal = True
        if (result or {}).get("entity_status") == "failed":
            fatal = True

    rec = {
        "mode": mode,
        "symbol": company["symbol"],
        "market": company["market"],
        "name": company.get("name", ""),
        "query": query,
        "run_id": run_id,
        "started_at": started,
        "duration_s": round(duration_s, 3),
        "error": error,
        "fatal": fatal,
        "entity_status": (result or {}).get("entity_status"),
        "profile_status": (result or {}).get("profile_status"),
        "financial_status": (result or {}).get("financial_status"),
        "report_status": (result or {}).get("report_status"),
        "final_status": (result or {}).get("final_status"),
        "entity": (result or {}).get("entity"),
        "period": period,
        "sections_ok": sections_ok,
        "sections": sections,
        "report_chars": len(report),
        "fields": fields,
        "metrics": metrics,
        "std": {k: v for k, v in std.items() if v is not None},
        "tool_calls": tool_calls,
        "llm_calls": llm_calls,
        "collector_errors": collector_errors,
        "cost": estimate_cost(llm_calls, DEFAULT_PRICES),
    }

    out_dir = RUNS_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    (out_dir / "result.json").write_text(
        json.dumps({k: v for k, v in rec.items() if k != "metrics"}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (out_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=str),
                                         encoding="utf-8")
    return rec


def _write_manifest(sample: str, companies: List[Dict]):
    import platform
    import sys
    from app.core.config import settings

    path = RESULTS_DIR / "manifest.json"
    path.write_text(json.dumps({
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "sample": sample,
        "symbols": [c["symbol"] for c in companies],
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "model": settings.deepseek_model,
        "search_provider": settings.search_provider,
        "quote_provider": settings.stock_quote_provider,
        "proxy": bool(settings.https_proxy),
        "edgar_dir": __import__("os").environ.get("EDGAR_LOCAL_DATA_DIR", ""),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


async def run_sample(sample: str = "core", limit: Optional[int] = None, force: bool = False,
                     mode: str = "e2e") -> List[Dict]:
    ensure_dirs()
    companies = companies_for(sample)
    if limit:
        companies = companies[:limit]
    _write_manifest(sample, companies)
    done = load_done()
    from app.graph.workflow import build_graph
    graph = build_graph(checkpointer=None)

    out = []
    for i, c in enumerate(companies, 1):
        if not force and c["symbol"] in done:
            print(f"[{i}/{len(companies)}] skip {c['symbol']} (already done)")
            out.append(done[c["symbol"]])
            continue
        print(f"[{i}/{len(companies)}] run {c['symbol']} ...", flush=True)
        rec = await run_one(graph, c, mode=mode)
        append_jsonl(rec)
        out.append(rec)
        print(
            f"    {rec['duration_s']:.1f}s status={rec['final_status']} "
            f"sections={rec['sections_ok']} fields={sum(1 for v in rec['fields'].values() if v['value'] is not None)}/5 "
            f"tools={len(rec['tool_calls'])} llm={len(rec['llm_calls'])} "
            f"tokens={sum(x['total_tokens'] for x in rec['llm_calls'])}"
        )
    return out
