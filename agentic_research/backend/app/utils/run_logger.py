import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
import structlog

logger = structlog.get_logger("run_logger")

RUNS_DIR = Path("runs")
GLOBAL_LOG_FILE = Path("logs/app.jsonl")

def run_log(
    run_id: str,
    node: str,
    event: str,
    level: str = "info",
    duration_ms: Optional[int] = None,
    retry_count: Optional[int] = None,
    error: Optional[str] = None,
    **extra: Any,
) -> None:
    """
    记录结构化日志：同时写入 stdout（通过 structlog）和 runs/{run_id}/logs.jsonl。
    """
    entry: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "run_id": run_id,
        "node": node,
        "event": event,
    }
    if duration_ms is not None:
        entry["duration_ms"] = duration_ms
    if retry_count is not None:
        entry["retry_count"] = retry_count
    if error is not None:
        entry["error"] = error
    if extra:
        entry["extra"] = _safe_serialize(extra)

    # 1) 输出到 stdout（structlog JSON）
    log_func = getattr(logger, level, logger.info)
    log_kwargs = {k: v for k, v in entry.items() if k != "event"}
    log_func(event, **log_kwargs)

    # 2) 写入 runs/{run_id}/logs.jsonl
    try:
        run_dir = RUNS_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        with open(run_dir / "logs.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        logger.error("failed_to_write_run_log", run_id=run_id, error=str(e))

    # 3) 可选：写入全局日志文件
    try:
        GLOBAL_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(GLOBAL_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass

def _safe_serialize(data: Dict[str, Any]) -> Dict[str, Any]:
    """将不可 JSON 序列化的值转为字符串"""
    result = {}
    for k, v in data.items():
        try:
            json.dumps(v)
            result[k] = v
        except (TypeError, ValueError):
            result[k] = str(v)
    return result