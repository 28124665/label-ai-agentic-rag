"""Resolve a report skill set from an incoming request context.

P0 修复（§18.1 主链路和正确性）：生命周期、租户、权限在候选选择前过滤。

设计文档 §10.3 硬过滤顺序：
    lifecycle -> tenant -> permission -> runtime capability gate -> feature flag

本模块在 P0 阶段的简化：
    - lifecycle：用 ``Skill.enabled`` 作为 active 判定（P1 Catalog Snapshot 引入后从 governance.status 读取）
    - tenant：``ReportSkill.allowed_tenants`` 白名单（空列表表示允许所有租户）
    - permission：``PermissionIntersection``（required_permissions ⊆ user_permissions）
    - runtime capability gate：``agent_config.db_tool.enabled`` 请求级开关

P1 混合召回改造（§18.3）：
    - keyword 路径注入 BM25 混合召回替代纯关键词匹配
    - BM25 检索器在启动时由 Catalog 构建，通过 ``get_skill_resolver`` 注入
    - BM25 不可用时透明降级回原关键词匹配

类比 Java：
    ``SkillResolver`` ≈ ``@Service``，``SkillRegistry`` ≈ ``@Repository``，
    ``SkillResolverLifecycleFilter`` / ``PermissionIntersection`` ≈ 独立的 ``@Component`` 校验器。
    Resolver 编排校验器，符合"组合优于继承"原则。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from agent.langgraph.skills.governance import (
    PermissionIntersection,
    SkillResolverLifecycleFilter,
    SKILL_STATUS_ACTIVE,
)
from agent.langgraph.skills.models import (
    ReportSkill,
    ResolvedSkillSet,
    SkillBase,
    SkillResolveContext,
    SkillResolveResult,
)
from agent.langgraph.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


# ========== 混合召回配置（§18.3） ==========


@dataclass
class HybridRecallConfig:
    """混合召回配置（P1，§18.3）。

    Attributes:
        enabled: 是否启用混合召回（默认 True，False=降级为原关键词匹配）
        bm25_top_k: BM25 召回 Top-K（默认 20）
        rrf_top_k: RRF 融合后保留 Top-K（默认 20）
        enable_embedding: 是否启用 Embedding 召回（默认 False，P2+）
        enable_reranker: 是否启用 LLM Reranker（默认 False，P2+）
    """

    enabled: bool = True
    bm25_top_k: int = 20
    rrf_top_k: int = 20
    enable_embedding: bool = False
    enable_reranker: bool = False


class SkillResolver:
    """Resolve report skills in explicit-to-fallback priority order.

    P0 修复：所有候选在选择前必须通过前置过滤（§10.3）：
        - lifecycle（enabled = active）
        - tenant（allowed_tenants 白名单）
        - permission（required_permissions ⊆ user_permissions）
        - runtime capability gate（agent_config.db_tool.enabled）

    显式 skill_id / report_type 路径：命中后仍需前置过滤，过滤失败直接返回失败。
    keyword 路径：按命中分数降序遍历候选，跳过不可用的，选第一个合法的
        （§18.1 交付：无权限最高分候选不阻塞后续合法候选）。
    """

    def __init__(
        self,
        registry: SkillRegistry,
        lifecycle_filter: SkillResolverLifecycleFilter | None = None,
        # P1 混合召回注入（§18.3）
        bm25_retriever: Any | None = None,
        embedding_retriever: Any | None = None,
        reranker: Any | None = None,
        recall_config: HybridRecallConfig | None = None,
    ) -> None:
        self.registry = registry
        # 复用 governance.py 的生命周期过滤器，避免重复实现（§11.6.2 约束1：共享校验内核）
        self._lifecycle_filter = lifecycle_filter or SkillResolverLifecycleFilter()
        # P1 混合召回检索器（None=不可用，降级为原关键词匹配）
        self._bm25 = bm25_retriever
        self._embedding = embedding_retriever
        self._reranker = reranker
        self._recall_config = recall_config or HybridRecallConfig()

    def resolve(self, context: SkillResolveContext) -> SkillResolveResult:
        """Resolve the best report skill set for the supplied context.

        解析优先级（§2.1）：
            显式 skill_id > report_type > intent_keywords > tenant default > fallback

        所有路径均经过 ``_resolve_report_skill`` 的前置过滤，确保未授权/不可用 Skill
        不会进入候选集（§10.3 显式指定 Skill 时也必须经过硬过滤）。
        """
        # ===== 显式 skill_id 路径 =====
        # §10.3：显式指定 Skill 时也必须经过硬过滤
        if context.skill_id is not None:
            skill_id = context.skill_id.strip()
            if not skill_id:
                return self._failure("SKILL_NOT_FOUND")
            skill = self.registry.get_by_id(skill_id)
            if not isinstance(skill, ReportSkill):
                return self._failure("SKILL_NOT_FOUND")
            return self._resolve_report_skill(skill, "skill_id", context)

        # ===== report_type 路径 =====
        report_type = context.report_type or self._route_report_type(context)
        if report_type:
            skill = self.registry.get_report_by_type(report_type)
            if skill:
                return self._resolve_report_skill(skill, "report_type", context)

        # ===== keyword 路径（P1 混合召回，降级到原关键词匹配）=====
        # §18.3 混合召回：BM25 语义召回替代纯关键词子串匹配
        # BM25 索引不可用时透明降级为 _ranked_keyword_candidates()
        for skill in self._hybrid_recall_candidates(context.user_question, context):
            result = self._resolve_report_skill(skill, "keyword", context)
            if result.resolved:
                return result
            # 不可用（过滤失败）则继续尝试下一个候选
            logger.debug(
                "[SkillResolver] keyword 候选 '%s' 不可用: %s，尝试下一个",
                skill.skill_id,
                result.reason,
            )

        # ===== tenant default 路径 =====
        tenant_default = self._tenant_default_skill(context)
        if tenant_default:
            skill = self.registry.get_by_id(tenant_default)
            if isinstance(skill, ReportSkill):
                return self._resolve_report_skill(skill, "tenant_default", context)

        # ===== fallback 路径 =====
        # §10.8：fallback 必须记录原因，fallback_used=True 不等于命中
        fallback = self.registry.get_by_id("generic_analysis")
        if isinstance(fallback, ReportSkill):
            return self._resolve_report_skill(
                fallback, "fallback", context, fallback_used=True
            )
        return self._failure("NO_SKILL_AVAILABLE")

    def _resolve_report_skill(
        self,
        report_skill: ReportSkill,
        source: str,
        context: SkillResolveContext,
        fallback_used: bool = False,
    ) -> SkillResolveResult:
        """解析 ReportSkill 并构建 ResolvedSkillSet。

        流程：
            1. 前置过滤（§10.3 硬过滤）—— 失败返回 failure
            2. 依赖解析（DataSkill / RetrievalSkill）
            3. 依赖级过滤（tenant / permission / runtime capability）
            4. 构建 ResolvedSkillSet
        """
        # ===== 1. 前置过滤（§10.3 硬过滤顺序）=====
        # 顺序：lifecycle -> tenant -> permission -> runtime capability gate
        # 过滤失败时：显式路径返回 failure（由调用方决定是否继续），keyword 路径由 resolve() 继续尝试
        filter_reason = self._prefilter_skill(report_skill, context)
        if filter_reason is not None:
            return self._failure(filter_reason)

        # ===== 2. 依赖解析 =====
        data_skill = self.registry.get_data_for_report(report_skill.skill_id)
        retrieval_skill = self.registry.get_retrieval_for_report(report_skill.skill_id)
        warnings: list[str] = []

        # ===== 3. 依赖级过滤 =====
        # §10.3.1 约束5：依赖级租户校验（P0 阶段 DataSkill/RetrievalSkill 无 allowed_tenants 字段，默认放行）
        # §12.2 约束1：组合后权限是全部依赖权限的并集
        if data_skill is None:
            warnings.append(f"No data skill linked to '{report_skill.skill_id}'")
        elif not data_skill.enabled:
            warnings.append(f"Data skill '{data_skill.skill_id}' is disabled")
            data_skill = None
        elif not self._dependency_permission_ok(data_skill, context):
            warnings.append(
                f"Data skill '{data_skill.skill_id}' permission denied"
            )
            data_skill = None

        if retrieval_skill is None:
            warnings.append(
                f"No retrieval skill linked to '{report_skill.skill_id}'"
            )
        elif not retrieval_skill.enabled:
            warnings.append(
                f"Retrieval skill '{retrieval_skill.skill_id}' is disabled"
            )
            retrieval_skill = None
        elif not self._dependency_permission_ok(retrieval_skill, context):
            warnings.append(
                f"Retrieval skill '{retrieval_skill.skill_id}' permission denied"
            )
            retrieval_skill = None

        # ===== 4. 构建 ResolvedSkillSet =====
        skill_set = ResolvedSkillSet(
            report_skill=report_skill,
            data_skill=data_skill,
            retrieval_skill=retrieval_skill,
            resolution_source=source,
            fallback_used=fallback_used,
            warnings=warnings,
        )
        return SkillResolveResult(
            skill_set=skill_set,
            resolved=True,
            fallback_used=fallback_used,
            reason="RESOLVED",
            warnings=warnings,
        )

    # ========== 前置过滤（§10.3 硬过滤）==========

    def _prefilter_skill(
        self,
        skill: ReportSkill,
        context: SkillResolveContext,
    ) -> str | None:
        """前置过滤（§10.3 硬过滤），返回 None 表示通过，返回字符串表示拒绝原因。

        过滤顺序（§10.3）：
            lifecycle -> tenant -> permission -> runtime capability gate

        设计原则（§5.3 合法候选优先）：只有合法候选可以进入语义召回。
        显式 skill_id / report_type 路径也必须经过此过滤（§10.3 约束）。
        """
        # 1. lifecycle（P0 简化：enabled=True 视为 active）
        if not self._is_lifecycle_selectable(skill, context):
            return "SKILL_PERMISSION_DENIED"

        # 2. tenant（allowed_tenants 白名单，空列表表示允许所有租户）
        if not self._tenant_allowed(skill.allowed_tenants, context.tenant_id):
            return "SKILL_PERMISSION_DENIED"

        # 3. permission（required_permissions ⊆ user_permissions）
        if not self._permission_ok(skill, context):
            return "SKILL_PERMISSION_DENIED"

        # 4. runtime capability gate（§10.3.1 请求级工具开关过滤）
        if not self._runtime_capability_ok(skill, context):
            return "SKILL_PERMISSION_DENIED"

        return None

    def _is_lifecycle_selectable(
        self, skill: ReportSkill, context: SkillResolveContext
    ) -> bool:
        """生命周期过滤（§12.1）。

        P0 阶段简化：``Skill.enabled`` 作为 lifecycle 的简化判定。
            - enabled=True → 视为 active（生产可用）
            - enabled=False → 视为 deprecated（不可选择）

        P1 Catalog Snapshot 引入后，从 ``governance.status`` 读取真实生命周期状态，
        支持 draft/review/active/deprecated/archived 完整状态机。
        """
        # enabled=False 直接拒绝（向后兼容：原实现也检查 enabled）
        if not skill.enabled:
            return False
        # 复用 SkillResolverLifecycleFilter 的 active 判定逻辑
        # P0 阶段 tenant_overrides=None（不支持灰度），active 状态直接放行
        return self._lifecycle_filter.is_selectable(
            skill_status=SKILL_STATUS_ACTIVE,
            tenant_id=context.tenant_id,
            tenant_overrides=None,
        )

    @staticmethod
    def _tenant_allowed(allowed_tenants: list[str], tenant_id: str) -> bool:
        """租户白名单校验（§10.3 tenant 过滤层，§8.1 tenant_overrides 语义）。

        空列表表示允许所有租户（开放访问）；
        非空列表表示白名单（仅列表中的租户可访问）。

        注意（§8.1 约束）：``tenant_overrides=false`` 不得绕过 ``allowed_tenants``。
        P0 阶段未实现 tenant_overrides，仅校验 allowed_tenants 白名单。
        """
        if not allowed_tenants:
            return True
        return tenant_id in allowed_tenants

    @staticmethod
    def _permission_ok(skill: ReportSkill, context: SkillResolveContext) -> bool:
        """权限校验（§10.3 permission 过滤层，§12.2 权限约束）。

        使用 ``PermissionIntersection``：用户权限必须覆盖 Skill 要求的全部权限
        （required_permissions ⊆ user_permissions）。

        向后兼容：未配置 permissions 时默认放行（早期配置无此字段）。
        """
        config = context.agent_config or {}
        permissions = config.get("permissions")
        if permissions is None:
            # 未配置权限要求时默认放行（向后兼容）
            return True
        if not isinstance(permissions, list):
            return False
        return PermissionIntersection.is_skill_available(
            user_permissions=permissions,
            skill_required_permissions=skill.required_permissions,
        )

    def _runtime_capability_ok(
        self, skill: ReportSkill, context: SkillResolveContext
    ) -> bool:
        """runtime capability gate（§10.3.1 请求级工具开关过滤）。

        与 Catalog 级 feature_flag 区别：本 gate 按会话变化，每次请求重新评估。
        读取 ``AgentState.agent_config``，过滤依赖被禁用工具的 Skill。

        P0 阶段实现的检查：
            - db_tool 禁用 → 依赖 database 能力的 ReportSkill 不可用
              判定方式：有 linked DataSkill 或 required_evidence_types 含 "db"

        P1 Catalog 引入 ``SkillCard.capabilities`` 字段后，改为按 capabilities 字段判定。
        """
        config = context.agent_config or {}
        db_tool_config = config.get("db_tool", {})
        db_enabled = (
            db_tool_config.get("enabled", True)
            if isinstance(db_tool_config, dict)
            else True
        )

        if not db_enabled:
            # §10.3.1 约束2：db_tool 禁用时，依赖 database 能力的 Skill 不得进入候选
            # 判定 1：有 linked DataSkill → 依赖 database
            data_skill = self.registry.get_data_for_report(skill.skill_id)
            if data_skill is not None:
                return False
            # 判定 2：required_evidence_types 含 "db" → 依赖 database
            if "db" in (skill.required_evidence_types or []):
                return False

        # ReAct 禁用检查（§10.3.1 约束3）：P0 阶段 SkillBase 无 execution_mode 字段，跳过
        # P1 Catalog 引入 execution_mode 后补充

        return True

    # ========== 依赖级过滤 ==========

    @staticmethod
    def _dependency_permission_ok(
        skill: SkillBase, context: SkillResolveContext
    ) -> bool:
        """依赖级权限校验（§12.2 约束1：组合后权限是全部依赖权限的并集）。

        DataSkill/RetrievalSkill 的 required_permissions 也必须被用户权限覆盖。
        与 ReportSkill._permission_ok 逻辑一致，复用 PermissionIntersection。
        """
        config = context.agent_config or {}
        permissions = config.get("permissions")
        if permissions is None:
            return True
        if not isinstance(permissions, list):
            return False
        return PermissionIntersection.is_skill_available(
            user_permissions=permissions,
            skill_required_permissions=skill.required_permissions,
        )

    # ========== keyword 候选排序 ==========

    def _ranked_keyword_candidates(self, user_question: str) -> list[ReportSkill]:
        """返回按关键词命中分数降序排列的候选 ReportSkill 列表。

        P0 修复（§18.1 交付）：无权限最高分候选不阻塞后续合法候选。
        调用方（resolve）遍历此列表，跳过不可用的候选，选第一个合法的。

        排序规则：
            1. 按命中关键词长度总和降序（与原实现一致）
            2. 同分保持配置加载顺序（稳定排序，避免同分时结果受加载顺序影响）

        与原实现的差异：
            原实现返回单个最高分 Skill，无权限时直接失败；
            新实现返回排序后的列表，由 resolve() 遍历尝试。
        """
        normalized_question = user_question.casefold()
        candidates = [
            skill
            for skill in self.registry.list_all()
            if isinstance(skill, ReportSkill) and skill.enabled
        ]
        scored = [
            (
                sum(
                    len(keyword)
                    for keyword in skill.intent_keywords
                    if keyword.casefold() in normalized_question
                ),
                skill,
            )
            for skill in candidates
        ]
        matched = [(score, skill) for score, skill in scored if score > 0]
        # 按分数降序排序；同分保持配置加载顺序（Python sort 稳定）
        matched.sort(key=lambda item: item[0], reverse=True)
        return [skill for _, skill in matched]

    # ========== 辅助方法 ==========

    @staticmethod
    def _route_report_type(context: SkillResolveContext) -> str | None:
        """从 route_decision.metadata 提取 report_type 强信号。"""
        metadata = (context.route_decision or {}).get("metadata", {})
        value = metadata.get("report_type") if isinstance(metadata, dict) else None
        return value if isinstance(value, str) else None

    @staticmethod
    def _tenant_default_skill(context: SkillResolveContext) -> str | None:
        """从 agent_config 提取租户默认 Skill。"""
        config = context.agent_config or {}
        defaults = config.get("tenant_defaults", {})
        if isinstance(defaults, dict):
            value = defaults.get(context.tenant_id)
            if isinstance(value, str):
                return value
        value = config.get("default_skill_id")
        return value if isinstance(value, str) else None

    # ========== P1 混合召回（§18.3） ==========

    def _hybrid_recall_candidates(
        self,
        user_question: str,
        context: SkillResolveContext,
    ) -> list[ReportSkill]:
        """混合召回候选（BM25 + 领域分类），降级到原关键词匹配。

        流程（§18.3）：
            1. 请求级开关检查：agent_config.skill_router.enabled
            2. 前置过滤：用 _prefilter_skill 过滤所有 ReportSkill，得到合法候选
            3. 领域分类：规则分类器（router.classify_domains_by_rule），缩小候选范围
            4. BM25 召回：从 BM25 索引搜索，按相关性分数降序
            5. 降级：BM25 不可用时，走原 _ranked_keyword_candidates()

        Args:
            user_question: 用户提问
            context: 解析上下文（含 agent_config 开关）

        Returns:
            按匹配度降序排列的 ReportSkill 列表
        """
        # 1. 请求级开关检查
        agent_config = context.agent_config or {}
        router_config = agent_config.get("skill_router", {})
        if not router_config.get("enabled", self._recall_config.enabled):
            return self._ranked_keyword_candidates(user_question)

        # 2. 前置过滤 → 合法候选集
        all_reports = [
            s
            for s in self.registry.list_all()
            if isinstance(s, ReportSkill) and self._prefilter_skill(s, context) is None
        ]
        if not all_reports:
            return []

        # 3. BM25 混合召回
        if self._bm25 is not None:
            try:
                # 3a. 构建 SkillCard 列表（从 registry snapshot）
                cards = self._build_cards_for_recall(all_reports)
                if not cards:
                    return self._ranked_keyword_candidates(user_question)

                # 3b. 领域分类（§10.5.2）
                from agent.langgraph.skills.router import classify_domains_by_rule

                domains = classify_domains_by_rule(user_question)
                domain_filtered = self._filter_cards_by_domain(cards, domains)
                if not domain_filtered:
                    return self._ranked_keyword_candidates(user_question)

                # 3c. BM25 搜索
                results = self._bm25.search(
                    user_question, self._recall_config.bm25_top_k
                )
                if results:
                    # 3d. ScoredCard → ReportSkill 列表
                    return self._scored_to_skills(results, all_reports)
            except Exception as e:
                logger.warning(
                    "[SkillResolver] 混合召回异常，降级为关键词匹配: %s", e
                )

        # 4. 降级：原关键词匹配
        return self._ranked_keyword_candidates(user_question)

    def _build_cards_for_recall(
        self,
        skills: list[ReportSkill],
    ) -> list[Any]:
        """从 registry snapshot 获取 SkillCard 列表。

        从 registry.current_snapshot 的 cards_by_key 中提取
        与给定 ReportSkill 列表对应的 SkillCard。

        Args:
            skills: ReportSkill 列表

        Returns:
            对应的 SkillCard 列表（空列表表示 snapshot 不可用）
        """
        try:
            snapshot = self.registry.current_snapshot
            cards_by_key = snapshot.get_cards_by_key()
            if not cards_by_key:
                return []
            skill_ids = {s.skill_id for s in skills}
            result: list[Any] = []
            seen: set[str] = set()
            for (sid, _), card in cards_by_key.items():
                if sid in skill_ids and sid not in seen:
                    result.append(card)
                    seen.add(sid)
            return result
        except Exception:
            return []

    @staticmethod
    def _filter_cards_by_domain(
        cards: list[Any],
        domains: list[str],
    ) -> list[Any]:
        """领域过滤（§10.5.4）。

        domain 与 card.domains 有交集的保留。
        generic domain 的 Skill 始终保留（兜底）。

        Args:
            cards: SkillCard 列表
            domains: 领域分类结果（如 ["quality"]）

        Returns:
            过滤后的 SkillCard 列表
        """
        if "generic" in domains:
            return cards
        domain_set = set(domains)
        result: list[Any] = []
        for card in cards:
            if "generic" in card.domains:
                result.append(card)
                continue
            if domain_set & set(card.domains):
                result.append(card)
        return result

    @staticmethod
    def _scored_to_skills(
        results: list[Any],
        all_reports: list[ReportSkill],
    ) -> list[ReportSkill]:
        """ScoredCard 结果 → ReportSkill 列表（去重，按分数降序）。

        Args:
            results: BM25 检索结果（ScoredCard[]，按分数降序）
            all_reports: 合法 ReportSkill 候选列表

        Returns:
            按分数降序排列的 ReportSkill 列表（去重）
        """
        report_by_id = {s.skill_id: s for s in all_reports}
        seen: set[str] = set()
        ordered: list[ReportSkill] = []
        for scored in results:
            sid = scored.card.skill_id
            if sid in seen:
                continue
            seen.add(sid)
            skill = report_by_id.get(sid)
            if skill is not None:
                ordered.append(skill)
        return ordered

    @staticmethod
    def _failure(reason: str) -> SkillResolveResult:
        """构造失败结果。"""
        return SkillResolveResult(
            skill_set=None,
            resolved=False,
            fallback_used=False,
            reason=reason,
        )
