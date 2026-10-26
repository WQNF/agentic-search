from langchain_openai import ChatOpenAI

from app.core.config import settings

def get_llm(temperature: float = 0.2, max_tokens: int = 2000):
    """获取 LLM 实例（DeepSeek 国内直连，不走代理）"""
    return ChatOpenAI(
        base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key,
        model=settings.deepseek_model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=60,
        max_retries=2,
    )