#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""ResponseBuilder — 端到端引用响应构建（v2.1 §4.8，P1-G 修复，验收项 16/24）。

设计目标：
    将验证完成后的 Claim 裁决、不可变 EvidenceSnapshot、结构化 Answer AST
    统一组装为对客户端的引用响应契约：answer + citation_catalog + claim_verdicts
    + metrics。整条链路（Prompt 组装 / verifier 验证 / 最终 references）通过
    同一个 ``evidence_snapshot_id`` 串起，保证证据一致性。

核心安全约束（评审硬约束 #1 + 验收项 16）：
    - 授权过滤不重编号：保留 snapshot 中的原始编号，越权 Evidence 标记
      ``accessible=false`` 而非删除，确保 ``claim.raw_declared_indices`` 与
      ``citation_catalog.index`` 仍能对应（否则客户端 [1] 会错位指向别条证据）。
    - DB 类型 source_uri 脱敏：无 ``db_admin`` 权限时只保留表名，移除 SQL 指纹
      与 query_id，禁止向无权限用户暴露内部查询结构。
    - Web 来源标记 ``injection_warning``：Web 内容可能携带恶意指令，前端需隔离渲染。

类比 Java 中的门面（Facade）：
    ``ResponseBuilder`` 编排 CitationMetrics / AnswerRenderer / EvidenceSnapshot
    等组件，对外提供单一 ``build`` 入口，类似 ``@Service class ResponseFacade``
    组合多个领域服务完成一次响应装配。
