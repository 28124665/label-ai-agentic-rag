#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""基于规则的事实校验工具。

对应需求：P2-FR-04（幻觉检测 - 规则层精确校验）

功能说明：
  提供轻量级的纯 Python 规则层，通过提取和归一化精确实体（数值、日期、比例、
  货币、专有名词）来验证论断是否与检索文档一致。

实现方式：
  1. 实体提取（extract_entities）：
     - date：支持 YYYY-MM-DD、YYYY年MM月DD日、YYYY-Qx、月份范围等多种格式
     - ratio：支持 15.3%、百分之十五点三 等中英文百分比
     - currency：支持 ¥1000、1000元、人民币1000元 等货币格式
     - range：支持 10~20、100到200 等范围格式
     - number：支持阿拉伯数字和中文数字（十五、三千万等）
     - proper_noun：支持大写英文和中文组织/产品名称
     - 去重：按类型优先级处理重叠 span
  2. 实体匹配（find_match）：
     - 日期：支持季度与月份等价（Q3 = 7-9月）、年份匹配
     - 数值：找最近的数值候选
     - 专有名词：精确匹配（忽略大小写）
  3. 矛盾判定（is_contradicted）：
     - 数值/货币：容差 ±0.1%（大数单位换算）
     - 比例：容差 ±0.5%
     - 日期：年份必须相同，月份/日期/季度必须一致或在范围内
     - 范围：两端点都在容差内
  4. 中文数字解析（_parse_chinese_number）：
     支持 "十五"、"三千万"、"百分之十五点三" 等中文数字表达
  5. 不依赖任何重型 NLP 库，纯正则 + 标准库实现
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

RuleResult = Literal["SUPPORTED", "NOT_SUPPORTED", "CONTRADICTED"]

# 容差配置常量
# PERCENT_TOLERANCE: 百分比比较的容差（±0.5%），用于处理四舍五入差异
# LARGE_NUMBER_TOLERANCE: 大数/单位换算的容差（±0.1%），如 1万 vs 10000
# CHINESE_NUMBER_UNITS: 中文数字单位到数值的映射
# QUARTER_MONTHS: 季度到月份范围的映射（Q1=1-3月, Q2=4-6月, ...）
PERCENT_TOLERANCE = 0.005  # ±0.5%
LARGE_NUMBER_TOLERANCE = 0.001  # ±0.1% for unit/scale conversions
CHINESE_NUMBER_UNITS = {
    "千": 1_000,
    "万": 10_000,
    "亿": 100_000_000,
    "百万": 1_000_000,
    "千万": 10_000_000,
}
QUARTER_MONTHS = {
    "Q1": (1, 3),
    "Q2": (4, 6),
    "Q3": (7, 9),
    "Q4": (10, 12),
}


@dataclass(frozen=True)
class Entity:
    """A fact-checkable entity extracted from text."""

    type: str
    value: str
    normalized: Any
    span: tuple[int, int] = field(default=(0, 0))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "value": self.value,
            "normalized": self.normalized,
            "span": list(self.span),
            "metadata": self.metadata,
        }


# ---------- Extraction patterns ----------

_CURRENCY_PREFIX = r"(?:[¥￥]|RMB|CNY|人民币)"
_CURRENCY_SUFFIX = r"(?:元|人民币)"

# 2024-09-30, 2024/09/30, 2024.09.30, 2024年9月30日, 2024年09月30日 etc.
_DATE_PATTERNS = [
    (
        re.compile(r"(?P<year>\d{4})\s*[-/年.\s]\s*(?P<month>\d{1,2})\s*[-/月.\s]\s*(?P<day>\d{1,2})\s*[日]?"),
        "date",
    ),
    (
        re.compile(r"(?P<year>\d{4})\s*[-/年.\s]\s*(?P<month>\d{1,2})\s*[月]?"),
        "date",
    ),
]

