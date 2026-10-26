"""报告结构解析与字段抽取。

只做确定性解析；解析不确定的值会带 needs_review 标记，交由人工复核。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .config import FIELD_LABELS, FIELD_PANEL, SECTION_ALIASES

# ---------------------------------------------------------------------------
# 基础清洗
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,4})\s*(.+?)\s*$")
_NUMBER_RE = re.compile(r"[-+]?\d[\d,，]*(?:\.\d+)?")


def decode_report(raw: str) -> str:
    """兼容两种来源：内存中的 Markdown，以及被 json.dump 成 \"...\\n...\" 的 .md 产物。"""
    if not raw:
        return ""
    s = raw.strip()
    if s.startswith('"') and s.endswith('"') and "\\n" in s:
        try:
            decoded = json.loads(s)
            if isinstance(decoded, str):
                return decoded
        except Exception:
            pass
    return raw


def strip_code_fences(md: str) -> str:
    return re.sub(r"```[a-zA-Z]*\n?|```", "", md)


def heading_title(line: str) -> Optional[Tuple[int, str]]:
    m = _HEADING_RE.match(line.strip())
    if not m:
        return None
    level = len(m.group(1))
    title = m.group(2).strip()
    title = re.sub(r"^\d+(?:\.\d+)*[.、)）]?\s*", "", title).strip()
    title = title.strip("*_ ")
    return level, title


def _match_section(title: str) -> Optional[str]:
    for canonical, aliases in SECTION_ALIASES.items():
        for a in aliases:
            if a == title or a in title:
                return canonical
    return None


def split_sections(md: str) -> Dict[str, Dict]:
    """按标题切分 5 个核心 section。返回 {canonical: {heading, body, level}}。"""
    md = decode_report(md)
    lines = md.splitlines()
    headings = []  # (idx, level, canonical)
    for i, line in enumerate(lines):
        ht = heading_title(line)
        if not ht:
            continue
        level, title = ht
        canonical = _match_section(title)
        headings.append((i, level, canonical, title))

    out: Dict[str, Dict] = {}
    for pos, (idx, level, canonical, title) in enumerate(headings):
        if not canonical:
            continue
        end = len(lines)
        for idx2, level2, _c2, _t2 in headings[pos + 1:]:
            if level2 <= level:
                end = idx2
                break
        body = "\n".join(lines[idx + 1:end]).strip()
        # 同名多级标题合并（取第一次出现，避免目录）
        if canonical not in out:
            out[canonical] = {"heading": title, "body": body, "level": level, "line": idx + 1}
    return out


_PLACEHOLDERS = re.compile(
    r"(数据缺失|暂无数据|暂无|未披露|不适用|无法获取|无法计算|未知|N/?A|无相关数据)",
    re.IGNORECASE,
)


def is_substantive(body: str, min_chars: int = 60) -> bool:
    """section 非空：去掉表格符号/占位词后仍有足够实义内容。"""
    if not body:
        return False
    b = re.sub(r"[|>\-*_#`\s]", "", body)
    b = _PLACEHOLDERS.sub("", b)
    # 至少 min_chars 个非占位字符，且包含中英文实义字符
    return len(b) >= min_chars and bool(re.search(r"[\u4e00-\u9fffA-Za-z]", b))


def section_status(md: str) -> Dict[str, Dict]:
    secs = split_sections(md)
    out = {}
    for canonical in SECTION_ALIASES:
        s = secs.get(canonical)
        body = s["body"] if s else ""
        out[canonical] = {
            "present": bool(s),
            "body_chars": len(body),
            "substantive": is_substantive(body),
            "line": s["line"] if s else None,
        }
    return out


def core_sections_non_empty(md: str) -> Tuple[bool, Dict[str, Dict]]:
    st = section_status(md)
    ok = all(v["substantive"] for v in st.values())
    return ok, st


# ---------------------------------------------------------------------------
# 数值与单位
# ---------------------------------------------------------------------------

_CN_MULT = [
    ("十亿", 1e9),
    ("亿", 1e8),
    ("千万", 1e7),
    ("百万", 1e6),
    ("万", 1e4),
    ("千", 1e3),
]
_EN_MULT = [
    ("billion", 1e9),
    ("million", 1e6),
    ("thousand", 1e3),
]

# 不带倍数的币种单位：出现“元/美元/港元”时倍数为 1；
# 仍按“最先出现”选，避免把“457,286,687,000 元（约 4,572.87 亿元）”乘 1e8。
_BARE_UNIT = [
    ("美元", 1.0), ("港元", 1.0), ("人民币", 1.0), ("元", 1.0),
    ("usd", 1.0), ("hkd", 1.0), ("cny", 1.0), ("rmb", 1.0),
]


