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

import copy
import json
import logging
import time
from collections import defaultdict
from typing import Any, Literal, TypedDict

from common.misc_utils import thread_pool_exec
from common.token_utils import num_tokens_from_string

from api.utils.multilingual_reranker import rerank as multilingual_rerank
from api.utils.query_preprocessor import preprocess_query
from agent.langgraph.config import get_config_section, is_feature_enabled
from agent.langgraph.gateways.errors import (
    RetrievalAuthError,
    RetrievalDataError,
    RetrievalServiceError,
)
from agent.langgraph.gateways.factory import GatewayResolver, get_gateway_resolver
from agent.langgraph.utils.lang_utils import to_langgraph_lang

logger = logging.getLogger(__name__)


def get_document_relevance_score(doc: dict) -> tuple[float, str]:
    """获取文档相关性分数，优先级：rerank_score → similarity → score → 0.0。

    Args:
        doc: 文档字典

    Returns:
        tuple: (relevance_score, raw_score_source)
            - raw_score_source: "rerank_score" / "similarity" / "score" / "none"
    """
    if "rerank_score" in doc and doc["rerank_score"] is not None:
        return (float(doc["rerank_score"]), "rerank_score")
    if "similarity" in doc and doc["similarity"] is not None:
        return (float(doc["similarity"]), "similarity")
    if "score" in doc and doc["score"] is not None:
        return (float(doc["score"]), "score")
    return (0.0, "none")


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
    # ★ 两阶段检索 Top-K 分离（§4.6 动态截断算法）
    # 粗排（向量检索）：取较大候选池，覆盖面广，成本低，默认 100
    retrieval_top_k: int
    # 精排（Cross-Encoder Rerank）：取较小候选池，精度高，成本高，默认 20
    rerank_top_k: int


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
    # 动态截断元数据（§4.6 动态截断算法）
    rag_avg_score: float  # 截断后文档的平均相关性分数
    rag_result_count: int  # 截断后文档数量
    rag_raw_score_source: str  # 原始分数来源（rerank_score / similarity / score / none）
    rag_query_signature: str  # 查询签名（用于缓存/去重）
    rag_evidence_signature: str  # 证据签名（用于缓存/去重）
    rag_retrieval_top_k: int  # 实际使用的粗排检索上限
    rag_rerank_top_k: int  # 实际使用的 Rerank 精排上限
    # 父块扩展元数据（§5.0 父块扩展策略）
    parent_expansion_total: int  # 参与扩展的截断后子块总数
    parent_expansion_full_parent: int  # 使用完整父块策略的子块数（含合并）
    parent_expansion_window: int  # 使用窗口截取策略的子块数
    parent_expansion_sub_only: int  # 保持子块不变的子块数


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

    # 两阶段检索 Top-K 配置（§4.6 动态截断算法）
    # 粗排（向量检索）：取较大候选池，覆盖面广，成本低
    DEFAULT_RETRIEVAL_TOP_K = 100  # 粗排检索候选上限
    # 精排（Cross-Encoder Rerank）：取较小候选池，精度高，成本高
    DEFAULT_RERANK_TOP_K = 20  # Rerank 精排候选上限
    # 动态截断算法（在 Rerank 精排后基于分数分布动态决定最终保留条数）
    DEFAULT_RELEVANCE_THRESHOLD = 0.3  # 绝对分数阈值
    DEFAULT_RELATIVE_SCORE_RATIO = 0.3  # 相对分数比例（top_score * ratio）
    DEFAULT_SCORE_DROP_THRESHOLD = 0.3  # 分数悬崖检测阈值
    DEFAULT_MIN_KEEP = 3  # 最少保留文档数

    # 父块扩展策略阈值（§5.0 父块扩展策略）
    # 父块 token 数 ≤ 此值：使用完整父块（收益 > 噪声）
    DEFAULT_FULL_PARENT_MAX_TOKENS = 1024
    # 父块 token 数 ≤ 此值：使用子块 + 局部上下文窗口截取
    DEFAULT_WINDOW_CONTEXT_MAX_TOKENS = 2048
    # 窗口截取时子块前后各保留的 token 数
    DEFAULT_CONTEXT_WINDOW_TOKENS = 512

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
        # ★ 两阶段检索 Top-K 分离（§4.6）
        # 粗排：向量检索取较大候选池（默认 100），覆盖面广，成本低
        retrieval_top_k = input_data.get("retrieval_top_k", self.DEFAULT_RETRIEVAL_TOP_K)
        # 精排：Cross-Encoder Rerank 取较小候选池（默认 20），精度高，成本高
        rerank_top_k = input_data.get("rerank_top_k", self.DEFAULT_RERANK_TOP_K)

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
                retrieval_top_k=retrieval_top_k,
                rerank_top_k=rerank_top_k,
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
                top_k=retrieval_top_k,
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
            retrieval_top_k=retrieval_top_k,
            rerank_top_k=rerank_top_k,
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
        retrieval_top_k: int = 100,
        rerank_top_k: int = 20,
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
        """子查询并行检索 + RRF 合并 + Rerank + 动态截断（详见设计文档 §4.4.4 / §4.6）。

        流程：
        1. 通过 asyncio.gather 并行检索所有子查询（经 RetrieverGateway 调用）
           - 每个子查询使用 retrieval_top_k（粗排，默认 100）
        2. RRF（Reciprocal Rank Fusion）合并结果：
           score(d) = Σ 1/(K + rank_i(d) + 1)，K=60 平滑常数
           同一文档被多个子查询命中时 RRF 分数累加
        3. 按 RRF 分数排序，取 retrieval_top_k 供后续 Rerank
        4. Rerank 精排，取 rerank_top_k（默认 20）
        5. 动态截断（§4.6）：基于 Rerank 分数分布动态决定最终保留条数

        参考 agent/component/sub_query_decomposer.py 的 merge_sub_query_results
        算法实现，保持 RRF 平滑常数 K=60 一致。

        Args:
            sub_queries: 拆解后的子查询列表
            kb_ids: 知识库 ID 列表
            tenant_id: 租户 ID
            top_k: 返回文档数量（对外接口参数，实际由动态截断决定）
            retrieval_top_k: 粗排检索候选上限（默认 100）
            rerank_top_k: Rerank 精排候选上限（默认 20）
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
            RAGToolOutput: RRF 合并 + Rerank + 动态截断后的检索结果
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
        # 每个子查询使用 retrieval_top_k（粗排，默认 100），保证覆盖面
        tasks = [
            gateway.retrieve(
                query=sq,
                kb_ids=kb_ids,
                tenant_id=tenant_id,
                top_k=retrieval_top_k,
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

        # 按 RRF 分数排序，取 retrieval_top_k（粗排候选池，默认 100）
        sorted_chunks = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:retrieval_top_k]
        merged_chunks = []
        for chunk_id, rrf_score in sorted_chunks:
            chunk = chunk_map[chunk_id]
            # 写入 RRF 分数，复用 rerank_score 字段供后续质量评估使用
            chunk["rrf_score"] = rrf_score
            chunk["rerank_score"] = rrf_score
            merged_chunks.append(chunk)

        logger.info(f"[RAGTool] 子查询 RRF 合并完成: {len(merged_chunks)} 条结果（来自 {len(sub_queries)} 个子查询）")

        # 动态截断（§4.6）：在 RRF 合并后先过滤低质量文档，再进入 Rerank
        truncation_config = {
            "relevance_threshold": self.DEFAULT_RELEVANCE_THRESHOLD,
            "relative_score_ratio": self.DEFAULT_RELATIVE_SCORE_RATIO,
            "score_drop_threshold": self.DEFAULT_SCORE_DROP_THRESHOLD,
            "min_keep": self.DEFAULT_MIN_KEEP,
        }
        merged_chunks, _ = self._dynamic_truncation(merged_chunks, truncation_config)

        if not merged_chunks:
            logger.warning("[RAGTool] 子查询 RRF 合并后动态截断结果为空")
            return self._empty_result(start_time, retrieval_mode=retrieval_mode)

        # 后续 Rerank + 动态截断 + 质量评估 + 格式化（复用主流程，与单查询路径一致）
        # Rerank 使用原始简化查询（而非子查询），保持与用户意图对齐
        return await self._rerank_evaluate_format(
            merged_chunks,
            rewritten_query=query_simplified,
            kb_ids=kb_ids,
            tenant_id=tenant_id,
            rerank_id=rerank_id,
            top_k=top_k,
            retrieval_top_k=retrieval_top_k,
            rerank_top_k=rerank_top_k,
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
        retrieval_top_k: int = 100,
        rerank_top_k: int = 20,
    ) -> RAGToolOutput:
        """Rerank 精排 + 动态截断 + 质量评估 + 格式化输出。

        提取为独立方法以避免 invoke 与 _retrieve_with_sub_queries 之间的代码重复。
        流程：
        1. Rerank 精排（可选，失败时使用原始排序），取 rerank_top_k（默认 20）
        2. 动态截断（§4.6）：在 Rerank 精排后基于分数分布动态决定最终保留条数
        3. 质量评估（在截断后的文档上重新评估，输出 score_source 分数来源标注）
        4. 格式化输出为 RAGToolOutput

        Args:
            chunks: 检索到的文档块列表（粗排候选池，已由 retrieval_top_k 控制规模）
            rewritten_query: 重写后的查询（用于 Rerank）
            kb_ids: 知识库 ID 列表
            tenant_id: 租户 ID
            rerank_id: Rerank 模型 ID
            top_k: 返回文档数量（对外接口参数，实际由动态截断决定）
            enable_rerank: 是否启用 Rerank
            query_simplified: 繁简转换后的原始查询（用于质量评估）
            detected_lang: 检测到的语言
            retrieval_mode: 检索模式
            start_time: 开始时间戳
            strategy_used: 实际使用的重写策略（观测用）
            rerank_top_k: Rerank 精排候选上限（默认 20），Cross-Encoder 计算量与此成正比

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
                        top_k=rerank_top_k,
                        rerank_model=rerank_mdl,
                    )
                    logger.info(f"[RAGTool] Rerank 完成: {len(chunks)} 条结果")
                else:
                    logger.info("[RAGTool] Rerank 模型未配置，跳过 Rerank")
            except Exception as e:
                logger.warning(f"[RAGTool] Rerank 失败，使用原始排序: {e}")

        # 动态截断（§4.6）：在 Rerank 后应用，过滤低质量文档
        truncation_config = {
            "relevance_threshold": self.DEFAULT_RELEVANCE_THRESHOLD,
            "relative_score_ratio": self.DEFAULT_RELATIVE_SCORE_RATIO,
            "score_drop_threshold": self.DEFAULT_SCORE_DROP_THRESHOLD,
            "min_keep": self.DEFAULT_MIN_KEEP,
        }
        truncated_chunks, truncation_metadata = self._dynamic_truncation(chunks, truncation_config)

        # ★ 父块扩展（§5.0 父块扩展策略）：在动态截断后、质量评估前
        # 仅对保留的 3-20 条子块做父块扩展，减少 dataStore.get 的 I/O 开销。
        # 按父块 token 数分流：≤1024 完整父块 / 1024~2048 窗口截取 / >2048 保持子块。
        parent_child_config = get_config_section("parent_child")
        if parent_child_config.get("enabled", True) and truncated_chunks:
            truncated_chunks, parent_expansion_meta = await self._expand_parents(
                truncated_chunks,
                tenant_id=tenant_id,
                kb_ids=kb_ids,
                full_parent_max_tokens=parent_child_config.get(
                    "full_parent_max_tokens", self.DEFAULT_FULL_PARENT_MAX_TOKENS
                ),
                window_context_max_tokens=parent_child_config.get(
                    "window_context_max_tokens", self.DEFAULT_WINDOW_CONTEXT_MAX_TOKENS
                ),
                context_window_tokens=parent_child_config.get(
                    "context_window_tokens", self.DEFAULT_CONTEXT_WINDOW_TOKENS
                ),
            )
        else:
            parent_expansion_meta = {
                "total": len(truncated_chunks),
                "full_parent": 0,
                "window_context": 0,
                "sub_chunk_only": len(truncated_chunks),
            }

        # 质量评估（在扩展后的文档上重新评估，输出 score_source 标注分数来源）
        # ★ score_source 标注分数来源（base / grader_merged），供主流程
        #   quality_check 按来源选择阈值（详见设计文档 §4.2.4）
        quality_score, has_relevant, relevant_count, top_score, score_source = await self._evaluate_quality(
            truncated_chunks,
            query=query_simplified,
            tenant_id=tenant_id,
            enable_grader=is_feature_enabled("grader"),
        )

        logger.info(f"[RAGTool] 质量评估: score={quality_score:.2f} ({score_source}), relevant={relevant_count}, top={top_score:.2f}")

        # 格式化输出（仅格式化截断后的文档）
        docs = self._format_docs(truncated_chunks)

        # 生成证据签名（用于缓存/去重）
        rag_raw_score_source = get_document_relevance_score(truncated_chunks[0])[1] if truncated_chunks else "none"
        rag_query_signature = query_simplified
        rag_evidence_signature = ",".join(doc.get("doc_id", "") for doc in truncated_chunks[:5]) if truncated_chunks else ""

        top_k_docs_summary = [
            {
                "chunk_id": doc.get("chunk_id", ""),
                "doc_name": doc.get("source", "unknown"),
                "score": round(doc.get("score", 0.0), 4),
            }
            for doc in docs[:top_k]
        ]
        logger.info("[RAGTool] Top-%d 检索文档: %s", len(top_k_docs_summary), json.dumps(top_k_docs_summary, ensure_ascii=False))

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
            rag_avg_score=truncation_metadata["avg_score"],  # §4.6 动态截断元数据
            rag_result_count=truncation_metadata["result_count"],  # §4.6 动态截断元数据
            rag_raw_score_source=rag_raw_score_source,  # §4.6 原始分数来源
            rag_query_signature=rag_query_signature,  # §4.6 查询签名
            rag_evidence_signature=rag_evidence_signature,  # §4.6 证据签名
            rag_retrieval_top_k=retrieval_top_k,  # §4.6 实际使用的粗排检索上限
            rag_rerank_top_k=rerank_top_k,  # §4.6 实际使用的 Rerank 精排上限
            parent_expansion_total=parent_expansion_meta["total"],  # §5.0 父块扩展元数据
            parent_expansion_full_parent=parent_expansion_meta["full_parent"],  # §5.0 父块扩展元数据
            parent_expansion_window=parent_expansion_meta["window_context"],  # §5.0 父块扩展元数据
            parent_expansion_sub_only=parent_expansion_meta["sub_chunk_only"],  # §5.0 父块扩展元数据
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
            relevance_score, _ = get_document_relevance_score(chunk)
            doc = {
                "content": chunk.get("content_with_weight", chunk.get("content", "")),
                "score": relevance_score,
                "source": chunk.get("docnm_kwd", "unknown"),
                "chunk_id": chunk.get("chunk_id", ""),
                "doc_id": chunk.get("doc_id", ""),
                "doc_type": chunk.get("doc_type", ""),
                "status": chunk.get("status", ""),
                "metadata": chunk.get("metadata", {}),
            }
            docs.append(doc)
        return docs

    async def _expand_parents(
        self,
        chunks: list[dict],
        *,
        tenant_id: str,
        kb_ids: list[str],
        full_parent_max_tokens: int = 1024,
        window_context_max_tokens: int = 2048,
        context_window_tokens: int = 512,
    ) -> tuple[list[dict], dict]:
        """父块扩展（§5.0 父块扩展策略）。

        对动态截断后保留的子块，按其父块 token 数分流为三档策略：
        1. 父块 ≤ full_parent_max_tokens：同一父块的子块合并为一条，使用完整父块；
        2. 父块 ≤ window_context_max_tokens：每个子块提取前后局部上下文窗口；
        3. 父块 > window_context_max_tokens：保持子块不变（噪声代价盖过语义收益）。

        Args:
            chunks: 动态截断后的子块列表（含 mom_id 字段）
            tenant_id: 租户 ID
            kb_ids: 知识库 ID 列表（兜底，用于无 kb_id 的子块）
            full_parent_max_tokens: ≤ 此值使用完整父块（默认 1024）
            window_context_max_tokens: ≤ 此值使用窗口截取（默认 2048）
            context_window_tokens: 窗口截取时前后各保留的 token 数（默认 512）

        Returns:
            tuple: (expanded_chunks, expansion_metadata)
                - expansion_metadata: {total, full_parent, window_context, sub_chunk_only}
        """
        if not chunks:
            return chunks, {"total": 0, "full_parent": 0, "window_context": 0, "sub_chunk_only": 0}

        stats = {"total": len(chunks), "full_parent": 0, "window_context": 0, "sub_chunk_only": 0}

        # 1. 按 mom_id 分组，区分有父块与无父块的子块
        parent_groups: dict[str, list[int]] = defaultdict(list)
        no_parent_indices: list[int] = []
        for i, chunk in enumerate(chunks):
            mom_id = chunk.get("mom_id")
            if isinstance(mom_id, str) and mom_id.strip():
                parent_groups[mom_id].append(i)
            else:
                no_parent_indices.append(i)

        # 无父块的子块保持原样
        expanded_chunks: list[dict] = []
        for i in no_parent_indices:
            chunks[i]["_parent_expansion"] = "sub_chunk_only"
            expanded_chunks.append(chunks[i])
        stats["sub_chunk_only"] += len(no_parent_indices)

        # 2. 按父块逐个处理
        for mom_id, indices in parent_groups.items():
            sub_chunks = [chunks[i] for i in indices]
            sub_kb_ids = list({ck.get("kb_id", "") for ck in sub_chunks if ck.get("kb_id")}) or list(kb_ids)

            parent = await self._fetch_parent_chunk(mom_id, tenant_id, sub_kb_ids)
            if not parent or not parent.get("content_with_weight"):
                # 父块获取失败或内容为空，保持子块
                for ck in sub_chunks:
                    ck["_parent_expansion"] = "sub_chunk_only"
                    ck["_parent_mom_id"] = mom_id
                expanded_chunks.extend(sub_chunks)
                stats["sub_chunk_only"] += len(indices)
                continue

            parent_content = parent["content_with_weight"]
            parent_tokens = self._count_tokens(parent_content)

            if parent_tokens <= full_parent_max_tokens:
                # 策略 1：完整父块，去重合并为一条
                merged = self._build_merged_parent_chunk(sub_chunks, parent, mom_id)
                expanded_chunks.append(merged)
                stats["full_parent"] += 1
                logger.debug(
                    "[RAGTool] 父块扩展 full_parent: mom_id=%s, tokens=%d, sub_chunks=%d",
                    mom_id, parent_tokens, len(indices),
                )

            elif parent_tokens <= window_context_max_tokens:
                # 策略 2：每个子块提取周边上下文窗口
                for ck in sub_chunks:
                    sub_content = ck.get("content_with_weight", ck.get("content", ""))
                    window_content = self._extract_window_context(
                        parent_content, sub_content, context_window_tokens
                    )
                    ck["content_with_weight"] = window_content
                    ck["_parent_expansion"] = "window_context"
                    ck["_parent_mom_id"] = mom_id
                expanded_chunks.extend(sub_chunks)
                stats["window_context"] += len(indices)
                logger.debug(
                    "[RAGTool] 父块扩展 window_context: mom_id=%s, tokens=%d, sub_chunks=%d",
                    mom_id, parent_tokens, len(indices),
                )

            else:
                # 策略 3：父块太大，保持子块不变
                for ck in sub_chunks:
                    ck["_parent_expansion"] = "sub_chunk_only"
                    ck["_parent_too_large"] = True
                    ck["_parent_mom_id"] = mom_id
                expanded_chunks.extend(sub_chunks)
                stats["sub_chunk_only"] += len(indices)
                logger.debug(
                    "[RAGTool] 父块扩展 sub_chunk_only (too large): mom_id=%s, tokens=%d",
                    mom_id, parent_tokens,
                )

        # 3. 恢复按相关性分数降序排序
        expanded_chunks.sort(key=lambda x: get_document_relevance_score(x)[0], reverse=True)

        logger.info(
            "[RAGTool] 父块扩展完成: total=%d, full_parent=%d, window_context=%d, sub_chunk_only=%d",
            stats["total"], stats["full_parent"], stats["window_context"], stats["sub_chunk_only"],
        )

        return expanded_chunks, stats

    async def _fetch_parent_chunk(
        self,
        mom_id: str,
        tenant_id: str,
        kb_ids: list[str],
    ) -> dict | None:
        """从 docStore 获取父块完整内容。

        复用 search.py retrieval_by_children 的取值逻辑：
        settings.docStoreConn.get(mom_id, index_name(tenant_id), kb_ids)。
        该方法是同步 I/O，通过 thread_pool_exec 包装进线程池执行。

        Args:
            mom_id: 父块 ID
            tenant_id: 租户 ID
            kb_ids: 知识库 ID 列表

        Returns:
            dict | None: 父块数据，含 content_with_weight；失败返回 None
        """
        from common import settings
        from rag.nlp.search import index_name

        if not mom_id or not tenant_id:
            return None

        try:
            idx_nm = index_name(tenant_id)
            return await thread_pool_exec(settings.docStoreConn.get, mom_id, idx_nm, kb_ids)
        except Exception as e:
            logger.warning("[RAGTool] 获取父块失败: mom_id=%s, error=%s", mom_id, e)
            return None

    @staticmethod
    def _count_tokens(text: str) -> int:
        """估算文本 Token 数。

        优先使用 tiktoken（cl100k_base，与项目全局一致）；
        失败（返回 0）时回退到字符级估算，避免 0 被误判为"父块很小"。

        Args:
            text: 文本内容

        Returns:
            int: 估算的 Token 数量
        """
        if not text:
            return 0

        count = num_tokens_from_string(text)
        if count > 0:
            return count

        # 回退：字符级估算（中文字符 ~1.5 tokens/char，其他 ~0.25 tokens/char）
        chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        other_chars = len(text) - chinese_chars
        return max(1, int(chinese_chars / 1.5 + other_chars / 4))

    @staticmethod
    def _estimate_char_count(text: str, tokens: int) -> int:
        """估算 tokens 对应的字符数（用于窗口截取的边界计算）。

        Args:
            text: 参考文本（用于判断中英文占比）
            tokens: 目标 token 数

        Returns:
            int: 估算的字符数
        """
        if not text:
            return 0
        chinese_ratio = sum(1 for c in text if "\u4e00" <= c <= "\u9fff") / len(text)
        if chinese_ratio > 0.5:
            return int(tokens * 1.5)  # 中文为主
        return int(tokens * 4)  # 英文为主

    @staticmethod
    def _extract_window_context(
        parent_content: str,
        sub_chunk_content: str,
        context_window_tokens: int = 512,
    ) -> str:
        """从父块中提取子块周边的局部上下文窗口。

        在父块文本中定位子块位置，向前后各扩展 context_window_tokens 个 token。
        若子块在父块中定位失败，回退为「子块 + 父块首尾各一段窗口」。

        Args:
            parent_content: 父块完整内容
            sub_chunk_content: 子块内容
            context_window_tokens: 窗口前后各保留的 token 数

        Returns:
            str: 截取后的上下文窗口文本
        """
        sub = sub_chunk_content.strip()
        pos = parent_content.find(sub)
        if pos == -1:
            # 定位失败，回退：子块 + 父块首尾各一段窗口
            half_win = RAGTool._estimate_char_count(parent_content, context_window_tokens)
            prefix = parent_content[:half_win]
            suffix = parent_content[-half_win:] if len(parent_content) > half_win else ""
            return prefix + "\n...\n" + sub_chunk_content + "\n...\n" + suffix

        win_chars = RAGTool._estimate_char_count(parent_content, context_window_tokens)
        start = max(0, pos - win_chars)
        end = min(len(parent_content), pos + len(sub) + win_chars)

        result = parent_content[start:end]
        if start > 0:
            result = "...\n" + result
        if end < len(parent_content):
            result = result + "\n..."
        return result

    def _build_merged_parent_chunk(
        self,
        sub_chunks: list[dict],
        parent: dict,
        mom_id: str,
    ) -> dict:
        """将同一父块的多个子块合并为一条完整父块结果（去重）。

        Args:
            sub_chunks: 同一父块下的子块列表
            parent: 父块数据（含 content_with_weight）
            mom_id: 父块 ID

        Returns:
            dict: 合并后的结果，content_with_weight 为完整父块内容
        """
        merged = copy.deepcopy(sub_chunks[0])
        merged["content_with_weight"] = parent["content_with_weight"]
        merged["chunk_id"] = mom_id
        merged["_parent_expansion"] = "full_parent"
        merged["_parent_mom_id"] = mom_id
        merged["_merged_sub_chunk_count"] = len(sub_chunks)

        # 相似度取所有子块的平均值
        scores = [get_document_relevance_score(ck)[0] for ck in sub_chunks]
        avg_score = sum(scores) / len(scores)
        merged["rerank_score"] = avg_score
        merged["similarity"] = avg_score

        # 合并关键词
        all_kwds: list[str] = []
        for ck in sub_chunks:
            all_kwds.extend(ck.get("important_kwd", []))
        merged["important_kwd"] = all_kwds

        return merged

    @classmethod
    def _dynamic_truncation(
        cls,
        docs: list[dict],
        config: dict | None = None,
    ) -> tuple[list[dict], dict]:
        """动态截断算法（§4.6）。

        按相关性分数排序 → doc_id 去重 → 应用绝对阈值、相对比例、分数悬崖，
        至少保留 min_keep 条结果。

        Args:
            docs: 文档列表
            config: 截断配置，可选键：
                - relevance_threshold: 绝对分数阈值（默认 0.3）
                - relative_score_ratio: 相对分数比例（默认 0.3）
                - score_drop_threshold: 分数悬崖检测阈值（默认 0.3）
                - min_keep: 最少保留文档数（默认 3）

        Returns:
            tuple: (selected_docs, truncation_metadata)
                - truncation_metadata: {top_score, avg_score, result_count}
        """
        if config is None:
            config = {}

        relevance_threshold = config.get("relevance_threshold", cls.DEFAULT_RELEVANCE_THRESHOLD)
        relative_score_ratio = config.get("relative_score_ratio", cls.DEFAULT_RELATIVE_SCORE_RATIO)
        score_drop_threshold = config.get("score_drop_threshold", cls.DEFAULT_SCORE_DROP_THRESHOLD)
        min_keep = config.get("min_keep", cls.DEFAULT_MIN_KEEP)

        if not docs:
            return [], {"top_score": 0.0, "avg_score": 0.0, "result_count": 0}

        # 计算相关性分数并排序
        scored_docs = []
        for doc in docs:
            score, source = get_document_relevance_score(doc)
            scored_docs.append((score, source, doc))

        scored_docs.sort(key=lambda x: x[0], reverse=True)

        # doc_id 去重（保留首次出现的最高分文档）
        seen_ids: set[str] = set()
        deduped: list[tuple[float, str, dict]] = []
        for score, source, doc in scored_docs:
            doc_id = doc.get("doc_id", "")
            if doc_id and doc_id in seen_ids:
                continue
            if doc_id:
                seen_ids.add(doc_id)
            deduped.append((score, source, doc))

        if not deduped:
            return [], {"top_score": 0.0, "avg_score": 0.0, "result_count": 0}

        top_score = deduped[0][0]

        # 绝对阈值过滤
        filtered = [(s, src, d) for s, src, d in deduped if s >= relevance_threshold]

        # 相对比例过滤
        if filtered:
            relative_cutoff = top_score * relative_score_ratio
            filtered = [(s, src, d) for s, src, d in filtered if s >= relative_cutoff]

        # 分数悬崖检测
        if len(filtered) > 1:
            for i in range(1, len(filtered)):
                drop = filtered[i - 1][0] - filtered[i][0]
                if drop > score_drop_threshold:
                    filtered = filtered[:i]
                    break

        # 至少保留 min_keep 条
        if len(filtered) < min_keep:
            filtered = deduped[:min_keep]

        selected_docs = [d for _, _, d in filtered]
        avg_score = sum(s for s, _, _ in filtered) / len(filtered) if filtered else 0.0

        truncation_metadata = {
            "top_score": top_score,
            "avg_score": avg_score,
            "result_count": len(selected_docs),
        }

        logger.info(
            "[RAGTool] 动态截断: 输入=%d → 去重后=%d → 截断后=%d, top_score=%.4f, avg_score=%.4f",
            len(docs), len(deduped), len(selected_docs), top_score, avg_score,
        )

        return selected_docs, truncation_metadata

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
            rag_avg_score=0.0,  # §4.6 空结果默认值
            rag_result_count=0,  # §4.6 空结果默认值
            rag_raw_score_source="none",  # §4.6 空结果默认值
            rag_query_signature="",  # §4.6 空结果默认值
            rag_evidence_signature="",  # §4.6 空结果默认值
            rag_retrieval_top_k=0,  # §4.6 空结果默认值
            rag_rerank_top_k=0,  # §4.6 空结果默认值
            parent_expansion_total=0,  # §5.0 空结果默认值
            parent_expansion_full_parent=0,  # §5.0 空结果默认值
            parent_expansion_window=0,  # §5.0 空结果默认值
            parent_expansion_sub_only=0,  # §5.0 空结果默认值
        )


# 全局实例（可选，方便 LangGraph 节点调用）
_rag_tool_instance: RAGTool | None = None


def get_rag_tool() -> RAGTool:
    """获取 RAGTool 单例实例。"""
    global _rag_tool_instance
    if _rag_tool_instance is None:
        _rag_tool_instance = RAGTool()
    return _rag_tool_instance
