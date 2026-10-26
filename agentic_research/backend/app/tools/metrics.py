"""
说明：

normalize_financial_data 只做了部分映射，对于 SEC 报表中的其他字段可能未覆盖，后续可根据实际数据调整键名。

A股/港股摘要的映射键需要根据实际接口返回调整，目前是示例。

计算时已做除零保护，缺失数据返回 None。

指标字典包含 30+ 个键，其中未计算的为 None。

"""




import logging
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)

class MetricsError(Exception):
    """指标计算异常"""
    pass

# 标准化财务数据字段名称
STANDARD_FIELDS = [
    "revenue", "gross_profit", "operating_income", "net_income", "ebitda",
    "total_assets", "total_liabilities", "total_equity",
    "current_assets", "current_liabilities", "inventory", "accounts_receivable",
    "operating_cash_flow", "capital_expenditure", "cash_and_equivalents",
    "short_term_investments", "long_term_debt", "short_term_debt",
    "shares_outstanding", "market_cap", "price", "eps", "dividend_per_share",
    "revenue_growth_yoy", "net_income_growth_yoy"
]

def safe_float(value: Any) -> Optional[float]:
    """尝试转换为 float，失败返回 None"""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

def normalize_financial_data(raw_data: Dict, source: str = "us") -> Dict:
    """
    将不同来源的原始财务数据转换为标准字段字典。
    
    参数:
        raw_data: 原始数据字典，可能包含 balance_sheet, income_statement, cash_flow 键（美股），
                  或直接是键值对（A股/港股摘要）
        source: 数据来源，"us", "cn", "hk" 之一
    
    返回:
        标准字段字典，缺失值为 None
    """
    std = {field: None for field in STANDARD_FIELDS}
    
    if source == "us":
        # 从三大报表中提取
        bs = raw_data.get("balance_sheet", {})
        is_ = raw_data.get("income_statement", {})
        cf = raw_data.get("cash_flow", {})
        
        # 收入：尝试多个可能的键
        revenue_keys = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", 
                        "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
                        "TotalRevenues", "Revenue", "Net sales", "Net Sales"]
        for key in revenue_keys:
            if key in is_:
                std["revenue"] = safe_float(is_[key])
                break
        
        # 毛利
        gross_profit_keys = ["GrossProfit", "GrossProfitLoss", "Gross margin", "Gross Margin"]
        for key in gross_profit_keys:
            if key in is_:
                std["gross_profit"] = safe_float(is_[key])
                break
        
        # 营业利润
        op_income_keys = ["OperatingIncomeLoss", "OperatingIncome", "Operating income", "Operating Income"]
        for key in op_income_keys:
            if key in is_:
                std["operating_income"] = safe_float(is_[key])
                break
        
        # 净利润
        net_income_keys = ["NetIncomeLoss", "ProfitLoss", "NetIncome", "Net income", "Net Income"]
        for key in net_income_keys:
            if key in is_:
                std["net_income"] = safe_float(is_[key])
                break
        
        # EBITDA 尝试直接获取，否则通过 营业利润 + 折旧摊销 计算
        ebitda_keys = ["EBITDA", "Ebitda"]
        for key in ebitda_keys:
            if key in is_:
                std["ebitda"] = safe_float(is_[key])
                break
        if std["ebitda"] is None:
            da_keys = ["Depreciation and amortization", "DepreciationAndAmortization",
                       "Depreciation, Depletion and Amortization"]
            dep = None
            for key in da_keys:
                if key in cf:
                    dep = safe_float(cf[key])
                    break
                if key in is_:
                    dep = safe_float(is_[key])
                    break
            if std["operating_income"] is not None and dep is not None:
                std["ebitda"] = std["operating_income"] + dep
        
        # 总资产
        total_assets_keys = ["Assets", "TotalAssets", "Total assets", "Total Assets"]
        for key in total_assets_keys:
            if key in bs:
                std["total_assets"] = safe_float(bs[key])
                break
        
        # 总负债
        total_liab_keys = ["Liabilities", "TotalLiabilities", "Total liabilities", "Total Liabilities"]
        for key in total_liab_keys:
            if key in bs:
                std["total_liabilities"] = safe_float(bs[key])
                break
        
        # 股东权益
        equity_keys = ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                       "TotalEquity", "Equity", "Total shareholders' equity", "Total shareholders’ equity",
                       "Total Shareholders' Equity", "Total Shareholders’ Equity"]
        for key in equity_keys:
            if key in bs:
                std["total_equity"] = safe_float(bs[key])
                break
        
        # 流动资产
        ca_keys = ["AssetsCurrent", "CurrentAssets", "Total current assets", "Total Current Assets"]
        for key in ca_keys:
            if key in bs:
                std["current_assets"] = safe_float(bs[key])
                break
        
        # 流动负债
        cl_keys = ["LiabilitiesCurrent", "CurrentLiabilities", "Total current liabilities", "Total Current Liabilities"]
        for key in cl_keys:
            if key in bs:
                std["current_liabilities"] = safe_float(bs[key])
                break
        
        # 存货
        inv_keys = ["InventoryNet", "Inventory", "Inventories"]
        for key in inv_keys:
            if key in bs:
                std["inventory"] = safe_float(bs[key])
                break
        
        # 应收账款
        ar_keys = ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent", "AccountsReceivable",
                   "Accounts receivable, net", "Accounts Receivable, net"]
        for key in ar_keys:
            if key in bs:
                std["accounts_receivable"] = safe_float(bs[key])
                break
        
        # 经营现金流
        ocf_keys = ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuing",
                    "Cash generated by operating activities", "Cash Generated by Operating Activities"]
        for key in ocf_keys:
            if key in cf:
                std["operating_cash_flow"] = safe_float(cf[key])
                break
        
        # 资本开支（通常在投资活动中，从报表获取较难，这里尝试常见键）
        capex_keys = ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsForCapitalExpenditures",
                      "Payments for acquisition of property, plant and equipment",
                      "Payments for Acquisition of Property, Plant and Equipment"]
        for key in capex_keys:
            if key in cf:
                std["capital_expenditure"] = safe_float(cf[key])
                break
        
        # 现金及等价物
        cash_keys = ["CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents",
                     "Cash and cash equivalents", "Cash and Cash Equivalents"]
        for key in cash_keys:
            if key in bs:
                std["cash_and_equivalents"] = safe_float(bs[key])
                break
        
        # 短期投资
        st_inv_keys = ["ShortTermInvestments", "AvailableForSaleSecuritiesCurrent",
                       "Marketable securities", "Marketable Securities"]
        for key in st_inv_keys:
            if key in bs:
                std["short_term_investments"] = safe_float(bs[key])
                break
        
        # 长期债务（非流动部分）
        lt_debt_keys = ["LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations",
                        "Long-term debt", "Long-Term Debt", "Term debt", "Term Debt"]
        for key in lt_debt_keys:
            if key in bs:
                std["long_term_debt"] = safe_float(bs[key])
                break
        
        # 短期债务
        st_debt_keys = ["ShortTermBorrowings", "ShortTermDebt", "DebtCurrent",
                        "Commercial paper", "Commercial Paper"]
        for key in st_debt_keys:
            if key in bs:
                std["short_term_debt"] = safe_float(bs[key])
                break
        
        # 每股收益
        eps_keys = ["Basic (in dollars per share)", "EarningsPerShareBasic",
                    "Basic EPS", "BasicEarningsPerShare"]
        for key in eps_keys:
            if key in is_:
                std["eps"] = safe_float(is_[key])
                break
        
        # 流通股数
        shares_keys = ["Basic (in shares)", "Common stock, shares outstanding (in shares)",
                       "CommonStockSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic"]
        for key in shares_keys:
            if key in is_:
                std["shares_outstanding"] = safe_float(is_[key])
                break
            if key in bs:
                std["shares_outstanding"] = safe_float(bs[key])
                break
        
    elif source == "cn":
        # A股摘要，raw_data 为扁平字典 {指标名: 最新值}
        # 注意：net_income 优先取“归母净利润”，与“归母权益/ROE”口径保持一致；
        # 若数据源没有归母，再退回“净利润”（合并口径）。
        mapping = {
            "revenue": ["营业总收入", "营业收入", "营业总收入(元)", "Total Revenue", "REVENUE"],
            "net_income": ["归母净利润", "归属于母公司所有者的净利润", "归属于母公司股东的净利润",
                           "净利润", "Net Income", "NET_INCOME"],
            "gross_profit": ["毛利", "毛利润", "Gross Profit"],
            "operating_income": ["营业利润", "经营溢利", "Operating Income"],
            "total_assets": ["总资产", "资产总计"],
            "total_liabilities": ["负债合计", "总负债"],
            "total_equity": ["股东权益合计(净资产)", "股东权益合计", "所有者权益合计",
                             "归属于母公司股东权益合计", "Total Equity", "股东权益"],
            "current_assets": ["流动资产合计"],
            "current_liabilities": ["流动负债合计"],
            "inventory": ["存货"],
            "accounts_receivable": ["应收账款"],
            "cash_and_equivalents": ["货币资金"],
            "operating_cash_flow": ["经营现金流量净额", "经营活动现金流量净额", "经营活动产生的现金流量净额",
                                     "Operating Cash Flow"],
            "eps": ["基本每股收益", "每股基本盈利", "EPS", "每股收益"],
            "dividend_per_share": ["每股股息", "每股股利", "Dividend per Share"],
            "revenue_growth_yoy": ["营业总收入增长率", "营业收入增长率"],
            "net_income_growth_yoy": ["归属母公司净利润增长率", "净利润增长率"],
        }
        for std_key, possible_keys in mapping.items():
            for pkey in possible_keys:
                if pkey in raw_data:
                    std[std_key] = safe_float(raw_data[pkey])
                    break

        # 派生计算：毛利率 -> gross_profit（仅当未直接映射到）
        if std["gross_profit"] is None:
            for key in ("毛利率", "销售毛利率"):
                if key in raw_data and std["revenue"] is not None:
                    gm = safe_float(raw_data[key])
                    if gm is not None:
                        std["gross_profit"] = std["revenue"] * gm / 100.0
                    break

        # 派生计算：营业利润率 -> operating_income（仅当未直接映射到）
        if std["operating_income"] is None:
            if "营业利润率" in raw_data and std["revenue"] is not None:
                om = safe_float(raw_data["营业利润率"])
                if om is not None:
                    std["operating_income"] = std["revenue"] * om / 100.0

        # 派生计算：资产负债率 + 权益 -> total_assets, total_liabilities
        if std["total_assets"] is None and "资产负债率" in raw_data and std["total_equity"] is not None:
            dr = safe_float(raw_data["资产负债率"])
            if dr is not None:
                ratio = dr / 100.0
                std["total_assets"] = std["total_equity"] / (1 - ratio) if ratio < 1 else None
                if std["total_assets"] is not None:
                    std["total_liabilities"] = std["total_assets"] - std["total_equity"]

        # 派生计算：每股净资产 -> shares_outstanding
        for key in ("每股净资产", "每股净资产_最新股数"):
            if key in raw_data and std["total_equity"] is not None:
                bvps = safe_float(raw_data[key])
                if bvps is not None and bvps != 0:
                    std["shares_outstanding"] = std["total_equity"] / bvps
                break

    elif source == "hk":
        # 港股：利润表 + 资产负债表 + 现金流量表，金额单位已由东财统一为报告币种基础单位
        mapping = {
            "revenue": ["营业额", "营运收入"],
            "net_income": ["股东应占溢利", "本公司拥有人应占溢利", "除税后溢利"],
            "gross_profit": ["毛利"],
            "operating_income": ["经营溢利"],
            "total_assets": ["总资产"],
            "total_liabilities": ["总负债"],
            # 股东权益=归母权益；净资产/总权益含少数股东，作为兜底
            "total_equity": ["股东权益", "净资产", "总权益"],
            "current_assets": ["流动资产合计"],
            "current_liabilities": ["流动负债合计"],
            "inventory": ["存货"],
            "cash_and_equivalents": ["现金及现金等价物", "期末现金"],
            "operating_cash_flow": ["经营业务现金净额", "经营活动现金流量净额"],
            "capital_expenditure": ["购买物业厂房及设备", "购买固定资产"],
            "eps": ["每股基本盈利", "每股摊薄盈利"],
            "dividend_per_share": ["每股股息", "每股股利"],
        }
        for std_key, possible_keys in mapping.items():
            for pkey in possible_keys:
                if pkey in raw_data:
                    std[std_key] = safe_float(raw_data[pkey])
                    break
        if std["total_assets"] is not None and std["total_liabilities"] is None and std["total_equity"] is not None:
            std["total_liabilities"] = std["total_assets"] - std["total_equity"]

    else:
        raise MetricsError(f"不支持的数据源: {source}")
    
    return std

