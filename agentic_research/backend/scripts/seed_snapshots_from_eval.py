"""把最近一次评测抓到的原始数据灌入本地快照，便于离线/弱网复跑。

用法（在 backend 下）：
    python scripts/seed_snapshots_from_eval.py

读取 tests/eval/results/runs.jsonl 中每个 symbol 的最后一次 e2e 运行，
把 app_runs/<run_id>/03_financial_raw.json、02_company_profile.json 写成快照。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services import snapshot_store  # noqa: E402

RESULTS = BACKEND / "tests" / "eval" / "results"
RUNS = RESULTS / "runs.jsonl"
APP_RUNS = RESULTS / "app_runs"

FIN_KIND = {"US": "financials_us", "CN": "financials_cn", "HK": "financials_hk"}


def main():
    if not RUNS.exists():
        raise SystemExit(f"找不到 {RUNS}；先跑一次评测或 prewarm")
    latest = {}
    for line in RUNS.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("mode") != "e2e":
            continue
        latest[r["symbol"]] = r

    ok_fin = ok_profile = 0
    for symbol, r in latest.items():
        run_dir = APP_RUNS / r["run_id"]
        market = r.get("market", "")
        fin_path = run_dir / "03_financial_raw.json"
        if fin_path.exists():
            payload = json.loads(fin_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload:
                snapshot_store.save(
                    FIN_KIND.get(market, "financials_other"), symbol, payload,
                    source="eval_seed", extra={"seed_run_id": r["run_id"]},
                )
                ok_fin += 1
        prof_path = run_dir / "02_company_profile.json"
        if prof_path.exists():
            payload = json.loads(prof_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload:
                snapshot_store.save(
                    "profiles", f"{market}:{symbol}", payload,
                    source="eval_seed", extra={"seed_run_id": r["run_id"]},
                )
                ok_profile += 1

    print(json.dumps({
        "symbols": len(latest),
        "financial_snapshots": ok_fin,
        "profile_snapshots": ok_profile,
        "snapshot_dir": str(snapshot_store.SNAPSHOT_DIR.resolve()),
        "stats": snapshot_store.stats(),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