# Month ranges: 2024年7-9月, 2024年1~3月
_MONTH_RANGE_PATTERN = re.compile(r"(?P<year>\d{4})\s*[-/年.\s]?\s*(?P<start_month>\d{1,2})\s*[-~到至～]\s*(?P<end_month>\d{1,2})\s*[月]?")

# Quarters: 2024-Q3, 2024年Q3, 2024年第3季度, 2024年第三季度
_QUARTER_PATTERN = re.compile(
    r"(?P<year>\d{4})\s*[-/年.\s]?\s*(?:"
    r"第(?P<quarter_cn>[1-4一二三四])\s*[季qQ](?:度)?"
    r"|[qQ](?P<quarter_q>[1-4一二三四])"
    r")"
)

# Numeric token used across percentage/range/currency/number patterns.
# Matches either comma-formatted numbers or plain integers/decimals.
_NUMERIC_TOKEN = r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[+-]?\d+(?:\.\d+)?"

# Percentages: 15.3%, 15.3  percent, 百分之十五点三
_PERCENT_PATTERN = re.compile(
    rf"(?P<num>{_NUMERIC_TOKEN}(?:\s*[百千万亿])?)\s*(?:%|百分之|percent|pct)"
    r"|百分之(?P<cn_num>[零一二三四五六七八九十百千万亿点两几]+)"
)

# Ranges: 10~20, 10-20, 100到200, 1,000至2,000
_RANGE_PATTERN = re.compile(
    rf"(?P<low>{_NUMERIC_TOKEN}(?:\s*[百千万亿])?)"
    rf"\s*(?:[~\-到至～])\s*"
    rf"(?P<high>{_NUMERIC_TOKEN}(?:\s*[百千万亿])?)"
)

# Currency: ¥1000, 1000元, 人民币1000元, RMB 1000
# Requires at least one currency marker (prefix or suffix) to avoid matching
# bare numbers such as years inside dates.
_CURRENCY_PATTERN = re.compile(
    rf"(?:{_CURRENCY_PREFIX}\s*(?P<num_prefix>{_NUMERIC_TOKEN}(?:\s*[百千万亿])?))"
    rf"|(?P<num_suffix>{_NUMERIC_TOKEN}(?:\s*[百千万亿])?)\s*{_CURRENCY_SUFFIX}"
)

# Plain numbers (avoid overlapping with currency/percent/range by looking for isolated decimals/integers)
# Also avoid extracting 4-digit years (e.g. 2025年) as plain numbers.
_NUMBER_PATTERN = re.compile(rf"(?<![¥￥\d.,])\s*(?P<num>{_NUMERIC_TOKEN}(?:\s*[百千万亿])?)(?!\s*(?:\d|[元%百分之百]|年))")

# Proper nouns: simple heuristic based on capitalized / mixed Chinese-English tokens
_PROPER_NOUN_PATTERN = re.compile(r"(?P<noun>[A-Z][A-Za-z0-9]+(?:[\s+][A-Z][A-Za-z0-9]+)*|[\u4e00-\u9fa5]{2,}(?:科技|集团|公司|银行|大学|学院|医院|研究所|平台|系统|框架|模型|语言|智能|云|网))")

_CHINESE_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "百": 100,
    "千": 1000,
    "万": 10_000,
    "亿": 100_000_000,
    "两": 2,
    "几": -1,  # marker for fuzzy
    "点": -2,  # decimal marker
}


