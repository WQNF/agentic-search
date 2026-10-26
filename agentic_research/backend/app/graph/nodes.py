import json
import time
import logging
from typing import Dict, Any, List, Optional
from langgraph.prebuilt import create_react_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage
from langchain_core.tools import Tool, StructuredTool
from app.graph.state import ResearchState
from app.services.entity_resolver import entity_resolver
from app.utils.artifact import save_artifact, append_log
from app.utils.run_logger import run_log
from app.core.llm import get_llm
from app.core.config import settings
from app.core.metrics import (
    track_node,
    research_llm_requests_total,
    research_llm_duration_seconds,
)
from app.tools.search import search_web
from app.tools.company_profile import get_company_profile
from app.tools.stock_quote import get_stock_quote
from app.tools.sec_financials import extract_financial_data
from app.tools.cn_hk_financials import get_cn_financial_abstract, get_hk_financial_abstract
from app.tools.metrics import normalize_financial_data, calculate_metrics
from app.prompts.report_prompt import REPORT_SYSTEM_PROMPT, REPORT_HUMAN_TEMPLATE
logger = logging.getLogger(__name__)


async def entity_resolver_node(state: ResearchState) -> Dict[str, Any]:
    """
    实体标准化节点：解析用户输入为 Entity，保存中间产物。
    """
    start_time = time.time()
    query = state["query"]
    run_id = state["run_id"]

    run_log(run_id, "entity_resolver", "node_start", query=query)
    save_artifact(run_id, "00_input.json", {"query": query})

    with track_node("entity_resolver") as tracker:
        try:
            entity = entity_resolver.resolve(query)
            entity_dict = entity.dict()
            save_artifact(run_id, "01_entity.json", entity_dict)

            duration_ms = int((time.time() - start_time) * 1000)
            tracker.mark_success()
            run_log(
                run_id, "entity_resolver", "node_success",
                duration_ms=duration_ms,
                entity_symbol=entity_dict.get("symbol"),
                entity_market=entity_dict.get("market"),
            )
            append_log(run_id, {
                "node": "entity_resolver",
                "event": "success",
                "query": query,
                "entity": entity_dict,
                "duration_ms": duration_ms
            })
            return {
                "entity": entity_dict,
                "entity_status": "success",
                "steps": [{"node": "entity_resolver", "status": "success", "duration_ms": int((time.time() - start_time) * 1000)}],
                "errors": []
            }
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(f"实体解析失败: {e}")
            tracker.mark_error()
            run_log(
                run_id, "entity_resolver", "node_error",
                level="error", duration_ms=duration_ms, error=str(e), query=query,
            )
            append_log(run_id, {
                "node": "entity_resolver",
                "event": "error",
                "query": query,
                "error": str(e),
                "duration_ms": duration_ms
            })
            return {
                "entity": None,
                "entity_status": "failed",
                "steps": [{"node": "entity_resolver", "status": "failed", "error": str(e), "duration_ms": int((time.time() - start_time) * 1000)}],
                "errors": [{"node": "entity_resolver", "error": str(e)}]
            }


