import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from app.core.config import settings
from app.core.logging import setup_logging
from app.core import metrics
from app.api.routes import api_router
from app.db.session import engine
from app.db.base import Base

# 初始化日志
setup_logging()

logger = logging.getLogger(__name__)

# 创建数据库表（开发阶段直接创建，后续可迁移到 Alembic）
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


app = FastAPI(title=settings.app_name)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus 监控
Instrumentator().instrument(app).expose(app)

# 路由
app.include_router(api_router, prefix="/api")


@app.on_event("startup")
async def startup_event():
    try:
        await init_db()
    except Exception as e:
        logger.warning(f"数据库初始化失败（开发环境可忽略）: {e}")

    # 初始化 LangGraph 图（使用内存 Checkpointer）
    from langgraph.checkpoint.memory import MemorySaver
    from app.graph.workflow import build_graph
    app.state.app_graph = build_graph(checkpointer=MemorySaver())
    logger.info("LangGraph 工作流已初始化（MemorySaver）")


@app.get("/")
async def root():
    return {"message": "Agentic Research API"}