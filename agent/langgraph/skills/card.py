"""SkillCard 数据结构与 Catalog Builder 推导逻辑（P2，§8.1/§8.4）。

设计文档 §8.1 SkillCard：
    SkillCard 是 Skill 的检索视图，包含语义召回所需的全部字段。
    与 SkillBase/SkillManifest 的区别：
        - SkillBase：YAML 加载的原始配置（ReportSkill/DataSkill/RetrievalSkill）
        - SkillCard：从 SkillBase 推导的检索卡片，供 BM25/Embedding/Reranker 使用

设计文档 §8.4 迁移路径：
    阶段 1（P2 首批）：全部由 Catalog Builder 推导，不修改 YAML
    阶段 2（P2 完善）：YAML 可选新增 aliases/positive_examples 等字段
    阶段 3（P3 灰度）：governance 文件成为生命周期唯一 Owner

类比 Java：
    ``SkillCard`` ≈ 只读 DTO（``@Value`` / ``record``），
    ``CatalogBuilder`` ≈ ``@Service`` 的工厂类，从 ``SkillBase`` 推导 ``SkillCard``。
    推导逻辑集中在 ``build_card`` 单一方法，禁止在多处分散推导（§8.4.4 约束1）。
"""
from __future__ import annotations

import logging
from typing import Literal

from agent.langgraph.skills.catalog_models import SkillCard  # noqa: F401 — 向后兼容重导出
from agent.langgraph.skills.models import DataSkill, ReportSkill, RetrievalSkill, SkillBase

logger = logging.getLogger(__name__)


# ========== §8.4.2 report_type → domains 推导映射表 ==========
# 新增 report_type 时必须在此注册，否则 Catalog Builder 构建失败（§8.4.2 约束1）
REPORT_TYPE_DOMAIN_MAP: dict[str, list[str]] = {
    "quality_analysis": ["quality"],
    "production_analysis": ["production"],
    "production_daily": ["production"],
    "factory_performance": ["production"],
    "cost_analysis": ["cost"],
    "delivery_analysis": ["delivery"],
    "operations_analysis": ["operations"],
    "supplier_quality_analysis": ["quality", "supplier"],
    "generic_analysis": ["generic"],
}

# risk_level 推导规则：report_type 含以下前缀时为 high
_HIGH_RISK_REPORT_PREFIXES = ("financial", "compliance", "audit")


# ========== 推导辅助类型别名（仅供 CatalogBuilder 方法签名使用） ==========

RiskLevel = Literal["low", "medium", "high", "critical"]
CostClass = Literal["low", "medium", "high"]
LatencyClass = Literal["interactive", "batch"]


# ========== Catalog Builder 推导逻辑（§8.4） ==========


