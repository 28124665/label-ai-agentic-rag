#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#
#      http://www.apache.org/licenses-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""组合 Skill 清单与融合规则（设计文档 v1.1 §9.3 / §9.6）。

当一次请求需要多个 Skill 协同（如多数据源 + 多检索源 + 报告聚合）时，
将各 Skill 的声明融合为一份 ``CompositeSkillSet``，供下游 Planner 使用。

融合规则（§9.3）：
    - evidence_spec：阈值取 max（更严格），source_quota 取并集（key 合并，冲突取 max）
    - claim_rules：is_required 取并集（任一声明必需即为必需）
    - section_templates：以 ReportSkill 为权威（ReportSkill 决定章节结构）

类比 Java：
    ``CompositeManifestBuilder`` ≈ ``@Service`` 编排类（门面 + 融合策略），
    ``MultiDataSkillDispatcher`` ≈ ``@Component`` 分发器（按 metric 路由到 DataSkill），
    ``CompositeSkillSet`` ≈ ``@Entity`` 聚合根（融合产物）。
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.langgraph.routers.models import PlanStep, StepArgs
from agent.langgraph.skills.catalog_models import SkillEvidenceSpec
from agent.langgraph.skills.dependency_graph import SkillDependencyGraph
from agent.langgraph.skills.models import DataSkill, ReportSkill, RetrievalSkill

logger = logging.getLogger(__name__)


