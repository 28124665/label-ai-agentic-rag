"""RAGAS 评估器封装。

将 RAG 管道输出转换为 RAGAS 兼容格式，执行分层质量评估。
支持检索质量（Context Precision / Recall）和生成质量（Faithfulness / Answer Relevancy）的独立评估。

使用方式:
    from test.agent.langgraph.evaluation import RAGASEvaluator

    evaluator = RAGASEvaluator()
    result = await evaluator.evaluate(
        question="用户问题",
        answer="生成答案",
        contexts=["检索上下文1", "检索上下文2"],
        ground_truth="参考答案（可选）",
    )
    print(result.to_dict())
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_openai import ChatOpenAI
from ragas import evaluate as ragas_evaluate
from ragas.metrics import (
    answer_relevancy,
    context_precision,
    context_recall,
    faithfulness,
)

logger = logging.getLogger(__name__)


@dataclass
class RAGASResult:
    """RAGAS 评估结果。

    Attributes:
        faithfulness: 忠实度（答案是否完全基于检索上下文，0-1）
        answer_relevancy: 答案相关性（答案是否直接回答用户问题，0-1）
        context_precision: 上下文精确率（检索到的上下文中真正有用的占比，0-1）
        context_recall: 上下文召回率（检索覆盖了参考答案中多少关键信息，0-1）
        metadata: 评估元数据（耗时、错误信息等）
    """

    faithfulness: float = 0.0
    answer_relevancy: float = 0.0
    context_precision: float = 0.0
    context_recall: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "faithfulness": round(self.faithfulness, 4),
            "answer_relevancy": round(self.answer_relevancy, 4),
            "context_precision": round(self.context_precision, 4),
            "context_recall": round(self.context_recall, 4),
            "metadata": self.metadata,
        }

    @property
    def retrieval_score(self) -> float:
        """检索综合得分（Context Precision + Recall 均值）。"""
        return (self.context_precision + self.context_recall) / 2

    @property
    def generation_score(self) -> float:
        """生成综合得分（Faithfulness + Answer Relevancy 均值）。"""
        return (self.faithfulness + self.answer_relevancy) / 2

    @property
    def overall_score(self) -> float:
        """整体综合得分。"""
        return (self.retrieval_score + self.generation_score) / 2


@dataclass
class EvaluationData:
    """RAGAS 评估输入数据。

    Attributes:
        question: 用户问题
        answer: 模型生成的答案
        contexts: 检索到的上下文列表（每个元素为文本片段）
        ground_truth: 参考答案（可选，用于 Context Recall 和 Answer Correctness）
    """

    question: str
    answer: str
    contexts: list[str]
    ground_truth: str = ""


class RAGASEvaluator:
    """RAGAS 评估器。

    封装 RAGAS 评估框架，提供简洁的评估接口。
    支持从 RAGToolOutput 和 AgentState 中提取数据并执行评估。

    Usage:
        evaluator = RAGASEvaluator()

        # 1. 直接评估
        result = await evaluator.evaluate(
            question="什么是RAG？",
            answer="RAG是检索增强生成...",
            contexts=["RAG结合了检索和生成...", "RAG可以减少幻觉..."],
        )

        # 2. 从 RAGTool 输出评估
        result = await evaluator.evaluate_from_rag_output(
            rag_output=rag_tool_output,
            answer=generated_answer,
        )

        # 3. 批量评估
        results = await evaluator.evaluate_batch([
            EvaluationData(question="Q1", answer="A1", contexts=["C1"]),
            EvaluationData(question="Q2", answer="A2", contexts=["C2"]),
        ])
    """

    # 默认模型配置
    DEFAULT_MODEL = "gpt-4o-mini"
    DEFAULT_TEMPERATURE = 0.0
    DEFAULT_MAX_TOKENS = 512

    def __init__(
        self,
        model_name: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        """初始化评估器。

        Args:
            model_name: LLM 模型名称（用于 RAGAS LLM-as-Judge 评分）
            temperature: 模型温度
            max_tokens: 最大 token 数
            base_url: API 地址（默认使用 OPENAI_BASE_URL 环境变量）
            api_key: API 密钥（默认使用 OPENAI_API_KEY 环境变量）
        """
        self._model_name = model_name or self.DEFAULT_MODEL
        self._temperature = temperature or self.DEFAULT_TEMPERATURE
        self._max_tokens = max_tokens or self.DEFAULT_MAX_TOKENS
        self._base_url = base_url
        self._api_key = api_key
        self._llm: ChatOpenAI | None = None

    @property
    def llm(self) -> ChatOpenAI:
        """懒加载 LLM 实例。"""
        if self._llm is None:
            kwargs: dict[str, Any] = {
                "model": self._model_name,
                "temperature": self._temperature,
                "max_tokens": self._max_tokens,
            }
            if self._base_url:
                kwargs["base_url"] = self._base_url
            if self._api_key:
                kwargs["api_key"] = self._api_key
            self._llm = ChatOpenAI(**kwargs)
        return self._llm

    async def evaluate(
        self,
        question: str,
        answer: str,
        contexts: list[str],
        ground_truth: str = "",
    ) -> RAGASResult:
        """执行单次 RAGAS 评估。

        Args:
            question: 用户问题
            answer: 模型生成的答案
            contexts: 检索到的上下文列表
            ground_truth: 参考答案（可选）

        Returns:
            RAGASResult: 评估结果
        """
        metrics = self._build_metrics(has_ground_truth=bool(ground_truth))

        dataset = {
            "question": [question],
            "answer": [answer],
            "contexts": [contexts],
        }
        if ground_truth:
            dataset["ground_truth"] = [ground_truth]

        try:
            result = ragas_evaluate(
                dataset=dataset,
                metrics=metrics,
                llm=self.llm,
            )
            return self._parse_result(result, has_ground_truth=bool(ground_truth))
        except Exception as e:
            logger.error(f"[RAGASEvaluator] 评估失败: {e}")
            return RAGASResult(
                metadata={"error": str(e)},
            )

    async def evaluate_batch(
        self,
        data_list: list[EvaluationData],
    ) -> list[RAGASResult]:
        """批量评估。

        Args:
            data_list: 评估数据列表

        Returns:
            list[RAGASResult]: 评估结果列表
        """
        has_ground_truth = any(d.ground_truth for d in data_list)
        metrics = self._build_metrics(has_ground_truth=has_ground_truth)

        dataset = {
            "question": [d.question for d in data_list],
            "answer": [d.answer for d in data_list],
            "contexts": [d.contexts for d in data_list],
        }
        if has_ground_truth:
            dataset["ground_truth"] = [d.ground_truth or "" for d in data_list]

        try:
            result = ragas_evaluate(
                dataset=dataset,
                metrics=metrics,
                llm=self.llm,
            )
            return self._parse_batch_result(result, len(data_list), has_ground_truth)
        except Exception as e:
            logger.error(f"[RAGASEvaluator] 批量评估失败: {e}")
            return [
                RAGASResult(metadata={"error": str(e)})
                for _ in data_list
            ]

    async def evaluate_from_rag_output(
        self,
        rag_output: dict[str, Any],
        answer: str,
        ground_truth: str = "",
    ) -> RAGASResult:
        """从 RAGTool 输出执行评估。

        Args:
            rag_output: RAGTool.invoke() 返回的 RAGToolOutput 字典
            answer: 生成器（LLM）产生的最终答案
            ground_truth: 参考答案（可选）

        Returns:
            RAGASResult: 评估结果
        """
        question = rag_output.get("query_simplified", "") or ""
        docs = rag_output.get("docs", []) or []
        contexts = [doc.get("content", "") for doc in docs if doc.get("content")]

        return await self.evaluate(
            question=question,
            answer=answer,
            contexts=contexts,
            ground_truth=ground_truth,
        )

    def _build_metrics(self, has_ground_truth: bool = False) -> list:
        """构建指标列表。

        默认包含无需参考答案的指标；如果提供了参考答案，追加 Context Recall。

        Args:
            has_ground_truth: 是否提供了参考答案

        Returns:
            list: RAGAS 指标列表
        """
        metrics = [
            faithfulness,
            answer_relevancy,
            context_precision,
        ]
        if has_ground_truth:
            metrics.append(context_recall)
        return metrics

    def _parse_result(
        self,
        result: Any,
        has_ground_truth: bool = False,
    ) -> RAGASResult:
        """解析单次评估结果。

        Args:
            result: RAGAS evaluate 返回的 EvaluationResult
            has_ground_truth: 是否包含参考答案

        Returns:
            RAGASResult: 解析后的结果
        """
        result_dict = result if isinstance(result, dict) else result._asdict()
        return RAGASResult(
            faithfulness=float(result_dict.get("faithfulness", 0.0)),
            answer_relevancy=float(result_dict.get("answer_relevancy", 0.0)),
            context_precision=float(result_dict.get("context_precision", 0.0)),
            context_recall=float(result_dict.get("context_recall", 0.0)) if has_ground_truth else 0.0,
        )

    def _parse_batch_result(
        self,
        result: Any,
        count: int,
        has_ground_truth: bool = False,
    ) -> list[RAGASResult]:
        """解析批量评估结果。

        Args:
            result: RAGAS evaluate 返回的 EvaluationResult
            count: 数据条数
            has_ground_truth: 是否包含参考答案

        Returns:
            list[RAGASResult]: 解析后的结果列表
        """
        result_dict = result if isinstance(result, dict) else result._asdict()
        results = []
        for i in range(count):
            results.append(RAGASResult(
                faithfulness=float(result_dict.get("faithfulness", [0.0])[i] if isinstance(result_dict.get("faithfulness"), list) else 0.0),
                answer_relevancy=float(result_dict.get("answer_relevancy", [0.0])[i] if isinstance(result_dict.get("answer_relevancy"), list) else 0.0),
                context_precision=float(result_dict.get("context_precision", [0.0])[i] if isinstance(result_dict.get("context_precision"), list) else 0.0),
                context_recall=float(result_dict.get("context_recall", [0.0])[i] if has_ground_truth and isinstance(result_dict.get("context_recall"), list) else 0.0),
            ))
        return results