#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
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
"""RAG Tool 封装模块 - LangGraph 版本。

将现有的 RAG 检索流程封装为独立的工具类，供 LangGraph 调用。
内部流程：查询预处理 → 查询重写 → 混合检索 → Rerank → 质量评估（单次执行）

方案 A（详见设计文档 §4.5）：RAGTool 退化为单次执行，重试决策和 Token 预算
管控完全交给主流程 quality_check 节点，避免与主流程外层重试构成双重重试。

参考原有实现：
- agent/tools/retrieval.py: Retrieval 组件（混合检索 + Rerank）
- agent/component/query_rewriter.py: QueryRewriter 组件（查询重写与复杂度分析）
- agent/component/grader.py: Grader 组件（检索结果质量评估）
- agent/component/retry_controller.py: RetryController 组件（重试控制，已上提到主流程）
- api/utils/multilingual_reranker.py: MultilingualReranker（多语言 Rerank）
- api/utils/query_preprocessor.py: preprocess_query（查询预处理）
"""

import json
import logging
import time
from typing import Any, Literal, TypedDict

from api.utils.multilingual_reranker import rerank as multilingual_rerank
from api.utils.query_preprocessor import preprocess_query
from agent.langgraph.config import is_feature_enabled
from agent.langgraph.gateways.errors import (
    RetrievalAuthError,
    RetrievalDataError,
    RetrievalServiceError,
)
from agent.langgraph.gateways.factory import GatewayResolver, get_gateway_resolver
from agent.langgraph.utils.lang_utils import to_langgraph_lang

logger = logging.getLogger(__name__)


class RAGToolInput(TypedDict, total=False):
    """RAG Tool 输入定义。"""

    query: str  # 用户查询（原始查询）
    query_lang: Literal["zh_CN", "zh_TW", "en"]  # 查询语言
    top_k: int  # 返回文档数量，默认 5
    enable_rewrite: bool  # 是否启用查询重写，默认 True
    enable_rerank: bool  # 是否启用 Rerank，默认 True
    kb_ids: list[str]  # 知识库 ID 列表
    tenant_id: str  # 租户 ID
    similarity_threshold: float  # 相似度阈值，默认 0.2
    keywords_similarity_weight: float  # 关键词相似度权重，默认 0.5
    rerank_id: str  # Rerank 模型 ID
    cross_languages: list[str]  # 跨语言扩展目标语言列表，空列表表示不扩展
    llm_id: str  # 用于跨语言扩展的 LLM 模型 ID
    document_filters: dict[str, Any]  # Skill 声明的文档过滤条件
    metadata_filters: dict[str, Any]  # Skill 声明的元数据过滤条件
    # ★ 方案 A 新增（Phase 2 Task 6 契约变更，详见设计文档 §4.5.4 / §4.4）
    # 用途：主流程透传的外层重试次数，作为查询重写策略轮转的起点
    #       策略轮转公式：strategies[(retry_count + attempt) % len(strategies)]
    # 默认值：0（未透传时策略轮转从 synonym_rewrite 起点，行为与改造前一致）
    # 写入方：主流程 rag_tool_node 节点（从 state["retry_count"] 读取后透传）
    # 读取方：RAGTool._rewrite_query()（Task 8 实现策略轮转协同）
    retry_count: int


class RAGToolOutput(TypedDict, total=False):
    """RAG Tool 输出定义。"""

    docs: list[dict]  # 检索到的文档列表 [{content, score, source, chunk_id}]
    quality_score: float  # 质量评分 0.0 ~ 1.0
    has_relevant: bool  # 是否有相关文档
    relevant_count: int  # 相关文档数量
    top_score: float  # 最高分
    query_simplified: str  # 繁简转换后的查询
    detected_lang: str  # 检测到的语言（LangGraph 标识：zh_CN/zh_TW/en）
    retrieval_time_ms: int  # 检索耗时（毫秒）
    # 微服务拆分 §5.8 降级状态契约（D4 显式增列）
    retrieval_error_code: str  # RETRIEVAL_SERVICE_ERROR / RETRIEVAL_TIMEOUT /
    #                              RETRIEVAL_CIRCUIT_OPEN / RETRIEVAL_AUTH / 空串
    retrieval_mode_used: str  # local / remote / shadow（观测用）
    # ★ 方案 A 新增（Phase 2 Task 6 契约变更，详见设计文档 §4.5.4 / §4.2）
    # 用途：分数来源标注（base / grader_merged），供主流程 quality_check 按来源选择阈值
    #       详见设计文档 §4.2.4：base→0.7，grader_merged→0.65（Grader 增强后分布偏低）
    # 默认值："base"（基础 Rerank 分数评估，未启用 Grader 增强或非边界值场景）
    # 写入方：RAGTool._evaluate_quality()（Task 7 实现分数来源标注）
    # 读取方：主流程 quality_check 节点（Task 7/9 实现按来源选阈值）
    score_source: str
    # ★ 方案 A 新增（Phase 2 Task 6 契约变更，详见设计文档 §4.5.4）
    # 用途：实际使用的查询重写策略（观测用，替代原 rewrite_history 列表）
    #       取值：synonym_rewrite / sub_query_decompose / hyde / "none"（未启用重写）
    # 默认值："none"（未启用 enable_rewrite 时）
    # 写入方：RAGTool.invoke()（Task 9 实现，由 _rewrite_query 返回的 strategy_used 透传）
    # 读取方：主流程 rag_tool_node 节点（日志/观测用）
    rewrite_strategy: str


