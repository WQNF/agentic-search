#港股适配器 HKAdapter

import re
import logging
from typing import Optional
import requests
from app.adapters.base import MarketAdapter, EntityResolutionError
from app.Schemas.entity import Entity

logger = logging.getLogger(__name__)

class HKAdapter(MarketAdapter):
    """港股适配器，通过新浪财经API解析实体"""

    SINA_QUOTE_URL = "https://hq.sinajs.cn/list="

    def _parse_code(self, code: str) -> Optional[str]:
        code = code.strip().upper()
        if code.endswith('.HK'):
            code = code[:-3]
        if code.isdigit():
            code = code.zfill(5)
            return f"{code}.HK"
        return None

    def _lookup_by_sina(self, code: str) -> Optional[Entity]:
        """通过新浪财经接口查询单个港股代码，验证是否存在并获取名称"""
        raw_code = code.split('.')[0]  # 00700
        url = f"{self.SINA_QUOTE_URL}hk{raw_code}"
        headers = {"Referer": "https://finance.sina.com.cn"}
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                return None
            text = resp.text
            if text == '' or '=' not in text:
                return None
            # 格式: var hq_str_hk00700="TENCENT,腾讯控股,..."
            data = text.split('"')[1] if '"' in text else ''
            if not data:
                return None
            parts = data.split(',')
            if len(parts) >= 2 and parts[1]:
                return Entity(
                    market="HK",
                    symbol=code,
                    exchange="HKEX",
                    company_name=parts[1]
                )
        except Exception as e:
            logger.warning(f"新浪查询港股 {code} 失败: {e}")
        return None

    def resolve(self, query: str) -> Entity:
        query = query.strip()
        # 尝试解析代码
        symbol = self._parse_code(query)
        if symbol:
            entity = self._lookup_by_sina(symbol)
            if entity:
                return entity
        # 按名称搜索：尝试新浪代码反查
        raise EntityResolutionError(
            f"无法识别港股实体：{query}。请使用港股代码（如 0700、00700.HK）查询"
        )