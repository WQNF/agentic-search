#定义 Entity 模型

from typing import Optional, Literal
from pydantic import BaseModel

class Entity(BaseModel):
    market: Literal["US", "CN", "HK"]
    symbol: str
    exchange: str
    company_name: str
    cik: Optional[str] = None
    isin: Optional[str] = None