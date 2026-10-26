
# 🔴 新文件: d:\Python_exercise\项目2-智慧投研Agent\③项目代码\agentic_research\backend\app\services\task_cache.py

import json
import logging
from typing import Optional, Dict, Any, List
from redis.asyncio import Redis
from app.core.config import settings
from app.core.metrics import research_cache_hits_total, research_cache_misses_total

logger = logging.getLogger(__name__)

TASK_CACHE_TTL = 86400  # 24 小时

def _get_redis() -> Redis:
    """创建 Redis 客户端（短连接，用后关闭）"""
    return Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        decode_responses=True,
    )

async def set_task_status(
    task_id: str, status: str, extra: Optional[Dict[str, Any]] = None
) -> None:
    """设置任务状态缓存"""
    r = _get_redis()
    try:
        payload = {"status": status}
        if extra:
            payload.update(extra)
        await r.set(
            f"task:{task_id}:status",
            json.dumps(payload, ensure_ascii=False, default=str),
            ex=TASK_CACHE_TTL,
        )
    except Exception as e:
        logger.warning(f"写入 Redis 任务状态失败: {e}")
    finally:
        await r.close()

async def get_task_status(task_id: str) -> Optional[Dict[str, Any]]:
    """读取任务状态缓存"""
    r = _get_redis()
    try:
        val = await r.get(f"task:{task_id}:status")
        if val:
            research_cache_hits_total.labels(key_type="task_status").inc()
            return json.loads(val)
        else:
            research_cache_misses_total.labels(key_type="task_status").inc()
            return None
    except Exception as e:
        logger.warning(f"读取 Redis 任务状态失败: {e}")
        research_cache_misses_total.labels(key_type="task_status").inc()
        return None
    finally:
        await r.close()

async def set_task_progress(
    task_id: str,
    current_node: str,
    steps: List[Dict[str, Any]],
    status: str = "running",
) -> None:
    """设置任务进度缓存"""
    r = _get_redis()
    try:
        payload = {
            "status": status,
            "current_node": current_node,
            "steps": steps,
        }
        await r.set(
            f"task:{task_id}:progress",
            json.dumps(payload, ensure_ascii=False, default=str),
            ex=TASK_CACHE_TTL,
        )
    except Exception as e:
        logger.warning(f"写入 Redis 任务进度失败: {e}")
    finally:
        await r.close()

async def get_task_progress(task_id: str) -> Optional[Dict[str, Any]]:
    """读取任务进度缓存"""
    r = _get_redis()
    try:
        val = await r.get(f"task:{task_id}:progress")
        if val:
            research_cache_hits_total.labels(key_type="task_progress").inc()
            return json.loads(val)
        else:
            research_cache_misses_total.labels(key_type="task_progress").inc()
            return None
    except Exception as e:
        logger.warning(f"读取 Redis 任务进度失败: {e}")
        research_cache_misses_total.labels(key_type="task_progress").inc()
        return None
    finally:
        await r.close()

async def clear_task_cache(task_id: str) -> None:
    """清理任务缓存（可选，任务归档时调用）"""
    r = _get_redis()
    try:
        await r.delete(f"task:{task_id}:status", f"task:{task_id}:progress")
    except Exception as e:
        logger.warning(f"清理 Redis 任务缓存失败: {e}")
    finally:
        await r.close()