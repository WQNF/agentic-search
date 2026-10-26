import logging
import structlog
import sys
from pathlib import Path


def setup_logging(log_file: str = "logs/app.jsonl", level: int = logging.INFO):
    """配置 structlog 输出 JSON 到 stdout，同时可选写入全局日志文件"""
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    # 基础 logging 配置（供第三方库使用）
    logging.basicConfig(
        level=level,
        format="%(message)s",
        stream=sys.stdout,
    )

    # 降低第三方库噪音
    for noisy in ["httpx", "httpcore", "urllib3", "asyncio", "openai"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # structlog 配置
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False, sort_keys=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = None):
    """获取 structlog logger"""
    return structlog.get_logger(name)