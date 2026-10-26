from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID


class TaskStatusResponse(BaseModel):
    task_id: UUID
    status: str
    run_id: Optional[str] = None
    current_node: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_ms: Optional[int] = None
    error: Optional[str] = None
    source: str = "redis"


class TaskProgressResponse(BaseModel):
    task_id: UUID
    status: str
    current_node: str
    steps: List[Dict[str, Any]] = []
    source: str = "redis"

class TaskCreate(BaseModel):
    query: str = Field(..., min_length=1, max_length=200, description="公司名或股票代码")

class TaskResponse(BaseModel):
    id: UUID
    user_id: UUID
    query: str
    run_id: str
    status: str
    final_status: Optional[str] = None
    entity: Optional[Dict[str, Any]] = None
    created_at: datetime
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None

    class Config:
        from_attributes = True

class TaskListResponse(BaseModel):
    total: int
    items: List[TaskResponse]

class ArtifactListResponse(BaseModel):
    run_id: str
    files: List[str]

class ArtifactResponse(BaseModel):
    filename: str
    content: Any
    content_type: str  # "json" or "text" or "markdown"

class ReportResponse(BaseModel):
    task_id: UUID
    run_id: str
    report_markdown: str
    final_status: str