def _local_unit(text: str) -> Optional[float]:
    """取文本中最先出现的单位，而不是按“亿/百万/万”优先级取。

    例：“706,413 百万美元（约 7,064 亿美元）”必须先命中“百万”，
    否则会把 706,413 当成 706,413 亿，放大 100 倍。
    “457,286,687,000 元（约 4,572.87 亿元）”必须先命中“元”（倍数 1），
    否则会被后面的“亿元”放大 1e8。
    """
    low = text.lower()
    hits = []
    for token, mult in _CN_MULT:
        idx = text.find(token)
        if idx >= 0:
            hits.append((idx, mult))
    for token, mult in _EN_MULT:
        idx = low.find(token)
        if idx >= 0:
            hits.append((idx, mult))
    for token, mult in _BARE_UNIT:
        idx = text.find(token) if token.isascii() is False else low.find(token)
        if idx >= 0:
            hits.append((idx, mult))
    if not hits:
        return None
    return min(hits, key=lambda x: x[0])[1]


_UNIT_HINT_RE = re.compile(
    r"(?:单位|金额|币种)\s*(?:为|是)?\s*[:：]?\s*[（(]?\s*(十亿|亿|千万|百万|万|千)?\s*(?:美元|港元|元|人民币)"
)
_PAREN_UNIT_RE = re.compile(r"[（(]\s*(十亿|亿|千万|百万|万|千)?\s*(美元|港元|元|人民币)\s*[）)]")


def _unit_from_text(text: str) -> Optional[float]:
    m = _PAREN_UNIT_RE.search(text)
    if m and m.group(1):
        return _local_unit(m.group(1))
    m = _UNIT_HINT_RE.search(text)
    if m and m.group(1):
        return _local_unit(m.group(1))
    return None


def _global_unit(md: str) -> Optional[float]:
    # 1) 显式“单位：亿美元 / 单位为百万美元 / 金额（亿美元）”
    for m in _UNIT_HINT_RE.finditer(md):
        if m.group(1):
            return _local_unit(m.group(1))
    # 2) 表头括号单位
    for m in _PAREN_UNIT_RE.finditer(md[:12000]):
        if m.group(1):
            return _local_unit(m.group(1))
    return None


def _line_unit(lines: List[str], idx: int, lookback: int = 8) -> Optional[float]:
    """数据行本身没有单位时，向上找最近的表头/说明单位。"""
    for j in range(idx, max(-1, idx - lookback) - 1, -1):
        u = _unit_from_text(lines[j])
        if u:
            return u
    return None


@dataclass
class ParsedValue:
    value: Optional[float]
    raw: str
    line_no: int
    label: str
    unit_mult: float = 1.0
    is_percent: bool = False
    needs_review: bool = False
    note: str = ""


def _extract_candidate_value(after: str, field_name: str, global_mult: Optional[float],
                             unit_context: str = "") -> ParsedValue:
    # 标签后可能先出现“（FY2026）”“（Net sales）”这类括号说明，里面的年份不能被当成数值。
    bar = after.find("|")
    head, tail_part = (after, "") if bar == -1 else (after[:bar], after[bar:])
    head = re.sub(r"[（(][^）)]*[）)]", "", head)
    after = head + tail_part
    m = _NUMBER_RE.search(after)
    if not m:
        return ParsedValue(None, after[:80], -1, "", note="no_number")
    raw_num = m.group(0)
    try:
        num = float(raw_num.replace(",", "").replace("，", ""))
    except ValueError:
        return ParsedValue(None, after[:80], -1, "", note="bad_number")

    # 只在本单元格内判断单位/百分号，避免把下一列的“同比 +1.30%”误当成数值单位。
    tail_cell = after[m.end():].split("|", 1)[0]
    tail = tail_cell[:14]
    is_percent = bool(re.search(r"[%％]", tail_cell))
    mult = _local_unit(tail_cell)
    # 兼容“数值一列、单位单独一列”的表格，例如 | 营业收入 | 644.90 | 亿元 |
    if mult is None and not is_percent and unit_context:
        for cell in unit_context.split("|"):
            if re.fullmatch(r"\s*(十亿|亿|千万|百万|万|千)?\s*(美元|港元|元|人民币)\s*", cell):
                mult = _local_unit(cell)
                if mult:
                    break
    note = ""

    if field_name in ("gross_margin", "net_margin", "operating_margin", "roe"):
        if is_percent:
            return ParsedValue(num / 100.0, raw_num, -1, "", 1.0, True)
        if abs(num) > 1.5:
            # 报告里常见的“46.91”未带 %；只在比率上下文中接受
            return ParsedValue(num / 100.0, raw_num, -1, "", 1.0, False, True, "percent_without_sign")
        return ParsedValue(num, raw_num, -1, "")
    else:
        if is_percent:
            return ParsedValue(num, raw_num, -1, "", 1.0, True, True, "percent_for_level")
        if mult:
            return ParsedValue(num * mult, raw_num, -1, "", mult)
        if global_mult:
            return ParsedValue(num * global_mult, raw_num, -1, "", global_mult, False, True, "global_unit")
        if abs(num) < 1e6:
            return ParsedValue(num, raw_num, -1, "", 1.0, False, True, "unit_missing")
        return ParsedValue(num, raw_num, -1, "")


