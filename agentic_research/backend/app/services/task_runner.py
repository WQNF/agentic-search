import time
import logging
from datetime import datetime
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.task import Task
from app.graph.state import ResearchState
from app.services.task_cache import set_task_status, set_task_progress
from app.core.metrics import (
    research_tasks_total,
    research_task_duration_seconds,
)

logger = logging.getLogger(__name__)

NODE_SEQUENCE = [
    "entity_resolver",
    "info_collector",
    "financial_analyzer",
    "report_writer",
    "quality_reviewer",
]


async def run_research_task(
    app_graph, task_id: UUID, run_id: str, query: str, user_id: str
):
    """后台任务执行器：流式执行 LangGraph 工作流并更新 Redis + 数据库"""
    start_time = time.time()
    task_id_str = str(task_id)
    logger.info(f"[后台任务启动] task_id={task_id_str}, query={query}, run_id={run_id}")

    async with AsyncSessionLocal() as db:
        logger.info(f"[后台任务] 数据库会话已创建: task_id={task_id_str}")
        try:
            # 1. 更新数据库状态为 running
            result = await db.execute(select(Task).where(Task.id == task_id))
            task = result.scalar_one_or_none()
            if not task:
                logger.error(f"任务不存在: {task_id}")
                return
            task.status = "running"
            await db.commit()

            # 2. 写入 Redis 状态
            await set_task_status(task_id_str, "running", {
                "run_id": run_id,
                "query": query,
                "started_at": datetime.utcnow().isoformat(),
            })
            await set_task_progress(task_id_str, "entity_resolver", [], status="running")

            # 3. 构造初始状态
            initial_state: ResearchState = {
                "query": query,
                "run_id": run_id,
                "user_id": user_id,
                "entity": None,
                "entity_status": "pending",
                "profile": None,
                "news": [],
                "profile_status": "pending",
                "financial_raw": None,
                "metrics": None,
                "financial_status": "pending",
                "report_markdown": "",
                "report_status": "pending",
                "review": None,
                "final_status": "pending",
                "steps": [],
                "errors": [],
            }

            config = {"configurable": {"thread_id": run_id}}

            # 4. 流式执行工作流
            accumulated_state = dict(initial_state)
            accumulated_steps = []
            current_node = "entity_resolver"

            async for event in app_graph.astream(
                initial_state, config=config, stream_mode="updates"
            ):
                for node_name, updates in event.items():
                    current_node = node_name
                    if not isinstance(updates, dict):
                        continue
                    # 累积 steps（追加而非替换）
                    if "steps" in updates and isinstance(updates["steps"], list):
                        accumulated_steps.extend(updates["steps"])
                        accumulated_state["steps"] = accumulated_steps
                    # 累积 errors
                    if "errors" in updates and isinstance(updates["errors"], list):
                        existing = accumulated_state.get("errors", [])
                        accumulated_state["errors"] = existing + updates["errors"]
                    # 更新其他字段
                    for k, v in updates.items():
                        if k in ("steps", "errors"):
                            continue
                        accumulated_state[k] = v

                    # 实时更新 Redis 进度
                    await set_task_progress(
                        task_id_str, current_node, accumulated_steps, status="running"
                    )
                    logger.info(f"任务 {task_id} 完成节点: {node_name}")

            # 5. 工作流结束，更新数据库
            duration_ms = int((time.time() - start_time) * 1000)
            duration_s = duration_ms / 1000.0
            final_status = accumulated_state.get("final_status", "failed")
            if final_status not in ("success", "partial_success", "failed"):
                final_status = "failed"

            # 记录 Prometheus 任务指标
            research_tasks_total.labels(status=final_status).inc()
            research_task_duration_seconds.labels(status=final_status).observe(duration_s)

            result = await db.execute(select(Task).where(Task.id == task_id))
            task = result.scalar_one_or_none()
            if task:
                task.status = final_status
                task.final_status = final_status
                task.entity = accumulated_state.get("entity")
                task.finished_at = datetime.utcnow()
                task.duration_ms = duration_ms
                errors = accumulated_state.get("errors", [])
                if errors:
                    task.error_message = "; ".join(
                        [str(e.get("error", "")) for e in errors]
                    )[:2000]
                await db.commit()

            # 6. 更新 Redis 最终状态
            await set_task_status(task_id_str, final_status, {
                "run_id": run_id,
                "query": query,
                "finished_at": datetime.utcnow().isoformat(),
                "duration_ms": duration_ms,
            })
            await set_task_progress(
                task_id_str, "quality_reviewer", accumulated_steps, status=final_status
            )

            logger.info(
                f"任务 {task_id} 完成，状态: {final_status}, 耗时: {duration_ms}ms"
            )

        except Exception as e:
            logger.exception(f"任务 {task_id} 执行失败: {e}")
            duration_ms = int((time.time() - start_time) * 1000)
            duration_s = duration_ms / 1000.0

            # 记录 Prometheus 任务指标
            research_tasks_total.labels(status="failed").inc()
            research_task_duration_seconds.labels(status="failed").observe(duration_s)

            try:
                result = await db.execute(select(Task).where(Task.id == task_id))
                task = result.scalar_one_or_none()
                if task:
                    task.status = "failed"
                    task.final_status = "failed"
                    task.error_message = str(e)[:2000]
                    task.finished_at = datetime.utcnow()
                    task.duration_ms = duration_ms
                    await db.commit()
            except Exception as db_err:
                logger.error(f"更新失败状态时出错: {db_err}")

            await set_task_status(task_id_str, "failed", {
                "run_id": run_id,
                "query": query,
                "error": str(e)[:500],
            })
            await set_task_progress(task_id_str, "unknown", [], status="failed")