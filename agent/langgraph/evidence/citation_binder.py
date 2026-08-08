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
"""引用绑定闭环（v2.1 §4.2.2，P0-4/P0-5/P0-8 全面修复；v3.0 NLI+LLM 分层融合）。

设计目标：
    将模型声明的引用编号解析、分类、验证、反证扫描串成一条闭环，
    为每个 Claim 产出独立的 pair_verdicts，并按 pair 聚合 verifier_status。

核心修复（v2 评审）：
    - P0-4: 保留 raw_declared_indices（含 [999] 等非法编号），分类保存
            invalid_indices / unauthorized_ids，禁止覆盖原始声明。
    - P0-5: 每个 Claim-Evidence pair 拥有独立 PairVerdict，异常不污染其它 pair，
            Claim 级 verifier_status 按 pair 聚合（不再无条件设为 ok）。
    - P0-8: 拆成两条链——Declared-pair verification（模型声明） +
            Counter-evidence scan（反证扫描，只写入 contradicting_ids，
            不计入模型引用），并补充联合证据蕴含检查（_verify_joint_support）。
    - 任务二建议 3: 整轮验证预算（Claim 数 / 引用数 / pair 数）超限即截断或
            标记 budget_exhausted，避免单轮验证雪崩。

v3.0 升级（NLI + LLM 分层验证融合）：
    - 新增 ``pair_verifier`` 注入参数，可选注入 PairVerifier 实例
    - 新增 ``bind_async()`` 方法，通过 PairVerifier 进行 batch 三层验证，
      替代原有的 _verify_pair 字符重叠度启发式
    - 向后兼容：bind() 仍使用旧版 _judge_overlap 启发式

简化说明：
    本实现的 _verify_pair 使用字符/实体重叠度启发式判断 supported /
    contradicted / insufficient；生产环境应在 bind_async 中传入
    PairVerifier 实例（rule + nli + llm 三层 batch 验证），
    接口签名保持兼容。

类比 Java 中的门面（Facade）+ 策略模式：
    ``CitationBinder`` 是编排门面，内部将“解析 / 验证 / 反证扫描 / 联合蕴含”
    拆为独立策略方法，新增验证器实现无需改动 bind 主流程。
"""

from __future__ import annotations

import logging
import re

from agent.langgraph.config import VerificationBudget
from agent.langgraph.evidence.claim import (
    Claim,
    ClaimFinalStatus,
    PairStatus,
    PairVerdict,
    VerifierStatus,
)
from agent.langgraph.evidence.snapshot import EvidenceSnapshot
from agent.langgraph.evidence.pair_verifier import PairInput, PairVerifier

logger = logging.getLogger(__name__)