async def info_collector_node(state: ResearchState) -> Dict[str, Any]:
    """信息搜集节点：使用 LLM 决策调用工具，整理公司概况和新闻"""
    start_time = time.time()
    run_id = state["run_id"]
    entity = state.get("entity")

    run_log(run_id, "info_collector", "node_start")

    if not entity:
        logger.error("实体为空，无法执行信息搜集")
        run_log(run_id, "info_collector", "node_error", level="error", error="实体为空")
        return {
            "profile": None,
            "news": [],
            "profile_status": "failed",
            "steps": [{"node": "info_collector", "status": "failed", "error": "实体为空"}],
            "errors": [{"node": "info_collector", "error": "实体为空"}]
        }
    
    # 构造实体对象（Pydantic）
    from app.Schemas.entity import Entity
    try:
        entity_obj = Entity(**entity)
    except Exception as e:
        logger.error(f"实体解析失败: {e}")
        return {
            "profile": None,
            "news": [],
            "profile_status": "failed",
            "steps": [{"node": "info_collector", "status": "failed", "error": f"实体解析失败: {e}"}],
            "errors": [{"node": "info_collector", "error": f"实体解析失败: {e}"}]
        }
    
    # 动态创建工具
    def _search(query: str) -> str:
        """搜索工具，输入查询词"""
        run_log(run_id, "info_collector", "tool_call", tool="search_web", query=query)
        try:
            results = search_web(query, max_results=5)
            run_log(run_id, "info_collector", "tool_success", tool="search_web", result_count=len(results))
            return json.dumps(results, ensure_ascii=False, default=str)
        except Exception as e:
            run_log(run_id, "info_collector", "tool_error", level="warning", tool="search_web", error=str(e))
            return f"搜索失败: {e}"

    def _profile(*args, **kwargs) -> str:
        """获取公司概况工具，无需参数"""
        run_log(run_id, "info_collector", "tool_call", tool="get_company_profile")
        try:
            profile = get_company_profile(entity_obj)
            run_log(run_id, "info_collector", "tool_success", tool="get_company_profile")
            return json.dumps(profile, ensure_ascii=False, default=str)
        except Exception as e:
            run_log(run_id, "info_collector", "tool_error", level="warning", tool="get_company_profile", error=str(e))
            return f"获取公司概况失败: {e}"

    def _quote(*args, **kwargs) -> str:
        """获取股票行情工具，无需参数"""
        run_log(run_id, "info_collector", "tool_call", tool="get_stock_quote")
        try:
            quote = get_stock_quote(entity_obj)
            run_log(run_id, "info_collector", "tool_success", tool="get_stock_quote")
            return json.dumps(quote, ensure_ascii=False, default=str)
        except Exception as e:
            run_log(run_id, "info_collector", "tool_error", level="warning", tool="get_stock_quote", error=str(e))
            return f"获取行情失败: {e}"
    
    tools = [
        Tool(
            name="search_web",
            func=_search,
            description="搜索最新新闻、公司动态、行业分析。输入应为搜索关键词。"
        ),
        StructuredTool.from_function(
            func=_profile,
            name="get_company_profile",
            description="获取公司的基本信息、行业、官网、描述等。无需输入参数。"
        ),
        StructuredTool.from_function(
            func=_quote,
            name="get_stock_quote",
            description="获取公司最新股价、市值、涨跌幅等行情数据。无需输入参数。"
        )
    ]
    
    # 构建 Agent
    llm = get_llm(temperature=0.2, max_tokens=16000)
    system_prompt = """你是一个投资研究助手。给定一家公司实体，你需要搜集该公司的概况信息和近期新闻。
你可以使用以下工具：
- search_web: 搜索新闻，需要提供查询关键词。
- get_company_profile: 获取公司概况。
- get_stock_quote: 获取行情。

最终输出必须是一个 JSON 对象，包含两个字段：
{{
  "profile": {{
    "name": "公司全名",
    "ticker": "股票代码",
    "exchange": "交易所",
    "industry": "行业",
    "sector": "板块",
    "website": "官网",
    "description": "公司业务描述（2-3句话）",
    "employees": "员工人数（若未知可为null）",
    "address": "地址",
    "city": "城市",
    "state": "州/省",
    "country": "国家",
    "ceo": "CEO姓名（若未知可为null）",
    "ipo_date": "上市日期（若未知可为null）",
    "sic_code": "SIC代码（若未知可为空字符串）",
    "sic_description": "SIC描述"
  }},
  "news": [
    {{
      "title": "新闻标题",
      "url": "链接",
      "content": "摘要",
      "date": "日期（若未知可为空字符串）"
    }},
    ...
  ]
}}
请确保 profile 字段尽可能完整，news 列表包含 3-5 条相关新闻。如果某些数据无法获取，对应的值设为 null 或空字符串。
"""

    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=system_prompt,
    )

    entity_info = json.dumps(entity, ensure_ascii=False, indent=2)

    with track_node("info_collector") as tracker:
        try:
            # 执行 Agent 并记录 LLM 指标
            llm_start = time.time()
            result = await agent.ainvoke({
                "messages": [HumanMessage(content=f"公司实体信息：{entity_info}。请开始搜集信息。")]
            })
            research_llm_requests_total.labels(model=settings.deepseek_model, status="success").inc()
            research_llm_duration_seconds.labels(model=settings.deepseek_model).observe(time.time() - llm_start)
            
            messages = result.get("messages", [])
            output_text = messages[-1].content if messages else ""
            
            # 检查 LLM 是否返回空内容
            if not output_text or not output_text.strip():
                raise ValueError("LLM 返回空内容，降级到工具直接调用")
            
            # 解析 JSON
            # 输出可能包含 markdown 代码块，需清理
            if "```json" in output_text:
                output_text = output_text.split("```json")[1].split("```")[0].strip()
            elif "```" in output_text:
                output_text = output_text.split("```")[1].split("```")[0].strip()
            data = json.loads(output_text)
            profile = data.get("profile", {})
            news = data.get("news", [])
            
            # 保存中间产物
            save_artifact(run_id, "02_company_profile.json", profile)
            save_artifact(run_id, "02_news.json", news)

            duration_ms = int((time.time() - start_time) * 1000)
            tracker.mark_success()
            run_log(run_id, "info_collector", "node_success", duration_ms=duration_ms)
            append_log(run_id, {
                "node": "info_collector",
                "event": "success",
                "duration_ms": duration_ms
            })
            
            return {
                "profile": profile,
                "news": news,
                "profile_status": "success",
                "steps": [{"node": "info_collector", "status": "success", "duration_ms": int((time.time() - start_time) * 1000)}],
                "errors": []
            }
        except Exception as e:
            research_llm_requests_total.labels(model=settings.deepseek_model, status="error").inc()
            research_llm_duration_seconds.labels(model=settings.deepseek_model).observe(time.time() - llm_start)
            logger.error(f"信息搜集 Agent 执行失败: {e}")
            tracker.mark_partial()
            # 降级：直接使用工具获取数据，构造简单的 profile 和 news
            try:
                profile = get_company_profile(entity_obj)
            except:
                profile = {
                    "name": entity_obj.company_name,
                    "ticker": entity_obj.symbol,
                    "exchange": entity_obj.exchange,
                    "industry": "",
                    "sector": "",
                    "website": "",
                    "description": "",
                    "employees": None,
                    "address": "",
                    "city": "",
                    "state": "",
                    "country": "",
                    "ceo": None,
                    "ipo_date": None,
                    "sic_code": "",
                    "sic_description": ""
                }
            try:
                search_results = search_web(f"{entity_obj.company_name} {entity_obj.symbol} news", max_results=5)
                news = [{"title": r.get("title",""), "url": r.get("url",""), "content": r.get("content",""), "date": ""} for r in search_results]
            except:
                news = []
            
            save_artifact(run_id, "02_company_profile.json", profile)
            save_artifact(run_id, "02_news.json", news)

            duration_ms = int((time.time() - start_time) * 1000)
            run_log(run_id, "info_collector", "node_partial", level="warning", duration_ms=duration_ms, error=str(e))
            append_log(run_id, {
                "node": "info_collector",
                "event": "partial",
                "error": str(e),
                "duration_ms": duration_ms
            })
            
            return {
                "profile": profile,
                "news": news,
                "profile_status": "partial",
                "steps": [{"node": "info_collector", "status": "partial", "error": str(e), "duration_ms": int((time.time() - start_time) * 1000)}],
                "errors": [{"node": "info_collector", "error": str(e)}]
            }
    
