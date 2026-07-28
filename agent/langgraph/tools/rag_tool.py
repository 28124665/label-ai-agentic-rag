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
内部流程：查询预处理 → 查询重写 → 混合检索 → Rerank → 质量评估 → 内部重试

参考原有实现：
- agent/tools/retrieval.py: Retrieval 组件（混合检索 + Rerank）
- agent/component/query_rewriter.py: QueryRewriter 组件（查询重写与复杂度分析）
- agent/component/grader.py: Grader 组件（检索结果质量评估）
- agent/component/retry_controller.py: RetryController 组件（分级重试控制）
- api/utils/multilingual_reranker.py: MultilingualReranker（多语言 Rerank）
- api/utils/query_preprocessor.py: preprocess_query（查询预处理）
"""

import logging
import time
from typing import Any, Literal, TypedDict

from api.utils.multilingual_reranker import rerank as multilingual_rerank
from api.utils.query_preprocessor import preprocess_query
from agent.langgraph.utils.lang_utils import to_langgraph_lang
from common import settings

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


class RAGToolOutput(TypedDict, total=False):
    """RAG Tool 输出定义。"""

    docs: list[dict]  # 检索到的文档列表 [{content, score, source, chunk_id}]
    quality_score: float  # 质量评分 0.0 ~ 1.0
    has_relevant: bool  # 是否有相关文档
    relevant_count: int  # 相关文档数量
    top_score: float  # 最高分
    rewrite_history: list[str]  # 重写历史（用于调试）
    query_simplified: str  # 繁简转换后的查询
    detected_lang: str  # 检测到的语言（LangGraph 标识：zh_CN/zh_TW/en）
    retrieval_time_ms: int  # 检索耗时（毫秒）


class RAGTool:
    """RAG 知识库检索工具 - LangGraph 版本。

    将现有的 RAG 检索流程封装为独立工具，不依赖 Canvas。
    内部包含完整的检索优化流程：预处理、重写、检索、Rerank、评估、重试。

    设计说明：
      - 查询重写：复用 agent.component.query_rewriter 的 analyze_query_complexity
        和 _build_synonym_expansion 函数，不依赖 Canvas
      - Rerank：复用 api.utils.multilingual_reranker.rerank 函数
      - 质量评估：参考 agent.component.grader 的阈值策略，简化为独立函数
      - 内部重试：参考 agent.component.retry_controller 的 evaluate_retry_conditions，
        最多 1 次内部重写重搜
    """

    # 质量评估阈值（参考 Grader 组件的 relevance_threshold）
    RELEVANT_THRESHOLD = 0.5
    QUALITY_PASS_THRESHOLD = 0.7
    MIN_RELEVANT_DOCS = 2
    MAX_INTERNAL_RETRIES = 1

    def __init__(self):
        """初始化 RAGTool。"""
        self.component_name = "RAGTool"

    async def invoke(self, input_data: RAGToolInput) -> RAGToolOutput:
        """执行 RAG 检索流程。

        Args:
            input_data: RAG Tool 输入参数

        Returns:
            RAGToolOutput: 检索结果，包含文档列表、质量评分等
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
        cross_languages = input_data.get("cross_languages", [])
        llm_id = input_data.get("llm_id", "")

        if not query or not kb_ids:
            return self._empty_result(start_time)

        # 1. 跨语言扩展（参考历史 retrieval.py 的 cross_languages 调用）
        expanded_query = query
        if cross_languages and tenant_id and llm_id:
            try:
                from rag.prompts.generator import cross_languages

                expanded_query = await cross_languages(
                    tenant_id, llm_id, query, cross_languages
                )
                logger.info(
                    f"[RAGTool] 跨语言扩展完成: 原始='{query}', "
                    f"扩展后='{expanded_query}'"
                )
            except Exception as e:
                logger.warning(f"[RAGTool] 跨语言扩展失败，使用原查询: {e}")

        # 2. 查询预处理（语言检测 + 繁简转换）
        preprocessed = preprocess_query(expanded_query)
        query_simplified = preprocessed["query_simplified"]
        detected_lang_historical = preprocessed["query_lang"]
        detected_lang = to_langgraph_lang(detected_lang_historical)

        logger.info(
            f"[RAGTool] 查询预处理完成: 原始='{query}', "
            f"扩展='{expanded_query}', 简化='{query_simplified}', "
            f"语言={detected_lang} ({detected_lang_historical})"
        )

        # 2. 查询重写 + 检索 + Rerank + 质量评估（带内部重试）
        rewrite_history = []
        best_result = None

        for attempt in range(1 + self.MAX_INTERNAL_RETRIES):
            rewritten_query = query_simplified

            # 2a. 查询重写（可选）
            if enable_rewrite:
                rewritten_query, strategy_used = self._rewrite_query(
                    query_simplified, attempt
                )
                rewrite_history.append(
                    f"attempt={attempt}, strategy={strategy_used}, "
                    f"rewritten='{rewritten_query}'"
                )
                logger.info(
                    f"[RAGTool] 查询重写 (attempt={attempt}): "
                    f"strategy={strategy_used}, result='{rewritten_query}'"
                )

            # 2b. 混合检索（BM25 + 向量）
            try:
                kbinfos = await self._retrieve(
                    query=rewritten_query,
                    kb_ids=kb_ids,
                    tenant_id=tenant_id,
                    top_k=top_k,
                    similarity_threshold=similarity_threshold,
                    keywords_similarity_weight=keywords_similarity_weight,
                )
            except Exception as e:
                logger.error(f"[RAGTool] 检索失败: {e}")
                return self._empty_result(start_time)

            chunks = kbinfos.get("chunks", [])
            if not chunks:
                logger.warning("[RAGTool] 检索结果为空")
                return self._empty_result(start_time)

            logger.info(f"[RAGTool] 检索到 {len(chunks)} 个候选文档")

            # 2c. Rerank 精排（可选）
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
                        logger.info(
                            f"[RAGTool] Rerank 完成: {len(chunks)} 条结果"
                        )
                    else:
                        logger.info("[RAGTool] Rerank 模型未配置，跳过 Rerank")
                except Exception as e:
                    logger.warning(
                        f"[RAGTool] Rerank 失败，使用原始排序: {e}"
                    )

            # 2d. 质量评估（参考 Grader 组件的阈值策略）
            quality_score, has_relevant, relevant_count, top_score = (
                self._evaluate_quality(chunks)
            )

            logger.info(
                f"[RAGTool] 质量评估: score={quality_score:.2f}, "
                f"relevant={relevant_count}, top={top_score:.2f}"
            )

            # 保存当前结果
            best_result = {
                "chunks": chunks,
                "quality_score": quality_score,
                "has_relevant": has_relevant,
                "relevant_count": relevant_count,
                "top_score": top_score,
            }

            # 2e. 内部重试决策（参考 RetryController 的 evaluate_retry_conditions）
            if self._should_internal_retry(
                has_relevant, relevant_count, attempt
            ):
                logger.info(
                    f"[RAGTool] 质量不达标，触发内部重试 "
                    f"(attempt={attempt}, score={quality_score:.2f})"
                )
                continue
            else:
                break

        # 3. 格式化输出
        chunks = best_result["chunks"]
        docs = self._format_docs(chunks)

        retrieval_time_ms = int((time.time() - start_time) * 1000)

        return RAGToolOutput(
            docs=docs,
            quality_score=best_result["quality_score"],
            has_relevant=best_result["has_relevant"],
            relevant_count=best_result["relevant_count"],
            top_score=best_result["top_score"],
            rewrite_history=rewrite_history,
            query_simplified=query_simplified,
            detected_lang=detected_lang,
            retrieval_time_ms=retrieval_time_ms,
        )

    def _rewrite_query(self, query: str, retry_count: int) -> tuple[str, str]:
        """查询重写（参考 QueryRewriter 组件）。

        根据查询复杂度选择合适的重写策略：
        - simple/boolean → synonym_rewrite（同义词扩展）
        - multi_aspect → sub_query_decompose（子查询拆解，当前简化为同义词）
        - factual → hyde（HyDE，当前简化为同义词）
        - complex → 三种策略依次轮转

        Args:
            query: 查询文本（已繁简转换）
            retry_count: 当前重试次数（用于策略轮转）

        Returns:
            tuple: (rewritten_query, strategy_used)
        """
        from agent.component.query_rewriter import (
            analyze_query_complexity,
            COMPLEXITY_STRATEGIES,
            DEFAULT_STRATEGIES,
            STRATEGY_SYNONYM_REWRITE,
            STRATEGY_SUB_QUERY_DECOMPOSE,
            STRATEGY_HYDE,
            _build_synonym_expansion,
        )

        # 分析查询复杂度
        complexity, strategies = analyze_query_complexity(query)
        logger.debug(
            f"[RAGTool] 查询复杂度: {complexity}, 策略列表: {strategies}"
        )

        # 根据重试次数轮转策略
        if not strategies:
            strategies = list(COMPLEXITY_STRATEGIES.get(complexity, DEFAULT_STRATEGIES))
        selected_strategy = strategies[retry_count % len(strategies)]

        if selected_strategy == STRATEGY_SYNONYM_REWRITE:
            # 同义词扩展：从本地词典查找同义词，用 OR 拼接
            expansion = _build_synonym_expansion(query, topn=3)
            if expansion.get("synonyms"):
                expanded_terms = [query]
                for term, syns in expansion["synonyms"].items():
                    for syn in syns:
                        expanded_terms.append(f'"{syn}"')
                return " OR ".join(expanded_terms), selected_strategy
            return query, selected_strategy

        elif selected_strategy == STRATEGY_SUB_QUERY_DECOMPOSE:
            # 子查询拆解：当前简化为同义词扩展（完整实现需要 LLM）
            # TODO: 集成 LLM 进行真正的子查询拆解 + RRF 合并
            expansion = _build_synonym_expansion(query, topn=3)
            if expansion.get("synonyms"):
                expanded_terms = [query]
                for term, syns in expansion["synonyms"].items():
                    for syn in syns:
                        expanded_terms.append(f'"{syn}"')
                return " OR ".join(expanded_terms), selected_strategy
            return query, selected_strategy

        elif selected_strategy == STRATEGY_HYDE:
            # HyDE：当前简化为同义词扩展（完整实现需要 LLM 生成假设答案）
            # TODO: 集成 LLM 生成假设性答案，用该答案代替原始查询进行检索
            expansion = _build_synonym_expansion(query, topn=3)
            if expansion.get("synonyms"):
                expanded_terms = [query]
                for term, syns in expansion["synonyms"].items():
                    for syn in syns:
                        expanded_terms.append(f'"{syn}"')
                return " OR ".join(expanded_terms), selected_strategy
            return query, selected_strategy

        return query, selected_strategy

    def _get_rerank_model(
        self, rerank_id: str, tenant_id: str, kb_ids: list[str]
    ) -> Any:
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
            rerank_model_config = get_model_config_by_type_and_name(
                actual_tenant_id, LLMType.RERANK, rerank_id
            )
            if rerank_model_config:
                return LLMBundle(actual_tenant_id, rerank_model_config)
            return None
        except Exception as e:
            logger.warning(f"[RAGTool] 获取 Rerank 模型失败: {e}")
            return None

    def _evaluate_quality(
        self, chunks: list[dict]
    ) -> tuple[float, bool, int, float]:
        """评估检索质量（参考 Grader 组件的阈值策略）。

        参考 agent/component/grader.py 的评估逻辑：
        - 使用 similarity/rerank_score 作为相关性分数
        - 分数 >= RELEVANT_THRESHOLD (0.5) 视为相关
        - 质量评分使用平均分数

        Args:
            chunks: 检索到的文档块列表

        Returns:
            tuple: (quality_score, has_relevant, relevant_count, top_score)
        """
        if not chunks:
            return 0.0, False, 0, 0.0

        # 提取所有分数（优先使用 rerank_score，其次 similarity，最后 score）
        scores = []
        for chunk in chunks:
            score = chunk.get(
                "rerank_score",
                chunk.get("similarity", chunk.get("score", 0.0)),
            )
            scores.append(score)

        if not scores:
            return 0.0, False, 0, 0.0

        top_score = max(scores)
        relevant_scores = [
            s for s in scores if s >= self.RELEVANT_THRESHOLD
        ]
        relevant_count = len(relevant_scores)
        has_relevant = relevant_count > 0

        # 质量评分：使用平均分数
        quality_score = sum(scores) / len(scores) if scores else 0.0

        return quality_score, has_relevant, relevant_count, top_score

    def _should_internal_retry(
        self, has_relevant: bool, relevant_count: int, attempt: int
    ) -> bool:
        """判断是否应触发内部重试（参考 RetryController）。

        参考 agent/component/retry_controller.py 的 evaluate_retry_conditions：
        - 条件：has_relevant=False 或 relevant_count < MIN_RELEVANT_DOCS
        - 且 attempt < MAX_INTERNAL_RETRIES

        Args:
            has_relevant: 是否有相关文档
            relevant_count: 相关文档数量
            attempt: 当前尝试次数（0-based）

        Returns:
            bool: 是否应重试
        """
        if attempt >= self.MAX_INTERNAL_RETRIES:
            return False

        needs_retry = (
            not has_relevant or relevant_count < self.MIN_RELEVANT_DOCS
        )
        return needs_retry

    async def _retrieve(
        self,
        query: str,
        kb_ids: list[str],
        tenant_id: str,
        top_k: int,
        similarity_threshold: float,
        keywords_similarity_weight: float,
    ) -> dict:
        """执行混合检索（BM25 + 向量）。

        参考 agent/tools/retrieval.py 的 _retrieve_kb 方法。

        Args:
            query: 查询文本（已繁简转换）
            kb_ids: 知识库 ID 列表
            tenant_id: 租户 ID
            top_k: 返回文档数量
            similarity_threshold: 相似度阈值
            keywords_similarity_weight: 关键词相似度权重

        Returns:
            dict: 检索结果，包含 chunks 和 doc_aggs
        """
        from api.db.services.knowledgebase_service import KnowledgebaseService
        from api.db.services.llm_service import LLMBundle
        from api.db.joint_services.tenant_model_service import (
            get_model_config_by_type_and_name,
        )
        from common.constants import LLMType

        # 获取知识库信息
        kbs = KnowledgebaseService.get_by_ids(kb_ids)
        if not kbs:
            raise Exception("No dataset is selected.")

        # 获取 Embedding 模型
        embd_nms = list(set([kb.embd_id for kb in kbs]))
        assert len(embd_nms) == 1, "Knowledge bases use different embedding models."

        actual_tenant_id = tenant_id or kbs[0].tenant_id
        embd_model_config = get_model_config_by_type_and_name(
            actual_tenant_id, LLMType.EMBEDDING, embd_nms[0]
        )
        embd_mdl = LLMBundle(actual_tenant_id, embd_model_config)

        # 执行检索
        kbinfos = await settings.retriever.retrieval(
            query,
            embd_mdl,
            [kb.tenant_id for kb in kbs],
            kb_ids,
            1,  # page
            top_k,
            similarity_threshold,
            1 - keywords_similarity_weight,
            aggs=False,
        )

        return kbinfos

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
                "content": chunk.get(
                    "content_with_weight", chunk.get("content", "")
                ),
                "score": chunk.get(
                    "rerank_score",
                    chunk.get("similarity", chunk.get("score", 0.0)),
                ),
                "source": chunk.get("docnm_kwd", "unknown"),
                "chunk_id": chunk.get("chunk_id", ""),
                "doc_id": chunk.get("doc_id", ""),
            }
            docs.append(doc)
        return docs

    def _empty_result(self, start_time: float) -> RAGToolOutput:
        """返回空结果。

        Args:
            start_time: 开始时间戳

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
            rewrite_history=[],
            query_simplified="",
            detected_lang="zh_CN",
            retrieval_time_ms=retrieval_time_ms,
        )


# 全局实例（可选，方便 LangGraph 节点调用）
_rag_tool_instance: RAGTool | None = None


def get_rag_tool() -> RAGTool:
    """获取 RAGTool 单例实例。"""
    global _rag_tool_instance
    if _rag_tool_instance is None:
        _rag_tool_instance = RAGTool()
    return _rag_tool_instance