class CitationBinder:
    """引用绑定闭环（v2 评审 P0-4/P0-5/P0-8 全面修复）。

    使用方式::

        binder = CitationBinder(snapshot, tenant_id, budget=VerificationBudget())
        claims = binder.bind(claims, answer_text)

    设计决策：
        - snapshot 在构造期建立 index↔evidence 双向映射，运行期只读，
          保证与 Prompt 组装、最终 references 引用同一份不可变证据。
        - 所有写操作作用于传入的 Claim 对象（就地修改），便于上游 state 直接序列化。
    """

    # 引用编号正则：匹配 [1]、[12] 等模型声明的编号（含非法 [999]）
    _CITATION_PATTERN = re.compile(r"\[(\d+)\]")

    # 否定/冲突指示词（用于矛盾预判，简化版 verifier）
    _NEGATION_WORDS: frozenset[str] = frozenset(
        {
            "不",
            "没有",
            "无",
            "非",
            "未",
            "否认",
            "反对",
            "拒绝",
            "错",
            "假",
            "否",
            "并非",
            "并未",
            "相反",
            "冲突",
        }
    )

    def __init__(
        self,
        snapshot: EvidenceSnapshot,
        tenant_id: str,
        budget: VerificationBudget | None = None,
        pair_verifier: PairVerifier | None = None,
    ) -> None:
        """初始化引用绑定器。

        Args:
            snapshot: 不可变 Evidence 快照（v2.1 §4.1.1），构造期建立索引映射。
            tenant_id: 当前租户 ID，用于越权过滤。
            budget: 整轮验证预算；为 None 时不做预算限制（仅供调试/单测）。
            pair_verifier: 可选 PairVerifier 实例（v3.0），
                注入后 bind_async 使用 batch 三层验证替代字符重叠度启发式。
        """
        self.snapshot = snapshot
        self.tenant_id = tenant_id
        self.budget = budget
        self.pair_verifier = pair_verifier

        # snapshot 提供防御性拷贝；若无该方法则直接读 evidences（tuple）
        evidences = snapshot.get_evidences() if hasattr(snapshot, "get_evidences") else list(getattr(snapshot, "evidences", []))

        # 1-based 编号 → Evidence（用于解析 [1][2] 等引用编号）
        self.index_to_evidence: dict[int, dict] = {idx: ev for idx, ev in enumerate(evidences, start=1)}
        # evidence_id → Evidence（用于越权过滤与 pair 验证）
        self.ev_id_to_evidence: dict[str, dict] = {ev.get("evidence_id", ""): ev for ev in evidences if ev.get("evidence_id")}
        # evidence_id → 1-based 编号（预算截断时反查原始编号写入 truncated_citations）
        self.ev_id_to_index: dict[str, int] = {ev.get("evidence_id", ""): idx for idx, ev in enumerate(evidences, start=1) if ev.get("evidence_id")}

        # 整轮 pair 预算计数（任务二建议 3）
        self._pair_budget_used = 0
        self._pair_budget_exhausted = False

    # ============================== 主入口 ==============================

    async def bind_async(self, claims: list[Claim], answer_text: str) -> list[Claim]:
        """异步绑定闭环（v3.0 NLI+LLM 分层融合，§5 设计实现）。

        与 ``bind()`` 流程相同，但使用 ``PairVerifier.verify_pairs()`` 进行
        batch 三层验证（Rule + NLI + LLM），替代 _judge_overlap 字符重叠度启发式。

        流程（每个 Claim）：
            1-4. 与 bind() 相同（预算/解析/分类/截断）
            5. 收集所有 Claim-Evidence pair → 调用 PairVerifier.verify_pairs()
            6. 回填 PairVerdict 到各 Claim
            7. 反证扫描（与 bind() 相同）
            8. 联合证据蕴含检查（与 bind() 相同）
            9. 按 pair 聚合 verifier_status

        Args:
            claims: Claim 列表（就地修改）。
            answer_text: 模型原始答案文本（用于解析 [1][2] 引用编号）。

        Returns:
            传入的 claims 列表（已就地更新 pair_verdicts / verifier_status 等）。

        Raises:
            RuntimeError: 未注入 pair_verifier 时调用 bind_async 抛出。
        """
        if self.pair_verifier is None:
            raise RuntimeError(
                "bind_async() 需要 pair_verifier 参数，请在构造时注入"
            )

        max_claims = self.budget.max_claims if self.budget else None

        for i, claim in enumerate(claims):
            # ---- 1. Claim 数量预算（与 bind() 相同）----
            if max_claims is not None and i >= max_claims:
                claim.final_status = ClaimFinalStatus.TRUNCATED.value
                claim.verifier_status = VerifierStatus.UNKNOWN.value
                logger.debug(
                    "[citation_binder] async claim %s 超出 max_claims=%d 被截断",
                    claim.claim_id,
                    max_claims,
                )
                continue

            # ---- 2. 解析模型声明的引用编号 ----
            raw_citations = self._extract_citations_for_claim(claim, answer_text)

            # ---- 3. 分类保存（P0-4）----
            self._parse_declared_citations(claim, raw_citations)

            # ---- 4. 引用数量预算 ----
            authorized_ids = self._truncate_citations_by_budget(claim)

            # 重置 pair_verdicts 以保证幂等
            claim.pair_verdicts = []
            claim.verified_support_ids = []
            claim.contradicting_ids = []

            # 暂存 authorized_ids 供后续收集（bind_async 内部使用）
            claim._auth_ids_for_async = authorized_ids

        # ---- 5. 收集所有 pair 并一次性调用 PairVerifier ----
        all_pairs: list[PairInput] = []
        pair_index_map: list[tuple[int, str]] = []  # (claim_index, evidence_id)

        for ci, claim in enumerate(claims):
            if claim.final_status == ClaimFinalStatus.TRUNCATED.value:
                continue
            auth_ids = getattr(claim, '_auth_ids_for_async', [])
            for ev_id in auth_ids:
                ev = self.ev_id_to_evidence.get(ev_id)
                if ev is None:
                    continue
                all_pairs.append(PairInput(
                    claim_text=claim.text or "",
                    evidence_content=ev.get("content", "") or "",
                    evidence_id=ev_id,
                ))
                pair_index_map.append((ci, ev_id))

        pair_results = await self.pair_verifier.verify_pairs(all_pairs)

        # ---- 6. 回填结果到各 Claim ----
        result_by_key: dict[tuple[int, str], PairVerdict] = {}
        for (ci, ev_id), result in zip(pair_index_map, pair_results):
            result_by_key[(ci, ev_id)] = result.to_pair_verdict()

        for ci, claim in enumerate(claims):
            if claim.final_status == ClaimFinalStatus.TRUNCATED.value:
                continue
            auth_ids = getattr(claim, '_auth_ids_for_async', [])
            for ev_id in auth_ids:
                verdict = result_by_key.get((ci, ev_id))
                if verdict is not None:
                    claim.add_pair_verdict(verdict)

            # 清理临时字段
            if hasattr(claim, '_auth_ids_for_async'):
                delattr(claim, '_auth_ids_for_async')

            # ---- 7. 反证扫描（P0-8）----
            counter_evidence = self._scan_counter_evidence(claim)
            for ev_id, verdict in counter_evidence.items():
                claim.add_pair_verdict(verdict)

            # ---- 8. 联合证据蕴含检查（P0-8）----
            if not claim.has_verified_support and len(auth_ids) > 1:
                joint_verdict = self._verify_joint_support(claim, auth_ids)
                if joint_verdict.pair_status == PairStatus.SUPPORTED.value:
                    claim.verified_support_ids.extend(auth_ids)
                    claim.pair_verdicts.append(joint_verdict)

            # ---- 9. 按 pair 聚合 verifier_status（P0-5）----
            claim.verifier_status = self._aggregate_verifier_status(claim.pair_verdicts)

        return claims

    def bind(self, claims: list[Claim], answer_text: str) -> list[Claim]:
        """完整绑定闭环（v2 修复 P0-4/P0-5/P0-8 + 任务二建议 3 预算）。

        流程（每个 Claim）：
            1. 预算检查：Claim 数量超限 → 标记剩余为 truncated，跳过验证。
            2. 从 answer_text 解析模型声明的引用编号（保留全部原始编号）。
            3. _parse_declared_citations：分类保存 raw/invalid/unauthorized/resolved。
            4. 预算检查：引用数量超限 → 截断 resolved，记录 truncated_citations。
            5. _verify_pair：逐个验证 Claim-Evidence pair（P0-5 独立裁决）。
            6. _scan_counter_evidence：反证扫描，只写入 contradicting_ids（P0-8）。
            7. _verify_joint_support：单 pair 不足时检查联合蕴含（P0-8）。
            8. _aggregate_verifier_status：按 pair 聚合 verifier_status（P0-5）。

        Args:
            claims: Claim 列表（就地修改）。
            answer_text: 模型原始答案文本（用于解析 [1][2] 引用编号）。

        Returns:
            传入的 claims 列表（已就地更新 pair_verdicts / verifier_status 等）。
        """
        max_claims = self.budget.max_claims if self.budget else None

        for i, claim in enumerate(claims):
            # ---- 1. Claim 数量预算（任务二建议 3）----
            # 超出 max_claims 的 Claim 不再验证，标记为 truncated 跳过下游矩阵
            if max_claims is not None and i >= max_claims:
                claim.final_status = ClaimFinalStatus.TRUNCATED.value
                claim.verifier_status = VerifierStatus.UNKNOWN.value
                logger.debug(
                    "[citation_binder] claim %s 超出 max_claims=%d 被截断",
                    claim.claim_id,
                    max_claims,
                )
                continue

            # ---- 2. 解析模型声明的引用编号（保留全部，含 [999]）----
            raw_citations = self._extract_citations_for_claim(claim, answer_text)

            # ---- 3. 分类保存（P0-4：禁止覆盖原始声明）----
            self._parse_declared_citations(claim, raw_citations)

            # ---- 4. 引用数量预算（任务二建议 3）----
            # resolved_declared_ids 超限时截断，被截断的原始编号写入 truncated_citations
            authorized_ids = self._truncate_citations_by_budget(claim)

            # ---- 5. 逐个验证 pair（P0-5：每个 pair 独立 PairVerdict）----
            # 重置 pair_verdicts 以保证幂等（重复调用 bind 不累积）
            claim.pair_verdicts = []
            claim.verified_support_ids = []
            claim.contradicting_ids = []
            for ev_id in authorized_ids:
                ev = self.ev_id_to_evidence.get(ev_id)
                if ev is None:
                    continue
                verdict = self._verify_pair_with_budget(claim, ev)
                # add_pair_verdict 会按 pair_status 自动归集到
                # verified_support_ids / contradicting_ids（评审硬约束 #2 分离保存）
                claim.add_pair_verdict(verdict)

            # ---- 6. 反证扫描（P0-8：只写入 contradicting_ids，不计入模型引用）----
            counter_evidence = self._scan_counter_evidence(claim)
            for ev_id, verdict in counter_evidence.items():
                # 反证 verdict 已带 is_counter_evidence=True；
                # add_pair_verdict 会将 contradicted 的 ID 归入 contradicting_ids
                claim.add_pair_verdict(verdict)

            # ---- 7. 联合证据蕴含检查（P0-8）----
            # 单 pair 不足但多 pair 联合支持时，verified_support_ids 含联合 ID
            if not claim.has_verified_support and len(authorized_ids) > 1:
                joint_verdict = self._verify_joint_support(claim, authorized_ids)
                if joint_verdict.pair_status == PairStatus.SUPPORTED.value:
                    # 联合支持：直接扩展 verified_support_ids（联合 ID 逗号串仅出现在
                    # pair_verdicts.evidence_id 中，不污染 verified_support_ids）
                    claim.verified_support_ids.extend(authorized_ids)
                    claim.pair_verdicts.append(joint_verdict)

            # ---- 8. 按 pair 聚合 verifier_status（P0-5：不无条件设为 ok）----
            claim.verifier_status = self._aggregate_verifier_status(claim.pair_verdicts)

        return claims

    # ============================== 引用解析 ==============================

    def _extract_citations_for_claim(
        self,
        claim: Claim,
        answer_text: str,
    ) -> list[int]:
        """从答案文本中提取该 Claim 区域内模型声明的引用编号。

        ★ P0-4：保留所有原始编号（含 [999] 等非法编号），本阶段不过滤，
        分类保存交由 _parse_declared_citations 完成。

        解析顺序：
            1. 优先用 claim.span 切片 answer_text（最贴近模型原始输出）；
            2. span 无效或切片内无引用编号时回退到 claim.text
               （AST 可能将引用保留在 text 中，或 span 未覆盖句尾引用编号）。

        Args:
            claim: 当前 Claim。
            answer_text: 模型原始答案文本。

        Returns:
            原始编号列表（保留顺序与重复，含非法编号）。
        """
        # 1. 优先用 span 切片
        if answer_text and claim.span and claim.span[1] > claim.span[0]:
            span_text = answer_text[claim.span[0] : claim.span[1]]
            cites = self._CITATION_PATTERN.findall(span_text)
            if cites:
                return [int(m) for m in cites]

        # 2. 回退到 claim.text（span 无效或未覆盖句尾引用编号时）
        return [int(m) for m in self._CITATION_PATTERN.findall(claim.text or "")]

    def _parse_declared_citations(
        self,
        claim: Claim,
        citations: list[int],
    ) -> None:
        """分类保存模型声明的引用编号（v2 评审 P0-4 修复）。

        ★ P0-4 核心修复：
            - raw_declared_indices 保留全部原始编号（含 [999] 等非法编号），
              禁止覆盖原始声明——评审硬约束要求“原始声明不可变”。
            - 分类保存：
                * invalid_indices：编号在 snapshot 中不存在（如 [999]）。
                * unauthorized_ids：编号存在但 tenant_id 越权。
                * resolved_declared_ids：合法且授权的 evidence_id（去重）。
            - 越权 ID 单独保存，不混入 resolved_declared_ids，避免下游误验证。

        幂等性：每次调用重置分类结果，确保重复调用 bind 不累积。

        Args:
            claim: 当前 Claim（就地更新字段）。
            citations: 模型声明的原始编号列表（含非法编号）。
        """
        # 保留原始声明（含非法编号），禁止覆盖
        claim.raw_declared_indices = list(citations)

        # 重置分类结果（幂等）
        claim.invalid_indices = []
        claim.unauthorized_ids = []
        claim.resolved_declared_ids = []

        for idx in citations:
            ev = self.index_to_evidence.get(idx)
            if ev is None:
                # 不存在的编号 → invalid_indices（如 [999]）
                if idx not in claim.invalid_indices:
                    claim.invalid_indices.append(idx)
                continue

            ev_id = ev.get("evidence_id", "")
            if not ev_id:
                # evidence_id 缺失视为非法编号
                if idx not in claim.invalid_indices:
                    claim.invalid_indices.append(idx)
                continue

            # 越权过滤：tenant_id 不匹配 → unauthorized_ids（不覆盖 resolved）
            if ev.get("tenant_id") != self.tenant_id:
                if ev_id not in claim.unauthorized_ids:
                    claim.unauthorized_ids.append(ev_id)
                continue

            # 合法且授权 → resolved_declared_ids（去重，保持声明顺序）
            if ev_id not in claim.resolved_declared_ids:
                claim.resolved_declared_ids.append(ev_id)

    def _truncate_citations_by_budget(self, claim: Claim) -> list[str]:
        """引用数量预算截断（任务二建议 3）。

        当 resolved_declared_ids 超过 max_citations_per_claim 时：
            - 保留前 N 个授权 ID（N = max_citations_per_claim）；
            - 被截断的原始编号反查写入 claim.truncated_citations（审计可见）；
            - 就地截断 claim.resolved_declared_ids。

        Args:
            claim: 当前 Claim。

        Returns:
            截断后的授权 evidence_id 列表（用于 pair 验证）。
        """
        claim.truncated_citations = []
        if self.budget is None:
            return list(claim.resolved_declared_ids)

        max_citations = self.budget.max_citations_per_claim
        resolved = claim.resolved_declared_ids
        if len(resolved) <= max_citations:
            return list(resolved)

        # 保留前 N 个，超出部分反查原始编号写入 truncated_citations
        keep_ids = resolved[:max_citations]
        drop_ids = resolved[max_citations:]
        for ev_id in drop_ids:
            idx = self.ev_id_to_index.get(ev_id)
            if idx is not None and idx not in claim.truncated_citations:
                claim.truncated_citations.append(idx)

        # 就地截断 resolved_declared_ids
        claim.resolved_declared_ids = list(keep_ids)
        logger.debug(
            "[citation_binder] claim %s 引用超限截断: 保留 %d，截断 %d",
            claim.claim_id,
            len(keep_ids),
            len(drop_ids),
        )
        return list(keep_ids)

    # ============================== pair 验证（P0-5）==============================

    def _verify_pair_with_budget(self, claim: Claim, evidence: dict) -> PairVerdict:
        """带预算检查的 pair 验证入口（任务二建议 3）。

        pair 预算耗尽时不再调用真实 verifier，直接产出 budget_exhausted 裁决，
        避免整轮验证雪崩。

        Args:
            claim: 当前 Claim。
            evidence: Evidence 字典。

        Returns:
            PairVerdict：正常验证或 budget_exhausted 裁决。
        """
        if self._pair_budget_exhausted:
            return PairVerdict(
                evidence_id=evidence.get("evidence_id", ""),
                pair_status=PairStatus.VERIFIER_ERROR.value,
                verifier_status=VerifierStatus.BUDGET_EXHAUSTED.value,
            )

        verdict = self._verify_pair(claim, evidence)

        # 消耗一个 pair 预算
        if self.budget is not None:
            self._pair_budget_used += 1
            if self._pair_budget_used >= self.budget.max_total_pairs:
                self._pair_budget_exhausted = True
                logger.debug(
                    "[citation_binder] pair 预算耗尽: used=%d/%d",
                    self._pair_budget_used,
                    self.budget.max_total_pairs,
                )

        return verdict

    def _verify_pair(self, claim: Claim, evidence: dict) -> PairVerdict:
        """验证单个 Claim-Evidence pair（v2 评审 P0-5 修复）。

        ★ P0-5 核心修复：
            - 每个 pair 拥有独立 PairVerdict，异常不覆盖其它 pair 的结果。
            - 返回 PairVerdict(evidence_id, pair_status, verifier_status)。
            - verifier_status=ok 表示验证器本身正常（不代表结论一定 supported）。

        简化实现：用字符/实体重叠度 + 否定词冲突判断 pair_status；
        生产环境应替换为 VerifierGateway（rule + nli + llm 三层），
        接口签名保持兼容（返回 PairVerdict 即可）。

        Args:
            claim: 当前 Claim。
            evidence: Evidence 字典。

        Returns:
            PairVerdict：supported / contradicted / insufficient / verifier_error。
        """
        evidence_id = evidence.get("evidence_id", "")
        ev_content = evidence.get("content", "") or ""
        try:
            # 零行/空内容证据无法支持任何 Claim（如 DB ZERO_ROWS）
            if not ev_content:
                return PairVerdict(
                    evidence_id=evidence_id,
                    pair_status=PairStatus.INSUFFICIENT.value,
                    verifier_status=VerifierStatus.OK.value,
                    rule_result=PairStatus.INSUFFICIENT.value,
                )

            pair_status = self._judge_overlap(claim.text or "", ev_content)
            return PairVerdict(
                evidence_id=evidence_id,
                pair_status=pair_status,
                verifier_status=VerifierStatus.OK.value,
                rule_result=pair_status,
            )
        except Exception as e:  # noqa: BLE001 - 简化 verifier 兜底
            # 异常隔离：单个 pair 失败不影响其它 pair（P0-5）
            logger.warning(
                "[citation_binder] pair 验证异常 claim=%s ev=%s: %s",
                claim.claim_id,
                evidence_id,
                e,
            )
            return PairVerdict(
                evidence_id=evidence_id,
                pair_status=PairStatus.VERIFIER_ERROR.value,
                verifier_status=VerifierStatus.ERROR.value,
            )

    def _aggregate_verifier_status(self, verdicts: list[PairVerdict]) -> str:
        """按 pair 聚合 verifier_status（v2 评审 P0-5 修复）。

        聚合规则（与 Claim.aggregate_verifier_status 对齐）：
            - 无 pair → unknown
            - 所有 pair 均 ok → ok
            - 任意 pair error → error（优先级高于 timeout）
            - 任意 pair timeout → timeout
            - 任意 pair budget_exhausted → budget_exhausted
            - 其余混合状态 → mixed

        ★ P0-5：不再无条件设为 ok，避免验证器异常被静默吞掉。

        Args:
            verdicts: pair 裁决列表。

        Returns:
            聚合后的 verifier_status 字符串。
        """
        if not verdicts:
            return VerifierStatus.UNKNOWN.value

        statuses = {v.verifier_status for v in verdicts}
        if statuses == {VerifierStatus.OK.value}:
            return VerifierStatus.OK.value
        if VerifierStatus.ERROR.value in statuses:
            return VerifierStatus.ERROR.value
        if VerifierStatus.TIMEOUT.value in statuses:
            return VerifierStatus.TIMEOUT.value
        if VerifierStatus.BUDGET_EXHAUSTED.value in statuses:
            return VerifierStatus.BUDGET_EXHAUSTED.value
        return VerifierStatus.MIXED.value

    # ============================== 反证扫描（P0-8）==============================

    def _scan_counter_evidence(self, claim: Claim) -> dict[str, PairVerdict]:
        """反证扫描：在整个 snapshot 中检索可能反证（v2 评审 P0-8 修复）。

        ★ P0-8 核心修复（拆出独立链路）：
            - 只扫描未声明的 Evidence（声明的已在 pair verification 中验证），
              避免重复验证。
            - 只写入 contradicting_ids，不计入模型引用（resolved_declared_ids），
              防止模型引用一个支持来源同时忽略更权威的冲突来源。
            - 快速预筛：_has_entity_overlap（实体匹配），避免对全量 Evidence
              调用昂贵 verifier。
            - pair 预算耗尽时停止扫描（反证扫描属 best-effort，不追加
              budget_exhausted 裁决，避免 pair_verdicts 膨胀）。

        Args:
            claim: 当前 Claim。

        Returns:
            {evidence_id: PairVerdict}：仅包含判定为 contradicted 的反证。
            每个 PairVerdict.is_counter_evidence=True。
        """
        # 已声明的 Evidence 跳过（含越权 ID，避免重复扫描）
        declared_set = set(claim.resolved_declared_ids) | set(claim.unauthorized_ids)
        counter: dict[str, PairVerdict] = {}

        for ev_id, ev in self.ev_id_to_evidence.items():
            if ev_id in declared_set:
                continue  # 跳过已声明的
            if ev.get("tenant_id") != self.tenant_id:
                continue  # 跳过越权的（反证也必须授权）

            # 快速预筛：实体匹配，无重叠则不可能为反证
            if not self._has_entity_overlap(claim.text or "", ev):
                continue

            # pair 预算耗尽 → 停止反证扫描（best-effort）
            if self._pair_budget_exhausted:
                break

            # 深度验证（复用 _verify_pair_with_budget 以计入 pair 预算）
            verdict = self._verify_pair_with_budget(claim, ev)
            if verdict.pair_status == PairStatus.CONTRADICTED.value:
                # 标记为反证扫描发现（非模型声明），便于审计区分
                verdict.is_counter_evidence = True
                counter[ev_id] = verdict

        return counter

    def _verify_joint_support(
        self,
        claim: Claim,
        evidence_ids: list[str],
    ) -> PairVerdict:
        """联合证据蕴含检查（v2 评审 P0-8 修复）。

        支持多个 Evidence 联合蕴含一个 Claim 的场景：
            例如 Evidence A 提供“公司 X”，Evidence B 提供“营收 Y”，
            联合支持“公司 X 营收 Y”。单 pair 不足但多 pair 联合支持时，
            verified_support_ids 含联合 ID。

        简化实现：将多条 Evidence 内容拼接后用 _judge_overlap 判断；
        生产环境应调用 LLM verifier 判断联合蕴含（_llm_verify_joint）。

        Args:
            claim: 当前 Claim。
            evidence_ids: 参与联合蕴含的 evidence_id 列表。

        Returns:
            PairVerdict：evidence_id 为逗号拼接的联合 ID；
            pair_status=supported 时由调用方扩展 verified_support_ids。
        """
        joint_id = ",".join(evidence_ids)
        evidences = [self.ev_id_to_evidence[eid] for eid in evidence_ids if eid in self.ev_id_to_evidence]
        try:
            joint_content = "\n".join(ev.get("content", "") for ev in evidences if ev.get("content"))
            if not joint_content:
                return PairVerdict(
                    evidence_id=joint_id,
                    pair_status=PairStatus.INSUFFICIENT.value,
                    verifier_status=VerifierStatus.OK.value,
                    llm_result=PairStatus.INSUFFICIENT.value,
                )
            pair_status = self._judge_overlap(claim.text or "", joint_content)
            return PairVerdict(
                evidence_id=joint_id,
                pair_status=pair_status,
                verifier_status=VerifierStatus.OK.value,
                llm_result=pair_status,
            )
        except Exception as e:  # noqa: BLE001 - 联合验证兜底
            logger.warning(
                "[citation_binder] 联合验证异常 claim=%s: %s",
                claim.claim_id,
                e,
            )
            return PairVerdict(
                evidence_id=joint_id,
                pair_status=PairStatus.VERIFIER_ERROR.value,
                verifier_status=VerifierStatus.ERROR.value,
            )

    # ============================== 实体预筛工具 ==============================

    def _has_entity_overlap(self, claim_text: str, evidence: dict) -> bool:
        """快速预筛：Claim 与 Evidence 是否有实体重叠（P0-8 反证扫描前置）。

        提取数字 / 日期 / 专有名词等高区分度实体，任一重叠即通过预筛。
        避免对全量 Evidence 调用昂贵 verifier。

        Args:
            claim_text: Claim 文本。
            evidence: Evidence 字典。

        Returns:
            bool：存在实体重叠为 True。
        """
        ev_content = evidence.get("content", "") or ""
        claim_ents = self._extract_entities(claim_text)
        if not claim_ents:
            return False
        ev_ents = self._extract_entities(ev_content)
        return bool(claim_ents & ev_ents)

    def _extract_entities(self, text: str) -> set[str]:
        """提取高区分度实体（数字 / 日期 / 中文词段 / 英文专有名词）。

        用于 _has_entity_overlap 与 _judge_overlap 的重叠度计算。
        刻意只取高区分度实体，避免常见停用词污染重叠率。

        Args:
            text: 待提取文本。

        Returns:
            实体集合。
        """
        if not text:
            return set()
        ents: set[str] = set()
        # 数字（含小数与百分比，如 15.3 / 15.3% / 15.3％）
        ents.update(re.findall(r"\d+(?:\.\d+)?[%％]?", text))
        # 日期（2024年 / 3月 / Q3 / 2024-03）
        ents.update(re.findall(r"\d{4}年|\d{1,2}月|Q[1-4]|20\d{2}-\d{1,2}", text))
        # 中文连续字符段（长度>=2，如“营收”“合规”）
        ents.update(re.findall(r"[\u4e00-\u9fa5]{2,}", text))
        # 英文专有名词（首字母大写，长度>=3，如 OpenAI）
        ents.update(re.findall(r"[A-Z][a-zA-Z]{2,}", text))
        return ents

    def _judge_overlap(self, claim_text: str, evidence_content: str) -> str:
        """基于实体重叠度 + 否定词冲突判断 pair_status（简化 verifier）。

        判定规则：
            - Claim 无可比较实体 → insufficient（无法判定）。
            - 实体重叠率 >= 0.5 → supported（证据覆盖大部分关键实体）。
            - 否定词冲突（一方否定一方肯定）且实体重叠率 >= 0.3 → contradicted。
            - 其余 → insufficient。

        Args:
            claim_text: Claim 文本。
            evidence_content: Evidence 内容文本。

        Returns:
            PairStatus 取值：supported / contradicted / insufficient。
        """
        claim_ents = self._extract_entities(claim_text)
        if not claim_ents:
            return PairStatus.INSUFFICIENT.value

        ev_ents = self._extract_entities(evidence_content)
        overlap = len(claim_ents & ev_ents)
        ratio = overlap / len(claim_ents)

        # 否定词冲突检测：一方含否定词另一方不含，且实体高度重叠 → 矛盾
        claim_neg = any(w in claim_text for w in self._NEGATION_WORDS)
        ev_neg = any(w in evidence_content for w in self._NEGATION_WORDS)
        if claim_neg != ev_neg and ratio >= 0.3:
            return PairStatus.CONTRADICTED.value

        if ratio >= 0.5:
            return PairStatus.SUPPORTED.value
        return PairStatus.INSUFFICIENT.value
