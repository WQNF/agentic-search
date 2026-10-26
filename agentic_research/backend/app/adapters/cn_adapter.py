#A股适配器 CNAdapter
import os
import json
import logging
import time
from pathlib import Path
from typing import Optional, Dict, List
import httpx
import akshare as ak
from app.adapters.base import MarketAdapter, EntityResolutionError
from app.Schemas.entity import Entity

logger = logging.getLogger(__name__)

class CNAdapter(MarketAdapter):
    """A股适配器，通过东方财富公开接口获取股票列表并缓存本地（24小时过期）"""
    
    # 东方财富沪深A股列表接口（无需认证，公开可用）
    EASTMONEY_URL = (
        "https://push2.eastmoney.com/api/qt/clist/get"
        "?pn=1&pz=6000&po=1&np=1&fltt=2&invt=2"
        "&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
        "&fields=f12,f14"
    )
    CACHE_FILE = Path("data/cn_stock_list.json")
    CACHE_TTL = 86400  # 缓存有效期：24小时（秒）
    
    def __init__(self):
        self._stock_list: Optional[List[Dict]] = None
    
    def _save_cache(self, stock_list: List[Dict]) -> None:
        self.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(self.CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(stock_list, f, ensure_ascii=False)
    
    def _download_from_eastmoney(self) -> List[Dict]:
        """备用方案：直接请求东方财富接口（绕过代理，可能受分页限制）"""
        try:
            logger.info("正在从东方财富接口下载A股股票列表（绕过代理）...")
            response = httpx.get(self.EASTMONEY_URL, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }, timeout=30, follow_redirects=True, trust_env=False)
            response.raise_for_status()
            data = response.json()
            items = data.get("data", {}).get("diff", [])
            if not items:
                raise EntityResolutionError("东方财富接口返回空数据")
            stock_list = []
            for item in items:
                code = item.get("f12", "")
                name = item.get("f14", "")
                if code and name:
                    stock_list.append({"code": code, "name": name})
            if not stock_list:
                raise EntityResolutionError("未能解析出股票列表")
            self._save_cache(stock_list)
            self._stock_list = stock_list
            logger.info(f"东方财富接口返回 {len(stock_list)} 只A股（可能不全）")
            return stock_list
        except EntityResolutionError:
            raise
        except Exception as e:
            logger.error(f"东方财富接口也失败: {e}")
            if self.CACHE_FILE.exists():
                logger.warning("降级使用过期缓存")
                with open(self.CACHE_FILE, "r", encoding="utf-8") as f:
                    self._stock_list = json.load(f)
                return self._stock_list
            raise EntityResolutionError("无法加载A股股票列表，请稍后重试")
    
    def _is_cache_valid(self) -> bool:
        """检查缓存是否在有效期内"""
        if not self.CACHE_FILE.exists():
            return False
        mtime = self.CACHE_FILE.stat().st_mtime
        return (time.time() - mtime) < self.CACHE_TTL
    
    def _load_stock_list(self) -> List[Dict]:
        """加载A股股票列表，优先使用本地缓存（24小时有效）"""
        if self._stock_list is not None:
            return self._stock_list
        
        # 如果缓存有效，直接加载
        if self._is_cache_valid():
            with open(self.CACHE_FILE, "r", encoding="utf-8") as f:
                self._stock_list = json.load(f)
            logger.info(f"从缓存加载了 {len(self._stock_list)} 只A股")
            return self._stock_list
        
        # 缓存过期或不存在，重新下载
        # 主方案：akshare（绕过代理，自动处理分页，能获取全量）
        try:
            logger.info("正在通过 akshare 下载A股股票列表（绕过代理）...")
            # 临时清除代理环境变量，绕过代理直连
            old_proxy = {k: os.environ.pop(k, None) for k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']}
            try:
                df = ak.stock_zh_a_spot_em()
            finally:
                # 恢复代理环境变量
                for k, v in old_proxy.items():
                    if v is not None:
                        os.environ[k] = v
            stock_list = []
            for _, row in df.iterrows():
                code = str(row.get('代码', ''))
                name = str(row.get('名称', ''))
                if code and name:
                    stock_list.append({"code": code, "name": name})
            if not stock_list:
                raise EntityResolutionError("akshare 返回空股票列表")
            self._save_cache(stock_list)
            self._stock_list = stock_list
            logger.info(f"下载并缓存了 {len(stock_list)} 只A股")
            return stock_list
        except EntityResolutionError:
            raise
        except Exception as e:
            logger.warning(f"akshare 下载失败: {e}，尝试东方财富接口")
            return self._download_from_eastmoney()
    
    def _parse_code(self, code: str) -> Optional[str]:
        """将输入标准化为 代码.交易所 格式，如 600519.SH"""
        code = code.strip().upper()
        # 去除可能的前缀/后缀
        if '.' in code:
            parts = code.split('.')
            code = parts[0]
            exchange = parts[1] if len(parts) > 1 else ''
        else:
            exchange = ''
        # 纯数字，长度6
        if code.isdigit() and len(code) == 6:
            if exchange in ['SH', 'SSE'] or (exchange == '' and code.startswith('6')):
                return f"{code}.SH"
            elif exchange in ['SZ', 'SZSE'] or (exchange == '' and code.startswith(('0', '3'))):
                return f"{code}.SZ"
            elif exchange == 'BJ':
                return f"{code}.BJ"
        return None
    
    def _search_by_name(self, name: str) -> Optional[Entity]:
        stock_list = self._load_stock_list()
        # 精确匹配
        for item in stock_list:
            if item['name'] == name:
                symbol = self._parse_code(item['code'])
                if symbol:
                    return Entity(market="CN", symbol=symbol, exchange=self._get_exchange(symbol), company_name=item['name'])
        # 模糊匹配
        matches = [item for item in stock_list if name in item['name']]
        if len(matches) == 1:
            item = matches[0]
            symbol = self._parse_code(item['code'])
            if symbol:
                return Entity(market="CN", symbol=symbol, exchange=self._get_exchange(symbol), company_name=item['name'])
        elif len(matches) > 1:
            raise EntityResolutionError(f"找到多个匹配：{[m['name'] for m in matches]}")
        return None
    
    def _get_exchange(self, symbol: str) -> str:
        if symbol.endswith('.SH'):
            return 'SSE'
        elif symbol.endswith('.SZ'):
            return 'SZSE'
        elif symbol.endswith('.BJ'):
            return 'BSE'
        return ''
    
    def _sina_exists(self, code: str) -> bool:
        """对不在本地列表里的 6 位代码做一次实时校验，避免把 999999.SH 当成有效实体。"""
        if code.startswith("6"):
            sina_code = f"sh{code}"
        elif code.startswith(("4", "8")):
            sina_code = f"bj{code}"
        else:
            sina_code = f"sz{code}"
        try:
            resp = httpx.get(
                f"https://hq.sinajs.cn/list={sina_code}",
                headers={
                    "Referer": "https://finance.sina.com.cn",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                },
                timeout=8,
                trust_env=False,
            )
            if resp.status_code != 200 or '"' not in resp.text:
                return False
            payload = resp.text.split('"')[1]
            fields = payload.split(",")
            return len(fields) >= 6 and bool(fields[0].strip())
        except Exception:
            return False

    def resolve(self, query: str) -> Entity:
        query = query.strip()
        # 尝试解析代码
        symbol = self._parse_code(query)
        if symbol:
            # 通过代码查找名称
            stock_list = self._load_stock_list()
            code = symbol.split('.')[0]
            for item in stock_list:
                if item['code'] == code:
                    return Entity(market="CN", symbol=symbol, exchange=self._get_exchange(symbol), company_name=item['name'])
            # 代码不在本地缓存（缓存可能不完整）：先做实时校验，存在才返回
            if self._sina_exists(code):
                logger.warning(f"代码 {code} 不在本地列表，但新浪行情存在，使用代码作为名称")
                return Entity(market="CN", symbol=symbol, exchange=self._get_exchange(symbol), company_name=code)
            raise EntityResolutionError(f"无法识别A股实体：{query}（代码不存在或已退市/停牌）")
        # 按名称搜索
        entity = self._search_by_name(query)
        if entity:
            return entity
        raise EntityResolutionError(f"无法识别A股实体：{query}")