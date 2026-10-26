"""本地数据快照：解决 akshare / 外部接口不稳定时的“可复跑、可降级”。

设计：
  - 财务/概况：**快照优先**（默认 30 天内直接读本地，不再打 akshare）；
    快照过期才尝试实时；实时失败则回退旧快照，并标记 stale=True。
  - 行情：**实时优先**；实时失败才回退快照（默认最多 1 天），并标记 stale=True。
  - 每个快照都有 meta：source / fetched_at / period_end / stale / age_days。
  - 写入用临时文件 + os.replace，避免半截 JSON。

目录：backend/data/snapshots/<kind>/<key>.json
可通过 DATA_SNAPSHOT_DIR 覆盖；DATA_FORCE_LIVE=1 可强制实时（预热脚本用）。
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

SNAPSHOT_DIR = Path(os.environ.get("DATA_SNAPSHOT_DIR", "data/snapshots"))
_LOCK = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe(key: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(key)).strip("_")
    return s or "unknown"


def snapshot_path(kind: str, key: str) -> Path:
    return SNAPSHOT_DIR / kind / f"{_safe(key)}.json"


def _infer_period(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    p = payload.get("period_end")
    if p:
        return str(p)
    fi = payload.get("filing_info") or {}
    if isinstance(fi, dict) and fi.get("period_of_report"):
        return str(fi["period_of_report"])
    return None


def save(kind: str, key: str, payload: Dict, source: str,
         period_end: Optional[str] = None, extra: Optional[Dict] = None) -> Dict:
    """原子写入快照，返回 meta。"""
    path = snapshot_path(kind, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "kind": kind,
        "key": key,
        "source": source,
        "fetched_at": _now().isoformat(),
        "period_end": period_end or _infer_period(payload),
    }
    if extra:
        meta.update(extra)
    record = {"meta": meta, "payload": payload}
    tmp = path.with_suffix(".json.tmp")
    with _LOCK:
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, path)
    return meta


def load(kind: str, key: str, max_age_days: Optional[float] = None) -> Optional[Dict]:
    """读快照；返回 {meta, payload}。max_age_days 只影响 meta.expired，不拒绝读取。"""
    path = snapshot_path(kind, key)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(record, dict) or "payload" not in record:
        return None
    meta = record.setdefault("meta", {})
    fetched = meta.get("fetched_at")
    if fetched:
        try:
            dt = datetime.fromisoformat(fetched)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_days = (_now() - dt).total_seconds() / 86400.0
            meta["age_days"] = round(age_days, 3)
            if max_age_days is not None:
                meta["expired"] = age_days > float(max_age_days)
        except Exception:
            pass
    return record


def load_payload(kind: str, key: str) -> Optional[Dict]:
    rec = load(kind, key)
    return rec["payload"] if rec else None


def _attach_meta(payload: Dict, meta: Dict, stale: bool, **extra) -> Dict:
    out = dict(payload) if isinstance(payload, dict) else payload
    if isinstance(out, dict):
        m = dict(meta or {})
        m["stale"] = stale
        m.update(extra)
        out["_data_meta"] = m
    return out


def get_or_fetch(kind: str, key: str, live_fn: Callable[[], Dict], source: str,
                 *, prefer_snapshot: bool = True, max_age_days: float = 30.0,
                 fallback_max_age_days: Optional[float] = None,
                 validate: Optional[Callable[[Dict], bool]] = None,
                 period_end: Optional[str] = None) -> Dict:
    """快照优先/实时优先的统一入口。

    Returns:
        payload（dict），并附带 `_data_meta`：
          - stale=False：本次新鲜（实时或新鲜快照）
          - stale=True：实时失败，回退旧快照
    """
    validate = validate or (lambda p: isinstance(p, dict) and len(p) > 0)
    force_live = os.environ.get("DATA_FORCE_LIVE") == "1"

    # 1) 快照优先：30 天内的财务/概况不再打外部接口
    if prefer_snapshot and not force_live:
        rec = load(kind, key, max_age_days=max_age_days)
        if rec and not rec.get("meta", {}).get("expired", True):
            meta = rec["meta"]
            return _attach_meta(rec["payload"], meta, stale=False,
                                source="snapshot", original_source=meta.get("source"))

    # 2) 实时
    try:
        payload = live_fn()
        if not validate(payload):
            raise ValueError(f"live payload invalid for {kind}/{key}")
        meta = save(kind, key, payload, source, period_end=period_end)
        return _attach_meta(payload, meta, stale=False, source=source)
    except Exception as e:
        # 3) 回退旧快照；若设了 fallback_max_age_days，过期快照不再使用
        rec = load(kind, key, max_age_days=fallback_max_age_days)
        if rec and not (fallback_max_age_days is not None and rec.get("meta", {}).get("expired")):
            meta = rec["meta"]
            return _attach_meta(
                rec["payload"], meta, stale=True,
                source=meta.get("source", "snapshot"),
                fallback_error=f"{type(e).__name__}: {e}"[:300],
            )
        raise


def stats() -> Dict:
    """返回快照数量/最新时间，供 /health 或运维脚本使用。"""
    out = {"dir": str(SNAPSHOT_DIR.resolve()), "kinds": {}}
    if not SNAPSHOT_DIR.exists():
        return out
    for d in sorted(SNAPSHOT_DIR.iterdir()):
        if not d.is_dir():
            continue
        files = list(d.glob("*.json"))
        latest = max((f.stat().st_mtime for f in files), default=0)
        out["kinds"][d.name] = {
            "count": len(files),
            "latest": datetime.fromtimestamp(latest, timezone.utc).isoformat() if latest else None,
        }
    return out
