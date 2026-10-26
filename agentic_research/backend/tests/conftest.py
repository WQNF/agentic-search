import asyncio
import uuid
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)

from app.main import app
from app.db.base import Base
from app.db.session import get_db
from app.core.config import settings
from app.models.User import User  # noqa: F401
from app.models.task import Task  # noqa: F401

# ---------- 测试数据库 ----------
TEST_DB_NAME = "research_test_db"
TEST_DB_URL = (
    f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
    f"@{settings.postgres_host}:{settings.postgres_port}/{TEST_DB_NAME}"
)
ADMIN_DB_URL = (
    f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
    f"@{settings.postgres_host}:{settings.postgres_port}/postgres"
)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_test_database():
    """确保测试数据库存在"""
    admin_engine = create_async_engine(ADMIN_DB_URL, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        result = await conn.execute(
            text(f"SELECT 1 FROM pg_database WHERE datname='{TEST_DB_NAME}'")
        )
        if not result.scalar():
            await conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    await admin_engine.dispose()
    yield


@pytest_asyncio.fixture(scope="function")
async def test_engine():
    """每个测试函数独立建表"""
    engine = create_async_engine(TEST_DB_URL, echo=False, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def test_session_factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


# ---------- Mock Graph ----------
def create_mock_graph():
    """构造一个 Mock Graph，模拟完整工作流输出"""

    ENTITY = {
        "market": "US",
        "symbol": "AAPL",
        "exchange": "NASDAQ",
        "company_name": "Apple Inc.",
        "cik": "0000320193",
    }
    PROFILE = {
        "name": "Apple Inc.",
        "ticker": "AAPL",
        "industry": "Technology",
        "sector": "Consumer Electronics",
        "description": "Apple designs and sells consumer electronics.",
    }
    NEWS = [
        {"title": "Apple Q4 earnings beat", "url": "https://example.com/1", "content": "...", "date": "2026-08-01"},
        {"title": "New iPhone launch", "url": "https://example.com/2", "content": "...", "date": "2026-08-10"},
    ]
    METRICS = {
        "gross_margin": 0.44,
        "net_margin": 0.25,
        "operating_margin": 0.30,
        "roe": 0.35,
        "roa": 0.18,
        "debt_to_asset_ratio": 0.35,
        "current_ratio": 1.5,
        "quick_ratio": 1.3,
        "pe": 28.5,
        "pb": 12.0,
        "ps": 7.5,
        "ev_to_ebitda": 20.0,
    }
    REPORT_MD = """# Apple Inc. 研究报告

## 1. 公司概览
Apple Inc.（AAPL）是一家美国科技公司。

## 2. 业务与行业
主营消费电子、软件和服务。

## 3. 近期新闻与舆情
- Apple Q4 earnings beat
- New iPhone launch

## 4. 财务分析
| 指标 | 数值 |
|------|------|
| 毛利率 | 44% |
| 净利率 | 25% |
| ROE | 35% |

## 5. 风险提示
- 供应链风险
- 市场竞争

## 6. 投资结论
长期看好。

## 7. 附录：数据来源与说明
数据来源：SEC EDGAR。
"""
    REVIEW = {
        "sections_complete": True,
        "missing_sections": [],
        "metric_count": len(METRICS),
        "metrics_sufficient": True,
        "final_status": "success",
    }

    class MockGraph:
        async def astream(self, state, config=None, stream_mode=None):
            yield {
                "entity_resolver": {
                    "entity": ENTITY,
                    "entity_status": "success",
                    "steps": [{"node": "entity_resolver", "status": "success", "duration_ms": 50}],
                    "errors": [],
                }
            }
            yield {
                "info_collector": {
                    "profile": PROFILE,
                    "news": NEWS,
                    "profile_status": "success",
                    "steps": [{"node": "info_collector", "status": "success", "duration_ms": 100}],
                    "errors": [],
                }
            }
            yield {
                "financial_analyzer": {
                    "financial_raw": {"income_statement": {"Revenues": 400000}},
                    "metrics": METRICS,
                    "financial_status": "success",
                    "steps": [{"node": "financial_analyzer", "status": "success", "duration_ms": 200}],
                    "errors": [],
                }
            }
            yield {
                "report_writer": {
                    "report_markdown": REPORT_MD,
                    "report_status": "success",
                    "steps": [{"node": "report_writer", "status": "success", "duration_ms": 500}],
                    "errors": [],
                }
            }
            yield {
                "quality_reviewer": {
                    "review": REVIEW,
                    "final_status": "success",
                    "steps": [{"node": "quality_reviewer", "status": "success", "duration_ms": 10}],
                    "errors": [],
                }
            }

        async def ainvoke(self, state, config=None):
            return {
                **state,
                "entity": ENTITY,
                "entity_status": "success",
                "profile": PROFILE,
                "news": NEWS,
                "profile_status": "success",
                "financial_raw": {"income_statement": {"Revenues": 400000}},
                "metrics": METRICS,
                "financial_status": "success",
                "report_markdown": REPORT_MD,
                "report_status": "success",
                "review": REVIEW,
                "final_status": "success",
                "steps": [
                    {"node": "entity_resolver", "status": "success", "duration_ms": 50},
                    {"node": "info_collector", "status": "success", "duration_ms": 100},
                    {"node": "financial_analyzer", "status": "success", "duration_ms": 200},
                    {"node": "report_writer", "status": "success", "duration_ms": 500},
                    {"node": "quality_reviewer", "status": "success", "duration_ms": 10},
                ],
                "errors": [],
            }

    return MockGraph()


# ---------- HTTP 客户端 ----------
@pytest_asyncio.fixture
async def client(test_session_factory, monkeypatch):
    """提供测试 HTTP 客户端，注入测试 DB、Mock Graph、Mock Cache"""

    # 1) 覆盖 get_db 依赖
    async def override_get_db():
        async with test_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # 2) task_runner 内部使用的 Session 工厂也指向测试 DB
    monkeypatch.setattr(
        "app.services.task_runner.AsyncSessionLocal",
        test_session_factory,
    )

    # 3) Mock Redis 缓存写入（避免依赖 Redis 状态）
    monkeypatch.setattr(
        "app.services.task_runner.set_task_status",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.task_runner.set_task_progress",
        AsyncMock(return_value=None),
    )

    # 4) 设置 Mock Graph
    app.state.app_graph = create_mock_graph()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# ---------- 认证 Fixture ----------
@pytest_asyncio.fixture
async def registered_user(client) -> dict:
    payload = {
        "username": "testuser",
        "email": "test@example.com",
        "password": "password123",
    }
    response = await client.post("/api/register", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


@pytest_asyncio.fixture
async def auth_headers(registered_user) -> dict:
    token = registered_user["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def second_user_headers(client) -> dict:
    """第二个用户，用于越权访问测试"""
    payload = {
        "username": "otheruser",
        "email": "other@example.com",
        "password": "password456",
    }
    response = await client.post("/api/register", json=payload)
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}