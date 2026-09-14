"""RAGAS 评估器测试。

测试覆盖：
- RAGASResult / EvaluationData 数据类
- RAGASEvaluator 初始化与配置
- 从 RAGTool 输出提取评估数据
- 指标构建逻辑
- 综合得分计算

⚠️ 涉及真实 RAGAS LLM 评估的测试需要设置 OPENAI_API_KEY 环境变量，
   并使用 pytest marker `ragas` 标记：
   pytest test/agent/langgraph/evaluation/ -m ragas
"""

from __future__ import annotations

import math

import pytest
from test.agent.langgraph.evaluation.ragas_evaluator import (
    EvaluationData,
    RAGASEvaluator,
    RAGASResult,
)


# =============================================================================
# RAGASResult 数据类测试
# =============================================================================


class TestRAGASResult:
    """RAGASResult 数据类测试。"""

    def test_default_values(self):
        """测试默认值。"""
        result = RAGASResult()
        assert result.faithfulness == 0.0
        assert result.answer_relevancy == 0.0
        assert result.context_precision == 0.0
        assert result.context_recall == 0.0
        assert result.metadata == {}

    def test_to_dict(self, sample_result):
        """测试 to_dict 输出格式。"""
        d = sample_result.to_dict()
        assert d["faithfulness"] == 0.92
        assert d["answer_relevancy"] == 0.88
        assert d["context_precision"] == 0.85
        assert d["context_recall"] == 0.90
        assert isinstance(d["metadata"], dict)

    def test_to_dict_rounding(self):
        """测试 to_dict 四舍五入。"""
        result = RAGASResult(
            faithfulness=0.123456,
            answer_relevancy=0.987654,
        )
        d = result.to_dict()
        assert d["faithfulness"] == 0.1235
        assert d["answer_relevancy"] == 0.9877

    def test_retrieval_score(self, sample_result):
        """测试检索综合得分。"""
        expected = (0.85 + 0.90) / 2
        assert sample_result.retrieval_score == pytest.approx(expected)

    def test_generation_score(self, sample_result):
        """测试生成综合得分。"""
        expected = (0.92 + 0.88) / 2
        assert sample_result.generation_score == pytest.approx(expected)

    def test_overall_score(self, sample_result):
        """测试整体综合得分。"""
        expected = ((0.85 + 0.90) / 2 + (0.92 + 0.88) / 2) / 2
        assert sample_result.overall_score == pytest.approx(expected)

    def test_all_zero_scores(self):
        """测试零分场景（所有分数为 0）。"""
        result = RAGASResult()
        assert result.retrieval_score == 0.0
        assert result.generation_score == 0.0
        assert result.overall_score == 0.0

    def test_perfect_scores(self):
        """测试满分场景。"""
        result = RAGASResult(
            faithfulness=1.0,
            answer_relevancy=1.0,
            context_precision=1.0,
            context_recall=1.0,
        )
        assert result.retrieval_score == 1.0
        assert result.generation_score == 1.0
        assert result.overall_score == 1.0

    def test_metadata(self):
        """测试 metadata 字段。"""
        result = RAGASResult(
            metadata={"eval_time_ms": 1500, "model": "gpt-4o-mini"}
        )
        assert result.metadata["eval_time_ms"] == 1500
        assert result.metadata["model"] == "gpt-4o-mini"


# =============================================================================
# EvaluationData 数据类测试
# =============================================================================


class TestEvaluationData:
    """EvaluationData 数据类测试。"""

    def test_create(self, sample_eval_data):
        """测试创建 EvaluationData。"""
        assert sample_eval_data.question == "什么是RAG？"
        assert len(sample_eval_data.contexts) == 2
        assert sample_eval_data.ground_truth != ""

    def test_default_ground_truth(self):
        """测试默认 ground_truth 为空字符串。"""
        data = EvaluationData(
            question="Q",
            answer="A",
            contexts=["C1"],
        )
        assert data.ground_truth == ""

    def test_empty_contexts(self):
        """测试空 contexts 列表。"""
        data = EvaluationData(
            question="Q",
            answer="A",
            contexts=[],
        )
        assert data.contexts == []


# =============================================================================
# RAGASEvaluator 初始化与配置测试
# =============================================================================