def _period_tokens(period_end: Optional[str]) -> set:
    """把 20260630 / 2026-06-30 / 2025-09-27 转成报告里可能出现的期间写法。"""
    if not period_end:
        return set()
    digits = re.sub(r"\D", "", str(period_end))
    if len(digits) < 8:
        return set()
    y, m, d = digits[:4], digits[4:6], digits[6:8]
    toks = {
        digits, f"{y}-{m}-{d}", f"{y}/{m}/{d}",
        f"{y}年{int(m)}月{int(d)}日",
    }
    if m == "06":
        toks |= {f"{y}年半年度", f"{y}H1", f"{y}上半年", f"{y} 年半年度", f"{y}年中"}
    if m == "12":
        toks |= {f"{y}年度", f"{y} 年年度", f"{y}年报", f"FY{y}", f"{y} 财年", f"{y}财年"}
    if m == "03":
        toks |= {f"{y}Q1", f"{y}一季", f"{y}第一季度"}
    if m == "09":
        toks |= {f"{y}Q3", f"{y}三季", f"{y}第三季度", f"FY{y}", f"{y} 财年"}
    return toks


def extract_fields(md: str, period_end: Optional[str] = None) -> Dict[str, Dict]:
    """从财务分析 section（缺失时全文）抽取 5 个字段。每个字段返回首个高置信候选。

    period_end 用于在多期表格（如“2026H1”+“2025年度”）中优先选与真值同期的行；
    不传时保持原行为。
    """
    md = strip_code_fences(decode_report(md))
    secs = split_sections(md)
    fin = secs.get("财务分析", {}).get("body", "")
    scope = fin if fin else md
    lines = scope.splitlines()
    global_mult = _global_unit(md)
    target_tokens = _period_tokens(period_end)

    result: Dict[str, Dict] = {}
    for fname in FIELD_PANEL:
        labels = sorted(FIELD_LABELS[fname], key=len, reverse=True)
        best: Optional[ParsedValue] = None
        best_score = -1
        for i, line in enumerate(lines):
            # 去掉表格首尾竖线，保留单元格
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            for label in labels:
                # 标签必须在某个单元格开头，避免“归母净利润”被“净利润”抢先
                cell_idx = None
                for ci, cell in enumerate(cells):
                    plain = re.sub(r"[*_`]", "", cell).strip()
                    if plain.startswith(label):
                        cell_idx = ci
                        break
                if cell_idx is None:
                    continue
                after = " | ".join(cells[cell_idx:])
                fallback = _line_unit(lines, i) or global_mult
                unit_context = " | ".join(cells[cell_idx + 1: cell_idx + 3])
                pv = _extract_candidate_value(after[len(label):], fname, fallback, unit_context)
                if pv.value is None:
                    continue
                score = 0
                if "|" in line:
                    score += 3
                if cell_idx == 0 or (cell_idx == 1 and len(cells) > 2):
                    score += 2
                score += min(len(label), 12) / 12.0
                if pv.needs_review:
                    score -= 1
                # 同期优先：多期表格里优先选与真值 period_end 相同的行
                if target_tokens:
                    ctx = "\n".join(lines[max(0, i - 5): i + 4])
                    if any(tok in ctx for tok in target_tokens):
                        score += 2.5
                # 数值合理性
                if fname in ("revenue", "net_income") and abs(pv.value) < 1e6:
                    score -= 2
                if score > best_score:
                    best_score = score
                    pv.line_no = i + 1
                    pv.label = label
                    best = pv
        if best is None:
            result[fname] = {"value": None, "raw": "", "line": None, "label": None,
                             "needs_review": False, "note": "not_found"}
        else:
            result[fname] = {
                "value": best.value,
                "raw": best.raw,
                "line": best.line_no,
                "label": best.label,
                "unit_mult": best.unit_mult,
                "is_percent": best.is_percent,
                "needs_review": best.needs_review,
                "note": best.note,
            }
    return result


def relative_error(actual: Optional[float], expected: Optional[float]) -> Optional[float]:
    if actual is None or expected is None:
        return None
    if expected == 0:
        return 0.0 if abs(actual) < 1e-12 else float("inf")
    return abs(actual - expected) / abs(expected)