def _parse_chinese_number(text: str) -> float | None:
    """将中文数字字符串解析为浮点数。

    支持的格式：
      - 基本数字：零、一、二、三...九
      - 带单位：十五、三千万、一亿等
      - 带小数：十五点三、百分之十五点三等
    不支持阿拉伯数字混合（由调用方单独处理）。
    """
    text = text.strip().replace(" ", "")
    if not text:
        return None

    # Handle simple case with Arabic digits mixed in
    if re.search(r"\d", text):
        return None

    if "点" in text:
        integer_part, _, fractional_part = text.partition("点")
        integer = _parse_chinese_number(integer_part)
        if integer is None:
            integer = 0.0
        fraction = 0.0
        for i, ch in enumerate(fractional_part):
            if ch in _CHINESE_DIGITS and _CHINESE_DIGITS[ch] >= 0:
                fraction += _CHINESE_DIGITS[ch] * (10 ** -(i + 1))
        return integer + fraction

    # Handle numbers like "十五点三" already processed above; handle "十五"
    total = 0
    section = 0
    digits = []
    for ch in text:
        val = _CHINESE_DIGITS.get(ch)
        if val is None:
            continue
        if val == -1:  # 几
            return None
        if val < 10:
            digits.append(val)
        elif val == 10:
            if not digits:
                section += val
            else:
                section += digits[-1] * val
                digits.pop()
        elif val in (100, 1000):
            if not digits:
                section += val
            else:
                section += digits[-1] * val
                digits.pop()
        elif val in (10_000, 100_000_000):
            if not digits:
                section += 1
            section *= val
            total += section
            section = 0
            digits = []

    if digits:
        section += sum(digits)
    return float(total + section)


def _parse_number(token: str) -> float | None:
    """Parse a numeric token possibly containing Chinese units or digits."""
    token = token.strip().replace(",", "")
    if not token:
        return None

    # Detect Chinese unit suffix
    unit_multiplier = 1.0
    for unit_name, unit_value in sorted(CHINESE_NUMBER_UNITS.items(), key=lambda kv: len(kv[0]), reverse=True):
        if token.endswith(unit_name):
            token = token[: -len(unit_name)].strip()
            unit_multiplier = float(unit_value)
            break

    # Try Arabic number
    try:
        return float(token) * unit_multiplier
    except ValueError:
        pass

    # Try Chinese number
    cn = _parse_chinese_number(token)
    if cn is not None:
        return cn * unit_multiplier

    return None


def _normalize_number(value: float | int | None) -> dict[str, Any] | None:
    """Return a normalized number representation for tolerant comparison."""
    if value is None:
        return None
    return {
        "value": float(value),
        "magnitude": abs(float(value)) if value != 0 else 1.0,
    }


def _normalize_date(
    year: int | None,
    month: int | None = None,
    day: int | None = None,
    quarter: int | None = None,
    start_month: int | None = None,
    end_month: int | None = None,
) -> dict[str, Any] | None:
    """Normalize a date into a comparable dictionary."""
    if year is None:
        return None
    normalized: dict[str, Any] = {"year": year}
    if quarter is not None:
        normalized["quarter"] = quarter
        normalized["months"] = QUARTER_MONTHS.get(f"Q{quarter}")
        return normalized
    if start_month is not None and end_month is not None:
        # If the month range equals a quarter, normalize as a quarter.
        for q_name, (q_start, q_end) in QUARTER_MONTHS.items():
            if start_month == q_start and end_month == q_end:
                normalized["quarter"] = int(q_name[1])
                normalized["months"] = (q_start, q_end)
                return normalized
        normalized["start_month"] = start_month
        normalized["end_month"] = end_month
        return normalized
    if month is not None:
        normalized["month"] = month
    if day is not None:
        normalized["day"] = day
    return normalized


def _match_date(d1: dict[str, Any], d2: dict[str, Any]) -> bool:
    """Check whether two normalized dates are equivalent under tolerance rules."""
    if d1.get("year") != d2.get("year"):
        return False

    # Quarter vs month equivalence: Q3 == 7-9月
    q1 = d1.get("quarter")
    q2 = d2.get("quarter")
    month1 = d1.get("month")
    month2 = d2.get("month")

    # Helper: get month range if available.
    def month_range(d: dict[str, Any]) -> tuple[int, int] | None:
        if "start_month" in d and "end_month" in d:
            return (d["start_month"], d["end_month"])
        if d.get("quarter") is not None and d.get("months") is not None:
            return d["months"]
        return None

    r1 = month_range(d1)
    r2 = month_range(d2)

    if q1 is not None and q2 is not None:
        return q1 == q2
    if r1 is not None and r2 is not None:
        return r1 == r2
    if r1 is not None and month2 is not None:
        return r1[0] <= month2 <= r1[1]
    if r2 is not None and month1 is not None:
        return r2[0] <= month1 <= r2[1]

    # Exact month/day when both present
    if month1 is not None and month2 is not None and month1 != month2:
        return False
    if "day" in d1 and "day" in d2 and d1["day"] != d2["day"]:
        return False
    return True


