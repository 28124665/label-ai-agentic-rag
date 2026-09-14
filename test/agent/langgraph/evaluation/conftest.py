"""RAGAS 评估测试 fixtures。

提供模拟 RAGTool 输出、评估数据等共享测试资源。
RAGAS 真实 LLM 评估测试需要设置 OPENAI_API_KEY 环境变量。
"""

from __future__ import annotations

import pytest

from test.agent.langgraph.evaluation.ragas_evaluator import (
    EvaluationData,
    RAGASEvaluator,
    RAGASResult,
)


# =============================================================================
# Mock RAGTool 输出 fixtures
# =============================================================================


@pytest.fixture
def sample_rag_output() -> dict:
    """模拟 RAGTool 输出 — 高质量检索结果。"""
    return {
        "docs": [
            {
                "content": "RAG（检索增强生成）是一种结合了信息检索和文本生成的技术架构。"
                "它通过从外部知识库中检索相关文档，并将这些文档作为上下文提供给大语言模型，"
                "从而减少模型幻觉，提高回答的准确性和时效性。",
                "score": 0.92,
                "source": "技术文档/rag-overview.md",
                "chunk_id": "chunk_001",
                "doc_id": "doc_rag_001",
            },
            {
                "content": "RAG 的核心流程包括：1) 文档分块和向量化；2) 用户查询编码；"
                "3) 相似度检索；4) 上下文融合；5) 答案生成。其中向量检索通常使用 FAISS 或 Milvus。",
                "score": 0.85,
                "source": "技术文档/rag-pipeline.md",
                "chunk_id": "chunk_002",
                "doc_id": "doc_rag_002",
            },
            {
                "content": "RAG 系统的评估指标包括忠实度（Faithfulness）、答案相关性（Answer Relevancy）、"
                "上下文精确率（Context Precision）和上下文召回率（Context Recall）。",
                "score": 0.78,
                "source": "技术文档/rag-evaluation.md",
                "chunk_id": "chunk_003",
                "doc_id": "doc_rag_003",
            },
        ],
        "quality_score": 0.85,
        "has_relevant": True,
        "relevant_count": 3,
        "top_score": 0.92,
        "query_simplified": "什么是RAG",
        "detected_lang": "zh_CN",
        "retrieval_time_ms": 120,
        "retrieval_error_code": "",
        "retrieval_mode_used": "local",
    }


@pytest.fixture
def sample_rag_output_empty() -> dict:
    """模拟 RAGTool 输出 — 无检索结果。"""
    return {
        "docs": [],
        "quality_score": 0.0,
        "has_relevant": False,
        "relevant_count": 0,
        "top_score": 0.0,
        "query_simplified": "未知问题",
        "detected_lang": "zh_CN",
        "retrieval_time_ms": 80,
        "retrieval_error_code": "",
        "retrieval_mode_used": "local",
    }


# =============================================================================
# 评估数据 fixtures
# =============================================================================


@pytest.fixture
def sample_eval_data() -> EvaluationData:
    """单条评估数据。"""
    return EvaluationData(
        question="什么是RAG？",
        answer="RAG（检索增强生成）是一种结合信息检索和文本生成的技术，"
        "通过检索外部知识库中的相关文档来增强大语言模型的回答质量。",
        contexts=[
            "RAG（检索增强生成）是一种结合了信息检索和文本生成的技术架构。",
            "RAG 的核心流程包括文档分块、向量化、相似度检索、上下文融合和答案生成。",
        ],
        ground_truth="RAG 是 Retrieval-Augmented Generation 的缩写，"
        "通过检索外部知识来增强 LLM 生成质量的技术。",
    )


@pytest.fixture
def sample_eval_data_no_ground_truth() -> EvaluationData:
    """评估数据 — 无参考答案。"""
    return EvaluationData(
        question="RAG 的主要优点是什么？",
        answer="RAG 可以减少大语言模型的幻觉问题，提高回答的准确性和时效性。",
        contexts=[
            "RAG 通过检索外部知识库来减少模型幻觉。",
            "RAG 可以提高回答的准确性和时效性。",
        ],
    )


@pytest.fixture
def sample_batch_eval_data() -> list[EvaluationData]:
    """批量评估数据。"""
    return [
        EvaluationData(
            question="什么是RAG？",
            answer="RAG是检索增强生成技术，结合检索和生成。",
            contexts=["RAG结合了信息检索和文本生成。", "RAG可以减少模型幻觉。"],
            ground_truth="RAG是Retrieval-Augmented Generation的缩写。",
        ),
        EvaluationData(
            question="RAG 如何减少幻觉？",
            answer="RAG 通过检索外部知识库中的真实文档作为上下文，使模型基于事实生成回答。",
            contexts=["RAG 检索真实文档作为上下文。", "基于检索到的文档生成回答。"],
            ground_truth="RAG 通过引入外部知识库中的真实信息来约束模型输出。",
        ),
        EvaluationData(
            question="向量数据库在RAG中的作用？",
            answer="向量数据库用于存储和检索文档的向量表示，支持高效的相似度搜索。",
            contexts=["向量数据库存储文档的向量表示。", "支持高效的相似度检索。"],
        ),
    ]


# =============================================================================
# 评估器 fixtures
# =============================================================================


@pytest.fixture
def evaluator() -> RAGASEvaluator:
    """创建 RAGAS 评估器（默认配置）。"""
    return RAGASEvaluator()


@pytest.fixture
def evaluator_custom() -> RAGASEvaluator:
    """创建自定义配置的 RAGAS 评估器。"""
    return RAGASEvaluator(
        model_name="gpt-4o",
        temperature=0.1,
        max_tokens=1024,
        base_url="https://custom-api.example.com/v1",
        api_key="sk-test-key",
    )


@pytest.fixture
def sample_result() -> RAGASResult:
    """示例 RAGASResult（高分场景）。"""
    return RAGASResult(
        faithfulness=0.92,
        answer_relevancy=0.88,
        context_precision=0.85,
        context_recall=0.90,
    )


@pytest.fixture
def sample_result_low() -> RAGASResult:
    """示例 RAGASResult（低分场景）。"""
    return RAGASResult(
        faithfulness=0.45,
        answer_relevancy=0.50,
        context_precision=0.30,
        context_recall=0.35,
    )