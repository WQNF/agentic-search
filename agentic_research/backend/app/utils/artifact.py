#中间产物保存/读取工具

import json
from pathlib import Path
from typing import Any, Dict, Optional
from app.utils.run_logger import run_log

RUNS_DIR = Path("runs")


def save_artifact(run_id: str, filename: str, data: Any) -> str:
    """保存中间产物到 runs/{run_id}/{filename}。

    注意：.md 报告必须写原始 Markdown，不能 json.dump 成带转义的字符串，
    否则 API/下载返回的会是一行 "\\n" 转义文本。
    """
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    filepath = run_dir / filename
    if filename.endswith(".md") and isinstance(data, str):
        filepath.write_text(data, encoding="utf-8")
    else:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    return str(filepath)


def read_artifact(run_id: str, filename: str) -> Optional[Any]:
    """读取中间产物"""
    filepath = RUNS_DIR / run_id / filename
    if not filepath.exists():
        return None
    if filename.endswith(".md"):
        return filepath.read_text(encoding="utf-8")
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def append_log(run_id: str, entry: Dict[str, Any]) -> None:
    """
    向后兼容的日志接口。
    输入形如 {"node": "xxx", "event": "success", "duration_ms": 123, "custom_field": ...}
    会被转换为 run_log 调用。
    """
    entry = dict(entry)  # 防止修改调用者的字典
    node = entry.pop("node", "unknown")
    event = entry.pop("event", "unknown")
    level = entry.pop("level", "info")
    duration_ms = entry.pop("duration_ms", None)
    retry_count = entry.pop("retry_count", None)
    error = entry.pop("error", None)
    entry.pop("timestamp", None)  # 由 run_log 生成，忽略旧的

    run_log(
        run_id=run_id,
        node=node,
        event=event,
        level=level,
        duration_ms=duration_ms,
        retry_count=retry_count,
        error=error,
        **entry,
    )