def _normalize_ratio(raw_value: str, parsed: float | None = None) -> float | None:
    """Normalize a percentage/ratio string to a decimal between 0 and 1."""
    raw_value = raw_value.replace(",", "").replace(" ", "")
    is_percent_syntax = bool(re.search(r"%|percent|pct|百分之", raw_value, re.IGNORECASE))

    if parsed is not None:
        number = parsed
    else:
        # Strip unit suffixes
        for suffix in ("%", "percent", "pct", "百分之"):
            if raw_value.lower().endswith(suffix):
                raw_value = raw_value[: -len(suffix)]
                break
        number = _parse_number(raw_value)

    if number is None:
        return None

    # If the caller already passed a decimal ratio (e.g. 0.153), keep it.
    if parsed is not None and not is_percent_syntax and number < 1 and "." in raw_value:
        return number

    # Percent-style input such as 15.3% means 0.153.
    if is_percent_syntax:
        return number / 100.0

    # Heuristic fallback: values < 1 with a decimal point are treated as decimals.
    if number < 1 and "." in raw_value:
        return number
    return number / 100.0


def extract_entities(text: str) -> list[Entity]:
    """从文本中提取可校验的事实实体。

    提取顺序：月份范围 -> 日期 -> 季度 -> 比例 -> 货币 -> 范围 -> 数字 -> 专有名词
    最后按类型优先级去重（如 "2024年Q3" 不会被同时提取为日期和数字）。

    支持的实体类型：
      - date: 日期（YYYY-MM-DD, YYYY年MM月, YYYY-Qx, 月份范围等）
      - ratio: 比例/百分比（15.3%, 百分之十五等）
      - currency: 货币金额（¥1000, 1000元等）
      - range: 数值范围（10~20, 100到200等）
      - number: 普通数字（支持中文数字和单位）
      - proper_noun: 专有名词（公司/产品/组织名称）
    """
    entities: list[Entity] = []
    if not text:
        return entities

    # Month ranges (e.g. 2024年7-9月) take precedence over generic date patterns.
    for m in _MONTH_RANGE_PATTERN.finditer(text):
        gd = m.groupdict()
        year = int(gd["year"])
        start_month = int(gd["start_month"])
        end_month = int(gd["end_month"])
        if not (1 <= start_month <= 12 and 1 <= end_month <= 12 and start_month < end_month):
            continue
        norm = _normalize_date(year, start_month=start_month, end_month=end_month)
        if norm:
            entities.append(
                Entity(
                    type="date",
                    value=m.group(0),
                    normalized=norm,
                    span=(m.start(), m.end()),
                )
            )

    # Dates
    for pattern, _ in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            gd = m.groupdict()
            year = int(gd["year"])
            month = int(gd["month"]) if gd.get("month") else None
            day = int(gd["day"]) if gd.get("day") else None
            norm = _normalize_date(year, month, day)
            if norm:
                entities.append(
                    Entity(
                        type="date",
                        value=m.group(0),
                        normalized=norm,
                        span=(m.start(), m.end()),
                    )
                )

    # Quarters
    for m in _QUARTER_PATTERN.finditer(text):
        gd = m.groupdict()
        year = int(gd["year"])
        quarter_raw = gd.get("quarter_cn") or gd.get("quarter_q")
        quarter_map = {"1": 1, "2": 2, "3": 3, "4": 4, "一": 1, "二": 2, "三": 3, "四": 4}
        quarter = quarter_map.get(quarter_raw) if quarter_raw else None
        if quarter:
            entities.append(
                Entity(
                    type="date",
                    value=m.group(0),
                    normalized=_normalize_date(year, None, None, quarter),
                    span=(m.start(), m.end()),
                )
            )

    # Ratios / percentages
    for m in _PERCENT_PATTERN.finditer(text):
        gd = m.groupdict()
        raw = m.group(0)
        if gd.get("cn_num"):
            parsed = _parse_chinese_number(gd["cn_num"])
            normalized = _normalize_ratio(raw, parsed if parsed else None)
        else:
            normalized = _normalize_ratio(raw, _parse_number(gd["num"]) if gd.get("num") else None)
        if normalized is not None:
            entities.append(
                Entity(
                    type="ratio",
                    value=raw,
                    normalized=normalized,
                    span=(m.start(), m.end()),
                )
            )

    # Currency
    for m in _CURRENCY_PATTERN.finditer(text):
        gd = m.groupdict()
        raw = m.group(0)
        num_token = gd.get("num_prefix") or gd.get("num_suffix")
        if num_token:
            parsed = _parse_number(num_token)
            if parsed is not None:
                entities.append(
                    Entity(
                        type="currency",
                        value=raw,
                        normalized=_normalize_number(parsed),
                        span=(m.start(), m.end()),
                    )
                )

    # Ranges
    for m in _RANGE_PATTERN.finditer(text):
        gd = m.groupdict()
        low = _parse_number(gd["low"])
        high = _parse_number(gd["high"])
        if low is not None and high is not None:
            entities.append(
                Entity(
                    type="range",
                    value=m.group(0),
                    normalized={"low": low, "high": high},
                    span=(m.start(), m.end()),
                )
            )

    # Plain numbers
    for m in _NUMBER_PATTERN.finditer(text):
        gd = m.groupdict()
        parsed = _parse_number(gd["num"])
        if parsed is not None:
            entities.append(
                Entity(
                    type="number",
                    value=m.group(0),
                    normalized=_normalize_number(parsed),
                    span=(m.start(), m.end()),
                )
            )

    # Proper nouns
    for m in _PROPER_NOUN_PATTERN.finditer(text):
        noun = m.group("noun").strip()
        if len(noun) >= 2:
            entities.append(
                Entity(
                    type="proper_noun",
                    value=noun,
                    normalized=noun.lower(),
                    span=(m.start(), m.end()),
                )
            )

    # Deduplicate overlapping spans, preferring more specific types
    type_priority = {"date": 0, "ratio": 1, "currency": 2, "range": 3, "number": 4, "proper_noun": 5}
    entities.sort(key=lambda e: (e.span[0], type_priority.get(e.type, 99)))
    filtered: list[Entity] = []
    for ent in entities:
        overlap = False
        for existing in filtered:
            if not (ent.span[1] <= existing.span[0] or ent.span[0] >= existing.span[1]):
                overlap = True
                break
        if not overlap:
            filtered.append(ent)

    return filtered


