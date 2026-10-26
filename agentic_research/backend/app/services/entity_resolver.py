#实体解析服务（统一入口）
from app.adapters.base import MarketAdapter, EntityResolutionError
from app.adapters.us_adapter import USAdapter
from app.adapters.cn_adapter import CNAdapter
from app.adapters.hk_adapter import HKAdapter
from app.Schemas.entity import Entity

class EntityResolver:
    def __init__(self):
        self._adapters = {
            "US": USAdapter(),
            "CN": CNAdapter(),
            "HK": HKAdapter(),
        }
    
    def resolve(self, query: str) -> Entity:
        query = query.strip()
        # 简单判断市场优先级
        # 1. 包含 .HK 或1~5位纯数字 => 港股
        if query.upper().endswith('.HK') or (query.isdigit() and 1 <= len(query) <= 5):
            return self._adapters["HK"].resolve(query)
        # 2. 包含 .SH/.SZ 或长度6位纯数字 => A股
        if any(suffix in query.upper() for suffix in ['.SH', '.SZ', '.BJ']) or (query.isdigit() and len(query) == 6):
            return self._adapters["CN"].resolve(query)
        # 3. 如果包含中文，优先尝试A股/港股名称
        if any('\u4e00' <= c <= '\u9fff' for c in query):
            try:
                return self._adapters["CN"].resolve(query)
            except EntityResolutionError:
                return self._adapters["HK"].resolve(query)
        # 4. 纯ASCII字母且长度较短，可能是美股代码
        if query.isascii() and query.isalpha() and len(query) <= 5:
            return self._adapters["US"].resolve(query)
        # 5. 如果以上都不匹配，先尝试美股（公司名），再尝试A股/港股名称
        try:
            return self._adapters["US"].resolve(query)
        except EntityResolutionError:
            try:
                return self._adapters["CN"].resolve(query)
            except EntityResolutionError:
                return self._adapters["HK"].resolve(query)

entity_resolver = EntityResolver()