class CompositeSkillSet(BaseModel):
    """组合 Skill 集合（设计文档 §9.6）。

    多个 ReportSkill / DataSkill / RetrievalSkill 融合后的产物，
    携带融合后的证据规格、断言规则、章节模板与依赖图。

    Attributes:
        report_skills: 参与组合的 ReportSkill 列表（章节模板权威来源）
        data_skills: 参与组合的 DataSkill 列表
        retrieval_skills: 参与组合的 RetrievalSkill 列表
        dependency_graph: 组合依赖图（§9.5）
        fused_evidence_spec: 融合后的证据规格（阈值 max、配额并集）
        fused_claim_rules: 融合后的断言规则（is_required 并集）
        fused_section_templates: 融合后的章节模板（ReportSkill 权威）
        composition_confidence: 组合置信度（0.0-1.0）
        composition_reason: 组合理由（人读）
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    report_skills: list[ReportSkill] = Field(default_factory=list)
    data_skills: list[DataSkill] = Field(default_factory=list)
    retrieval_skills: list[RetrievalSkill] = Field(default_factory=list)
    dependency_graph: SkillDependencyGraph = Field(default_factory=SkillDependencyGraph)
    fused_evidence_spec: SkillEvidenceSpec = Field(default_factory=SkillEvidenceSpec)
    fused_claim_rules: list[dict[str, Any]] = Field(default_factory=list)
    fused_section_templates: list[dict[str, Any]] = Field(default_factory=list)
    composition_confidence: float = 1.0
    composition_reason: str = ""


# CompositeManifest 为 CompositeSkillSet 的语义别名（§9.6 称其为 Manifest）
CompositeManifest = CompositeSkillSet


class CompositeManifestBuilder:
    """组合清单构建器（设计文档 §9.6）。

    类比 Java 中的 ``@Service`` 门面：编排证据规格 / 断言规则 / 章节模板的融合策略。
    """

    def build_composite_manifest(
        self,
        report_skills: list[ReportSkill],
        graph: SkillDependencyGraph,
        data_skills: list[DataSkill] | None = None,
        retrieval_skills: list[RetrievalSkill] | None = None,
    ) -> CompositeManifest:
        """构建组合清单。

        Args:
            report_skills: ReportSkill 列表（章节模板权威来源）
            graph: 组合依赖图
            data_skills: DataSkill 列表（可选）
            retrieval_skills: RetrievalSkill 列表（可选）

        Returns:
            CompositeManifest: 融合后的组合清单
        """
        data_skills = data_skills or []
        retrieval_skills = retrieval_skills or []

        fused_evidence_spec = self._fuse_evidence_spec(data_skills, retrieval_skills)
        fused_claim_rules = self._fuse_claim_rules(report_skills)
        fused_section_templates = self._fuse_section_templates(report_skills)
        confidence, reason = self._assess_composition(
            report_skills, data_skills, retrieval_skills, graph
        )

        return CompositeSkillSet(
            report_skills=list(report_skills),
            data_skills=list(data_skills),
            retrieval_skills=list(retrieval_skills),
            dependency_graph=graph,
            fused_evidence_spec=fused_evidence_spec,
            fused_claim_rules=fused_claim_rules,
            fused_section_templates=fused_section_templates,
            composition_confidence=confidence,
            composition_reason=reason,
        )

    # ========== 融合规则（§9.3） ==========
    def _fuse_evidence_spec(
        self,
        data_skills: list[DataSkill],
        retrieval_skills: list[RetrievalSkill],
    ) -> SkillEvidenceSpec:
        """融合证据规格（§9.3）：阈值取 max，source_quota 取并集。

        证据来源优先级：
            1. SkillManifest.evidence_spec（若传入的是 Manifest 包装对象）
            2. DataSkill/RetrievalSkill.evidence_requirements（列表 dict 提取）
        """
        specs = self._collect_evidence_specs(data_skills, retrieval_skills)
        if not specs:
            return SkillEvidenceSpec()
        merged_quota: dict[str, int] = {}
        for spec in specs:
            for source_type, quota in (spec.source_quota or {}).items():
                merged_quota[source_type] = max(
                    merged_quota.get(source_type, 0), int(quota or 0)
                )
        return SkillEvidenceSpec(
            min_freshness_score=max(s.min_freshness_score for s in specs),
            min_authority_score=max(s.min_authority_score for s in specs),
            min_relevance_score=max(s.min_relevance_score for s in specs),
            source_quota=merged_quota,
        )

    def _collect_evidence_specs(
        self,
        data_skills: list[DataSkill],
        retrieval_skills: list[RetrievalSkill],
    ) -> list[SkillEvidenceSpec]:
        """收集证据规格：优先 evidence_spec 属性，其次从 evidence_requirements 提取。"""
        specs: list[SkillEvidenceSpec] = []
        for skill in (*data_skills, *retrieval_skills):
            es = getattr(skill, "evidence_spec", None)
            if isinstance(es, SkillEvidenceSpec):
                specs.append(es)
                continue
            # 从 evidence_requirements（list[dict]）提取（DataSkill/RetrievalSkill）
            reqs = getattr(skill, "evidence_requirements", None) or []
            spec = self._spec_from_requirements(reqs)
            if spec is not None:
                specs.append(spec)
        return specs

    @staticmethod
    def _spec_from_requirements(
        reqs: list[Any],
    ) -> SkillEvidenceSpec | None:
        """从 evidence_requirements 列表提取证据规格。"""
        fresh = auth = rel = 0.0
        quota: dict[str, int] = {}
        found = False
        for req in reqs:
            if not isinstance(req, dict):
                continue
            found = True
            fresh = max(fresh, float(req.get("min_freshness_score", 0.0) or 0.0))
            auth = max(auth, float(req.get("min_authority_score", 0.0) or 0.0))
            rel = max(rel, float(req.get("min_relevance_score", 0.0) or 0.0))
            sq = req.get("source_quota")
            if isinstance(sq, dict):
                for k, v in sq.items():
                    quota[k] = max(quota.get(k, 0), int(v or 0))
        if not found:
            return None
        return SkillEvidenceSpec(
            min_freshness_score=fresh,
            min_authority_score=auth,
            min_relevance_score=rel,
            source_quota=quota,
        )

    def _fuse_claim_rules(
        self, report_skills: list[ReportSkill]
    ) -> list[dict[str, Any]]:
        """融合断言规则（§9.3）：is_required 取并集（任一必需即为必需）。"""
        merged: dict[str, dict[str, Any]] = {}
        for skill in report_skills:
            for rule in skill.claim_rules:
                if not isinstance(rule, dict):
                    continue
                key = str(
                    rule.get("claim_id") or rule.get("id") or rule.get("name")
                    or f"{skill.skill_id}:{id(rule)}"
                )
                existing = merged.get(key)
                if existing is None:
                    merged[key] = dict(rule)
                else:
                    # is_required 取并集（任一 True 即 True）
                    if rule.get("is_required") or existing.get("is_required"):
                        existing["is_required"] = True
                    # 合并其余字段（后者覆盖前者，保留首个的非空值）
                    for k, v in rule.items():
                        if k in ("claim_id", "id", "name", "is_required"):
                            continue
                        if existing.get(k) in (None, "", [], {}):
                            existing[k] = v
        return list(merged.values())

    def _fuse_section_templates(
        self, report_skills: list[ReportSkill]
    ) -> list[dict[str, Any]]:
        """融合章节模板（§9.3）：以 ReportSkill 为权威，按声明顺序去重。"""
        seen: set[str] = set()
        templates: list[dict[str, Any]] = []
        for skill in report_skills:
            for tmpl in skill.section_templates:
                if not isinstance(tmpl, dict):
                    continue
                sid = str(
                    tmpl.get("section_id") or tmpl.get("id") or tmpl.get("name") or ""
                )
                if sid and sid in seen:
                    continue
                if sid:
                    seen.add(sid)
                templates.append(dict(tmpl))
        return templates

    def _assess_composition(
        self,
        report_skills: list[ReportSkill],
        data_skills: list[DataSkill],
        retrieval_skills: list[RetrievalSkill],
        graph: SkillDependencyGraph,
    ) -> tuple[float, str]:
        """评估组合置信度与理由。"""
        n_report = len(report_skills)
        if n_report == 0:
            return 0.0, "无 ReportSkill，无法组合"
        # 多 ReportSkill 时降低置信度
        confidence = 1.0 if n_report == 1 else max(0.5, 1.0 - 0.15 * (n_report - 1))
        reasons: list[str] = []
        if n_report > 1:
            reasons.append(f"多 ReportSkill 组合（{n_report} 个）")
        if data_skills:
            reasons.append(f"{len(data_skills)} 个 DataSkill")
        if retrieval_skills:
            reasons.append(f"{len(retrieval_skills)} 个 RetrievalSkill")
        # 依赖图存在环时显著降低置信度
        cycle = graph.detect_cycle()
        if cycle:
            confidence *= 0.5
            reasons.append(f"依赖图存在环：{' -> '.join(cycle)}")
        return max(0.0, min(1.0, confidence)), "；".join(reasons) or "单一 ReportSkill 组合"


class MultiDataSkillDispatcher:
    """多数据源分发器（设计文档 §9.6）。

    按章节模板的 ``required_metric_ids`` 路由到能提供该指标的 DataSkill，
    生成对应的 ``PlanStep``（database 步骤）。

    类比 Java 中的 ``@Component`` 分发器：按 metric → DataSkill 映射分发查询。
    """

    def dispatch_queries(
        self,
        composite: CompositeSkillSet,
        section_templates: list[dict[str, Any]],
    ) -> list[PlanStep]:
        """按 required_metric_ids 将章节查询分发到对应 DataSkill。

        Args:
            composite: 组合清单（提供 data_skills）
            section_templates: 章节模板列表（含 required_metric_ids）

        Returns:
            数据库查询步骤列表（可并行）
        """
        steps: list[PlanStep] = []
        if not composite.data_skills:
            logger.debug("[MultiDataSkillDispatcher] 组合清单无 DataSkill，跳过分发")
            return steps

        for idx, tmpl in enumerate(section_templates):
            if not isinstance(tmpl, dict):
                continue
            metric_ids: list[str] = list(tmpl.get("required_metric_ids", []) or [])
            data_skill = self._find_data_skill(composite.data_skills, metric_ids)
            if data_skill is None:
                logger.warning(
                    "[MultiDataSkillDispatcher] 章节 %s 无匹配 DataSkill（metrics=%s）",
                    tmpl.get("section_id", f"section_{idx}"), metric_ids,
                )
                continue
            db_id = self._first_db_id(data_skill)
            section_id = str(tmpl.get("section_id", f"section_{idx}"))
            steps.append(PlanStep(
                step_id=f"composite_query_{idx}_{data_skill.skill_id}",
                tool="database",
                args=StepArgs(
                    query=str(tmpl.get("query", tmpl.get("purpose", ""))),
                    db_id=db_id,
                    extra={
                        "data_skill_id": data_skill.skill_id,
                        "required_metric_ids": metric_ids,
                        "section_id": section_id,
                    },
                ),
                depends_on=[],
                can_parallel=True,
                description=f"组合分发：{data_skill.skill_id} 处理章节 {section_id} 指标 {metric_ids}",
            ))
        return steps

    @staticmethod
    def _find_data_skill(
        data_skills: list[DataSkill], metric_ids: list[str]
    ) -> DataSkill | None:
        """查找能覆盖指定指标的 DataSkill（全覆盖优先，否则取覆盖最多者）。"""
        if not data_skills:
            return None
        if not metric_ids:
            return data_skills[0]
        best: DataSkill | None = None
        best_score = -1
        for skill in data_skills:
            bindings = skill.metric_bindings or {}
            covered = sum(1 for m in metric_ids if m in bindings)
            if covered == len(metric_ids):
                return skill
            if covered > best_score:
                best, best_score = skill, covered
        return best

    @staticmethod
    def _first_db_id(data_skill: DataSkill) -> str:
        """取 DataSkill 第一个 db_target 的 db_id（无则空串）。"""
        for target in data_skill.db_targets or []:
            if isinstance(target, dict) and target.get("db_id"):
                return str(target["db_id"])
        return ""