class RAGTool:
    """RAG 知识库检索工具 - LangGraph 版本。

    将现有的 RAG 检索流程封装为独立工具，不依赖 Canvas。
    内部包含完整的检索优化流程：预处理、重写、检索、Rerank、评估（单次执行）。

    设计说明：
      - 查询重写：复用 agent.component.query_rewriter 的 analyze_query_complexity
        和 _build_synonym_expansion 函数，不依赖 Canvas
      - Rerank：复用 api.utils.multilingual_reranker.rerank 函数
      - 质量评估：参考 agent.component.grader 的阈值策略，简化为独立函数，
        输出 score_source 分数来源标注（base / grader_merged）
      - 检索网关：通过 GatewayResolver 按租户运行时配置路由到 local/remote/shadow
        实现（微服务拆分 §5.1/§5.7）；PR-0.2 仅 local 模式，零行为变化。

    方案 A（详见设计文档 §4.5）：
      - RAGTool 单次执行，移除内层重试循环（原 MAX_INTERNAL_RETRIES 已删除）
      - 重试决策完全交给主流程 quality_check 节点（max_retries=3）
      - Token 预算管控完全交给主流程 quality_check 节点
      - 接收主流程透传的 retry_count 作为查询重写策略轮转起点
    """

    # 质量评估阈值（参考 Grader 组件的 relevance_threshold）
    # 保留用于 _evaluate_quality 打分，不参与重试决策（重试决策由主流程 quality_check 承担）
    RELEVANT_THRESHOLD = 0.5
    QUALITY_PASS_THRESHOLD = 0.7
    MIN_RELEVANT_DOCS = 2

    def __init__(self, resolver: "GatewayResolver | None" = None):
        """初始化 RAGTool。

        Args:
            resolver: 网关解析器（§5.7）；None 时取进程级单例。
                      PR-0.2：单例已注入 LocalRetrieverGateway，行为与改造前一致。
        """
        self.component_name = "RAGTool"
        self._resolver = resolver

    async def invoke(self, input_data: RAGToolInput) -> RAGToolOutput:
        """执行 RAG 检索流程（单次执行，无内层重试）。

        方案 A（详见设计文档 §4.5）：重试决策由主流程 quality_check 节点统一管控，
        本方法只负责一次完整的：预处理→重写→检索→Rerank→打分。

        Args:
            input_data: RAG Tool 输入参数。
                新增字段 retry_count（由主流程透传）用于查询重写策略轮转起点。

        Returns:
            RAGToolOutput: 检索结果，包含 quality_score 和 score_source。
        """
        start_time = time.time()

        # 提取参数（带默认值）
        query = input_data.get("query", "")
        top_k = input_data.get("top_k", 5)
        enable_rewrite = input_data.get("enable_rewrite", True)
        enable_rerank = input_data.get("enable_rerank", True)
        kb_ids = input_data.get("kb_ids", [])
        tenant_id = input_data.get("tenant_id", "")
        similarity_threshold = input_data.get("similarity_threshold", 0.2)
        keywords_similarity_weight = input_data.get("keywords_similarity_weight", 0.5)
        rerank_id = input_data.get("rerank_id", "")
        cross_languages_param = input_data.get("cross_languages", [])
        llm_id = input_data.get("llm_id", "")
        # ★ 主流程透传的 retry_count（用于查询重写策略轮转起点，详见设计文档 §4.4）
        retry_count = input_data.get("retry_count", 0)

        # 获取网关解析器与检索模式（§5.7 按请求解析，不在构造期绑定）
        resolver = self._resolver or get_gateway_resolver()
        retrieval_mode = await resolver._resolve_retrieval_mode(tenant_id)

        if not query or not kb_ids:
            return self._empty_result(start_time, retrieval_mode=retrieval_mode)

        # 1. 跨语言扩展（参考历史 retrieval.py 的 cross_languages 调用）
        # local 模式：本地执行（§5.0）；remote 模式：服务端执行，跳过本地（PR-1.3 实现）
        expanded_query = query
        if retrieval_mode == "local" and cross_languages_param and tenant_id and llm_id:
            try:
                from rag.prompts.generator import cross_languages

                expanded_query = await cross_languages(tenant_id, llm_id, query, cross_languages_param)
                logger.info(f"[RAGTool] 跨语言扩展完成: 原始='{query}', 扩展后='{expanded_query}'")
            except Exception as e:
                logger.warning(f"[RAGTool] 跨语言扩展失败，使用原查询: {e}")

        # 2. 查询预处理（语言检测 + 繁简转换）
        preprocessed = preprocess_query(expanded_query)
        query_simplified = preprocessed["query_simplified"]
        detected_lang_historical = preprocessed["query_lang"]
        detected_lang = to_langgraph_lang(detected_lang_historical)

        logger.info(f"[RAGTool] 查询预处理完成: 原始='{query}', 扩展='{expanded_query}', 简化='{query_simplified}', 语言={detected_lang} ({detected_lang_historical})")

        # 获取检索网关（§5.7 按请求解析，模式与凭证按租户路由）
        try:
            gateway = await resolver.retriever_for(tenant_id)
        except NotImplementedError:
            # PR-0.2 过渡期：resolver 未注入 LocalRetrieverGateway 时直接回退
            logger.warning("[RAGTool] 网关未注入，回退到 LocalRetrieverGateway")
            from agent.langgraph.gateways.local_retriever import LocalRetrieverGateway

            gateway = LocalRetrieverGateway()

        # 3. 查询重写（单次，retry_count 作为策略轮转起点）
        # ★ 方案 A：移除内层 attempt 循环，attempt 固定为 0
        #   策略轮转公式：strategies[(retry_count + 0) % len(strategies)]
        #   详见设计文档 §4.4（外层重试时 retry_count 递增，策略不重复）
        rewritten_query = query_simplified
        strategy_used = "none"
        if enable_rewrite:
            rewritten_query, strategy_used = await self._rewrite_query(query_simplified, retry_count, tenant_id=tenant_id)
            logger.info(f"[RAGTool] 查询重写: strategy={strategy_used}, retry_count={retry_count}, result='{rewritten_query}'")

        # ★ 子查询拆解结果处理：并行检索 + RRF 合并（详见设计文档 §4.4.4）
        # _rewrite_query 返回 __sub_query__: 标记时，调用 _retrieve_with_sub_queries
        # 并行检索所有子查询并使用 RRF 算法合并结果
        if rewritten_query.startswith("__sub_query__:"):
            try:
                sub_queries = json.loads(rewritten_query[len("__sub_query__:") :])
            except json.JSONDecodeError as e:
                logger.warning(f"[RAGTool] 子查询 JSON 解析失败，降级为空结果: {e}")
                return self._empty_result(start_time, retrieval_mode=retrieval_mode)
            if not sub_queries:
                logger.warning("[RAGTool] 子查询列表为空，降级为空结果")
                return self._empty_result(start_time, retrieval_mode=retrieval_mode)
            logger.info(f"[RAGTool] 子查询拆解: {len(sub_queries)} 个子查询，启动并行检索 + RRF 合并")
            return await self._retrieve_with_sub_queries(
                sub_queries=sub_queries,
                kb_ids=kb_ids,
                tenant_id=tenant_id,
                top_k=top_k,
                similarity_threshold=similarity_threshold,
                keywords_similarity_weight=keywords_similarity_weight,
                rerank_id=rerank_id,
                cross_languages=cross_languages_param,
                enable_rerank=enable_rerank,
                query_simplified=query_simplified,
                detected_lang=detected_lang,
                retrieval_mode=retrieval_mode,
                start_time=start_time,
            )

        # 4. 混合检索（单次，无内层循环）—— 经 RetrieverGateway 调用
        # §5.8 异常分类捕获：服务异常→空结果+error_code，认证错误→空结果+告警，
        # 数据错误→透传抛出（业务错误），其他异常→空结果（维持现状）
        try:
            kbinfos = await gateway.retrieve(
                query=rewritten_query,
                kb_ids=kb_ids,
                tenant_id=tenant_id,
                top_k=top_k,
                similarity_threshold=similarity_threshold,
                keywords_similarity_weight=keywords_similarity_weight,
                rerank_id=rerank_id or None,
                cross_languages=cross_languages_param or None,
            )
        except RetrievalServiceError as e:
            # 超时/5xx/熔断 → 转空结果 + 写 retrieval_error_code（warn，不刷屏）
            logger.warning(f"[RAGTool] 检索服务异常: {e}")
            return self._empty_result(
                start_time,
                retrieval_mode=retrieval_mode,
                error_code="RETRIEVAL_SERVICE_ERROR",
            )
        except RetrievalAuthError as e:
            # 401/403 → 转空结果 + RETRIEVAL_AUTH + error 日志（配置问题需告警）
            logger.error(f"[RAGTool] 检索认证失败: {e}")
            return self._empty_result(
                start_time,
                retrieval_mode=retrieval_mode,
                error_code="RETRIEVAL_AUTH",
            )
        except RetrievalDataError:
            # 数据错误 → 透传抛出（业务错误，如 KB 无权限，不静默）
            raise
        except Exception as e:
            # 其他未知异常 → 维持现状：error 日志 + 空结果（不写 error_code）
            logger.error(f"[RAGTool] 检索失败: {e}")
            return self._empty_result(start_time, retrieval_mode=retrieval_mode)

        chunks = kbinfos.get("chunks", [])
        if not chunks:
            logger.warning("[RAGTool] 检索结果为空")
            return self._empty_result(start_time, retrieval_mode=retrieval_mode)

        logger.info(f"[RAGTool] 检索到 {len(chunks)} 个候选文档")

        # 5-7. Rerank + 质量评估 + 格式化（复用 _rerank_evaluate_format，与子查询合并路径一致）
        return await self._rerank_evaluate_format(
            chunks,
            rewritten_query=rewritten_query,
            kb_ids=kb_ids,
            tenant_id=tenant_id,
            rerank_id=rerank_id,
            top_k=top_k,
            enable_rerank=enable_rerank,
            query_simplified=query_simplified,
            detected_lang=detected_lang,
            retrieval_mode=retrieval_mode,
            start_time=start_time,
            strategy_used=strategy_used,
        )

    async def _rewrite_query(
        self,
        query: str,
        retry_count: int = 0,
        attempt: int = 0,
        tenant_id: str = "",
    ) -> tuple[str, str]:
        """查询重写（完整三策略实现 + 策略轮转协同，详见设计文档 §4.4）。

        策略轮转公式：strategies[(retry_count + attempt) % len(strategies)]
        - retry_count：主流程透传的外层重试次数，确保外层重试时切换策略
        - attempt：内层 attempt（方案 A 下固定为 0，保留为未来扩展留余地）

        三种策略（完整实现，恢复 Phase 3 Task 13 + Task 14）：
        - synonym_rewrite：同义词扩展（本地词典，零 LLM 成本）
        - sub_query_decompose：调用 SubQueryDecomposer 组件拆解子查询
          （LLM 拆解 + 并行检索 + RRF 合并，详见 §4.4.4）
        - hyde：调用 HyDE 组件生成假设性答案用于检索
          （LLM 生成假设文档嵌入，详见 §4.4.2）

        降级兜底（设计文档 §2.4）：SubQueryDecomposer / HyDE 组件调用失败、
        未启用、或 LLM 未配置时，自动降级为同义词扩展，保证主流程可用。

        Args:
            query: 查询文本（已繁简转换）
            retry_count: 主流程透传的外层重试次数（策略轮转起点）
            attempt: 内层 attempt（方案 A 下固定为 0）
            tenant_id: 租户 ID（用于 LLM 配置解析和组件构造）

        Returns:
            tuple: (rewritten_query, strategy_used)
                - sub_query_decompose 策略返回特殊标记 "__sub_query__:<json>"，
                  供 invoke() 检测并调用 _retrieve_with_sub_queries 并行检索 + RRF 合并
        """
        from agent.component.query_rewriter import (
            analyze_query_complexity,
            COMPLEXITY_STRATEGIES,
            DEFAULT_STRATEGIES,
            STRATEGY_SYNONYM_REWRITE,
            STRATEGY_SUB_QUERY_DECOMPOSE,
            STRATEGY_HYDE,
        )

        # 分析查询复杂度
        complexity, strategies = analyze_query_complexity(query)
        logger.debug(f"[RAGTool] 查询复杂度: {complexity}, 策略列表: {strategies}")

        if not strategies:
            strategies = list(COMPLEXITY_STRATEGIES.get(complexity, DEFAULT_STRATEGIES))

        # ★ 策略轮转协同：(retry_count + attempt) % len(strategies)
        # 确保外层重试时策略不重复（详见设计文档 §4.4.3）
        selected_index = (retry_count + attempt) % len(strategies)
        selected_strategy = strategies[selected_index]

        logger.info(f"[RAGTool] 策略选择: retry_count={retry_count}, attempt={attempt}, selected_index={selected_index}, selected_strategy={selected_strategy}")

        # 策略 1：同义词扩展（保留现有逻辑，零 LLM 成本）
        if selected_strategy == STRATEGY_SYNONYM_REWRITE:
            return self._synonym_rewrite(query), selected_strategy

        # 策略 2：子查询拆解（恢复完整实现，调用 SubQueryDecomposer 组件）
        # 详见设计文档 §4.4.4：LLM 拆解 + 并行检索 + RRF 合并
        elif selected_strategy == STRATEGY_SUB_QUERY_DECOMPOSE:
            if not is_feature_enabled("query_rewrite.sub_query_decompose"):
                logger.info("[RAGTool] 子查询拆解未启用，降级为同义词扩展")
                return self._synonym_rewrite(query), "synonym_rewrite(fallback)"
            try:
                sub_queries = await self._decompose_sub_queries(query, tenant_id)
                if not sub_queries:
                    logger.warning("[RAGTool] SubQueryDecomposer 返回空列表，降级为同义词扩展")
                    return self._synonym_rewrite(query), "synonym_rewrite(fallback)"
                # 返回特殊标记，invoke() 检测后调用 _retrieve_with_sub_queries 并行检索 + RRF 合并
                return f"__sub_query__:{json.dumps(sub_queries, ensure_ascii=False)}", selected_strategy
            except Exception as e:
                logger.warning(f"[RAGTool] SubQueryDecomposer 调用失败，降级为同义词扩展: {e}")
                return self._synonym_rewrite(query), "synonym_rewrite(fallback)"

        # 策略 3：HyDE 假设文档嵌入（恢复完整实现，调用 HyDE 组件）
        # 详见设计文档 §4.4.2：LLM 生成假设答案用于检索
        elif selected_strategy == STRATEGY_HYDE:
            if not is_feature_enabled("query_rewrite.hyde"):
                logger.info("[RAGTool] HyDE 未启用，降级为同义词扩展")
                return self._synonym_rewrite(query), "synonym_rewrite(fallback)"
            try:
                hypothetical_answer = await self._generate_hyde_answer(query, tenant_id)
                if not hypothetical_answer:
                    logger.warning("[RAGTool] HyDE 生成空答案，降级为同义词扩展")
                    return self._synonym_rewrite(query), "synonym_rewrite(fallback)"
                return hypothetical_answer, selected_strategy
            except Exception as e:
                logger.warning(f"[RAGTool] HyDE 调用失败，降级为同义词扩展: {e}")
                return self._synonym_rewrite(query), "synonym_rewrite(fallback)"

        return query, selected_strategy

    def _synonym_rewrite(self, query: str) -> str:
        """同义词扩展（提取为独立方法，供降级路径复用）。

        从本地词典查找查询词的同义词，用 OR 拼接扩展查询。
        逻辑与 QueryRewriter 组件的 _rewrite_with_synonyms fallback 一致。
        """
        from agent.component.query_rewriter import _build_synonym_expansion

        expansion = _build_synonym_expansion(query, topn=3)
        if expansion.get("synonyms"):
            expanded_terms = [query]
            for term, syns in expansion["synonyms"].items():
                for syn in syns:
                    expanded_terms.append(f'"{syn}"')
            return " OR ".join(expanded_terms)
        return query

    async def _decompose_sub_queries(self, query: str, tenant_id: str) -> list[str]:
        """调用 SubQueryDecomposer 组件拆解子查询（Task 13 恢复完整实现）。

        复用 LocalVerifierGateway 的 Canvas 桩模式构造组件实例
        （SubQueryDecomposer 继承 ComponentBase，构造时断言 canvas 是 Graph 实例），
        直接调用组件内部的 _decompose 方法（async）执行 LLM 拆解。

        组件接口适配说明（以实际代码为准，与设计文档伪代码的差异）：
        - SubQueryDecomposer 构造签名为 (canvas, id, param)，非 (tenant_id=...)
        - 拆解方法为 _decompose(query)（async），非 decompose(query)
        - LLM 配置通过 self._param.llm_id + self._canvas.get_tenant_id() 解析

        Args:
            query: 待拆解的查询文本
            tenant_id: 租户 ID（用于 LLM 配置解析）

        Returns:
            拆解后的子查询列表（2-5 个）；LLM 未配置或拆解失败时返回空列表
        """
        # 延迟导入避免循环依赖
        from agent.component.sub_query_decomposer import (
            SubQueryDecomposer,
            SubQueryDecomposerParam,
        )
        from agent.langgraph.gateways.local_verifier import (
            LocalVerifierGateway,
            _make_canvas_stub,
        )

        # 解析租户默认聊天模型（SubQueryDecomposer 需要 LLM 拆解查询）
        # 复用 LocalVerifierGateway 的静态方法，保持 LLM 解析逻辑一致
        llm_id = LocalVerifierGateway._resolve_tenant_llm_id(tenant_id)
        if not llm_id:
            logger.warning(f"[RAGTool] 租户 {tenant_id} 未配置默认聊天模型，SubQueryDecomposer 无法执行")
            return []

        # 构建 Canvas 桩（满足 ComponentBase 的 isinstance(canvas, Graph) 断言）
        canvas = _make_canvas_stub(tenant_id)

        # 构造组件参数（保持默认的 min_count=2 / max_count=5 / rrf_k=60）
        param = SubQueryDecomposerParam()
        param.llm_id = llm_id
        param.check()

        # 构造 SubQueryDecomposer 实例
        decomposer = SubQueryDecomposer(canvas, "rag_tool_sub_query_decompose", param)

        # 直接调用组件内部的 _decompose 方法（async，绕过 _invoke_async 的 @timeout 装饰器）
        # _decompose 内部通过 self._create_llm_bundle 解析 LLM 配置并调用 async_chat
        return await decomposer._decompose(query)

    async def _generate_hyde_answer(self, query: str, tenant_id: str) -> str:
        """调用 HyDE 组件生成假设性答案（Task 14 恢复完整实现）。

        复用 LocalVerifierGateway 的 Canvas 桩模式构造组件实例
        （HyDE 继承 ComponentBase，构造时断言 canvas 是 Graph 实例），
        直接调用组件内部的 _generate_hypothetical_answer 方法（async）执行 LLM 生成。

        组件接口适配说明（以实际代码为准，与设计文档伪代码的差异）：
        - HyDE 构造签名为 (canvas, id, param)，非 (tenant_id=...)
        - 生成方法为 _generate_hypothetical_answer(query)（async），非 generate(query)
        - LLM 配置通过 self._param.llm_id + self._canvas.get_tenant_id() 解析
        - enable_hyde 需显式置 True（组件默认 False，跳过生成）

        Args:
            query: 用户查询文本
            tenant_id: 租户 ID（用于 LLM 配置解析）

        Returns:
            假设性答案文本（≤200字，仅取第一段保持检索聚焦）；
            LLM 未配置或生成失败时返回空字符串
        """
        # 延迟导入避免循环依赖
        from agent.component.hyde import HyDE, HyDEParam
        from agent.langgraph.gateways.local_verifier import (
            LocalVerifierGateway,
            _make_canvas_stub,
        )

        # 解析租户默认聊天模型（HyDE 需要 LLM 生成假设答案）
        llm_id = LocalVerifierGateway._resolve_tenant_llm_id(tenant_id)
        if not llm_id:
            logger.warning(f"[RAGTool] 租户 {tenant_id} 未配置默认聊天模型，HyDE 无法执行")
            return ""

        # 构建 Canvas 桩
        canvas = _make_canvas_stub(tenant_id)

        # 构造组件参数
        # enable_hyde 必须置 True（组件默认 False 会直接跳过生成）
        # temperature=0.3 / max_tokens=256 保持组件默认值（低温度确保输出稳定）
        param = HyDEParam()
        param.llm_id = llm_id
        param.enable_hyde = True
        param.check()

        # 构造 HyDE 实例
        hyde = HyDE(canvas, "rag_tool_hyde", param)

        # 直接调用组件内部的 _generate_hypothetical_answer 方法（async）
        # 该方法内部通过 self._create_llm_bundle 解析 LLM 配置并调用 async_chat
        return await hyde._generate_hypothetical_answer(query)

    def _get_rerank_model(self, rerank_id: str, tenant_id: str, kb_ids: list[str]) -> Any:
        """获取 Rerank 模型。

        参考 agent/tools/retrieval.py 中的 Rerank 模型获取逻辑。

        Args:
            rerank_id: Rerank 模型 ID
            tenant_id: 租户 ID
            kb_ids: 知识库 ID 列表

        Returns:
            Rerank 模型实例，如果未配置则返回 None
        """
        if not rerank_id:
            return None

        try:
            from api.db.services.llm_service import LLMBundle
            from api.db.joint_services.tenant_model_service import (
                get_model_config_by_type_and_name,
            )
            from api.db.services.knowledgebase_service import KnowledgebaseService
            from common.constants import LLMType

            # 获取知识库所属的租户 ID
            kbs = KnowledgebaseService.get_by_ids(kb_ids)
            if not kbs:
                return None

            actual_tenant_id = tenant_id or kbs[0].tenant_id
            rerank_model_config = get_model_config_by_type_and_name(actual_tenant_id, LLMType.RERANK, rerank_id)
            if rerank_model_config:
                return LLMBundle(actual_tenant_id, rerank_model_config)
            return None
        except Exception as e:
            logger.warning(f"[RAGTool] 获取 Rerank 模型失败: {e}")
            return None

    async def _evaluate_quality(
        self,
        chunks: list[dict],
        query: str = "",
        tenant_id: str = "",
        enable_grader: bool = True,
    ) -> tuple[float, bool, int, float, str]:
        """评估检索质量（参考 Grader 组件的阈值策略，输出分数来源标注）。

        策略：
        1. 基础评估（快速、零成本）：Rerank 分数阈值 → score_source="base"
        2. 增强评估（边界值触发）：Grader 三模式语义评估 → score_source="grader_merged"

        参考 agent/component/grader.py 的评估逻辑：
        - 使用 similarity/rerank_score 作为相关性分数
        - 分数 >= RELEVANT_THRESHOLD (0.5) 视为相关
        - 质量评分使用平均分数

        Args:
            chunks: 检索到的文档块列表
            query: 查询文本（用于 Grader 语义评估）
            tenant_id: 租户 ID
            enable_grader: 是否启用 Grader 增强（由配置开关控制）

        Returns:
            tuple: (quality_score, has_relevant, relevant_count, top_score, score_source)
                - score_source: "base"（基础评估）或 "grader_merged"（Grader 增强后融合分数），
                  供主流程 quality_check 按来源选择阈值（详见设计文档 §4.2.4）
        """
        if not chunks:
            return 0.0, False, 0, 0.0, "base"

        # 提取所有分数（优先使用 rerank_score，其次 similarity，最后 score）
        scores = []
        for chunk in chunks:
            score = chunk.get(
                "rerank_score",
                chunk.get("similarity", chunk.get("score", 0.0)),
            )
            scores.append(score)

        if not scores:
            return 0.0, False, 0, 0.0, "base"

        top_score = max(scores)
        relevant_scores = [s for s in scores if s >= self.RELEVANT_THRESHOLD]
        relevant_count = len(relevant_scores)
        has_relevant = relevant_count > 0

        # 基础质量评分：使用平均分数
        base_quality_score = sum(scores) / len(scores) if scores else 0.0

        # ★ Grader 增强路径：仅边界值（0.4~0.7）时触发语义评估
        # 详见设计文档 §4.2.3：成本控制，仅边界值场景触发 LLM 调用
        if enable_grader and query and tenant_id and 0.4 <= base_quality_score < 0.7:
            try:
                verifier = await get_gateway_resolver().verifier_for(tenant_id)
                grader_result = await verifier.grade_retrieval(query, chunks, tenant_id)

                # 融合分数：基础分 × 0.4 + Grader 分 × 0.6
                grader_score = grader_result["quality_score"]
                quality_score = base_quality_score * 0.4 + grader_score * 0.6
                relevant_count = grader_result["relevant_count"]
                has_relevant = grader_result["has_relevant"]

                logger.info(f"[RAGTool] Grader 增强: base={base_quality_score:.2f}, grader={grader_score:.2f}, merged={quality_score:.2f}")
                # 标注分数来源为 grader_merged，供 quality_check 使用更严格的阈值
                return (
                    quality_score,
                    has_relevant,
                    relevant_count,
                    top_score,
                    "grader_merged",
                )
            except Exception as e:
                # Grader 调用失败降级：使用基础评估结果，标注分数来源为 base
                logger.warning(f"[RAGTool] Grader 评估失败，使用基础评估: {e}")

        # 基础评估结果，标注分数来源为 base
        return (
            base_quality_score,
            has_relevant,
            relevant_count,
            top_score,
            "base",
        )

    # ★ 方案 A：_should_internal_retry 方法已删除（重试决策交给主流程 quality_check）
    # 原 _should_internal_retry(has_relevant, relevant_count, attempt) 逻辑已上提到
    # quality_check._make_decision()，详见设计文档 §4.5.3 / §4.5.5

    async def _retrieve_with_sub_queries(
        self,
        sub_queries: list[str],
        *,
        kb_ids: list[str],
        tenant_id: str,
        top_k: int,
        similarity_threshold: float,
        keywords_similarity_weight: float,
        rerank_id: str,
        cross_languages: list[str],
        enable_rerank: bool,
        query_simplified: str,
        detected_lang: str,
        retrieval_mode: str,
        start_time: float,
    ) -> RAGToolOutput:
        """子查询并行检索 + RRF 合并（Task 13 恢复完整实现，详见设计文档 §4.4.4）。

        流程：
        1. 通过 asyncio.gather 并行检索所有子查询（经 RetrieverGateway 调用）
        2. RRF（Reciprocal Rank Fusion）合并结果：
           score(d) = Σ 1/(K + rank_i(d) + 1)，K=60 平滑常数
           同一文档被多个子查询命中时 RRF 分数累加
        3. 按 RRF 分数排序，取 top_k
        4. 后续 Rerank + 质量评估复用 _rerank_evaluate_format（与单查询主流程一致）

        参考 agent/component/sub_query_decomposer.py 的 merge_sub_query_results
        算法实现，保持 RRF 平滑常数 K=60 一致。

        Args:
            sub_queries: 拆解后的子查询列表
            kb_ids: 知识库 ID 列表
            tenant_id: 租户 ID
            top_k: 返回文档数量
            similarity_threshold: 相似度阈值
            keywords_similarity_weight: 关键词相似度权重
            rerank_id: Rerank 模型 ID
            cross_languages: 跨语言扩展目标语言列表
            enable_rerank: 是否启用 Rerank
            query_simplified: 繁简转换后的原始查询（用于 Rerank 和质量评估）
            detected_lang: 检测到的语言
            retrieval_mode: 检索模式
            start_time: 开始时间戳

        Returns:
            RAGToolOutput: RRF 合并 + Rerank + 质量评估后的检索结果
        """
        import asyncio

        # 获取检索网关（与主流程一致，含 NotImplementedError 回退）
        try:
            gateway = await get_gateway_resolver().retriever_for(tenant_id)
        except NotImplementedError:
            logger.warning("[RAGTool] 网关未注入，回退到 LocalRetrieverGateway")
            from agent.langgraph.gateways.local_retriever import LocalRetrieverGateway

            gateway = LocalRetrieverGateway()

        # 并行检索所有子查询（return_exceptions=True 防止单个失败影响整体）
        tasks = [
            gateway.retrieve(
                query=sq,
                kb_ids=kb_ids,
                tenant_id=tenant_id,
                top_k=top_k,
                similarity_threshold=similarity_threshold,
                keywords_similarity_weight=keywords_similarity_weight,
                rerank_id=rerank_id or None,
                cross_languages=cross_languages or None,
            )
            for sq in sub_queries
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # RRF 合并（Reciprocal Rank Fusion）
        # 公式：score(d) = Σ 1/(K + rank_i(d) + 1)，K=60 平滑常数
        # 与 SubQueryDecomposer 组件 DEFAULT_RRF_K 一致
        rrf_k = 60
        rrf_scores: dict[str, float] = {}
        chunk_map: dict[str, dict] = {}

        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"[RAGTool] 子查询检索失败: {result}")
                continue
            chunks = result.get("chunks", []) if isinstance(result, dict) else []
            for rank, chunk in enumerate(chunks):
                # 文档唯一标识：优先 chunk_id，其次 doc_id，最后用对象 id 兜底
                # 与 SubQueryDecomposer.merge_sub_query_results 的 id 解析策略一致
                chunk_id = str(chunk.get("chunk_id") or chunk.get("id") or chunk.get("doc_id") or id(chunk))
                # RRF 分数累加：同一文档被多个子查询命中时分数叠加
                rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + 1.0 / (rrf_k + rank + 1)
                if chunk_id not in chunk_map:
                    chunk_map[chunk_id] = chunk

        if not chunk_map:
            logger.warning("[RAGTool] 子查询 RRF 合并结果为空")
            return self._empty_result(start_time, retrieval_mode=retrieval_mode)

        # 按 RRF 分数排序，取 top_k
        sorted_chunks = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        merged_chunks = []
        for chunk_id, rrf_score in sorted_chunks:
            chunk = chunk_map[chunk_id]
            # 写入 RRF 分数，复用 rerank_score 字段供后续质量评估使用
            chunk["rrf_score"] = rrf_score
            chunk["rerank_score"] = rrf_score
            merged_chunks.append(chunk)

        logger.info(f"[RAGTool] 子查询 RRF 合并完成: {len(merged_chunks)} 条结果（来自 {len(sub_queries)} 个子查询）")

        # 后续 Rerank + 质量评估 + 格式化（复用主流程，与单查询路径一致）
        # Rerank 使用原始简化查询（而非子查询），保持与用户意图对齐
        return await self._rerank_evaluate_format(
            merged_chunks,
            rewritten_query=query_simplified,
            kb_ids=kb_ids,
            tenant_id=tenant_id,
            rerank_id=rerank_id,
            top_k=top_k,
            enable_rerank=enable_rerank,
            query_simplified=query_simplified,
            detected_lang=detected_lang,
            retrieval_mode=retrieval_mode,
            start_time=start_time,
            strategy_used="sub_query_decompose",
        )

    async def _rerank_evaluate_format(
        self,
        chunks: list[dict],
        *,
        rewritten_query: str,
        kb_ids: list[str],
        tenant_id: str,
        rerank_id: str,
        top_k: int,
        enable_rerank: bool,
        query_simplified: str,
        detected_lang: str,
        retrieval_mode: str,
        start_time: float,
        strategy_used: str,
    ) -> RAGToolOutput:
        """Rerank + 质量评估 + 格式化输出（供 invoke 主流程和子查询合并后复用）。

        提取为独立方法以避免 invoke 与 _retrieve_with_sub_queries 之间的代码重复。
        三步流程与原 invoke 步骤 5-7 完全一致：
        1. Rerank 精排（可选，失败时使用原始排序）
        2. 质量评估（输出 score_source 分数来源标注）
        3. 格式化输出为 RAGToolOutput

        Args:
            chunks: 检索到的文档块列表（子查询场景为 RRF 合并后的 chunks）
            rewritten_query: 重写后的查询（用于 Rerank）
            kb_ids: 知识库 ID 列表
            tenant_id: 租户 ID
            rerank_id: Rerank 模型 ID
            top_k: 返回文档数量
            enable_rerank: 是否启用 Rerank
            query_simplified: 繁简转换后的原始查询（用于质量评估）
            detected_lang: 检测到的语言
            retrieval_mode: 检索模式
            start_time: 开始时间戳
            strategy_used: 实际使用的重写策略（观测用）

        Returns:
            RAGToolOutput: 包含 docs / quality_score / score_source / rewrite_strategy 等
        """
        # Rerank 精排（单次）
        if enable_rerank and chunks:
            try:
                rerank_mdl = self._get_rerank_model(rerank_id, tenant_id, kb_ids)
                if rerank_mdl is not None:
                    chunks = multilingual_rerank(
                        rewritten_query,
                        chunks,
                        top_k=top_k,
                        rerank_model=rerank_mdl,
                    )
                    logger.info(f"[RAGTool] Rerank 完成: {len(chunks)} 条结果")
                else:
                    logger.info("[RAGTool] Rerank 模型未配置，跳过 Rerank")
            except Exception as e:
                logger.warning(f"[RAGTool] Rerank 失败，使用原始排序: {e}")

        # 质量评估（输出 score_source 标注分数来源）
        # ★ score_source 标注分数来源（base / grader_merged），供主流程
        #   quality_check 按来源选择阈值（详见设计文档 §4.2.4）
        quality_score, has_relevant, relevant_count, top_score, score_source = await self._evaluate_quality(
            chunks,
            query=query_simplified,
            tenant_id=tenant_id,
            enable_grader=is_feature_enabled("grader"),
        )

        logger.info(f"[RAGTool] 质量评估: score={quality_score:.2f} ({score_source}), relevant={relevant_count}, top={top_score:.2f}")

        # 格式化输出（单次执行，无中间结果缓存，无重写历史列表）
        docs = self._format_docs(chunks)
        retrieval_time_ms = int((time.time() - start_time) * 1000)

        return RAGToolOutput(
            docs=docs,
            quality_score=quality_score,
            has_relevant=has_relevant,
            relevant_count=relevant_count,
            top_score=top_score,
            score_source=score_source,  # ★ 方案 A：分数来源标注
            rewrite_strategy=strategy_used,  # ★ 方案 A：重写策略（观测用）
            query_simplified=query_simplified,
            detected_lang=detected_lang,
            retrieval_time_ms=retrieval_time_ms,
            retrieval_mode_used=retrieval_mode,
        )

    def _format_docs(self, chunks: list[dict]) -> list[dict]:
        """格式化文档输出。

        Args:
            chunks: 原始文档块列表

        Returns:
            list[dict]: 格式化后的文档列表
        """
        docs = []
        for chunk in chunks:
            doc = {
                "content": chunk.get("content_with_weight", chunk.get("content", "")),
                "score": chunk.get(
                    "rerank_score",
                    chunk.get("similarity", chunk.get("score", 0.0)),
                ),
                "source": chunk.get("docnm_kwd", "unknown"),
                "chunk_id": chunk.get("chunk_id", ""),
                "doc_id": chunk.get("doc_id", ""),
                "doc_type": chunk.get("doc_type", ""),
                "status": chunk.get("status", ""),
                "metadata": chunk.get("metadata", {}),
            }
            docs.append(doc)
        return docs

    def _empty_result(
        self,
        start_time: float,
        *,
        retrieval_mode: str = "",
        error_code: str = "",
    ) -> RAGToolOutput:
        """返回空结果。

        方案 A：返回值包含 score_source="base" 和 rewrite_strategy="none"，
        替代原 rewrite_history=[]（单次执行不再有历史列表）。

        Args:
            start_time: 开始时间戳
            retrieval_mode: 实际使用的检索模式（§5.8 观测用）
            error_code: 检索错误码（§5.8，空串表示无错误/未知异常）

        Returns:
            RAGToolOutput: 空的检索结果
        """
        retrieval_time_ms = int((time.time() - start_time) * 1000)
        return RAGToolOutput(
            docs=[],
            quality_score=0.0,
            has_relevant=False,
            relevant_count=0,
            top_score=0.0,
            score_source="base",  # ★ 方案 A：空结果默认 base 分数
            rewrite_strategy="none",  # ★ 方案 A：空结果默认未使用重写策略
            query_simplified="",
            detected_lang="zh_CN",
            retrieval_time_ms=retrieval_time_ms,
            retrieval_error_code=error_code,
            retrieval_mode_used=retrieval_mode,
        )


# 全局实例（可选，方便 LangGraph 节点调用）
_rag_tool_instance: RAGTool | None = None


def get_rag_tool() -> RAGTool:
    """获取 RAGTool 单例实例。"""
    global _rag_tool_instance
    if _rag_tool_instance is None:
        _rag_tool_instance = RAGTool()
    return _rag_tool_instance
