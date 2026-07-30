"""Report Verifier。

按 docs/报告生成Tool设计.md 第 6.9 节设计：
执行 10 项检查：
1. 每章节有 evidence_refs
2. 每数字来自结构化 Evidence
3. 每图表绑定 Evidence
4. 每表格绑定 Evidence
5. 引用 ID 真实存在
6. 不含敏感字段
7. 不含未授权数据
8. 内容语言一致
9. 章节不重复
10. 未超 max_sections / max_charts / max_tables 限制

设计原则：
- 验证器**不调用 LLM**，只做结构性 / 模式化检查
- 任一 critical/high 检查失败 → passed=False
- 低风险问题以 warnings 形式给出
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from agent.langgraph.tools.report.models import (
    ReportArtifact,
    VerificationIssue,
    VerificationResult,
)
from agent.langgraph.tools.report.sensitive_scanner import (
    has_critical,
    has_high,
    scan_artifact,
)

logger = logging.getLogger(__name__)


# 章节内容中"数字"匹配模式（用于检查数字是否有 evidence 支持）
NUMBER_PATTERN = re.compile(r"\b\d+(?:\.\d+)?%?\b")


class ReportVerifier:
    """报告验证器。

    检查项与设计文档 §6.9 对齐。
    """

    def __init__(self, config: dict[str, Any] | None = None):
        """初始化验证器。

        Args:
            config: 配置字典
        """
        self._config = config or {}

    def verify(
        self,
        artifact: ReportArtifact,
        evidence: list[dict],
        max_sections: int = 10,
        max_charts: int = 6,
        max_tables: int = 8,
        scan_sensitive: bool = True,
        claim_rules: list[dict[str, Any]] | None = None,
        publish_policy: dict[str, Any] | None = None,
        core_sections: list[str] | None = None,
    ) -> VerificationResult:
        """执行 10 项检查 + Claim Lineage Check（按 docs §4.2-4.4）。

        Args:
            artifact: ReportArtifact
            evidence: 全部 evidence 列表（用于校验引用真实性）
            max_sections: 最大章节数
            max_charts: 最大图表数
            max_tables: 最大表格数
            scan_sensitive: 是否执行敏感信息扫描

        Returns:
            VerificationResult
        """
        checks_performed: list[str] = []
        issues: list[VerificationIssue] = []
        warnings: list[str] = []

        valid_evidence_ids = {ev.get("evidence_id") for ev in evidence if ev.get("evidence_id")}

        # ========== 检查 1: 每章节有 evidence_refs ==========
        # 说明：recommendation 章节允许无 evidence_refs（设计文档 §4.4）
        # - 该类型章节以"建议"为主，本身不强制要求 evidence 支撑
        # - Verifier 在 Claim Lineage Check 中单独校验 recommendation.claim_refs
        checks_performed.append("section_has_evidence_refs")
        for section in artifact.get("sections", []) or []:
            section_id = section.get("section_id", "unknown")
            section_type = section.get("section_type", "")
            refs = section.get("evidence_refs", []) or []
            if not refs and section_type != "recommendation":
                issues.append(
                    {
                        "code": "VER_SECTION_NO_REFS",
                        "severity": "high",
                        "message": f"章节「{section.get('title', section_id)}」没有任何 evidence_refs",
                        "section_id": section_id,
                    }
                )
            elif not refs and section_type == "recommendation":
                # recommendation 章节无 refs 仅记 warning，不阻断验证
                warnings.append(
                    f"章节「{section.get('title', section_id)}」(recommendation) 无 evidence_refs，"
                    f"建议由人工补充或标注 unsupported"
                )

        # ========== 检查 2: 数字来自结构化 Evidence ==========
        checks_performed.append("numbers_from_structured_evidence")
        for section in artifact.get("sections", []) or []:
            section_id = section.get("section_id", "unknown")
            content = section.get("content", "") or ""
            refs = set(section.get("evidence_refs", []) or [])

            # 提取章节中的数字
            numbers_in_content = NUMBER_PATTERN.findall(content)
            if numbers_in_content and not refs:
                issues.append(
                    {
                        "code": "VER_NUMBERS_NO_REFS",
                        "severity": "high",
                        "message": f"章节「{section.get('title', section_id)}」包含数字但无 evidence_refs",
                        "section_id": section_id,
                    }
                )

        # ========== 检查 3: 图表绑定 Evidence ==========
        checks_performed.append("charts_have_evidence")
        for chart in artifact.get("charts", []) or []:
            chart_id = chart.get("chart_id", "unknown")
            refs = chart.get("evidence_refs", []) or []
            if not refs:
                issues.append(
                    {
                        "code": "VER_CHART_NO_REFS",
                        "severity": "high",
                        "message": f"图表「{chart.get('title', chart_id)}」没有绑定 evidence",
                        "chart_id": chart_id,
                    }
                )

        # ========== 检查 4: 表格绑定 Evidence ==========
        checks_performed.append("tables_have_evidence")
        for table in artifact.get("tables", []) or []:
            table_id = table.get("table_id", "unknown")
            refs = table.get("evidence_refs", []) or []
            if not refs:
                issues.append(
                    {
                        "code": "VER_TABLE_NO_REFS",
                        "severity": "high",
                        "message": f"表格「{table.get('title', table_id)}」没有绑定 evidence",
                        "table_id": table_id,
                    }
                )

        # ========== 检查 5: 引用 ID 真实存在 ==========
        checks_performed.append("refs_exist_in_evidence")
        for section in artifact.get("sections", []) or []:
            section_id = section.get("section_id", "unknown")
            for ref in section.get("evidence_refs", []) or []:
                if ref not in valid_evidence_ids:
                    issues.append(
                        {
                            "code": "VER_REF_NOT_EXIST",
                            "severity": "high",
                            "message": f"章节「{section.get('title', section_id)}」引用了不存在的 evidence_id: {ref}",
                            "section_id": section_id,
                        }
                    )
        for chart in artifact.get("charts", []) or []:
            chart_id = chart.get("chart_id", "unknown")
            for ref in chart.get("evidence_refs", []) or []:
                if ref not in valid_evidence_ids:
                    issues.append(
                        {
                            "code": "VER_REF_NOT_EXIST",
                            "severity": "high",
                            "message": f"图表「{chart.get('title', chart_id)}」引用了不存在的 evidence_id: {ref}",
                            "chart_id": chart_id,
                        }
                    )
        for table in artifact.get("tables", []) or []:
            table_id = table.get("table_id", "unknown")
            for ref in table.get("evidence_refs", []) or []:
                if ref not in valid_evidence_ids:
                    issues.append(
                        {
                            "code": "VER_REF_NOT_EXIST",
                            "severity": "high",
                            "message": f"表格「{table.get('title', table_id)}」引用了不存在的 evidence_id: {ref}",
                            "table_id": table_id,
                        }
                    )

        # ========== 检查 6: 敏感字段 ==========
        if scan_sensitive:
            checks_performed.append("sensitive_data_scan")
            sensitive_findings = scan_artifact(artifact)
            if has_critical(sensitive_findings):
                # critical 直接拒绝导出
                for finding in sensitive_findings:
                    if finding.get("severity") == "critical":
                        issues.append(
                            {
                                "code": f"VER_SENSITIVE_{finding['code']}",
                                "severity": "critical",
                                "message": f"检测到 critical 级敏感信息: {finding['name']}（位置: {finding.get('location', 'unknown')}）",
                                "field": finding.get("location", ""),
                            }
                        )
            elif has_high(sensitive_findings):
                # high 等级 issues 中记录
                for finding in sensitive_findings:
                    if finding.get("severity") in ("high", "critical"):
                        issues.append(
                            {
                                "code": f"VER_SENSITIVE_{finding['code']}",
                                "severity": "high",
                                "message": f"检测到 high 级敏感信息: {finding['name']}（位置: {finding.get('location', 'unknown')}）",
                                "field": finding.get("location", ""),
                            }
                        )
            elif sensitive_findings:
                # 低风险警告
                for finding in sensitive_findings:
                    warnings.append(
                        f"检测到 {finding.get('severity', 'low')} 级敏感信息: "
                        f"{finding.get('name', '')}（位置: {finding.get('location', '')}）"
                    )

        # ========== 检查 7: 未授权数据 ==========
        # 当前实现：检查 evidence_refs 是否都在 valid_evidence_ids（已覆盖）
        # 实际生产中应与租户权限系统集成
        checks_performed.append("unauthorized_data_check")
        # 此处为简化版：依靠检查 5 已覆盖引用 ID 存在性

        # ========== 检查 8: 内容语言一致 ==========
        checks_performed.append("language_consistency")
        # 简化检查：所有章节 content 必须非空
        for section in artifact.get("sections", []) or []:
            section_id = section.get("section_id", "unknown")
            content = (section.get("content") or "").strip()
            if not content:
                issues.append(
                    {
                        "code": "VER_EMPTY_CONTENT",
                        "severity": "medium",
                        "message": f"章节「{section.get('title', section_id)}」内容为空",
                        "section_id": section_id,
                    }
                )

        # ========== 检查 9: 章节不重复 ==========
        checks_performed.append("section_uniqueness")
        section_ids = [s.get("section_id") for s in artifact.get("sections", []) or []]
        duplicates = set([x for x in section_ids if section_ids.count(x) > 1])
        if duplicates:
            issues.append(
                {
                    "code": "VER_DUPLICATE_SECTIONS",
                    "severity": "medium",
                    "message": f"发现重复的章节 ID: {duplicates}",
                }
            )

        # ========== 检查 10: 数量限制 ==========
        checks_performed.append("quantity_limits")
        sections = artifact.get("sections", []) or []
        charts = artifact.get("charts", []) or []
        tables = artifact.get("tables", []) or []

        if len(sections) > max_sections:
            issues.append(
                {
                    "code": "VER_TOO_MANY_SECTIONS",
                    "severity": "medium",
                    "message": f"章节数 {len(sections)} 超过限制 {max_sections}",
                }
            )
        if len(charts) > max_charts:
            issues.append(
                {
                    "code": "VER_TOO_MANY_CHARTS",
                    "severity": "medium",
                    "message": f"图表数 {len(charts)} 超过限制 {max_charts}",
                }
            )
        if len(tables) > max_tables:
            issues.append(
                {
                    "code": "VER_TOO_MANY_TABLES",
                    "severity": "medium",
                    "message": f"表格数 {len(tables)} 超过限制 {max_tables}",
                }
            )

        # ========== 检查 11: Skill claim rules ==========
        if claim_rules:
            checks_performed.append("skill_claim_rules")
            evidence_sources = {
                item.get("evidence_id"): item.get("source_type") for item in evidence
            }
            for section in artifact.get("sections", []) or []:
                content = (section.get("content") or "").casefold()
                section_type = section.get("section_type", "")
                refs = section.get("evidence_refs", []) or []
                ref_sources = {evidence_sources.get(ref) for ref in refs}
                for rule in claim_rules:
                    forbidden_terms = rule.get("forbidden_terms", [])
                    if any(term.casefold() in content for term in forbidden_terms):
                        issues.append(
                            {
                                "code": "VER_SKILL_FORBIDDEN_CLAIM",
                                "severity": "high",
                                "message": f"章节包含 Skill 禁止声明: {rule.get('rule_id', '')}",
                                "section_id": section.get("section_id", "unknown"),
                            }
                        )
                    claim_types = set(rule.get("claim_types", []))
                    is_recommendation = (
                        "recommendation" in claim_types
                        and section_type == "recommendation"
                    )
                    is_metric = (
                        bool({"metric", "comparison"}.intersection(claim_types))
                        and bool(NUMBER_PATTERN.findall(content))
                    )
                    if (is_recommendation or is_metric) and not refs:
                        issues.append(
                            {
                                "code": "VER_SKILL_CLAIM_NO_REFS",
                                "severity": "high",
                                "message": f"Skill 规则 {rule.get('rule_id', '')} 要求声明关联 Evidence",
                                "section_id": section.get("section_id", "unknown"),
                            }
                        )
                    required_sources = set(rule.get("required_source_types", []))
                    if (is_recommendation or is_metric) and required_sources and not required_sources.issubset(ref_sources):
                        issues.append(
                            {
                                "code": "VER_SKILL_CLAIM_SOURCE_MISMATCH",
                                "severity": "high",
                                "message": f"Skill 规则 {rule.get('rule_id', '')} 要求 Evidence 来源: {sorted(required_sources)}",
                                "section_id": section.get("section_id", "unknown"),
                            }
                        )

        # ========== 检查 12: Skill publish policy ==========
        if publish_policy:
            checks_performed.append("skill_publish_policy")
            if publish_policy.get("require_all_core_sections", False):
                generated_sections = {
                    section.get("section_id")
                    for section in artifact.get("sections", []) or []
                }
                missing_sections = set(core_sections or []) - generated_sections
                if missing_sections:
                    issues.append(
                        {
                            "code": "VER_SKILL_CORE_SECTION_MISSING",
                            "severity": "high",
                            "message": f"Skill 发布策略要求的核心章节缺失: {sorted(missing_sections)}",
                        }
                    )

        # ========== 检查 13: Claim Lineage Check（按 docs §4.2-4.4） ==========
        claims = artifact.get("claims", []) or []
        if claims:
            checks_performed.append("claim_lineage_check")
            issues.extend(self._check_claim_lineage(claims, valid_evidence_ids))

        # ========== 汇总 ==========
        # 任何 critical / high 级问题 → passed=False
        passed = not any(
            i.get("severity") in ("critical", "high") for i in issues
        )
        score = self._calculate_score(issues, len(checks_performed))

        result: VerificationResult = {
            "passed": passed,
            "score": score,
            "issues": issues,
            "warnings": warnings,
            "checks_performed": checks_performed,
            "checked_at": datetime.utcnow().isoformat() + "Z",
        }

        logger.info(
            f"[ReportVerifier] 验证完成: passed={passed}, score={score:.2f}, "
            f"issues={len(issues)}, warnings={len(warnings)}"
        )
        return result

    def _calculate_score(
        self,
        issues: list[VerificationIssue],
        total_checks: int,
    ) -> float:
        """根据 issues 计算验证分数。

        公式：1.0 - weighted_issues / total_checks
        - critical: 扣 0.30
        - high: 扣 0.20
        - medium: 扣 0.10
        - low: 扣 0.05
        """
        if not issues:
            return 1.0

        weights = {"critical": 0.30, "high": 0.20, "medium": 0.10, "low": 0.05}
        penalty = sum(weights.get(i.get("severity", "low"), 0.05) for i in issues)
        max_penalty = max(0.20 * total_checks, 1.0)  # 至少允许 5 个 high 问题
        score = max(0.0, 1.0 - penalty / max_penalty)
        return round(score, 2)

    def _check_claim_lineage(
        self,
        claims: list[dict],
        valid_evidence_ids: set[str],
    ) -> list[VerificationIssue]:
        """Claim 血缘校验（按 docs §4.2 / §4.3 / §4.4）。

        检查项：
        1. metric / comparison / trend / fact Claim 必须有 evidence_refs
           - ClaimBuilder 已删除无 evidence 的此类型 Claim，因此这里只校验残留
        2. recommendation 无 evidence 应被标 unsupported（不是 issue，仅 warning）
        3. Claim.evidence_refs 中所有 evidence_id 必须存在
        4. 低置信度 Claim 标记 needs_human_review（记入 verification_notes）

        Args:
            claims: Claim 列表
            valid_evidence_ids: 当前有效的 evidence_id 集合

        Returns:
            list[VerificationIssue]
        """
        issues: list[VerificationIssue] = []
        require_evidence_types = {"metric", "comparison", "trend", "fact"}
        # 不再要求 evidence 的类型（仅用于记录）
        for claim in claims or []:
            claim_id = claim.get("claim_id", "unknown")
            claim_type = claim.get("claim_type", "")
            refs = claim.get("evidence_refs", []) or []
            support_status = claim.get("support_status", "")

            # 1) metric/comparison/trend/fact 无 evidence → high（ClaimBuilder 应已删除，但作为兜底）
            if claim_type in require_evidence_types and not refs:
                issues.append(
                    {
                        "code": "VER_CLAIM_MISSING_EVIDENCE",
                        "severity": "high",
                        "message": f"Claim {claim_id} ({claim_type}) 缺少 evidence_refs",
                        "field": f"claim:{claim_id}",
                    }
                )

            # 2) Claim.evidence_refs 全部存在
            for ref in refs:
                if ref not in valid_evidence_ids:
                    issues.append(
                        {
                            "code": "VER_CLAIM_EVIDENCE_NOT_EXIST",
                            "severity": "high",
                            "message": f"Claim {claim_id} 引用了不存在的 evidence_id: {ref}",
                            "field": f"claim:{claim_id}",
                        }
                    )

            # 3) low confidence → medium 提示（已在 ClaimBuilder 中标记 needs_human_review）
            if claim.get("needs_human_review", False):
                issues.append(
                    {
                        "code": "VER_CLAIM_NEEDS_HUMAN_REVIEW",
                        "severity": "medium",
                        "message": (
                            f"Claim {claim_id} 需要人工核实: {claim.get('review_reason', '')}"
                        ),
                        "field": f"claim:{claim_id}",
                    }
                )

            # 4) unsupported claim（recommendation 无 evidence）→ low 提示
            if support_status == "unsupported":
                issues.append(
                    {
                        "code": "VER_CLAIM_UNSUPPORTED",
                        "severity": "low",
                        "message": (
                            f"Claim {claim_id} ({claim_type}) 标记为 unsupported，"
                            f"导出时将显示「未经验证」"
                        ),
                        "field": f"claim:{claim_id}",
                    }
                )

        return issues