async def financial_analyzer_node(state: ResearchState) -> Dict[str, Any]:
    """财报分析节点：获取财务数据，计算指标"""
    import os
    if os.getenv("TEST_FAIL_FINANCIAL") == "1":
        raise RuntimeError("模拟财务分析节点失败")
    
    start_time = time.time()
    run_id = state["run_id"]
    entity = state.get("entity")

    run_log(run_id, "financial_analyzer", "node_start")

    if not entity:
        logger.error("实体为空，无法执行财报分析")
        run_log(run_id, "financial_analyzer", "node_error", level="error", error="实体为空")
        return {
            "financial_raw": None,
            "metrics": None,
            "financial_status": "failed",
            "steps": [{"node": "financial_analyzer", "status": "failed", "error": "实体为空"}],
            "errors": [{"node": "financial_analyzer", "error": "实体为空"}]
        }
    
    try:
        # 构造实体对象
        from app.Schemas.entity import Entity
        entity_obj = Entity(**entity)
        market = entity_obj.market
        logger.info(f"开始财报分析，市场: {market}, symbol: {entity_obj.symbol}")
        
        with track_node("financial_analyzer") as tracker:
            raw_data = None
            source = ""
            
            if market == "US":
                # 美股：获取 SEC 财报
                try:
                    raw_data = extract_financial_data(entity_obj)
                    source = "us"
                except Exception as e:
                    logger.error(f"获取美股财报失败: {e}")
                    raise
            elif market == "CN":
                # A股：获取财务摘要
                try:
                    raw_data = get_cn_financial_abstract(entity_obj)
                    source = "cn"
                except Exception as e:
                    logger.error(f"获取A股财务摘要失败: {e}")
                    raise
            elif market == "HK":
                # 港股：获取财务摘要
                try:
                    raw_data = get_hk_financial_abstract(entity_obj)
                    source = "hk"
                except Exception as e:
                    logger.error(f"获取港股财务摘要失败: {e}")
                    raise
            else:
                raise ValueError(f"不支持的市场: {market}")
            
            # 保存原始数据
            if raw_data is not None:
                save_artifact(run_id, "03_financial_raw.json", raw_data)
            
            # 标准化并计算指标
            std = normalize_financial_data(raw_data, source=source)
            metrics = calculate_metrics(std)
            
            # 保存指标
            save_artifact(run_id, "04_metrics.json", metrics)
            
            duration_ms = int((time.time() - start_time) * 1000)
            tracker.mark_success()
            run_log(run_id, "financial_analyzer", "node_success", duration_ms=duration_ms)
            append_log(run_id, {
                "node": "financial_analyzer",
                "event": "success",
                "duration_ms": duration_ms
            })
            
            return {
                "financial_raw": raw_data,
                "metrics": metrics,
                "financial_status": "success",
                "steps": [{"node": "financial_analyzer", "status": "success", "duration_ms": int((time.time() - start_time) * 1000)}],
                "errors": []
            }
    except Exception as e:
        logger.error(f"财报分析失败: {e}")
        # 降级：指标设为 None，但报告仍可继续
        with track_node("financial_analyzer") as tracker2:
            duration_ms = int((time.time() - start_time) * 1000)
            tracker2.mark_partial()
            run_log(run_id, "financial_analyzer", "node_partial", level="warning", duration_ms=duration_ms, error=str(e))
            append_log(run_id, {
                "node": "financial_analyzer",
                "event": "partial",
                "error": str(e),
                "duration_ms": duration_ms
            })
            return {
                "financial_raw": None,
                "metrics": None,
                "financial_status": "partial",
                "steps": [{"node": "financial_analyzer", "status": "partial", "error": str(e), "duration_ms": int((time.time() - start_time) * 1000)}],
                "errors": [{"node": "financial_analyzer", "error": str(e)}]
            }
    