class CatalogBuilder:
    """从 SkillBase 列表推导 SkillCard（§8.4）。

    推导逻辑集中在 ``build_cards`` 方法，禁止在多处分散推导（§8.4.4 约束1）。
    推导失败（如未知 report_type）必须使 Catalog 构建失败（§8.4.4 约束3）。

    类比 Java：
        ``CatalogBuilder`` ≈ ``@Service`` 工厂类，
        ``build_card`` ≈ 工厂方法，从 ``SkillBase`` 原料生产 ``SkillCard`` 成品。
    """

    def __init__(
        self,
        data_skills_by_report: dict[str, DataSkill] | None = None,
        retrieval_skills_by_report: dict[str, RetrievalSkill] | None = None,
    ) -> None:
        """初始化 Catalog Builder。

        Args:
            data_skills_by_report: linked_report_skill_id → DataSkill 映射（用于推导 capabilities）
            retrieval_skills_by_report: linked_report_skill_id → RetrievalSkill 映射
        """
        self._data_skills_by_report = data_skills_by_report or {}
        self._retrieval_skills_by_report = retrieval_skills_by_report or {}

    def build_cards(self, skills: list[SkillBase]) -> dict[tuple[str, str], SkillCard]:
        """从 SkillBase 列表构建 SkillCard 字典（§8.4.4 约束1：集中推导）。

        Args:
            skills: 已通过校验的 SkillBase 列表

        Returns:
            dict: (skill_id, version) → SkillCard 映射

        Raises:
            ValueError: 推导失败（如未知 report_type，§8.4.4 约束3）
        """
        cards: dict[tuple[str, str], SkillCard] = {}
        for skill in skills:
            card = self.build_card(skill)
            key = (card.skill_id, card.version)
            cards[key] = card
        return cards

    def build_card(self, skill: SkillBase) -> SkillCard:
        """从单个 SkillBase 推导 SkillCard（§8.4.4 约束1：单一推导入口）。

        推导规则按字段类型分组：
            1. 直接映射字段（skill_id/version/name/description/...）
            2. 从 report_type 推导的字段（domains/risk_level）
            3. 从依赖关系推导的字段（capabilities/cost_class）
            4. 从 YAML 可选字段读取的字段（aliases/positive_examples/...）

        Args:
            skill: SkillBase 实例（ReportSkill/DataSkill/RetrievalSkill）

        Returns:
            SkillCard: 推导出的检索卡片

        Raises:
            ValueError: ReportSkill 的 report_type 未在 REPORT_TYPE_DOMAIN_MAP 注册
        """
        if isinstance(skill, ReportSkill):
            return self._build_report_card(skill)
        if isinstance(skill, DataSkill):
            return self._build_data_card(skill)
        if isinstance(skill, RetrievalSkill):
            return self._build_retrieval_card(skill)
        raise ValueError(f"Unknown skill type: {type(skill).__name__}")

    # ========== ReportSkill → SkillCard ==========

    def _build_report_card(self, skill: ReportSkill) -> SkillCard:
        """从 ReportSkill 推导 SkillCard。

        推导规则（§8.4.1）：
            - domains: 从 report_type 推导（REPORT_TYPE_DOMAIN_MAP）
            - capabilities: 有 linked DataSkill → ["database","report"]，
                           有 linked RetrievalSkill → 追加 "rag"，否则 ["report"]
            - intents: 从 intent_keywords 复用
            - risk_level: report_type 含 financial/compliance → "high"，否则 "medium"
            - cost_class: 有 DataSkill → "medium"，纯 RAG → "low"
            - latency_class: report_type 含 batch/monthly/quarterly → "batch"
            - lifecycle_status: enabled=True → "active"，enabled=False → "deprecated"
            - namespace: 从 domain + skill_type + skill_id 拼接（§8.1 稳定 namespace）
            - required_dependency_ids: 从 linked DataSkill/RetrievalSkill 推导（§10.3.1 依赖级租户校验）
        """
        # §8.4.2 约束1：未知 report_type 导致构建失败
        domains = REPORT_TYPE_DOMAIN_MAP.get(skill.report_type)
        if domains is None:
            raise ValueError(
                f"ReportSkill '{skill.skill_id}' has unregistered report_type "
                f"'{skill.report_type}'; register it in REPORT_TYPE_DOMAIN_MAP"
            )

        # 推导 capabilities（§8.4.1）
        capabilities = self._derive_capabilities(skill)

        # 推导 risk_level（§8.4.1）
        risk_level = self._derive_risk_level(skill.report_type)

        # 推导 cost_class（§8.4.1）
        cost_class = self._derive_cost_class(skill)

        # 推导 latency_class（§8.4.1）
        latency_class = self._derive_latency_class(skill.report_type)

        # 推导 lifecycle_status（§8.4.1：enabled → active/deprecated）
        lifecycle_status: str = "active" if skill.enabled else "deprecated"

        # 读取 YAML 可选字段（阶段 2 填充，阶段 1 为空列表/默认值）
        aliases = list(getattr(skill, "aliases", []) or [])
        positive_examples = list(getattr(skill, "positive_examples", []) or [])
        negative_examples = list(getattr(skill, "negative_examples", []) or [])
        exclusion_rules = list(getattr(skill, "exclusion_rules", []) or [])

        # 推导 required_dependency_ids（§10.3.1 约束5：依赖级租户校验）
        required_dependency_ids = self._derive_required_dependency_ids(skill)

        return SkillCard(
            # 基础标识
            skill_id=skill.skill_id,
            version=skill.version,
            skill_type="report",
            namespace=self._derive_namespace(skill.skill_id, "report", domains),
            name=skill.name,
            description=skill.description,
            # 检索维度
            domains=domains,
            capabilities=capabilities,
            intents=list(skill.intent_keywords),
            aliases=aliases,
            languages=["zh_CN"],
            # 检索语料
            positive_examples=positive_examples,
            negative_examples=negative_examples,
            required_context=list(skill.required_context),
            exclusion_rules=exclusion_rules,
            # 治理投影
            required_permissions=list(skill.required_permissions),
            allowed_tenants=list(skill.allowed_tenants),
            lifecycle_status=lifecycle_status,
            tenant_overrides={},
            # 风险与成本
            risk_level=risk_level,
            cost_class=cost_class,
            latency_class=latency_class,
            # 依赖与冲突（P3 阶段由 governance/manifest 填充，阶段 1 默认值）
            conflicts_with=[],
            dependencies_healthy=True,
            required_dependency_ids=required_dependency_ids,
            # 执行模式
            execution_mode="main_chain",
            # 路由投影（§10.4 Stage 2 强信号匹配用）
            report_type=skill.report_type,
        )

    # ========== DataSkill → SkillCard ==========

    def _build_data_card(self, skill: DataSkill) -> SkillCard:
        """从 DataSkill 推导 SkillCard。

        DataSkill 的 SkillCard 用于依赖图和组合 Skill 检索，
        不直接参与 Stage 4 混合召回（混合召回只针对 ReportSkill）。
        """
        lifecycle_status: str = "active" if skill.enabled else "deprecated"
        return SkillCard(
            # 基础标识
            skill_id=skill.skill_id,
            version=skill.version,
            skill_type="data",
            namespace=self._derive_namespace(skill.skill_id, "data", ["data"]),
            name=skill.name,
            description=skill.description,
            # 检索维度
            domains=["data"],
            capabilities=["database"],
            intents=[],
            aliases=[],
            languages=["zh_CN"],
            # 检索语料
            positive_examples=[],
            negative_examples=[],
            required_context=[],
            exclusion_rules=[],
            # 治理投影
            required_permissions=list(skill.required_permissions),
            allowed_tenants=[],
            lifecycle_status=lifecycle_status,
            tenant_overrides={},
            # 风险与成本
            risk_level="medium",
            cost_class="medium",
            latency_class="interactive",
            # 依赖与冲突
            conflicts_with=[],
            dependencies_healthy=True,
            required_dependency_ids=[],
            # 执行模式
            execution_mode="main_chain",
            # 路由投影
            report_type=None,
        )

    # ========== RetrievalSkill → SkillCard ==========

    def _build_retrieval_card(self, skill: RetrievalSkill) -> SkillCard:
        """从 RetrievalSkill 推导 SkillCard。"""
        lifecycle_status: str = "active" if skill.enabled else "deprecated"
        return SkillCard(
            # 基础标识
            skill_id=skill.skill_id,
            version=skill.version,
            skill_type="retrieval",
            namespace=self._derive_namespace(skill.skill_id, "retrieval", ["retrieval"]),
            name=skill.name,
            description=skill.description,
            # 检索维度
            domains=["retrieval"],
            capabilities=["rag"],
            intents=[],
            aliases=[],
            languages=["zh_CN"],
            # 检索语料
            positive_examples=[],
            negative_examples=[],
            required_context=[],
            exclusion_rules=[],
            # 治理投影
            required_permissions=list(skill.required_permissions),
            allowed_tenants=[],
            lifecycle_status=lifecycle_status,
            tenant_overrides={},
            # 风险与成本
            risk_level="low",
            cost_class="low",
            latency_class="interactive",
            # 依赖与冲突
            conflicts_with=[],
            dependencies_healthy=True,
            required_dependency_ids=[],
            # 执行模式
            execution_mode="main_chain",
            # 路由投影
            report_type=None,
        )

    # ========== 推导辅助方法 ==========

    def _derive_capabilities(self, report_skill: ReportSkill) -> list[str]:
        """推导 capabilities（§8.4.1）。

        有 linked DataSkill → ["database", "report"]
        有 linked RetrievalSkill → 追加 "rag"
        否则 → ["report"]
        """
        caps: list[str] = ["report"]
        if self._data_skills_by_report.get(report_skill.skill_id):
            caps.append("database")
        if self._retrieval_skills_by_report.get(report_skill.skill_id):
            caps.append("rag")
        return caps

    @staticmethod
    def _derive_risk_level(report_type: str) -> RiskLevel:
        """推导 risk_level（§8.4.1）。

        report_type 含 financial/compliance/audit → "high"
        否则 → "medium"
        critical 级别只能通过 YAML 显式字段设置（不在推导范围内）。
        """
        report_lower = report_type.lower()
        if any(prefix in report_lower for prefix in _HIGH_RISK_REPORT_PREFIXES):
            return "high"
        return "medium"

    @staticmethod
    def _derive_cost_class(report_skill: ReportSkill) -> CostClass:
        """推导 cost_class（§8.4.1）。

        有 DataSkill 依赖 → "medium"
        纯 RAG（无 DataSkill）→ "low"
        批量报告（report_type 含 batch）→ "high"
        """
        report_lower = (report_skill.report_type or "").lower()
        if "batch" in report_lower:
            return "high"
        # 通过 linked_skills 判断是否有 DataSkill
        if report_skill.linked_skills.get("data"):
            return "medium"
        return "low"

    @staticmethod
    def _derive_latency_class(report_type: str) -> LatencyClass:
        """推导 latency_class（§8.4.1）。

        report_type 含 batch/monthly/quarterly → "batch"
        否则 → "interactive"
        """
        report_lower = report_type.lower()
        if any(tag in report_lower for tag in ("batch", "monthly", "quarterly")):
            return "batch"
        return "interactive"

    @staticmethod
    def _derive_namespace(skill_id: str, skill_type: str, domains: list[str]) -> str:
        """推导 namespace（§8.1 稳定 namespace）。

        格式：``{primary_domain}.{skill_type}.{skill_id}``，如
        ``quality.report.exception_analysis``。
        无 domain 时退化为 ``{skill_type}.{skill_id}``。
        """
        primary = domains[0] if domains else skill_type
        return f"{primary}.{skill_type}.{skill_id}"

    def _derive_required_dependency_ids(self, report_skill: ReportSkill) -> list[str]:
        """推导 required_dependency_ids（§10.3.1 约束5：依赖级租户校验）。

        收集 linked DataSkill / RetrievalSkill 的 skill_id，
        供 HardFilterChain 在依赖级租户校验时查询依赖 Skill 的 allowed_tenants。
        """
        dep_ids: list[str] = []
        data_skill = self._data_skills_by_report.get(report_skill.skill_id)
        if data_skill:
            dep_ids.append(data_skill.skill_id)
        retrieval_skill = self._retrieval_skills_by_report.get(report_skill.skill_id)
        if retrieval_skill:
            dep_ids.append(retrieval_skill.skill_id)
        return dep_ids


def build_cards_from_skills(
    skills: list[SkillBase],
    data_skills_by_report: dict[str, DataSkill] | None = None,
    retrieval_skills_by_report: dict[str, RetrievalSkill] | None = None,
) -> dict[tuple[str, str], SkillCard]:
    """便捷函数：从 SkillBase 列表构建 SkillCard 字典。

    Args:
        skills: 已通过校验的 SkillBase 列表
        data_skills_by_report: linked_report_skill_id → DataSkill 映射
        retrieval_skills_by_report: linked_report_skill_id → RetrievalSkill 映射

    Returns:
        dict: (skill_id, version) → SkillCard 映射
    """
    builder = CatalogBuilder(data_skills_by_report, retrieval_skills_by_report)
    return builder.build_cards(skills)
