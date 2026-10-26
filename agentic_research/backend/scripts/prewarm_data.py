"""主动刷新本地快照（弱网/akshare 不稳定时建议在评测前跑一次）。

用法（在 backend 下）：
    python scripts/prewarm_data.py --sample core
    python scripts/prewarm_data.py --symbols AAPL,600519.SH,00700.HK
    python scripts/prewarm_data.py --sample core --only financials
    python scripts/prewarm_data.py --sample core --only financials --force

说明：
  - 默认强制实时（DATA_FORCE_LIVE=1），成功即写快照；
  - 实时失败但有旧快照时，会打印 stale=True，不会中断整批；
  - 只刷新，不生成报告。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

os.environ["DATA_FORCE_LIVE"] = "1"

from app.services.entity_resolver import entity_resolver  # noqa: E402
from app.tools.company_profile import get_company_profile  # noqa: E402
from app.tools.stock_quote import get_stock_quote  # noqa: E402
from app.tools.sec_financials import extract_financial_data  # noqa: E402
from app.tools.cn_hk_financials import (  # noqa: E402
    get_cn_financial_abstract, get_hk_financial_abstract,
)
from app.services import snapshot_store  # noqa: E402


def core_companies():
    # 与评测样本保持一致；脚本只依赖 symbol/market/name
    from tests.eval.config import companies_for
    return companies_for("core")


def financial_tool(market: str):
    return {"US": extract_financial_data, "CN": get_cn_financial_abstract,
            "HK": get_hk_financial_abstract}.get(market)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sample", default="core", choices=["core", "pilot"])
    p.add_argument("--symbols", default="")
    p.add_argument("--only", default="all", choices=["all", "financials", "profiles", "quotes"])
    p.add_argument("--force", action="store_true", help="强制实时（默认已是）")
    args = p.parse_args()

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        companies = core_companies() if args.sample == "core" else core_companies()[:3]
        symbols = [c["symbol"] for c in companies]

    rows = []
    for sym in symbols:
        t = time.time()
        row = {"symbol": sym, "ok": 0, "stale": 0, "errors": []}
        try:
            entity = entity_resolver.resolve(sym)
        except Exception as e:
            row["errors"].append(f"entity: {type(e).__name__}: {e}")
            rows.append(row)
            continue

        tasks = []
        if args.only in ("all", "profiles"):
            tasks.append(("profile", get_company_profile))
        if args.only in ("all", "quotes"):
            tasks.append(("quote", get_stock_quote))
        ft = financial_tool(entity.market)
        if args.only in ("all", "financials") and ft:
            tasks.append(("financials", ft))

        for name, fn in tasks:
            try:
                res = fn(entity)
                meta = res.get("_data_meta", {}) if isinstance(res, dict) else {}
                stale = bool(meta.get("stale"))
                row["ok"] += 1
                row["stale"] += int(stale)
                print(f"  {sym:12s} {name:10s} ok  source={meta.get('source','live')} "
                      f"period={meta.get('period_end')} stale={stale}")
            except Exception as e:
                row["errors"].append(f"{name}: {type(e).__name__}: {str(e)[:160]}")
                print(f"  {sym:12s} {name:10s} ERR {type(e).__name__}: {str(e)[:160]}")
        row["elapsed_s"] = round(time.time() - t, 1)
        rows.append(row)

    ok = sum(1 for r in rows if r["ok"] > 0 and not r["errors"])
    print(json.dumps({
        "total": len(rows),
        "success_symbols": ok,
        "stale_calls": sum(r["stale"] for r in rows),
        "snapshot_stats": snapshot_store.stats(),
        "errors": [{"symbol": r["symbol"], "errors": r["errors"]} for r in rows if r["errors"]],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
