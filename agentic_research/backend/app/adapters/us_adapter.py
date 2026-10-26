import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
import httpx
from app.adapters.base import MarketAdapter, EntityResolutionError
from app.Schemas.entity import Entity

logger = logging.getLogger(__name__)

class USAdapter(MarketAdapter):
    """美股适配器，基于 SEC EDGAR 公司映射数据"""
    
    SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    CACHE_FILE = Path("data/sec_company_tickers.json")
    
    def __init__(self):
        self._tickers_data: Optional[Dict] = None
    
    def _load_tickers_data(self) -> Dict:
        """加载 SEC 公司映射数据，优先使用本地缓存"""
        if self._tickers_data is not None:
            return self._tickers_data
        
        # 如果缓存文件存在，加载
        if self.CACHE_FILE.exists():
            with open(self.CACHE_FILE, "r") as f:
                self._tickers_data = json.load(f)
            return self._tickers_data
        
        # 否则下载并缓存
        try:
            response = httpx.get(self.SEC_TICKERS_URL, headers={"User-Agent": "research-agent contact@example.com"})
            response.raise_for_status()
            self._tickers_data = response.json()
            # 保存缓存
            self.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.CACHE_FILE, "w") as f:
                json.dump(self._tickers_data, f)
            return self._tickers_data
        except Exception as e:
            logger.error(f"Failed to load SEC tickers data: {e}")
            raise EntityResolutionError("无法加载 SEC 公司数据，请稍后重试")
    
    def _search_by_ticker(self, ticker: str) -> Optional[Entity]:
        """按股票代码精确匹配"""
        data = self._load_tickers_data()
        for item in data.values():
            if item["ticker"].upper() == ticker.upper():
                return Entity(
                    market="US",
                    symbol=item["ticker"],
                    exchange="",  # 需要额外获取交易所信息，暂时留空，后续可补充
                    company_name=item["title"],
                    cik=str(item["cik_str"]).zfill(10)
                )
        return None
    
    def _search_by_name(self, name: str) -> Optional[Entity]:
        """按公司名称模糊匹配"""
        data = self._load_tickers_data()
        name_upper = name.upper()
        
        # 先尝试精确匹配
        for item in data.values():
            if item["title"].upper() == name_upper:
                return Entity(
                    market="US",
                    symbol=item["ticker"],
                    exchange="",
                    company_name=item["title"],
                    cik=str(item["cik_str"]).zfill(10)
                )
        
        # 再尝试包含匹配
        matches = [
            item for item in data.values()
            if name_upper in item["title"].upper()
        ]
        if len(matches) == 1:
            item = matches[0]
            return Entity(
                market="US",
                symbol=item["ticker"],
                exchange="",
                company_name=item["title"],
                cik=str(item["cik_str"]).zfill(10)
            )
        elif len(matches) > 1:
            # 多个匹配，抛出异常，提示用户更具体
            raise EntityResolutionError(f"找到多个匹配：{[m['title'] for m in matches]}")
        
        return None
    
    def resolve(self, query: str) -> Entity:
        query = query.strip()
        if not query:
            raise EntityResolutionError("输入不能为空")
        
        # 判断是否像股票代码（全字母，长度<=5）
        if query.isalpha() and len(query) <= 5:
            entity = self._search_by_ticker(query)
            if entity:
                return entity
        
        # 尝试按名称搜索
        entity = self._search_by_name(query)
        if entity:
            return entity
        
        raise EntityResolutionError(f"无法识别美股实体：{query}")