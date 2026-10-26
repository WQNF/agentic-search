#适配器抽象基类

from abc import ABC, abstractmethod
from app.Schemas.entity import Entity

class EntityResolutionError(Exception):
    """实体解析失败异常"""
    pass

class MarketAdapter(ABC):
    """市场适配器抽象基类"""
    
    @abstractmethod
    def resolve(self, query: str) -> Entity:
        """解析用户输入为 Entity，失败抛出 EntityResolutionError"""
        pass