"""
from __future__ import annotations

from typing import Any

from agent.langgraph.evidence.answer_ast import AnswerAST, AnswerRenderer
from agent.langgraph.evidence.claim import Claim
from agent.langgraph.evidence.citation_metrics import CitationMetrics
from agent.langgraph.evidence.snapshot import EvidenceSnapshot

# 合法的 enforcement_mode 取值（与 config.EnforcementMode 取值保持一致）。
# 此处刻意不跨模块导入 EnforcementMode，保持本模块自包含、可独立序列化，
# 避免 evidence 子包反向依赖 langgraph.config 产生循环导入。
_VALID_ENFORCEMENT_MODES = frozenset({"disabled", "shadow", "enforced"})


class ResponseBuilder:
    """构建端到端引用响应，含租户授权复核与脱敏约束（v2.1 §4.8，P1-G 修复）。

    职责边界：
        - 只做“装配 + 授权过滤 + 脱敏”，不重新计算 Claim 状态或重跑验证；
        - Claim 状态由 VerdictMatrix 判定，指标由 CitationMetrics 计算，
          答案文本由 AnswerRenderer 渲染，本类仅负责编排与安全裁剪。

    Attributes:
        enforcement_mode: 强制级别 disabled / shadow / enforced，原样写入响应，
            供客户端感知本轮是否提供 Claim 可追溯保证（P0-3 修复：feature flag
            关闭不再是逃逸口，响应显式标注 disabled）。
    """

    def __init__(self, enforcement_mode: str = "disabled") -> None:
        """初始化响应构建器。

        Args:
            enforcement_mode: 强制级别，取值 disabled / shadow / enforced。

        Raises:
            ValueError: enforcement_mode 不在合法取值集合内。
        """
        if enforcement_mode not in _VALID_ENFORCEMENT_MODES:
            raise ValueError(
                f"非法 enforcement_mode: {enforcement_mode!r}，"
                f"合法取值: {sorted(_VALID_ENFORCEMENT_MODES)}"
            )
        self.enforcement_mode = enforcement_mode
        # 无状态组件，复用实例避免每次 build 重复构造
        self._metrics_calculator = CitationMetrics()
        self._renderer = AnswerRenderer()

    def build(
        self,
        answer_ast: AnswerAST,
        claims: list[Claim],
        snapshot: EvidenceSnapshot,
        tenant_id: str,
        user_permissions: list[str],
    ) -> dict[str, Any]:
        """构建最终引用响应。

        Args:
            answer_ast: 结构化答案 AST（已由 VerdictMatrix 回填 final_status）。
            claims: Claim 列表（含引用绑定与验证裁决）。
            snapshot: 不可变 Evidence 快照（evidences 顺序即引用编号顺序）。
            tenant_id: 当前请求租户 ID（用于授权复核）。
            user_permissions: 当前用户权限列表（如 ``["db_admin"]``）。

        Returns:
            dict: 端到端引用响应契约，结构见模块 docstring。
        """
        # 1. 租户授权复核（评审硬约束 #1）+ citation_catalog 构建
        # ★ 验收项 16：授权过滤不重编号
        #   保留 snapshot 中的原始编号（1-based，顺序与 snapshot.evidences 一致），
        #   越权 Evidence 标记 accessible=false 而非删除，
        #   确保 claim.raw_declared_indices 与 citation_catalog.index 能对应。
        citation_catalog: list[dict[str, Any]] = []
        for idx, ev in enumerate(snapshot.evidences, start=1):
            accessible = self._is_accessible(ev, tenant_id, user_permissions)
            citation_catalog.append({
                "index": idx,  # ★ 保留 snapshot 原始编号，不重编号
                "evidence_id": ev.get("evidence_id", ""),
                "source_type": ev.get("source_type", ""),
                # 越权时不暴露任何定位信息，source_uri 置空
                "source_uri": self._scrub_uri(ev.get("source_uri", ""), user_permissions) if accessible else "",
                "title": ev.get("title", "") if accessible else "",
                "page": ev.get("metadata", {}).get("page", "") if accessible else "",
                "authority_score": ev.get("authority_score", 0.0) if accessible else 0.0,
                "accessible": accessible,  # ★ 越权标记 false，不删除（保留编号对应关系）
            })

        # 2. Web/RAG Prompt Injection 隔离（验收项 24 配套约束）
        # Web 抓取内容可能包含恶意指令，前端需隔离渲染，不可直接拼入 Prompt
        for item in citation_catalog:
            if item["source_type"] == "web":
                item["injection_warning"] = True

        return {
            "schema_version": "1.0",
            "answer": self._render_answer(answer_ast),
            "evidence_snapshot_id": snapshot.snapshot_id,
            "enforcement_mode": self.enforcement_mode,
            "citation_catalog": citation_catalog,
            "claim_verdicts": self._build_claim_verdicts(claims),
            "metrics": self._compute_metrics(claims, snapshot),
        }

    def _is_accessible(
        self,
        evidence: dict[str, Any],
        tenant_id: str,
        permissions: list[str],
    ) -> bool:
        """判断用户是否有权访问该 Evidence（评审硬约束 #1）。

        授权规则：
            - 租户隔离：evidence.tenant_id 必须与请求 tenant_id 一致；
            - DB 类型需 ``db_admin`` 权限：数据库行级证据可能含敏感数据，
              无 db_admin 权限的用户即使同租户也只能看到脱敏后的表名。

        Args:
            evidence: Evidence 字典。
            tenant_id: 当前请求租户 ID。
            permissions: 当前用户权限列表。

        Returns:
            bool: 是否可访问。
        """
        # 租户隔离：跨租户证据一律不可访问
        if evidence.get("tenant_id", "") != tenant_id:
            return False
        # DB 类型需 db_admin 权限（行级证据含具体业务数据，权限收紧）
        if evidence.get("source_type") == "db" and "db_admin" not in permissions:
            return False
        return True

    def _scrub_uri(self, uri: str, permissions: list[str]) -> str:
        """脱敏 source_uri（禁止暴露 SQL 指纹 / query_id 给无权限用户）。

        DB 类型 uri 形如 ``db://{table_path}/{query_id}/{sql_fingerprint}/{row_key}?as_of=...``，
        无 ``db_admin`` 权限时只保留表名（``db://{table_path}``），移除 query_id、
        SQL 指纹与行键，避免泄露内部查询结构与参数化指纹。

        Args:
            uri: 原始 source_uri。
            permissions: 当前用户权限列表。

        Returns:
            str: 脱敏后的 uri（非 DB 类型或有 db_admin 权限时原样返回）。
        """
        if "db_admin" not in permissions and uri.startswith("db://"):
            # "db://table_path/query_id/fingerprint/row_key?as_of=..."
            # split("/") → ["db:", "", "table_path", "query_id", ...]
            # parts[2] 即 table_path（含多表时为 table1/table2 形式）
            parts = uri.split("/")
            table_part = parts[2] if len(parts) > 2 and parts[2] else "unknown"
            return f"db://{table_part}"
        return uri

    def _build_claim_verdicts(self, claims: list[Claim]) -> list[dict[str, Any]]:
        """序列化 Claim 裁决列表（v2.1 §4.8.1，P0-4 修复）。

        分类保存引用编号（评审硬约束 #2 + 验收项 15/16）：
            - raw_declared_indices：模型原始声明（含 [999] 等非法编号，不丢失）
            - resolved_declared_ids：合法映射的 evidence_id
            - invalid_indices：不存在的编号
            - unauthorized_ids：越权 evidence_id（与 citation_catalog.accessible 对应）
            - verified_support_ids：验证通过的支持 ID
            - contradicting_ids：反证 ID
            - server_is_required：服务端重新计算的关键性（不信任模型输出）

        Args:
            claims: Claim 列表。

        Returns:
            list[dict]: Claim 裁决字典列表。
        """
        verdicts: list[dict[str, Any]] = []
        for claim in claims:
            verdicts.append({
                "claim_id": claim.claim_id,
                "text": claim.text,
                "final_status": claim.final_status,
                # ★ 分类保存引用编号，禁止覆盖原始声明
                "raw_declared_indices": list(claim.raw_declared_indices),
                "resolved_declared_ids": list(claim.resolved_declared_ids),
                "invalid_indices": list(claim.invalid_indices),
                "unauthorized_ids": list(claim.unauthorized_ids),
                # ★ 分离保存验证结果
                "verified_support_ids": list(claim.verified_support_ids),
                "contradicting_ids": list(claim.contradicting_ids),
                # 服务端重新计算的字段优先（任务二建议 2）
                "claim_type": claim.server_claim_type,
                "is_required": claim.is_required,
                "server_is_required": claim.server_is_required,
            })
        return verdicts

    def _compute_metrics(
        self,
        claims: list[Claim],
        snapshot: EvidenceSnapshot,
    ) -> dict[str, float]:
        """计算引用与忠实度指标（v2.1 §4.7，P0-C 修复）。

        委托 CitationMetrics 基于 (claim_id, evidence_id) 对聚合计算，避免 v1
        全局去重导致的虚引漏检。snapshot 当前仅作上下文保留，指标口径完全由
        claims 驱动，便于后续扩展（如按 snapshot 来源配额加权）。

        Args:
            claims: Claim 列表（含验证结果与引用绑定）。
            snapshot: 不可变 Evidence 快照（保留用于未来扩展）。

        Returns:
            dict: citation_validity / citation_correctness /
                citation_completeness / faithfulness 四项指标。
        """
        return self._metrics_calculator.compute(claims)

    def _render_answer(self, answer_ast: AnswerAST) -> str:
        """渲染答案 AST 为 Markdown 文本（v2 修复 P0-7）。

        委托 AnswerRenderer 只渲染 ``final_status == "supported"`` 的 Claim，
        Narrative 原样输出，避免 ``" ".join`` 重组破坏标题/段落/表格结构。
        未验证或验证失败的 Claim 不出现在最终答案文本中（fail-closed）。

        Args:
            answer_ast: 结构化答案 AST。

        Returns:
            str: 仅包含 supported claim 的 Markdown 文本。
        """
        return self._renderer.render(answer_ast)