async def report_writer_node(state: ResearchState) -> Dict[str, Any]:
    """报告撰写节点：根据已有数据生成 Markdown 报告"""
    start_time = time.time()
    run_id = state["run_id"]

    run_log(run_id, "report_writer", "node_start")

    # 准备数据
    entity = state.get("entity")
    if not entity:
        report_content = """# 研究报告生成失败

**原因**：无法解析输入的公司实体。

请检查输入是否正确。系统支持美股、A股、港股的公司名称或股票代码。

## 数据缺失说明
- 公司概览：数据缺失
- 业务与行业：数据缺失
- 财务分析：数据缺失
- 风险提示：数据缺失
- 投资结论：数据缺失
"""
        save_artifact(run_id, "05_report.md", report_content)
        duration_ms = int((time.time() - start_time) * 1000)
        run_log(run_id, "report_writer", "node_partial", level="warning", duration_ms=duration_ms, error="实体为空")
        append_log(run_id, {
            "node": "report_writer",
            "event": "partial",
            "reason": "entity is None",
            "duration_ms": duration_ms
        })
        return {
            "report_markdown": report_content,
            "report_status": "partial",
            "steps": [{"node": "report_writer", "status": "partial", "error": "实体为空", "duration_ms": duration_ms}],
            "errors": [{"node": "report_writer", "error": "实体为空"}]
        }
    
    profile = state.get("profile") or {}
    news = state.get("news") or []
    metrics = state.get("metrics") or {}
    financial_raw = state.get("financial_raw") or {}

    # 生成结构化财务摘要：不要把原始 JSON 直接截断到 2000 字，
    # 否则利润表可能在“税前利润”处被切断，净利润进不了 prompt。
    std = {}
    try:
        market = (entity or {}).get("market", "US")
        source = {"US": "us", "CN": "cn", "HK": "hk"}.get(market, "us")
        if financial_raw:
            std = normalize_financial_data(financial_raw, source=source)
    except Exception as e:
        logger.warning(f"构造财务摘要时标准化失败: {e}")

    summary = {
        "period_end": (
            (financial_raw.get("period_end") if isinstance(financial_raw, dict) else None)
            or ((financial_raw.get("filing_info") or {}).get("period_of_report")
                if isinstance(financial_raw, dict) else None)
        ),
        "revenue": std.get("revenue"),
        "net_income": std.get("net_income"),
        "gross_profit": std.get("gross_profit"),
        "operating_income": std.get("operating_income"),
        "total_assets": std.get("total_assets"),
        "total_liabilities": std.get("total_liabilities"),
        "total_equity": std.get("total_equity"),
        "operating_cash_flow": std.get("operating_cash_flow") or metrics.get("operating_cash_flow"),
        "capital_expenditure": std.get("capital_expenditure") or metrics.get("capital_expenditure"),
        "free_cash_flow": metrics.get("free_cash_flow"),
        "eps": std.get("eps") or metrics.get("eps"),
    }
    for key in (
        "gross_margin", "net_margin", "operating_margin", "roe", "roa",
        "debt_to_asset_ratio", "current_ratio", "quick_ratio",
        "market_cap", "pe", "pb", "ps", "ev_to_ebitda", "dividend_yield",
    ):
        summary[key] = metrics.get(key)
    data_meta = financial_raw.get("_data_meta") if isinstance(financial_raw, dict) else None
    if isinstance(data_meta, dict):
        summary["data_source"] = data_meta.get("source")
        summary["data_fetched_at"] = data_meta.get("fetched_at")
        summary["data_stale"] = data_meta.get("stale")
        summary["data_period_end"] = data_meta.get("period_end") or summary.get("period_end")
    financial_raw_summary = json.dumps(
        {k: v for k, v in summary.items() if v is not None},
        ensure_ascii=False, default=str,
    )
    
    # 构造 Prompt
    llm = get_llm(temperature=0.3, max_tokens=16000)
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", REPORT_SYSTEM_PROMPT),
        ("human", REPORT_HUMAN_TEMPLATE),
    ])
    
    chain = prompt | llm
    
    with track_node("report_writer") as tracker:
        try:
            # 调用 LLM 生成报告并记录 LLM 指标
            llm_start = time.time()
            report_md = await chain.ainvoke({
                "entity_info": json.dumps(entity, ensure_ascii=False, indent=2),
                "profile": json.dumps(profile, ensure_ascii=False, indent=2),
                "news": json.dumps(news, ensure_ascii=False, indent=2)[:3000],
                "financial_raw_summary": financial_raw_summary,
                "metrics": json.dumps(metrics, ensure_ascii=False, indent=2)[:3000],
            })
            research_llm_requests_total.labels(model=settings.deepseek_model, status="success").inc()
            research_llm_duration_seconds.labels(model=settings.deepseek_model).observe(time.time() - llm_start)
            
            # 提取内容（返回可能是 AIMessage）
            if hasattr(report_md, 'content'):
                report_content = report_md.content
            else:
                report_content = str(report_md)
            
            # 调试：打印 LLM 返回的原始信息
            logger.info(f"LLM 返回类型: {type(report_md).__name__}, 内容长度: {len(report_content) if report_content else 0}")
            if not report_content:
                logger.warning(f"LLM 返回了空内容！完整对象: {report_md}")
            
            # 确保报告内容不为空
            if not report_content or len(report_content.strip()) < 100:
                raise ValueError(f"LLM 生成的报告内容过短或为空（长度={len(report_content) if report_content else 0}）")
            
            # 保存报告
            save_artifact(run_id, "05_report.md", report_content)

            duration_ms = int((time.time() - start_time) * 1000)
            tracker.mark_success()
            run_log(run_id, "report_writer", "node_success", duration_ms=duration_ms, report_length=len(report_content))
            append_log(run_id, {
                "node": "report_writer",
                "event": "success",
                "report_length": len(report_content),
                "duration_ms": duration_ms
            })
            
            return {
                "report_markdown": report_content,
                "report_status": "success",
                "steps": [{"node": "report_writer", "status": "success", "duration_ms": int((time.time() - start_time) * 1000)}],
                "errors": []
            }
        except Exception as e:
            research_llm_requests_total.labels(model=settings.deepseek_model, status="error").inc()
            research_llm_duration_seconds.labels(model=settings.deepseek_model).observe(time.time() - llm_start)
            logger.error(f"报告生成失败: {e}")
            tracker.mark_partial()
            # 降级：使用简单模板填充，生成基础报告
            fallback_report = generate_fallback_report(entity, profile, news, metrics)
            save_artifact(run_id, "05_report.md", fallback_report)

            duration_ms = int((time.time() - start_time) * 1000)
            run_log(run_id, "report_writer", "node_partial", level="warning", duration_ms=duration_ms, error=str(e))
            append_log(run_id, {
                "node": "report_writer",
                "event": "partial",
                "error": str(e),
                "duration_ms": duration_ms
            })
            
            return {
                "report_markdown": fallback_report,
                "report_status": "partial",
                "steps": [{"node": "report_writer", "status": "partial", "error": str(e), "duration_ms": int((time.time() - start_time) * 1000)}],
                "errors": [{"node": "report_writer", "error": str(e)}]
            }
    