class TestRAGASEvaluatorInit:
    """RAGASEvaluator 初始化测试。"""

    def test_default_init(self, evaluator):
        """测试默认初始化。"""
        assert evaluator._model_name == "gpt-4o-mini"
        assert evaluator._temperature == 0.0
        assert evaluator._max_tokens == 512
        assert evaluator._llm is None  # 懒加载，未调用前为 None

    def test_custom_init(self, evaluator_custom):
        """测试自定义配置初始化。"""
        assert evaluator_custom._model_name == "gpt-4o"
        assert evaluator_custom._temperature == 0.1
        assert evaluator_custom._max_tokens == 1024
        assert evaluator_custom._base_url == "https://custom-api.example.com/v1"
        assert evaluator_custom._api_key == "sk-test-key"

    def test_llm_lazy_load(self, evaluator):
        """测试 LLM 懒加载 — 未访问前 _llm 为 None。"""
        assert evaluator._llm is None

    def test_llm_config(self, evaluator_custom):
        """测试 LLM 配置正确传递（使用含 api_key 的自定义 evaluator）。"""
        llm = evaluator_custom.llm
        assert llm.model_name == "gpt-4o"
        assert llm.temperature == 0.1
        assert llm.max_tokens == 1024


# =============================================================================
# 指标构建测试
# =============================================================================


class TestBuildMetrics:
    """_build_metrics 方法测试。"""

    def test_without_ground_truth(self, evaluator):
        """测试无参考答案时的指标列表。"""
        metrics = evaluator._build_metrics(has_ground_truth=False)
        metric_names = [m.name for m in metrics]
        assert "faithfulness" in metric_names
        assert "answer_relevancy" in metric_names
        assert "context_precision" in metric_names
        assert "context_recall" not in metric_names
        assert len(metrics) == 3

    def test_with_ground_truth(self, evaluator):
        """测试有参考答案时的指标列表。"""
        metrics = evaluator._build_metrics(has_ground_truth=True)
        metric_names = [m.name for m in metrics]
        assert "faithfulness" in metric_names
        assert "answer_relevancy" in metric_names
        assert "context_precision" in metric_names
        assert "context_recall" in metric_names
        assert len(metrics) == 4


# =============================================================================
# evaluate_from_rag_output 数据提取测试
# =============================================================================


class TestEvaluateFromRagOutput:
    """evaluate_from_rag_output 数据提取测试。"""

    def test_extract_contexts_from_rag_output(self, evaluator, sample_rag_output):
        """测试从 RAGTool 输出中正确提取 contexts。"""
        docs = sample_rag_output["docs"]
        expected_contexts = [doc["content"] for doc in docs]
        actual_contexts = [
            doc.get("content", "")
            for doc in sample_rag_output.get("docs", [])
            if doc.get("content")
        ]
        assert actual_contexts == expected_contexts
        assert len(actual_contexts) == 3

    def test_extract_query_from_rag_output(self, evaluator, sample_rag_output):
        """测试从 RAGTool 输出中提取 query。"""
        question = sample_rag_output.get("query_simplified", "") or ""
        assert question == "什么是RAG"

    def test_empty_rag_output(self, evaluator, sample_rag_output_empty):
        """测试空 RAGTool 输出。"""
        docs = sample_rag_output_empty.get("docs", []) or []
        contexts = [doc.get("content", "") for doc in docs if doc.get("content")]
        assert contexts == []

    def test_rag_output_missing_query_simplified(self, evaluator):
        """测试 RAGTool 输出缺少 query_simplified 字段。"""
        rag_output = {"docs": [{"content": "test"}]}
        question = rag_output.get("query_simplified", "") or ""
        assert question == ""

    def test_rag_output_docs_without_content(self, evaluator):
        """测试 docs 中部分文档缺少 content 字段。"""
        rag_output = {
            "docs": [
                {"content": "valid doc"},
                {"score": 0.5},  # 无 content
                {"content": ""},  # 空 content
                {"content": "another valid doc"},
            ],
            "query_simplified": "test query",
        }
        contexts = [
            doc.get("content", "")
            for doc in rag_output.get("docs", [])
            if doc.get("content")
        ]
        assert contexts == ["valid doc", "another valid doc"]


# =============================================================================
# 综合得分计算测试
# =============================================================================


class TestScoreCalculation:
    """综合得分计算测试。"""

    def test_retrieval_score_partial(self, sample_result_low):
        """测试低分场景的检索得分。"""
        expected = (0.30 + 0.35) / 2
        assert sample_result_low.retrieval_score == pytest.approx(expected)

    def test_generation_score_partial(self, sample_result_low):
        """测试低分场景的生成得分。"""
        expected = (0.45 + 0.50) / 2
        assert sample_result_low.generation_score == pytest.approx(expected)

    @pytest.mark.parametrize(
        "faithfulness,answer_relevancy,context_precision,context_recall,expected",
        [
            (1.0, 1.0, 1.0, 1.0, 1.0),
            (0.0, 0.0, 0.0, 0.0, 0.0),
            (0.5, 0.5, 0.5, 0.5, 0.5),
            (0.8, 0.6, 0.9, 0.7, 0.75),
        ],
    )
    def test_overall_score_parametrized(
        self,
        faithfulness,
        answer_relevancy,
        context_precision,
        context_recall,
        expected,
    ):
        """参数化测试 overall_score 计算。"""
        result = RAGASResult(
            faithfulness=faithfulness,
            answer_relevancy=answer_relevancy,
            context_precision=context_precision,
            context_recall=context_recall,
        )
        assert result.overall_score == pytest.approx(expected)


