#创建 ResearchState 定义

from typing import TypedDict, List, Optional, Dict, Any

class ResearchState(TypedDict):
    # 输入
    query: str
    run_id: str
    user_id: str
    
    # 实体标准化
    entity: Optional[Dict[str, Any]]   # Entity 的 dict 形式
    entity_status: str                  # success / partial / failed
    
    # 信息搜集
    profile: Optional[Dict[str, Any]]
    news: List[Dict[str, Any]]
    profile_status: str
    
    # 财务分析
    financial_raw: Optional[Dict[str, Any]]
    metrics: Optional[Dict[str, Any]]
    financial_status: str
    
    # 报告
    report_markdown: str
    report_status: str
    
    # 审核
    review: Optional[Dict[str, Any]]
    final_status: str
    
    # 步骤与错误
    steps: List[Dict[str, Any]]
    errors: List[Dict[str, Any]]