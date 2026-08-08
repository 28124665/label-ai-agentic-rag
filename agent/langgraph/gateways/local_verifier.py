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
"""本地验证器网关（单体阶段，RAG 增强能力融合 §3.3）。

LocalVerifierGateway 通过 import 调用 RAGFlow 组件库（HallucinationDetector /
Grader），将其能力封装在 VerifierGateway 接口下。微服务拆分时，替换为
HttpVerifierGateway，上层 LangGraph 代码零改动。

实现要点：
  1. 延迟导入：所有 RAGFlow 组件 import 放在方法内部，避免循环依赖和启动开销。
  2. Canvas 桩：HallucinationDetector / Grader 继承 ComponentBase，构造时断言
     canvas 是 Graph 实例。本网关创建最小化 Canvas 桩（_TenantCanvasStub），
     继承 Graph 以通过类型检查，但跳过 DSL 解析，仅提供组件直接调用所需的方法。
  3. 接口适配：组件的实际方法签名与设计文档伪代码有差异，以实际代码为准：
     - _verify_claims 是同步方法，接收 retrieved_docs: list[dict]（非 context 字符串）
     - _dispose 签名为 (answer, query, claims, score, retrieved_docs)
     - Grader 通过 _evaluate_with_llm / _evaluate_with_local_nli 进行评估
  4. 线程隔离：_verify_claims 内部通过 asyncio 事件循环调用 LLM。为避免在
     async 上下文中阻塞主事件循环，通过 asyncio.to_thread 在独立线程中执行，
     并为新线程创建独立事件循环。
  5. 异常传播：组件调用失败时抛出异常，由调用方（hallucination 节点等）决定
     降级策略（如回退为字符重叠度评估）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agent.langgraph.gateways.verifier import VerifierGateway

logger = logging.getLogger(__name__)

# 模块级缓存：延迟创建 Canvas 桩类（避免模块导入时触发 agent.canvas 重依赖）
_CanvasStubCls: type | None = None


def _make_canvas_stub(tenant_id: str):
    """创建最小化 Canvas 桩实例（延迟导入 agent.canvas.Graph）。

    HallucinationDetector / Grader 继承 ComponentBase，构造时断言 canvas 是
    Graph 实例（assert isinstance(canvas, Graph)）。本函数延迟导入 Graph 并
    创建子类桩，跳过 Graph.__init__ 的 DSL 解析，仅提供组件直接调用所需的最小
    方法集。

    Graph 子类化是必须的——ComponentBase.__init__ 中的 isinstance 断言无法绕过。
    桩对象不参与 Canvas 工作流编排，仅满足组件方法调用的最小依赖。
    """
    global _CanvasStubCls
    if _CanvasStubCls is None:
        from agent.canvas import Graph

        class _TenantCanvasStub(Graph):
            """为 RAGFlow 组件提供最小化的 Canvas 运行时环境（防腐层内部使用）。

            跳过 Graph.__init__ 的 DSL 解析，仅提供组件直接调用所需的方法：
            - get_tenant_id()：LLM 配置解析需要
            - is_canceled()：组件 _invoke 检查取消状态（桩恒返回 False）
            - get_variable_value()：组件 _resolve_input 解析变量（桩返回 None）
            - is_reff()：组件 _resolve_input 判断变量引用（桩返回 False）
            - get_component() / get_component_name()：组件可能调用（桩返回空）
            """

            def __init__(self, tenant_id: str):
                # 跳过 Graph.__init__ 的 DSL 解析（不需要工作流编排）
                self._tenant_id = tenant_id
                self.task_id = ""
                self.components = {}
                self.globals = {}
                self.variables = {}

            def get_tenant_id(self):
                return self._tenant_id

            def get_variable_value(self, exp: str) -> Any:
                return self.globals.get(exp)

            def is_reff(self, exp: str) -> bool:
                return False

            def is_canceled(self) -> bool:
                return False

            def get_component(self, cpn_id):
                return None

            def get_component_name(self, cid):
                return ""

            def _extract_kb_id(self):
                return "unknown"

        _CanvasStubCls = _TenantCanvasStub

    return _CanvasStubCls(tenant_id)


def _run_verify_in_thread(detector, claims, retrieved_docs) -> None:
    """在独立线程中同步执行论断验证（含 LLM 调用）。

    HallucinationDetector._verify_claims 是同步方法，但内部通过
    asyncio.get_event_loop() 获取事件循环来执行 LLM 协程。
    在 async 上下文中直接调用会导致事件循环冲突，因此通过 asyncio.to_thread
    在独立线程中执行，并为新线程创建独立事件循环。

    线程内事件循环生命周期：
    1. 创建新事件循环并设为当前线程的循环
    2. 执行 _verify_claims（内部通过 loop.run_until_complete 调用 LLM）
    3. 关闭循环并清理（避免资源泄漏）
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        detector._verify_claims(claims, retrieved_docs)
    finally:
        loop.close()
        asyncio.set_event_loop(None)