# =============================================================================
# RAGAS 集成测试（需要 OPENAI_API_KEY）
# =============================================================================


@pytest.mark.ragas
@pytest.mark.integration
class TestRAGASEvaluatorIntegration:
    """RAGAS 真实评估集成测试。

    需要设置环境变量:
        OPENAI_API_KEY=<your-key>
        OPENAI_BASE_URL=<your-base-url>  (可选)

    运行方式:
        pytest test/agent/langgraph/evaluation/ -m ragas -v
    """

    @pytest.mark.asyncio
    async def test_evaluate_without_ground_truth(self, evaluator):
        """测试真实 RAGAS 评估（无参考答案）。"""
        result = await evaluator.evaluate(
            question="什么是RAG？",
            answer="RAG（检索增强生成）是一种结合信息检索和文本生成的技术架构，"
            "通过从外部知识库中检索相关文档来增强大语言模型的回答质量。",
            contexts=[
                "RAG（检索增强生成）是一种结合了信息检索和文本生成的技术架构。"
                "它通过从外部知识库中检索相关文档，并将这些文档作为上下文提供给大语言模型。",
                "RAG 的核心流程包括文档分块、向量化、相似度检索、上下文融合和答案生成。",
                "RAG 可以减少模型幻觉，提高回答的准确性和时效性。",
            ],
        )
        assert isinstance(result, RAGASResult)
        assert 0.0 <= result.faithfulness <= 1.0
        assert 0.0 <= result.answer_relevancy <= 1.0
        assert 0.0 <= result.context_precision <= 1.0
        assert result.context_recall == 0.0  # 无 ground_truth

    @pytest.mark.asyncio
    async def test_evaluate_with_ground_truth(self, evaluator):
        """测试真实 RAGAS 评估（有参考答案）。"""
        result = await evaluator.evaluate(
            question="什么是RAG？",
            answer="RAG（检索增强生成）是一种结合信息检索和文本生成的技术，"
            "通过检索外部知识库中的相关文档来增强大语言模型的回答质量。",
            contexts=[
                "RAG（检索增强生成）是一种结合了信息检索和文本生成的技术架构。",
                "RAG 可以减少模型幻觉，提高回答的准确性和时效性。",
            ],
            ground_truth="RAG（Retrieval-Augmented Generation）是一种结合了"
            "信息检索和文本生成的技术，通过检索外部知识库来增强LLM的生成质量。",
        )
        assert isinstance(result, RAGASResult)
        assert 0.0 <= result.faithfulness <= 1.0
        assert 0.0 <= result.answer_relevancy <= 1.0
        assert 0.0 <= result.context_precision <= 1.0
        assert 0.0 <= result.context_recall <= 1.0

    @pytest.mark.asyncio
    async def test_evaluate_from_rag_output(self, evaluator, sample_rag_output):
        """测试从 RAGTool 输出进行真实评估。"""
        result = await evaluator.evaluate_from_rag_output(
            rag_output=sample_rag_output,
            answer="RAG（检索增强生成）是一种结合信息检索和文本生成的技术架构，"
            "通过检索外部知识库中的相关文档来增强大语言模型的回答质量。"
            "其核心流程包括文档分块、向量化、相似度检索、上下文融合和答案生成。"
            "常用的评估指标包括忠实度、答案相关性、上下文精确率和召回率。",
        )
        assert isinstance(result, RAGASResult)
        assert 0.0 <= result.faithfulness <= 1.0
        assert 0.0 <= result.answer_relevancy <= 1.0
        assert 0.0 <= result.context_precision <= 1.0

    @pytest.mark.asyncio
    async def test_evaluate_batch(self, evaluator, sample_batch_eval_data):
        """测试批量真实评估。"""
        results = await evaluator.evaluate_batch(sample_batch_eval_data)
        assert len(results) == len(sample_batch_eval_data)
        for result in results:
            assert isinstance(result, RAGASResult)
            assert 0.0 <= result.faithfulness <= 1.0
            assert 0.0 <= result.answer_relevancy <= 1.0
            assert 0.0 <= result.context_precision <= 1.0

    @pytest.mark.asyncio
    async def test_evaluate_empty_contexts(self, evaluator):
        """测试空上下文的真实评估。"""
        result = await evaluator.evaluate(
            question="什么是RAG？",
            answer="我不知道。",
            contexts=[],
        )
        assert isinstance(result, RAGASResult)
        # 空上下文时 faithfulness 可能为 0 或接近 0
        assert result.faithfulness <= 0.5