def generate_fallback_report(entity: Dict, profile: Dict, news: List, metrics: Dict) -> str:
    """当 LLM 生成失败时，使用模板生成一份“可读、明确标注缺失”的基础报告。

    注意：fallback 也必须满足 5 个核心 section 有实义内容，否则用户会拿到一份
    “标题齐全、正文为空”的假报告（评测指标 16 的 llm_timeout 用例会失败）。
    """
    company_name = entity.get("company_name", "未知公司")
    symbol = entity.get("symbol", "N/A")
    exchange = entity.get("exchange", "N/A")
    industry = profile.get("industry") or "未知行业"
    description = profile.get("description") or "本次未获取到公司简介。"

    lines = [
        f"# {company_name}（{symbol}）研究报告",
        "",
        "> 本报告由系统在降级模式下自动生成。部分数据缺失，已在对应位置明确标注；"
        "请勿把“数据缺失”理解为“该指标不存在”。",
        "",
        "## 1. 公司概览",
        f"- 公司名称：{company_name}",
        f"- 股票代码：{symbol}",
        f"- 交易所：{exchange}",
        f"- 所属行业：{industry}",
        f"- 公司简介：{description}",
        "- 说明：本节信息来自实体解析与已获取的公司概况；未取到的字段保持缺失，不做推测。",
        "",
        "## 2. 业务与行业",
        f"- 主营业务：{industry}。",
        "- 行业概况：本次未获取到完整的行业数据，无法对市场规模、增速与竞争格局做量化判断。",
        "- 竞争格局：建议结合公司年报、交易所公告与行业研究补充；在数据补齐前，"
        "不应仅凭本报告判断公司的行业地位。",
        "",
        "## 3. 近期新闻与舆情",
    ]
    if news:
        for n in news[:5]:
            title = n.get("title") or "（无标题）"
            date = n.get("date") or "日期缺失"
            lines.append(f"- {title}（{date}）")
        lines.append("- 以上新闻来自公开搜索接口，未逐条核验原文；舆情倾向需结合正文判断。")
    else:
        lines += [
            "- 本次未获取到可核验的近期新闻，新闻与舆情部分暂缺。",
            "- 这不代表公司没有重大事项；建议在决策前补充交易所公告、公司官网与主流财经媒体。",
            "- 数据缺失原因：搜索接口未返回有效结果或调用失败，系统未用旧闻或推测填充。",
        ]

    lines += [
        "",
        "## 4. 财务分析",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
    ]
    for key in ["revenue", "net_income", "gross_margin", "net_margin", "roe",
                "roa", "debt_to_asset_ratio", "current_ratio", "pe", "pb"]:
        val = metrics.get(key)
        if val is not None:
            lines.append(f"| {key} | {val:.6f} |")
        else:
            lines.append(f"| {key} | 数据缺失 |")
    lines += [
        "",
        "财务解读：上表为系统在降级模式下从已获取数据计算/汇总的指标；"
        "标注“数据缺失”的项表示本次未取到可靠数据，不应据此推断公司没有该指标。"
        "在数据补齐前，建议不要基于本表做估值判断。",
        "",
        "## 5. 风险提示",
        "- 数据缺失风险：关键财务或新闻数据未取到，可能导致结论偏差。",
        "- 行业与竞争风险：行业景气度、竞争格局与价格战可能影响收入与利润率。",
        "- 财务与估值风险：杠杆、现金流与估值口径若与行业惯例不一致，可能误导判断。",
        "- 宏观与政策风险：利率、汇率、监管与产业政策变化可能影响公司经营。",
        "- 流动性风险：市场情绪与交易拥挤度可能放大短期波动。",
        "",
        "## 6. 投资结论",
        "- 由于本次数据不完整，暂不给出明确投资评级或目标价。",
        "- 建议在补齐财务、新闻与估值数据后，结合行业研究与风险偏好再做决策。",
        "",
        "## 7. 附录：数据来源与说明",
        "- 数据来源：系统自动采集（实体解析、公开搜索、财务数据接口）。",
        "- 缺失数据说明：部分财务数据或新闻数据未获取成功，已在正文标注。",
        "- 免责声明：本报告为系统自动生成，不构成投资建议。",
    ]
    return "\n".join(lines)

