import logging
from typing import Optional
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.base import BaseCheckpointSaver
from app.core.config import settings
from app.graph.state import ResearchState
from app.graph.nodes import (
    entity_resolver_node,
    info_collector_node,
    financial_analyzer_node,
    report_writer_node,
    quality_reviewer_node
)


logger = logging.getLogger(__name__)

async def get_checkpointer():
    from langgraph.checkpoint.redis import RedisSaver
    from redis.asyncio import Redis
    redis_client = Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        decode_responses=False  # checkpointer 需要二进制
    )
    return RedisSaver(redis_client=redis_client)


def route_after_entity(state: ResearchState) -> str:
    if state.get("entity_status") == "success":
        return "info_collector"
    else:
        return "report_writer"


def route_after_financial(state: ResearchState) -> str:
    return "report_writer"


def build_graph(checkpointer=None):
    graph = StateGraph(ResearchState)
    
    # 添加节点
    graph.add_node("entity_resolver", entity_resolver_node)
    graph.add_node("info_collector", info_collector_node)
    graph.add_node("financial_analyzer", financial_analyzer_node)
    graph.add_node("report_writer", report_writer_node)
    graph.add_node("quality_reviewer", quality_reviewer_node)
    
    # 设置入口
    graph.set_entry_point("entity_resolver")
    
    # 实体解析后的条件边：成功→信息搜集，失败→报告撰写
    graph.add_conditional_edges(
        "entity_resolver",
        route_after_entity,
        {
            "info_collector": "info_collector",
            "report_writer": "report_writer",
        }
    )
    
    # 信息搜集 -> 财报分析
    graph.add_edge("info_collector", "financial_analyzer")
    
    # 财报分析 -> 报告撰写（条件边，始终返回 report_writer）
    graph.add_conditional_edges(
        "financial_analyzer",
        route_after_financial,
        {
            "report_writer": "report_writer",
        }
    )
    
    # 报告撰写 -> 质量审核
    graph.add_edge("report_writer", "quality_reviewer")
    
    # 质量审核 -> 结束
    graph.add_edge("quality_reviewer", END)
    
   # 编译，传入 checkpointer
    if checkpointer:
        return graph.compile(checkpointer=checkpointer)
    else:
        return graph.compile()