class LocalVerifierGateway(VerifierGateway):
    """本地验证器网关（单体阶段）。

    通过 import 调用 RAGFlow 组件库，将 HallucinationDetector（幻觉检测）
    和 Grader（检索质量评估）的能力封装在 VerifierGateway 接口下。

    单体阶段使用本实现（进程内调用，零网络开销）；
    微服务拆分时替换为 HttpVerifierGateway（HTTP 调用），上层代码不变。
    """

    @staticmethod
    def _resolve_tenant_llm_id(tenant_id: str) -> str:
        """解析租户默认聊天模型 ID。

        HallucinationDetector 的 LLM/NLI 验证层和 Grader 的 LLM 评估都需要
        租户的聊天模型配置。本方法通过 get_tenant_default_model_by_type 获取
        租户配置的默认 CHAT 模型。

        Args:
            tenant_id: 租户 ID

        Returns:
            模型 ID 字符串。解析失败时返回空字符串：
            - HallucinationDetector 可降级为规则层验证（空 llm_id 时跳过 LLM）
            - Grader 必须有 llm_id，调用方需检查并决定是否抛出异常
        """
        if not tenant_id:
            return ""
        try:
            from api.db.joint_services.tenant_model_service import (
                get_tenant_default_model_by_type,
            )
            from common.constants import LLMType

            return get_tenant_default_model_by_type(tenant_id, LLMType.CHAT) or ""
        except Exception as e:
            logger.warning(
                "[LocalVerifierGateway] 解析租户 %s 默认 LLM 失败: %s",
                tenant_id,
                e,
            )
            return ""

    async def verify_faithfulness(
        self, answer: str, context: str, query: str, tenant_id: str
    ) -> dict[str, Any]:
        """验证答案忠实度（幻觉检测）。

        调用 HallucinationDetector 三层验证流程：
        1. _decompose_claims：将答案拆解为独立论断
        2. _verify_claims：对每条论断执行规则 + NLI + LLM 三层验证
        3. _aggregate_scores：加权投票计算综合忠实度分数
        4. _dispose：根据分数执行分级处置（pass/filter/regenerate/refuse）

        组件接口适配说明（与设计文档伪代码的差异）：
        - _verify_claims 接收 retrieved_docs: list[dict]（非 context 字符串），
          需将 context 转换为 [{"content": context}] 格式
        - _verify_claims 是同步方法但内部调用 LLM 协程，通过 asyncio.to_thread
          在独立线程中执行以避免阻塞主事件循环
        - _dispose 签名为 (answer, query, claims, score, retrieved_docs)，
          返回 (action, final_answer, regenerate_context, regenerate_prompt) 四元组

        Args:
            answer: 待验证的生成答案文本
            context: 检索到的参考上下文（已拼接的文档内容）
            query: 用户原始查询
            tenant_id: 租户 ID

        Returns:
            标准化结果字典：
            - faithfulness_score: 忠实度分数 (0.0 ~ 1.0)
            - action: 处置建议 (pass/filter/regenerate/refuse)
            - claims: 论断列表

        Raises:
            Exception: 组件构造或调用失败时抛出。
        """
        # 延迟导入：避免模块级触发 agent.component 重依赖
        from agent.component.hallucination_detector import (
            HallucinationDetector,
            HallucinationDetectorParam,
        )

        # 解析租户默认聊天模型（用于 LLM/NLI 验证层）
        # 解析失败时返回空字符串，HallucinationDetector 会降级为规则层验证
        llm_id = self._resolve_tenant_llm_id(tenant_id)

        # 构建 Canvas 桩（满足 ComponentBase 的 isinstance(canvas, Graph) 断言）
        canvas = _make_canvas_stub(tenant_id)

        # 构造组件参数
        param = HallucinationDetectorParam()
        param.llm_id = llm_id
        # 保持默认的三层验证配置（规则 0.4 + NLI 0.4 + LLM 0.2）
        param.check()

        # 构造 HallucinationDetector 实例
        detector = HallucinationDetector(canvas, "local_verifier_faithfulness", param)

        # context 字符串 → retrieved_docs 列表
        # _verify_claims 接收 list[dict]，内部 _build_context 提取 doc["content"]
        retrieved_docs = [{"content": context}] if context else []

        # Step 1: 论断拆解（同步，仅正则处理，无 LLM 调用）
        claims = detector._decompose_claims(answer)
        if not claims:
            # 无可验证论断，视为完全忠实
            return {
                "faithfulness_score": 1.0,
                "action": "pass",
                "claims": [],
            }

        # Step 2: 多层验证（同步方法但内部调用 LLM 协程）
        # 在独立线程中执行，避免阻塞主事件循环
        await asyncio.to_thread(
            _run_verify_in_thread, detector, claims, retrieved_docs
        )

        # Step 3: 加权投票计算综合分数
        faithfulness_score, _hallucination_count = detector._aggregate_scores(claims)

        # Step 4: 分级处置
        action, _final_answer, _regenerate_context, _regenerate_prompt = (
            detector._dispose(
                answer, query, claims, faithfulness_score, retrieved_docs
            )
        )

        logger.info(
            "[LocalVerifierGateway] 幻觉检测完成: score=%.4f, action=%s, claims=%d",
            faithfulness_score,
            action,
            len(claims),
        )

        return {
            "faithfulness_score": round(faithfulness_score, 4),
            "action": action,
            "claims": [c.to_dict() for c in claims],
        }

    async def verify_faithfulness_claims(
        self,
        claims: list,
        answer_text: str,
        snapshot: Any,
        tenant_id: str,
    ) -> list:
        """使用 PairVerifier 进行 Claim 级三层验证（v3.0 NLI+LLM 分层融合）。

        构建 CitationBinder + PairVerifier，调用 bind_async 对每个 Claim 的
        Claim-Evidence pair 执行 Rule + NLI + LLM 三层 batch 验证。

        Args:
            claims: Claim 列表（就地修改）。
            answer_text: 模型原始答案文本（用于解析 [1][2] 引用编号）。
            snapshot: EvidenceSnapshot 实例（不可变快照）。
            tenant_id: 租户 ID。

        Returns:
            传入的 claims 列表（已就地更新 pair_verdicts / verifier_status 等）。
        """
        from agent.langgraph.config import VerificationBudget
        from agent.langgraph.evidence.citation_binder import CitationBinder
        from agent.langgraph.evidence.pair_verifier import (
            PairVerifier,
            PairVerifierConfig,
        )

        # 解析租户默认聊天模型
        llm_id = self._resolve_tenant_llm_id(tenant_id)
        if not llm_id:
            logger.warning(
                "[LocalVerifierGateway] 租户 %s 无默认 LLM，PairVerifier 降级为纯 Rule 层",
                tenant_id,
            )
            budget = VerificationBudget()
            binder = CitationBinder(snapshot, tenant_id, budget)
            return binder.bind(claims, answer_text)

        # 构建 PairVerifier
        config = PairVerifierConfig(batch_size=5)
        pair_verifier = PairVerifier(
            llm_id=llm_id,
            tenant_id=tenant_id,
            config=config,
        )
        budget = VerificationBudget()
        pair_verifier.set_max_llm_calls(budget.max_llm_calls)

        # 构建 CitationBinder 并注入 PairVerifier
        binder = CitationBinder(snapshot, tenant_id, budget, pair_verifier)

        # 执行异步绑定闭环
        result = await binder.bind_async(claims, answer_text)

        logger.info(
            "[LocalVerifierGateway] Claim 级验证完成: claims=%d, llm_calls=%d",
            len(claims),
            pair_verifier.llm_calls_used,
        )

        return result

    async def grade_retrieval(
        self, query: str, chunks: list[dict], tenant_id: str
    ) -> dict[str, Any]:
        """评估检索结果质量。

        调用 Grader 组件对检索文档进行深层语义评估：
        1. _normalize_docs：标准化文档格式（添加 index 字段）
        2. _evaluate_with_llm / _evaluate_with_local_nli：LLM 批量评估
        3. 聚合结果计算 quality_score / has_relevant / relevant_count

        组件接口适配说明：
        - Grader 的 evaluator_model 仅支持 "llm" / "local_nli"（已移除 cross_encoder）
        - GraderParam.check() 要求 llm_id 非空，必须先解析租户默认聊天模型
        - 不调用 _invoke_async（避免 @timeout 装饰器和 Canvas 交互），
          直接调用 _evaluate_with_llm 进行评估

        Args:
            query: 用户查询文本
            chunks: 检索到的文档块列表
            tenant_id: 租户 ID

        Returns:
            标准化结果字典：
            - quality_score: 检索质量分数 (0.0 ~ 1.0)
            - has_relevant: 是否存在相关文档
            - relevant_count: 相关文档数量
            - graded_docs: 评估后的文档列表

        Raises:
            Exception: 组件构造或调用失败时抛出。
        """
        # 延迟导入
        from agent.component.grader import RELEVANT, Grader, GraderParam

        # 解析租户默认聊天模型（Grader 强制要求 llm_id）
        llm_id = self._resolve_tenant_llm_id(tenant_id)
        if not llm_id:
            raise RuntimeError(
                f"[LocalVerifierGateway] 租户 {tenant_id} 未配置默认聊天模型，"
                f"Grader 评估无法执行"
            )

        # 构建 Canvas 桩
        canvas = _make_canvas_stub(tenant_id)

        # 构造组件参数
        param = GraderParam()
        param.llm_id = llm_id
        param.evaluator_model = "llm"  # 使用 LLM-as-Judge 模式
        param.fallback_on_failure = True  # 评估失败时降级为 Rerank 分数
        param.check()

        # 构造 Grader 实例
        grader = Grader(canvas, "local_verifier_grader", param)

        # 标准化文档格式
        docs = grader._normalize_docs(chunks)
        if not docs:
            return {
                "quality_score": 0.0,
                "has_relevant": False,
                "relevant_count": 0,
                "graded_docs": [],
            }

        # 执行 LLM 评估
        graded_docs = await grader._evaluate_with_llm(query, docs)

        # 聚合结果
        scores = [
            d.get("score", 0.0) for d in graded_docs if isinstance(d, dict)
        ]
        quality_score = sum(scores) / len(scores) if scores else 0.0
        has_relevant = any(
            d.get("relevance") == RELEVANT for d in graded_docs
        )
        relevant_count = sum(
            1 for d in graded_docs if d.get("relevance") == RELEVANT
        )

        logger.info(
            "[LocalVerifierGateway] 检索质量评估完成: score=%.4f, "
            "relevant=%d/%d",
            quality_score,
            relevant_count,
            len(graded_docs),
        )

        return {
            "quality_score": round(quality_score, 4),
            "has_relevant": has_relevant,
            "relevant_count": relevant_count,
            "graded_docs": graded_docs,
        }