async def quality_reviewer_node(state: ResearchState) -> Dict[str, Any]:
    """质量审核节点：检查报告完整性，判定最终状态"""
    start_time = time.time()
    run_id = state["run_id"]

    run_log(run_id, "quality_reviewer", "node_start")

    report_md = state.get("report_markdown", "")
    metrics = state.get("metrics") or {}
    entity_status = state.get("entity_status", "pending")
    report_status = state.get("report_status", "pending")
    financial_status = state.get("financial_status", "pending")
    
    # 检查章节完整性
    required_sections = [
        "## 1. 公司概览",
        "## 2. 业务与行业",
        "## 3. 近期新闻与舆情",
        "## 4. 财务分析",
        "## 5. 风险提示",
        "## 6. 投资结论",
        "## 7. 附录：数据来源与说明",
    ]
    missing_sections = [s for s in required_sections if s not in report_md]
    sections_complete = len(missing_sections) == 0
    
    # 统计非空指标数量
    non_null_metrics = {k: v for k, v in metrics.items() if v is not None}
    metric_count = len(non_null_metrics)
    min_required_metrics = 8
    metrics_sufficient = metric_count >= min_required_metrics
    
    # 检查报告是否有内容
    report_has_content = len(report_md.strip()) > 100  # 至少100字符
    
    # 判定最终状态
    if entity_status == "failed" or not report_has_content:
        final_status = "failed"
    elif sections_complete and metrics_sufficient:
        final_status = "success"
    else:
        final_status = "partial_success"
    
    # 构建审核结果
    review = {
        "sections_complete": sections_complete,
        "missing_sections": missing_sections,
        "metric_count": metric_count,
        "min_required_metrics": min_required_metrics,
        "metrics_sufficient": metrics_sufficient,
        "report_length": len(report_md),
        "entity_status": entity_status,
        "report_status": report_status,
        "financial_status": financial_status,
        "final_status": final_status,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }
    
    # 保存审核结果
    save_artifact(run_id, "06_review.json", review)

    with track_node("quality_reviewer") as tracker:
        duration_ms = int((time.time() - start_time) * 1000)
        tracker.mark_success()
        run_log(run_id, "quality_reviewer", "node_success", duration_ms=duration_ms, final_status=final_status)
        append_log(run_id, {
            "node": "quality_reviewer",
            "event": "success",
            "final_status": final_status,
            "missing_sections": missing_sections,
            "metric_count": metric_count,
            "duration_ms": duration_ms
        })
        
        return {
            "review": review,
            "final_status": final_status,
            "steps": [{"node": "quality_reviewer", "status": "success", "duration_ms": int((time.time() - start_time) * 1000)}],
            "errors": []
        }