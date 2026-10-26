import uuid
import json
import logging
import asyncio
from pathlib import Path
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.db.session import get_db
from app.models import User
from app.models.task import Task
from app.Schemas.task import (
    TaskCreate, TaskResponse, TaskListResponse,
    ArtifactListResponse, ArtifactResponse, ReportResponse,
    TaskStatusResponse, TaskProgressResponse
)
from app.api.depends import get_current_user
from app.services.task_runner import run_research_task
from app.services.task_cache import get_task_status, get_task_progress
from app.core.metrics import research_tasks_created_total

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])

RUNS_DIR = Path("runs")

@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    task_data: TaskCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """创建投研任务，触发后台工作流"""
    run_id = str(uuid.uuid4())
    task = Task(
        user_id=current_user.id,
        query=task_data.query,
        run_id=run_id,
        status="pending"
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    
    research_tasks_created_total.inc()
    
    # 获取全局图实例
    app_graph = getattr(request.app.state, "app_graph", None)
    if app_graph is None:
        logger.error("app_graph 未初始化")
        raise HTTPException(status_code=500, detail="服务尚未就绪，请稍后重试")
    
    # 使用 asyncio.create_task 显式调度后台任务，确保在当前事件循环中执行
    loop = asyncio.get_running_loop()
    loop.create_task(
        run_research_task(
            app_graph=app_graph,
            task_id=task.id,
            run_id=run_id,
            query=task_data.query,
            user_id=str(current_user.id)
        )
    )
    logger.info(f"后台任务已调度: task_id={task.id}, query={task_data.query}")
    
    return task

@router.get("", response_model=TaskListResponse)
async def list_tasks(
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取当前用户的任务列表"""
    # 总数
    count_result = await db.execute(
        select(func.count(Task.id)).where(Task.user_id == current_user.id)
    )
    total = count_result.scalar_one()
    
    # 列表
    result = await db.execute(
        select(Task)
        .where(Task.user_id == current_user.id)
        .order_by(Task.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    tasks = result.scalars().all()

    items = []
    for t in tasks:
        cached = await get_task_status(str(t.id))
        if cached and cached.get("status") != t.status:
            t_dict = TaskResponse.model_validate(t).model_dump()
            t_dict["status"] = cached["status"]
            items.append(TaskResponse(**t_dict))
        else:
            items.append(TaskResponse.model_validate(t))

    return TaskListResponse(total=total, items=items)

@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取任务详情"""
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == current_user.id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("/{task_id}/status", response_model=TaskStatusResponse)
async def get_task_status_endpoint(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """轻量级状态查询：优先读 Redis，未命中回退数据库"""
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == current_user.id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    cached = await get_task_status(str(task_id))
    if cached:
        return TaskStatusResponse(
            task_id=task.id,
            status=cached.get("status", task.status),
            run_id=cached.get("run_id", task.run_id),
            started_at=cached.get("started_at"),
            finished_at=cached.get("finished_at"),
            duration_ms=cached.get("duration_ms"),
            error=cached.get("error"),
            source="redis",
        )

    return TaskStatusResponse(
        task_id=task.id,
        status=task.status,
        run_id=task.run_id,
        finished_at=task.finished_at.isoformat() if task.finished_at else None,
        duration_ms=task.duration_ms,
        error=task.error_message,
        source="database",
    )


@router.get("/{task_id}/progress", response_model=TaskProgressResponse)
async def get_task_progress_endpoint(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """查询任务进度（当前节点、步骤列表）"""
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == current_user.id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    progress = await get_task_progress(str(task_id))
    if progress:
        return TaskProgressResponse(
            task_id=task.id,
            status=progress.get("status", task.status),
            current_node=progress.get("current_node", "unknown"),
            steps=progress.get("steps", []),
            source="redis",
        )

    return TaskProgressResponse(
        task_id=task.id,
        status=task.status,
        current_node="unknown",
        steps=[],
        source="database",
    )


@router.get("/{task_id}/artifacts", response_model=ArtifactListResponse)
async def list_artifacts(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """列出任务的所有中间产物"""
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == current_user.id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    run_dir = RUNS_DIR / task.run_id
    if not run_dir.exists():
        return ArtifactListResponse(run_id=task.run_id, files=[])
    
    files = sorted([f.name for f in run_dir.iterdir() if f.is_file()])
    return ArtifactListResponse(run_id=task.run_id, files=files)

@router.get("/{task_id}/artifacts/{filename}", response_model=ArtifactResponse)
async def get_artifact(
    task_id: uuid.UUID,
    filename: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取指定中间产物内容"""
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == current_user.id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    # 安全检查：防止路径穿越
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="非法文件名")
    
    filepath = RUNS_DIR / task.run_id / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    
    content = filepath.read_text(encoding="utf-8")
    
    # 根据扩展名判断内容类型
    if filename.endswith(".json"):
        try:
            return ArtifactResponse(filename=filename, content=json.loads(content), content_type="json")
        except json.JSONDecodeError:
            return ArtifactResponse(filename=filename, content=content, content_type="text")
    elif filename.endswith(".md"):
        return ArtifactResponse(filename=filename, content=content, content_type="markdown")
    elif filename.endswith(".jsonl"):
        lines = [json.loads(line) for line in content.strip().split("\n") if line.strip()]
        return ArtifactResponse(filename=filename, content=lines, content_type="jsonl")
    else:
        return ArtifactResponse(filename=filename, content=content, content_type="text")

@router.get("/{task_id}/report", response_model=ReportResponse)
async def get_report(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取任务的最终报告"""
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == current_user.id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    report_path = RUNS_DIR / task.run_id / "05_report.md"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="报告尚未生成")
    
    report_md = report_path.read_text(encoding="utf-8")
    return ReportResponse(
        task_id=task.id,
        run_id=task.run_id,
        report_markdown=report_md,
        final_status=task.final_status or task.status
    )

@router.get("/{task_id}/report/download")
async def download_report(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """下载报告 Markdown 文件"""
    from fastapi.responses import FileResponse
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == current_user.id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    report_path = RUNS_DIR / task.run_id / "05_report.md"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="报告尚未生成")
    
    return FileResponse(
        path=str(report_path),
        media_type="text/markdown",
        filename=f"{task.query.replace(' ', '_')}_report.md"
    )