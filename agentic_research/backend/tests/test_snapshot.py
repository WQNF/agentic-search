"""快照层单测：不联网。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services import snapshot_store as ss


@pytest.fixture(autouse=True)
def _tmp_snapshot_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "SNAPSHOT_DIR", tmp_path / "snapshots")
    monkeypatch.delenv("DATA_FORCE_LIVE", raising=False)


def test_save_and_load_roundtrip():
    payload = {"revenue": 100.0, "period_end": "2025-12-31"}
    meta = ss.save("financials_cn", "600519.SH", payload, "test")
    assert meta["kind"] == "financials_cn"
    assert meta["period_end"] == "2025-12-31"
    rec = ss.load("financials_cn", "600519.SH")
    assert rec["payload"]["revenue"] == 100.0
    assert rec["meta"]["source"] == "test"


def test_fresh_snapshot_skips_live():
    ss.save("financials_cn", "600519.SH", {"revenue": 100.0}, "seed")
    calls = []

    def live():
        calls.append(1)
        return {"revenue": 200.0}

    out = ss.get_or_fetch("financials_cn", "600519.SH", live, "live", prefer_snapshot=True, max_age_days=30)
    assert out["revenue"] == 100.0
    assert out["_data_meta"]["stale"] is False
    assert out["_data_meta"]["source"] == "snapshot"
    assert calls == []


def test_live_success_saves_then_snapshot_is_used():
    calls = []

    def live():
        calls.append(1)
        return {"revenue": 200.0}

    out1 = ss.get_or_fetch("financials_cn", "600519.SH", live, "live", prefer_snapshot=True, max_age_days=30)
    assert out1["revenue"] == 200.0
    assert calls == [1]

    out2 = ss.get_or_fetch("financials_cn", "600519.SH", live, "live", prefer_snapshot=True, max_age_days=30)
    assert out2["revenue"] == 200.0
    assert out2["_data_meta"]["source"] == "snapshot"
    assert calls == [1]


def _make_old(key: str, payload: dict, days: int = 60):
    ss.save("financials_cn", key, payload, "seed")
    path = ss.snapshot_path("financials_cn", key)
    rec = json.loads(path.read_text(encoding="utf-8"))
    old = datetime.now(timezone.utc) - timedelta(days=days)
    rec["meta"]["fetched_at"] = old.isoformat()
    path.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")


def test_live_failure_falls_back_to_stale_snapshot():
    _make_old("600519.SH", {"revenue": 100.0})

    def boom():
        raise RuntimeError("akshare down")

    out = ss.get_or_fetch("financials_cn", "600519.SH", boom, "live", prefer_snapshot=True, max_age_days=30)
    assert out["revenue"] == 100.0
    assert out["_data_meta"]["stale"] is True
    assert "akshare down" in out["_data_meta"]["fallback_error"]


def test_no_snapshot_and_live_failure_raises():
    def boom():
        raise RuntimeError("akshare down")

    with pytest.raises(RuntimeError):
        ss.get_or_fetch("financials_cn", "000001.SZ", boom, "live", prefer_snapshot=True, max_age_days=30)


def test_force_live_bypasses_fresh_snapshot(monkeypatch):
    ss.save("financials_cn", "600519.SH", {"revenue": 100.0}, "seed")
    monkeypatch.setenv("DATA_FORCE_LIVE", "1")
    out = ss.get_or_fetch("financials_cn", "600519.SH", lambda: {"revenue": 300.0}, "live",
                          prefer_snapshot=True, max_age_days=30)
    assert out["revenue"] == 300.0
    assert out["_data_meta"]["source"] == "live"
