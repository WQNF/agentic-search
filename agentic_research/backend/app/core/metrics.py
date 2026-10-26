import time
import functools
from contextlib import contextmanager
from typing import Callable

from prometheus_client import Counter, Histogram

# ========== 任务级指标 ==========

research_tasks_created_total = Counter(
    "research_tasks_submitted",
    "Total number of research tasks created"
)

research_tasks_total = Counter(
    "research_tasks",
    "Total number of research tasks completed by final status",
    labelnames=["status"]
)

research_task_duration_seconds = Histogram(
    "research_task_duration_seconds",
    "Task total duration in seconds",
    labelnames=["status"],
    buckets=[5, 15, 30, 60, 120, 300, 600, 1200]
)

# ========== 节点级指标 ==========

research_node_duration_seconds = Histogram(
    "research_node_duration_seconds",
    "Node duration in seconds",
    labelnames=["node", "status"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60, 120]
)

research_node_events_total = Counter(
    "research_node_events",
    "Node event counter",
    labelnames=["node", "event"]
)

research_retry_total = Counter(
    "research_retry",
    "Total number of retries",
    labelnames=["node"]
)

# ========== 工具级指标 ==========

research_tool_calls_total = Counter(
    "research_tool_calls",
    "Total number of tool calls",
    labelnames=["tool"]
)

research_tool_errors_total = Counter(
    "research_tool_errors",
    "Total number of tool errors",
    labelnames=["tool", "error_type"]
)

research_tool_duration_seconds = Histogram(
    "research_tool_duration_seconds",
    "Tool call duration in seconds",
    labelnames=["tool"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60]
)

# ========== LLM 指标 ==========

research_llm_requests_total = Counter(
    "research_llm_requests",
    "Total number of LLM requests",
    labelnames=["model", "status"]
)

research_llm_duration_seconds = Histogram(
    "research_llm_duration_seconds",
    "LLM request duration in seconds",
    labelnames=["model"],
    buckets=[0.5, 1, 2, 5, 10, 30, 60, 120]
)

# ========== 缓存指标（可选） ==========

research_cache_hits_total = Counter(
    "research_cache_hits",
    "Total number of cache hits",
    labelnames=["key_type"]
)

research_cache_misses_total = Counter(
    "research_cache_misses",
    "Total number of cache misses",
    labelnames=["key_type"]
)

# ========== 工具调用埋点装饰器 ==========

def track_tool(tool_name: str):
    """工具调用埋点装饰器。自动记录调用次数、耗时、错误。"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            research_tool_calls_total.labels(tool=tool_name).inc()
            start = time.time()
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start
                research_tool_duration_seconds.labels(tool=tool_name).observe(duration)
                return result
            except Exception as e:
                duration = time.time() - start
                research_tool_duration_seconds.labels(tool=tool_name).observe(duration)
                error_type = type(e).__name__
                research_tool_errors_total.labels(tool=tool_name, error_type=error_type).inc()
                raise
        return wrapper
    return decorator

# ========== 节点埋点上下文管理器 ==========

@contextmanager
def track_node(node_name: str):
    """节点埋点上下文管理器。自动记录节点事件、耗时。"""
    start = time.time()
    status = ["unknown"]
    research_node_events_total.labels(node=node_name, event="node_start").inc()

    class NodeTracker:
        def mark_success(self):
            status[0] = "success"
            research_node_events_total.labels(node=node_name, event="node_success").inc()

        def mark_partial(self):
            status[0] = "partial"
            research_node_events_total.labels(node=node_name, event="node_partial").inc()

        def mark_error(self):
            status[0] = "error"
            research_node_events_total.labels(node=node_name, event="node_error").inc()

    tracker = NodeTracker()
    try:
        yield tracker
    finally:
        duration = time.time() - start
        research_node_duration_seconds.labels(node=node_name, status=status[0]).observe(duration)