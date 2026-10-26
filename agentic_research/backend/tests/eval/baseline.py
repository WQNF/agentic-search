"""单 LLM 基线：同报告 prompt、无工具数据。

与系统报告层字段准确率使用同一套解析器与真值，保证可比。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from .config import BASELINE_RUNS_DIR, DEFAULT_PRICES, RESULTS_DIR, companies_for, ensure_dirs
from .instrument import Collector, estimate_cost
from .text import core_sections_non_empty, extract_fields

JSONL = RESULTS_DIR / "baseline.jsonl"


def load_done() -> Dict[str, Dict]:
    if not JSONL.exists():
        return {}
    out = {}
    for line in JSONL.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        out[rec["symbol"]] = rec
    return out


async def run_one(company: Dict, timeout_s: int = 300) -> Dict:
    from app.core.llm import get_llm
    from app.prompts.report_prompt import REPORT_SYSTEM_PROMPT, REPORT_HUMAN_TEMPLATE
    from langchain_core.prompts import ChatPromptTemplate

    ensure_dirs()
    BASELINE_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = str(uuid.uuid4())
    col = Collector(run_id)
    handler = col.make_handler()
    llm = get_llm(temperature=0.3, max_tokens=16000)
    prompt = ChatPromptTemplate.from_messages([
        ("system", REPORT_SYSTEM_PROMPT),
        ("human", REPORT_HUMAN_TEMPLATE),
    ])
    chain = prompt | llm

    entity_info = json.dumps({
        "company_name": company.get("name") or company["symbol"],
        "symbol": company["symbol"],
        "market": company["market"],
    }, ensure_ascii=False)
    inputs = {
        "entity_info": entity_info,
        "profile": "{}",
        "news": "[]",
        "financial_raw_summary": "{}",
        "metrics": "{}",
    }

    t0 = time.perf_counter()
    err = ""
    content = ""
    try:
        msg = await asyncio.wait_for(chain.ainvoke(inputs, config={"callbacks": [handler]}), timeout=timeout_s)
        content = getattr(msg, "content", None) or str(msg)
    except asyncio.TimeoutError:
        err = f"timeout>{timeout_s}s"
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    duration = time.perf_counter() - t0

    ok, secs = core_sections_non_empty(content)
    fields = extract_fields(content)
    rec = {
        "mode": "baseline",
        "symbol": company["symbol"],
        "market": company["market"],
        "name": company.get("name", ""),
        "run_id": run_id,
        "duration_s": round(duration, 3),
        "error": err,
        "report_chars": len(content),
        "sections_ok": ok,
        "sections": secs,
        "fields": fields,
        "llm_calls": col.llm_calls,
        "cost": estimate_cost(col.llm_calls, DEFAULT_PRICES),
    }
    d = BASELINE_RUNS_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.md").write_text(content, encoding="utf-8")
    (d / "result.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return rec


async def run_sample(sample: str = "core", limit: Optional[int] = None, force: bool = False) -> List[Dict]:
    ensure_dirs()
    companies = companies_for(sample)
    if limit:
        companies = companies[:limit]
    done = load_done()
    out = []
    for i, c in enumerate(companies, 1):
        if not force and c["symbol"] in done:
            print(f"[{i}/{len(companies)}] skip baseline {c['symbol']}")
            out.append(done[c["symbol"]])
            continue
        print(f"[{i}/{len(companies)}] baseline {c['symbol']} ...", flush=True)
        rec = await run_one(c)
        with JSONL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        out.append(rec)
        print(f"    {rec['duration_s']:.1f}s chars={rec['report_chars']} fields="
              f"{sum(1 for v in rec['fields'].values() if v['value'] is not None)}/5 "
              f"tokens={sum(x['total_tokens'] for x in rec['llm_calls'])}")
    return out
