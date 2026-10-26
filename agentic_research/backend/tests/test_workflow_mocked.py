import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.graph.workflow import build_graph
from app.graph.state import ResearchState
from app.Schemas.entity import Entity


def _initial_state(run_id: str, query: str = "AAPL") -> ResearchState:
    return {
        "query": query,
        "run_id": run_id,
        "user_id": "test_user",
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


async def test_workflow_entity_failure_goes_to_report_writer(monkeypatch, tmp_path):
    """
    实体解析失败 → 跳过信息搜集和财务分析 → 直接报告撰写 → 质量审核
    """
    monkeypatch.chdir(tmp_path)

    # Mock 实体解析抛出异常
    monkeypatch.setattr(
        "app.graph.nodes.entity_resolver.resolve",
        MagicMock(side_effect=Exception("无法识别实体")),
    )

    graph = build_graph(checkpointer=None)
    run_id = str(uuid.uuid4())
    result = await graph.ainvoke(_initial_state(run_id, "ZZZZZ"))

    assert result["entity_status"] == "failed"
    assert result["report_status"] == "partial"
    assert result["final_status"] == "failed"
    # 注意：ResearchState 无自定义 reducer，后续节点返回 "errors": [] 会覆盖前序 errors


async def test_workflow_full_success(monkeypatch, tmp_path):
    """
    完整工作流：Mock 工具和 LLM，验证最终状态 success
    """
    monkeypatch.chdir(tmp_path)

    # Mock 实体解析
    fake_entity = Entity(
        market="US", symbol="AAPL", exchange="NASDAQ",
        company_name="Apple Inc.", cik="0000320193",
    )
    monkeypatch.setattr(
        "app.graph.nodes.entity_resolver.resolve",
        MagicMock(return_value=fake_entity),
    )

    # Mock 工具（被 info_collector 内部包装调用）
    monkeypatch.setattr(
        "app.graph.nodes.search_web",
        MagicMock(return_value=[
            {"title": "News 1", "url": "http://x.com/1", "content": "abc", "score": 0.9}
        ]),
    )
    monkeypatch.setattr(
        "app.graph.nodes.get_company_profile",
        MagicMock(return_value={"name": "Apple Inc.", "industry": "Tech"}),
    )
    monkeypatch.setattr(
        "app.graph.nodes.get_stock_quote",
        MagicMock(return_value={"symbol": "AAPL", "price": 200.0}),
    )

    # Mock 财报数据
    monkeypatch.setattr(
        "app.graph.nodes.extract_financial_data",
        MagicMock(return_value={
            "income_statement": {"Revenues": 400000, "NetIncomeLoss": 100000, "GrossProfit": 180000},
            "balance_sheet": {"Assets": 350000, "Liabilities": 200000, "StockholdersEquity": 150000},
            "cash_flow": {"NetCashProvidedByUsedInOperatingActivities": 110000},
        }),
    )

    # Mock LLM：info_collector 的 Agent 和 report_writer 的链
    class FakeMessage:
        def __init__(self, content):
            self.content = content

    async def fake_ainvoke(*args, **kwargs):
        # 判断是报告撰写还是信息搜集
        prompt_text = str(args) + str(kwargs)
        if "业务与行业" in prompt_text or "公司概览" in prompt_text:
            return FakeMessage(
                "# Apple 报告\n\n"
                "## 1. 公司概览\nApple Inc.\n\n"
                "## 2. 业务与行业\n消费电子\n\n"
                "## 3. 近期新闻与舆情\nNews 1\n\n"
                "## 4. 财务分析\nROE=0.67\n\n"
                "## 5. 风险提示\n供应链风险\n\n"
                "## 6. 投资结论\n长期看好\n\n"
                "## 7. 附录：数据来源与说明\nSEC\n"
            )
        return FakeMessage(
            json.dumps({
                "profile": {"name": "Apple Inc.", "industry": "Tech"},
                "news": [{"title": "News 1", "url": "http://x.com/1", "content": "abc", "date": ""}],
            })
        )

    fake_llm = MagicMock()
    fake_llm.ainvoke = AsyncMock(side_effect=fake_ainvoke)
    fake_llm.bind_tools = MagicMock(return_value=fake_llm)

    # 对 create_openai_tools_agent 和 ChatPromptTemplate 的 chain 都要 mock
    with patch("app.graph.nodes.get_llm", return_value=fake_llm), \
         patch("app.graph.nodes.create_react_agent") as mock_create_agent:

        mock_executor = MagicMock()
        mock_executor.ainvoke = AsyncMock(return_value={
            "messages": [FakeMessage(json.dumps({
                "profile": {"name": "Apple Inc.", "industry": "Tech"},
                "news": [{"title": "News 1", "url": "http://x.com/1", "content": "abc", "date": ""}],
            }))]
        })
        mock_create_agent.return_value = mock_executor

        graph = build_graph(checkpointer=None)
        run_id = str(uuid.uuid4())
        result = await graph.ainvoke(_initial_state(run_id, "AAPL"))

    assert result["entity_status"] == "success"
    assert result["profile_status"] in ("success", "partial")
    assert result["financial_status"] == "success"
    assert result["report_status"] in ("success", "partial")
    # 最终状态至少为 partial_success
    assert result["final_status"] in ("success", "partial_success")