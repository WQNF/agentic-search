"""无效/停牌代码降级测试：3 个样本，按严格契约判定。"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from .config import INVALID_CODES, RESULTS_DIR
from .runner import run_one

JSONL = RESULTS_DIR / "invalid.jsonl"
_EXPLICIT = ("无法", "无效", "不存在", "失败", "未能", "不支持", "未找到", "请检查")

_CONTRACT = {
    "ZZZZZZ": "格式无效/不存在",
    "999999.SH": "格式有效但不存在",
    "88888.HK": "格式有效但不存在",
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
        out[r["symbol"]] = r
    return out


def contract(rec: Dict) -> Dict:
    from pathlib import Path
    p = Path(RESULTS_DIR) / "runs" / rec.get("run_id", "") / "report.md"
    report = p.read_text(encoding="utf-8") if p.exists() else ""
    explicit = any(k in report for k in _EXPLICIT)
    checks = {
        "no_crash": rec.get("error") in ("", None),
        "report_exists": len(report.strip()) >= 100,
        "explicit_invalid": explicit,
        "entity_not_fabricated": rec.get("entity_status") != "success",
        "financial_not_fabricated": rec.get("financial_status") != "success",
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "failed_checks": [k for k, v in checks.items() if not v],
        "report_chars": len(report),
    }


async def run_all(force: bool = False) -> List[Dict]:
    from app.graph.workflow import build_graph
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    done = load_done()
    out = []
    for item in INVALID_CODES:
        sym = item["code"]
        if not force and sym in done:
            print(f"skip invalid {sym}")
            out.append(done[sym])
            continue
        company = {"symbol": sym, "market": item["market"], "name": sym, "currency": "USD"}
        print(f"invalid {sym} ...", flush=True)
        graph = build_graph(checkpointer=None)
        rec = await run_one(graph, company, mode="invalid", query=sym)
        c = contract(rec)
        row = {"symbol": sym, "kind": item["kind"], "run": rec, "contract": c}
        with JSONL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        out.append(row)
        print(f"    passed={c['passed']} failed={c['failed_checks']} "
              f"entity={rec.get('entity_status')} final={rec.get('final_status')}")
    return out