def calculate_metrics(std: Dict) -> Dict:
    """
    根据标准化财务数据计算财务指标。
    输入: std 字典（可由 normalize_financial_data 生成，或手动构建）
    输出: 指标字典，键为指标名称，值为 float 或 None
    """
    metrics = {}
    
    # 提取常用字段
    revenue = std.get("revenue")
    gross_profit = std.get("gross_profit")
    operating_income = std.get("operating_income")
    net_income = std.get("net_income")
    ebitda = std.get("ebitda")
    total_assets = std.get("total_assets")
    total_liabilities = std.get("total_liabilities")
    total_equity = std.get("total_equity")
    current_assets = std.get("current_assets")
    current_liabilities = std.get("current_liabilities")
    inventory = std.get("inventory")
    accounts_receivable = std.get("accounts_receivable")
    ocf = std.get("operating_cash_flow")
    capex = std.get("capital_expenditure")
    cash = std.get("cash_and_equivalents")
    st_invest = std.get("short_term_investments")
    lt_debt = std.get("long_term_debt")
    st_debt = std.get("short_term_debt")
    shares = std.get("shares_outstanding")
    market_cap = std.get("market_cap")
    price = std.get("price")
    eps = std.get("eps")
    dps = std.get("dividend_per_share")
    
    # --- 盈利能力 ---
    # 毛利率
    if revenue and gross_profit is not None and revenue != 0:
        metrics["gross_margin"] = gross_profit / revenue
    else:
        metrics["gross_margin"] = None
    
    # 净利率
    if revenue and net_income is not None and revenue != 0:
        metrics["net_margin"] = net_income / revenue
    else:
        metrics["net_margin"] = None
    
    # 营业利润率
    if revenue and operating_income is not None and revenue != 0:
        metrics["operating_margin"] = operating_income / revenue
    else:
        metrics["operating_margin"] = None
    
    # EBITDA利润率
    if revenue and ebitda is not None and revenue != 0:
        metrics["ebitda_margin"] = ebitda / revenue
    else:
        metrics["ebitda_margin"] = None
    
    # ROE
    if net_income is not None and total_equity and total_equity != 0:
        metrics["roe"] = net_income / total_equity
    else:
        metrics["roe"] = None
    
    # ROA
    if net_income is not None and total_assets and total_assets != 0:
        metrics["roa"] = net_income / total_assets
    else:
        metrics["roa"] = None
    
    # --- 成长能力 ---
    # 如果提供了同比增长率，直接使用，否则尝试计算（需要历史数据，这里无法计算）
    metrics["revenue_growth_yoy"] = std.get("revenue_growth_yoy")
    metrics["net_income_growth_yoy"] = std.get("net_income_growth_yoy")
    # 如果未提供，则尝试从摘要中获取（但已在标准化时提取）
    
    # EPS同比增长率（无法计算，留None）
    metrics["eps_growth_yoy"] = None
    
    # --- 现金流 ---
    metrics["operating_cash_flow"] = ocf
    metrics["capital_expenditure"] = capex
    if ocf is not None and capex is not None:
        metrics["free_cash_flow"] = ocf - capex
    else:
        metrics["free_cash_flow"] = None
    
    # --- 偿债能力与杠杆 ---
    if total_assets and total_assets != 0 and total_liabilities is not None:
        metrics["debt_to_asset_ratio"] = total_liabilities / total_assets
    else:
        metrics["debt_to_asset_ratio"] = None
    
    if current_assets is not None and current_liabilities and current_liabilities != 0:
        metrics["current_ratio"] = current_assets / current_liabilities
    else:
        metrics["current_ratio"] = None
    
    # 速动比率 = (流动资产 - 存货) / 流动负债
    if current_assets is not None and inventory is not None and current_liabilities and current_liabilities != 0:
        metrics["quick_ratio"] = (current_assets - inventory) / current_liabilities
    else:
        metrics["quick_ratio"] = None
    
    # --- 运营效率 ---
    # 应收账款周转率 = 收入 / 应收账款（平均，但我们只有期末）
    if revenue and accounts_receivable and accounts_receivable != 0:
        metrics["receivables_turnover"] = revenue / accounts_receivable
    else:
        metrics["receivables_turnover"] = None
    
    # 存货周转率 = 收入 / 存货（或销货成本，无则用收入近似）
    if revenue and inventory and inventory != 0:
        metrics["inventory_turnover"] = revenue / inventory
    else:
        metrics["inventory_turnover"] = None
    
    # 总资产周转率 = 收入 / 总资产
    if revenue and total_assets and total_assets != 0:
        metrics["asset_turnover"] = revenue / total_assets
    else:
        metrics["asset_turnover"] = None
    
    # --- 市场估值 ---
    # 市值优先使用提供的 market_cap，否则尝试用 股价 * 股本
    if market_cap is None and price is not None and shares is not None:
        market_cap = price * shares
    metrics["market_cap"] = market_cap
    
    # PE = 市值 / 净利润
    if market_cap and net_income and net_income != 0:
        metrics["pe"] = market_cap / net_income
    else:
        metrics["pe"] = None
    
    # PB = 市值 / 股东权益
    if market_cap and total_equity and total_equity != 0:
        metrics["pb"] = market_cap / total_equity
    else:
        metrics["pb"] = None
    
    # PS = 市值 / 收入
    if market_cap and revenue and revenue != 0:
        metrics["ps"] = market_cap / revenue
    else:
        metrics["ps"] = None
    
    # EV/EBITDA
    # EV = 市值 + 总负债 - 现金及等价物
    if market_cap is not None and total_liabilities is not None and cash is not None:
        ev = market_cap + total_liabilities - cash
        if ebitda and ebitda != 0:
            metrics["ev_to_ebitda"] = ev / ebitda
        else:
            metrics["ev_to_ebitda"] = None
    else:
        metrics["ev_to_ebitda"] = None
    
    # --- 股东回报 ---
    metrics["eps"] = eps
    if price and dps is not None and price != 0:
        metrics["dividend_yield"] = dps / price
    else:
        metrics["dividend_yield"] = None
    
    # 额外计算：资产负债率（同 debt_to_asset_ratio）
    # 已包含
    return metrics

# 为了向后兼容，提供便捷函数
def calculate_metrics_from_raw(raw_data: Dict, source: str = "us") -> Dict:
    std = normalize_financial_data(raw_data, source)
    return calculate_metrics(std)