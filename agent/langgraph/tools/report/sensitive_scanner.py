"""敏感信息扫描器。

按 docs/报告生成Tool设计.md 第 7.2 节要求扫描：
- 身份证号（18 位）
- 手机号（11 位中国大陆）
- 邮箱
- API Key（含 sk-, AKIA, ghp_, xoxb- 等前缀）
- 密码字段（password=, pwd=）
- 数据库连接串（mongodb://, postgresql://, mysql://）
- 内网 IP（10.x, 172.16-31.x, 192.168.x）
"""
from __future__ import annotations

import logging
import re
from typing import Any, Literal

logger = logging.getLogger(__name__)

Severity = Literal["low", "medium", "high", "critical"]


# ========== 敏感模式 ==========
SENSITIVE_PATTERNS: list[dict[str, Any]] = [
    {
        "code": "SENS_ID_CARD",
        "name": "身份证号",
        "pattern": r"\b\d{17}[\dXx]\b",
        "severity": "high",
        "description": "18 位身份证号",
    },
    {
        "code": "SENS_PHONE",
        "name": "手机号",
        "pattern": r"\b1[3-9]\d{9}\b",
        "severity": "medium",
        "description": "11 位中国大陆手机号",
    },
    {
        "code": "SENS_EMAIL",
        "name": "邮箱",
        "pattern": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        "severity": "low",
        "description": "电子邮箱地址",
    },
    {
        "code": "SENS_API_KEY",
        "name": "API Key",
        "pattern": r"(?:sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|xoxb-[0-9A-Za-z-]{20,}|Bearer\s+[A-Za-z0-9_.-]{20,})",
        "severity": "critical",
        "description": "API Key / Token",
    },
    {
        "code": "SENS_PASSWORD",
        "name": "密码字段",
        "pattern": r"(?i)(?:password|pwd|passwd)\s*[:=]\s*\S+",
        "severity": "high",
        "description": "密码字段赋值",
    },
    {
        "code": "SENS_DB_CONN",
        "name": "数据库连接串",
        "pattern": r"(?:mongodb|postgresql|mysql|redis|amqp):\/\/[^\s]+",
        "severity": "critical",
        "description": "数据库连接串（含凭据）",
    },
    {
        "code": "SENS_INTERNAL_IP",
        "name": "内网 IP",
        "pattern": r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b",
        "severity": "medium",
        "description": "内网 IP 地址",
    },
    {
        "code": "SENS_BANK_CARD",
        "name": "银行卡号",
        "pattern": r"\b\d{16,19}\b",
        "severity": "high",
        "description": "16-19 位连续数字（疑似银行卡号）",
    },
]


def scan_text(text: str) -> list[dict[str, Any]]:
    """扫描文本中的敏感信息。

    Args:
        text: 待扫描文本

    Returns:
        list: 敏感信息命中列表，每项含 code/name/severity/snippet
    """
    if not text:
        return []

    findings: list[dict[str, Any]] = []
    for pattern_def in SENSITIVE_PATTERNS:
        try:
            matches = re.finditer(pattern_def["pattern"], text)
            for match in matches:
                snippet = match.group(0)
                # 截断过长的 snippet
                display_snippet = snippet if len(snippet) <= 50 else snippet[:47] + "..."
                findings.append(
                    {
                        "code": pattern_def["code"],
                        "name": pattern_def["name"],
                        "severity": pattern_def["severity"],
                        "description": pattern_def["description"],
                        "snippet": display_snippet,
                        "start": match.start(),
                        "end": match.end(),
                    }
                )
        except re.error as e:
            logger.warning(f"[SensitiveScanner] 正则错误: {pattern_def['code']}: {e}")
            continue

    return findings


def scan_artifact(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    """扫描 ReportArtifact 中的所有文本字段。

    扫描范围：
    - summary
    - sections[*].content
    - tables[*].rows
    - charts[*].notes

    Args:
        artifact: ReportArtifact dict

    Returns:
        list: 所有命中的敏感信息
    """
    if not artifact:
        return []

    all_findings: list[dict[str, Any]] = []

    # 扫描 summary
    summary = artifact.get("summary", "")
    if summary:
        for finding in scan_text(summary):
            finding["location"] = "summary"
            all_findings.append(finding)

    # 扫描 sections
    for section in artifact.get("sections", []) or []:
        section_id = section.get("section_id", "unknown")
        content = section.get("content", "")
        if content:
            for finding in scan_text(content):
                finding["location"] = f"section:{section_id}"
                all_findings.append(finding)

    # 扫描 tables（rows 可能包含数字）
    for table in artifact.get("tables", []) or []:
        table_id = table.get("table_id", "unknown")
        rows = table.get("rows", []) or []
        for row in rows:
            row_text = " ".join(str(cell) for cell in row)
            for finding in scan_text(row_text):
                finding["location"] = f"table:{table_id}"
                all_findings.append(finding)

    # 扫描 charts
    for chart in artifact.get("charts", []) or []:
        chart_id = chart.get("chart_id", "unknown")
        notes = chart.get("notes", "")
        if notes:
            for finding in scan_text(notes):
                finding["location"] = f"chart:{chart_id}"
                all_findings.append(finding)

    return all_findings


def has_critical(findings: list[dict[str, Any]]) -> bool:
    """判断是否包含 critical 级别敏感信息。"""
    return any(f.get("severity") == "critical" for f in findings)


def has_high(findings: list[dict[str, Any]]) -> bool:
    """判断是否包含 high 或 critical 级别敏感信息。"""
    return any(f.get("severity") in ("high", "critical") for f in findings)