def normalize_entity(entity: Entity) -> Entity:
    """Normalize an extracted entity into a canonical comparable form.

    This function is idempotent: entities produced by :func:`extract_entities`
    are already normalized, but callers may use it to normalize raw entities
    built elsewhere.
    """
    return entity


def find_match(claim_entity: Entity, doc_entities: list[Entity]) -> Entity | None:
    """Find the best corresponding document entity for *claim_entity*.

    The returned entity is the most likely counterpart in the retrieved
    documents. Callers should use :func:`is_contradicted` to decide whether
    the correspondence supports or contradicts the claim.
    """
    if not doc_entities:
        return None

    candidates = [de for de in doc_entities if de.type == claim_entity.type]
    if not candidates:
        return None

    if claim_entity.type == "date":
        return _find_best_date_match(claim_entity, candidates)

    if claim_entity.type in ("number", "currency", "ratio", "range"):
        return _find_closest_numeric_match(claim_entity, candidates)

    if claim_entity.type == "proper_noun":
        claim_norm = str(claim_entity.normalized)
        for doc_ent in candidates:
            if str(doc_ent.normalized) == claim_norm:
                return doc_ent
        return None

    # Fallback to exact normalized comparison.
    for doc_ent in candidates:
        if claim_entity.normalized == doc_ent.normalized:
            return doc_ent
    return None


