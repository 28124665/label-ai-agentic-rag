"""Claim 数据模型与 Claim Builder（按 docs §4.2 / §4.3 / §4.4）。

Claim 是"报告段落 → Evidence → query_id/chunk_id"血缘链路的中间层。
每条 Claim 至少绑定 1 个 evidence_ref（除 recommendation 允许 0）。

未验证 Claim 策略（按 docs §4.4）：
- metric / comparison / trend：无 evidence 时直接删除
- fact：无 evidence 时默认删除
- recommendation：无 evidence 时保留并标记 unsupported（导出时显示"未经验证"）

每个 Section / Chart / Table 通过 claim_refs 双向引用 Claim，
构成完整血缘链路。
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Optional

from agent.langgraph.evidence.provenance import extract_provenance_summary
from agent.langgraph.tools.report.models import (
    Claim,
    ClaimType,
    EvidenceSourceSummary,
    SupportStatus,
)

logger = logging.getLogger(__name__)


# ========== 未验证 Claim 策略配置 ==========
# 哪些 ClaimType 必须有 evidence
CLAIM_TYPES_REQUIRE_EVIDENCE = {"metric", "comparison", "trend", "fact"}
# 哪些 ClaimType 允许无 evidence 但标记 unsupported
CLAIM_TYPES_ALLOW_NO_EVIDENCE = {"recommendation"}


def _make_claim_id(text: str, claim_type: str) -> str:
    """生成 Claim 唯一 ID。"""
    raw = f"{claim_type}|{text[:200]}"
    return "claim_" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def _build_evidence_sources(
    evidence_list: list[dict],
    evidence_refs: list[str],
) -> list[EvidenceSourceSummary]:
    """从 evidence 列表提取 evidence_sources 摘要。

    Args:
        evidence_list: 标准化 Evidence 列表
        evidence_refs: Claim 关联的 evidence_id 列表

    Returns:
        list[EvidenceSourceSummary]
    """
    sources: list[EvidenceSourceSummary] = []
    ev_map = {ev.get("evidence_id", ""): ev for ev in evidence_list or []}
    for ref in evidence_refs or []:
        ev = ev_map.get(ref)
        if not ev:
            continue
        summary_dict = extract_provenance_summary(ev)
        # 保留 evidence_id 与 source_type，其它字段来自 provenance 摘要
        sources.append(
            EvidenceSourceSummary(
                evidence_id=summary_dict.get("evidence_id", ref),
                source_type=summary_dict.get("source_type", ""),
                db_id=summary_dict.get("db_id", ""),
                table_name=summary_dict.get("table_name", ""),
                query_id=summary_dict.get("query_id", ""),
                rows_used=summary_dict.get("rows_used", 0),
                kb_id=summary_dict.get("kb_id", ""),
                doc_id=summary_dict.get("doc_id", ""),
                chunk_id=summary_dict.get("chunk_id", ""),
                doc_title=summary_dict.get("doc_title", ""),
            )
        )
    return sources


def _compute_confidence(
    claim_type: ClaimType,
    evidence_refs: list[str],
    evidence_list: list[dict],
) -> float:
    """计算 Claim 置信度（取关联 evidence 的 confidence 平均）。"""
    if not evidence_refs:
        return 0.0
    ev_map = {ev.get("evidence_id", ""): ev for ev in evidence_list or []}
    confs: list[float] = []
    for ref in evidence_refs:
        ev = ev_map.get(ref)
        if ev:
            c = ev.get("confidence", 0.0) or 0.0
            confs.append(float(c))
    if not confs:
        return 0.0
    return round(sum(confs) / len(confs), 4)


class ClaimBuilder:
    """Claim 构造器（按 docs §4.2 / §4.3 / §4.4）。

    职责：
    1. 从 sections / charts / tables 与 evidence 列表生成 Claim
    2. 双向绑定 section.claim_refs / chart.claim_refs / table.claim_refs
    3. 执行未验证 Claim 策略（删除/标记）
    4. 计算 support_status / confidence
    """

    def __init__(
        self,
        low_confidence_threshold: float = 0.7,
    ):
        """初始化。

        Args:
            low_confidence_threshold: 低于此阈值的 Claim 标记 needs_human_review
        """
        self._low_confidence_threshold = low_confidence_threshold

    def build(
        self,
        sections: list[dict],
        charts: list[dict],
        tables: list[dict],
        evidence_list: list[dict],
    ) -> list[Claim]:
        """从 sections / charts / tables 生成 Claim 列表。

        每个章节、每个图表、每个表格至少生成 1 个 Claim。
        删除无 evidence 的 metric/comparison/trend/fact Claim。
        recommendation 无 evidence 保留并标记 unsupported。

        Args:
            sections: 章节列表（每项含 section_id / evidence_refs / title / content）
            charts: 图表列表
            tables: 表格列表
            evidence_list: 标准化 Evidence 列表

        Returns:
            list[Claim]: 构造并过滤后的 Claim 列表
        """
        claims: list[Claim] = []

        # 1. 章节级 Claim
        for sec in sections or []:
            sec_id = sec.get("section_id", "")
            sec_evidence_refs = list(sec.get("evidence_refs") or [])
            sec_text = (sec.get("title", "") or "") + "：\n" + (sec.get("content", "") or "")[:200]
            sec_claim_type: ClaimType = "fact"  # 章节默认 fact

            sec_claim = self._create_claim(
                text=sec_text.strip().rstrip("：\n") or "（章节内容）",
                claim_type=sec_claim_type,
                evidence_refs=sec_evidence_refs,
                evidence_list=evidence_list,
                section_id=sec_id,
            )
            if sec_claim:
                claims.append(sec_claim)
                sec["claim_refs"] = list({*sec.get("claim_refs", []), sec_claim["claim_id"]})

        # 2. 图表级 Claim（数据可追溯 → 默认 metric）
        for chart in charts or []:
            chart_id = chart.get("chart_id", "")
            chart_evidence_refs = list(chart.get("evidence_refs") or [])
            chart_claim = self._create_claim(
                text=chart.get("title", "图表"),
                claim_type="metric",
                evidence_refs=chart_evidence_refs,
                evidence_list=evidence_list,
                chart_id=chart_id,
            )
            if chart_claim:
                claims.append(chart_claim)
                chart["claim_refs"] = list({*chart.get("claim_refs", []), chart_claim["claim_id"]})

        # 3. 表格级 Claim（数据可追溯 → 默认 fact）
        for tbl in tables or []:
            tbl_id = tbl.get("table_id", "")
            tbl_evidence_refs = list(tbl.get("evidence_refs") or [])
            tbl_claim = self._create_claim(
                text=tbl.get("title", "表格"),
                claim_type="fact",
                evidence_refs=tbl_evidence_refs,
                evidence_list=evidence_list,
                table_id=tbl_id,
            )
            if tbl_claim:
                claims.append(tbl_claim)
                tbl["claim_refs"] = list({*tbl.get("claim_refs", []), tbl_claim["claim_id"]})

        logger.info(
            f"[ClaimBuilder] 生成 {len(claims)} 条 Claim，"
            f"evidence={len(evidence_list)}"
        )
        return claims

    def _create_claim(
        self,
        text: str,
        claim_type: ClaimType,
        evidence_refs: list[str],
        evidence_list: list[dict],
        section_id: str = "",
        chart_id: str = "",
        table_id: str = "",
    ) -> Optional[Claim]:
        """创建单条 Claim，执行未验证策略。

        Returns:
            Claim 或 None（被删除时）
        """
        claim_id = _make_claim_id(text, claim_type)
        has_evidence = bool(evidence_refs)
        needs_evidence = claim_type in CLAIM_TYPES_REQUIRE_EVIDENCE

        # 未验证策略：metric/comparison/trend/fact 无 evidence → 删除
        if needs_evidence and not has_evidence:
            logger.info(
                f"[ClaimBuilder] 删除无 Evidence 的 {claim_type} Claim: {claim_id}"
            )
            return None

        # recommendation 无 evidence 保留
        if claim_type in CLAIM_TYPES_ALLOW_NO_EVIDENCE and not has_evidence:
            support_status: SupportStatus = "unsupported"
            confidence = 0.0
            needs_human_review = True
            review_reason = (
                f"recommendation 类型 Claim 未找到支撑 Evidence，建议人工确认"
            )
        else:
            support_status = "supported"
            confidence = _compute_confidence(claim_type, evidence_refs, evidence_list)
            needs_human_review = (
                confidence < self._low_confidence_threshold
            )
            review_reason = (
                f"Claim 支撑 Evidence 置信度 {confidence:.2f}，低于阈值 "
                f"{self._low_confidence_threshold:.2f}"
                if needs_human_review
                else ""
            )

        # 构造 evidence_sources 摘要
        evidence_sources = _build_evidence_sources(evidence_list, evidence_refs)

        claim: Claim = {
            "claim_id": claim_id,
            "text": text[:500],
            "claim_type": claim_type,
            "evidence_refs": list(evidence_refs),
            "evidence_sources": evidence_sources,
            "support_status": support_status,
            "verification_notes": [],
            "confidence": confidence,
            "needs_human_review": needs_human_review,
            "review_reason": review_reason,
        }
        # 附加定位信息到 metadata（便于追溯）
        if section_id or chart_id or table_id:
            claim_meta: dict[str, Any] = {}
            if section_id:
                claim_meta["section_id"] = section_id
            if chart_id:
                claim_meta["chart_id"] = chart_id
            if table_id:
                claim_meta["table_id"] = table_id
            # TypedDict 不允许任意字段，使用 metadata 字段（兼容）
            claim["verification_notes"] = [
                f"位置: {claim_meta}"
            ]
        return claim


def needs_human_review_for_claim(claim: Claim) -> bool:
    """判断 Claim 是否需要人工审核（低置信度或 unsupported）。"""
    if claim.get("needs_human_review", False):
        return True
    if claim.get("support_status") == "unsupported":
        return True
    return False
