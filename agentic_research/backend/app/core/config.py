from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # Application
    app_name: str = "Agentic Research"
    app_env: str = "development"
    debug: bool = True
    secret_key: str = "change_me"
    access_token_expire_minutes: int = 1440

    # Database
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_user: str = "research"
    postgres_password: str = "research_password"
    postgres_db: str = "research_db"

    # Redis
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 0

    # LLM（密钥只从 .env / 环境变量读取，不要在代码里写默认 key）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # Search
    tavily_api_key: str = ""
    serper_api_key: str = ""
    search_provider: str = "tavily"

    # Data Source
    sec_user_agent: str = "contact@example.com"

    # Proxy
    https_proxy: str = ""  # 如 http://127.0.0.1:7890

    # Stock Quote
    finnhub_api_key: str = ""  # 可选，用于行情备用
    stock_quote_provider: str = "yfinance"  # yfinance, finnhub, or akshare

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()