def _find_best_date_match(claim_entity: Entity, candidates: list[Entity]) -> Entity | None:
    """Return the candidate with the same year; prefer exact quarter/month match."""
    claim_year = claim_entity.normalized.get("year")
    same_year = [de for de in candidates if de.normalized.get("year") == claim_year]
    if not same_year:
        return None

    # Prefer exact match first.
    for de in same_year:
        if _match_date(claim_entity.normalized, de.normalized):
            return de

    # Otherwise return the first same-year candidate (caller checks contradiction).
    return same_year[0]


def _find_closest_numeric_match(claim_entity: Entity, candidates: list[Entity]) -> Entity:
    """Return the numeric candidate with the smallest relative difference."""

    def _value(entity: Entity) -> float:
        if entity.type == "ratio":
            return float(entity.normalized)
        if entity.type == "range":
            # Use midpoint for distance, endpoints are checked later.
            return (float(entity.normalized["low"]) + float(entity.normalized["high"])) / 2.0
        return float(entity.normalized["value"])

    claim_value = _value(claim_entity)
    best = min(candidates, key=lambda de: abs(_value(de) - claim_value))
    return best


def _number_equal(a: float, b: float) -> bool:
    """Number equality with tolerance for scale/unit conversions."""
    if a == b:
        return True
    magnitude = max(abs(a), abs(b), 1.0)
    return abs(a - b) / magnitude <= LARGE_NUMBER_TOLERANCE


def _ratio_equal(a: float, b: float) -> bool:
    """Ratio equality with ±0.5% tolerance."""
    if a == b:
        return True
    return abs(a - b) <= PERCENT_TOLERANCE


def is_contradicted(claim_entity: Entity, matched_entity: Entity) -> bool:
    """Return True if *matched_entity* contradicts *claim_entity*.

    For ordered numeric entities (number, ratio, currency, range) a value
    outside the configured tolerance is treated as a contradiction. For dates,
    inconsistent year/month/day/quarter components are treated as a
    contradiction.
    """
    if claim_entity.type != matched_entity.type:
        return False

    if claim_entity.type == "date":
        claim_norm = claim_entity.normalized
        matched_norm = matched_entity.normalized
        if claim_norm.get("year") != matched_norm.get("year"):
            return True

        def month_range(d: dict[str, Any]) -> tuple[int, int] | None:
            if "start_month" in d and "end_month" in d:
                return (d["start_month"], d["end_month"])
            if d.get("quarter") is not None and d.get("months") is not None:
                return d["months"]
            return None

        # If both have a month range/quarter, they must overlap.
        cr = month_range(claim_norm)
        mr = month_range(matched_norm)
        if cr is not None and mr is not None:
            return not (cr[0] <= mr[1] and mr[0] <= cr[1])

        # If one has a range and the other a single month, the month must fall
        # inside the range.
        cmonth = claim_norm.get("month")
        mmonth = matched_norm.get("month")
        if cr is not None and mmonth is not None:
            return not (cr[0] <= mmonth <= cr[1])
        if mr is not None and cmonth is not None:
            return not (mr[0] <= cmonth <= mr[1])

        # Both have months: must match.
        if cmonth is not None and mmonth is not None and cmonth != mmonth:
            return True
        # Both have days: must match.
        cday = claim_norm.get("day")
        mday = matched_norm.get("day")
        if cday is not None and mday is not None and cday != mday:
            return True
        return False

    if claim_entity.type in ("number", "currency"):
        claim_value = float(claim_entity.normalized["value"])
        matched_value = float(matched_entity.normalized["value"])
        return not _number_equal(claim_value, matched_value)

    if claim_entity.type == "ratio":
        claim_value = float(claim_entity.normalized)
        matched_value = float(matched_entity.normalized)
        return not _ratio_equal(claim_value, matched_value)

    if claim_entity.type == "range":
        claim_low = float(claim_entity.normalized["low"])
        claim_high = float(claim_entity.normalized["high"])
        matched_low = float(matched_entity.normalized["low"])
        matched_high = float(matched_entity.normalized["high"])
        return not (_number_equal(claim_low, matched_low) and _number_equal(claim_high, matched_high))

    return False


def rule_check_fact(claim: str, retrieved_docs: list[dict[str, Any]]) -> dict[str, Any]:
    """对单条论断执行规则层事实校验。

    校验流程：
      1. 从论断中提取可校验实体（数值/日期/专有名词等）
      2. 从所有检索文档中提取实体
      3. 对每个论断实体，在文档实体中寻找最佳匹配
      4. 判定结果：
         - CONTRADICTED：找到匹配但值矛盾（如论断说15%，文档说12%）
         - NOT_SUPPORTED：找不到对应实体（文档中没有提到该数据）
         - SUPPORTED：所有实体都找到匹配且不矛盾

    如果没有可校验实体（如纯文本描述），默认返回 SUPPORTED。
    矛盾是最强信号，发现第一个矛盾即停止检查。

    Args:
        claim: 单条论断（如 "2024年Q3营收增长15.3%"）。
        retrieved_docs: 检索文档列表，从 ``content`` 字段读取内容。

    Returns:
        包含 ``claim``、``rule_result``（SUPPORTED/NOT_SUPPORTED/CONTRADICTED）、
        ``matched_evidence`` 和 ``extracted_entities`` 的字典。
    """
    if not claim:
        return {
            "claim": claim,
            "rule_result": "SUPPORTED",
            "matched_evidence": None,
            "extracted_entities": [],
        }

    claim_entities = extract_entities(claim)

    doc_entities: list[Entity] = []
    for doc in retrieved_docs:
        content = doc if isinstance(doc, str) else doc.get("content", "")
        if content:
            doc_entities.extend(extract_entities(str(content)))

    if not claim_entities:
        # No precise entities to verify; rule layer cannot decide.
        return {
            "claim": claim,
            "rule_result": "SUPPORTED",
            "matched_evidence": None,
            "extracted_entities": [],
        }

    first_unsupported: Entity | None = None
    first_contradicted: tuple[Entity, Entity] | None = None

    for ce in claim_entities:
        matched = find_match(ce, doc_entities)
        if matched is None:
            if first_unsupported is None:
                first_unsupported = ce
            continue
        if is_contradicted(ce, matched):
            first_contradicted = (ce, matched)
            # Contradiction is the strongest signal; we can stop early.
            break

    if first_contradicted is not None:
        ce, matched = first_contradicted
        return {
            "claim": claim,
            "rule_result": "CONTRADICTED",
            "matched_evidence": matched.value,
            "extracted_entities": [e.to_dict() for e in claim_entities],
            "contradicted_entity": ce.to_dict(),
            "contradicted_with": matched.to_dict(),
        }

    if first_unsupported is not None:
        return {
            "claim": claim,
            "rule_result": "NOT_SUPPORTED",
            "matched_evidence": None,
            "extracted_entities": [e.to_dict() for e in claim_entities],
            "unsupported_entity": first_unsupported.to_dict(),
        }

    # All entities supported; use the first match as representative evidence.
    first_match = find_match(claim_entities[0], doc_entities)
    return {
        "claim": claim,
        "rule_result": "SUPPORTED",
        "matched_evidence": first_match.value if first_match else None,
        "extracted_entities": [e.to_dict() for e in claim_entities],
    }
