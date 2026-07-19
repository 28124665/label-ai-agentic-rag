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
"""端到端检索流程集成测试（Task 13）。

模拟繁体查询 → 检索 → Rerank → Prompt → 返回的全流程。
不依赖真实的向量库与 LLM，全部使用 Mock 构造 mock retrieval 与 mock rerank_model。

覆盖场景
--------

1. ``test_e2e_traditional_query_flow``：繁体查询完整流程
   - preprocess_query → rerank → build_prompt 全链路
   - 验证 query_simplified 是简体
   - 验证 rerank 结果按 score 降序
   - 验证 prompt 包含「请用繁体中文回答。」
   - 验证 prompt 的 context 包含 original_text（繁体原文）

2. ``test_e2e_simplified_query_flow``：简体查询完整流程
   - 验证 prompt 包含「请用简体中文回答。」

3. ``test_e2e_english_query_flow``：英文查询完整流程
   - 验证 prompt 包含「Please answer in English.」

4. ``test_e2e_rerank_uses_search_text``：验证 rerank 阶段 pairs 使用 search_text
   - 构造 chunks: original_text=繁体, search_text=简体
   - 验证 rerank_model.score_pairs 接收的 pair 第二个元素是 search_text（简体）

5. ``test_e2e_prompt_uses_original_text``：验证 prompt 中 context 使用 original_text
   - 构造 chunks: original_text=繁体, search_text=简体
   - 验证 prompt 中包含繁体 original_text，不包含简体 search_text

6. ``test_e2e_chunk_preprocessor_integration``：验证 preprocess_chunk → rerank → prompt 全链路字段流转

7. ``test_e2e_retrieval_results_contain_dual_fields``：验证检索结果包含 original_text 和 original_lang 字段

注意
----

- 本测试不依赖真实的向量库 / LLM / Rerank 模型，全部使用 Mock。
- 为避免 t2s 在特定环境下因 OpenCC 配置路径问题导致纯字形转换失效而影响本测试，
  ``preprocess_query`` 与 ``preprocess_chunk`` 内部调用的 ``convert`` 用 ``mock.patch``
  替换为确定性实现。t2s 模块自身的转换正确性已由 ``test/test_t2s.py`` 独立覆盖。
"""

from unittest.mock import MagicMock, patch

import pytest

from api.utils.chunk_preprocessor import preprocess_chunk
from api.utils.multilingual_reranker import MultilingualReranker, rerank
from api.utils.prompt_builder import build_prompt
from api.utils.query_preprocessor import preprocess_query
from rag.nlp.lang_detect import LANG_EN, LANG_ZH_SIMPLIFIED, LANG_ZH_TRADITIONAL


def _fake_convert(text: str) -> str:
    """确定性繁转简实现，避免依赖 OpenCC 环境。

    覆盖测试中出现的所有繁体查询与文档原文。未命中的文本原样返回
    （简体 / 英文经 convert 后应保持不变）。
    """
    mapping = {
        "供應鏈管理的最佳實踐": "供应链管理的最佳实践",
        "供應鏈管理是企業競爭力的核心": "供应链管理是企业竞争力的核心",
        "供應鏈管理的最佳實踐包括需求預測與庫存控制": "供应链管理的最佳实践包括需求预测与库存控制",
        "供應鏈管理是什麼？": "供应链管理是什么？",
    }
    return mapping.get(text, text)


@pytest.fixture(autouse=True)
def _mock_convert():
    """自动应用 mock：将 query_preprocessor 与 chunk_preprocessor 中的 convert 替换为确定性实现。

    这样可以避免 OpenCC 在特定环境下因配置路径问题导致转换失效，保证 E2E 测试稳定通过。
    t2s 模块自身的转换正确性由 test/test_t2s.py 独立覆盖。
    """
    with patch("api.utils.query_preprocessor.convert", side_effect=_fake_convert) as m1, patch("api.utils.chunk_preprocessor.convert", side_effect=_fake_convert) as m2:
        yield (m1, m2)


def _build_mock_rerank_model(scores):
    """构造一个 mock rerank_model，其 score_pairs 返回预设的 scores 列表。"""
    model = MagicMock()
    model.score_pairs = MagicMock(return_value=list(scores))
    return model


def _build_traditional_chunks():
    """构造带 original_text/search_text/original_lang 字段的繁体 chunks。"""
    return [
        {
            "content": "供應鏈管理是企業競爭力的核心",
            "original_text": "供應鏈管理是企業競爭力的核心",
            "original_lang": LANG_ZH_TRADITIONAL,
            "search_text": "供应链管理是企业竞争力的核心",
            "doc_id": "doc1",
        },
        {
            "content": "供應鏈管理的最佳實踐包括需求預測與庫存控制",
            "original_text": "供應鏈管理的最佳實踐包括需求預測與庫存控制",
            "original_lang": LANG_ZH_TRADITIONAL,
            "search_text": "供应链管理的最佳实践包括需求预测与库存控制",
            "doc_id": "doc2",
        },
    ]


def _build_simplified_chunks():
    """构造带 original_text/search_text/original_lang 字段的简体 chunks。"""
    return [
        {
            "content": "供应链管理是企业竞争力的核心",
            "original_text": "供应链管理是企业竞争力的核心",
            "original_lang": LANG_ZH_SIMPLIFIED,
            "search_text": "供应链管理是企业竞争力的核心",
            "doc_id": "doc1",
        },
    ]


def _build_english_chunks():
    """构造带 original_text/search_text/original_lang 字段的英文 chunks。"""
    return [
        {
            "content": "Supply chain management is the core competitiveness of enterprises.",
            "original_text": "Supply chain management is the core competitiveness of enterprises.",
            "original_lang": LANG_EN,
            "search_text": "Supply chain management is the core competitiveness of enterprises.",
            "doc_id": "doc1",
        },
    ]


def test_e2e_traditional_query_flow():
    """繁体查询完整流程：preprocess → rerank → build_prompt。

    覆盖场景：
    - preprocess_query 返回的 query_simplified 是简体
    - rerank 返回结果按 score 降序
    - build_prompt 包含「请用繁体中文回答。」
    - prompt 的 context 包含 original_text（繁体原文）
    """
    query = "供應鏈管理的最佳實踐"

    # Step 1: 查询预处理
    preprocessed = preprocess_query(query)
    assert preprocessed["query_lang"] == LANG_ZH_TRADITIONAL
    assert preprocessed["query_simplified"] == "供应链管理的最佳实践"

    # Step 2: Mock retrieval 返回带 original_text/search_text 的 chunks
    chunks = _build_traditional_chunks()

    # Step 3: Rerank
    mock_model = _build_mock_rerank_model([0.5, 0.9])
    reranked = rerank(preprocessed["query_simplified"], chunks, top_k=3, rerank_model=mock_model)

    # 验证按分数降序：第二条（0.9）应排在第一条（0.5）之前
    assert len(reranked) == 2
    assert reranked[0]["rerank_score"] == pytest.approx(0.9)
    assert reranked[1]["rerank_score"] == pytest.approx(0.5)

    # Step 4: Build prompt
    prompt = build_prompt(query, reranked, query_lang=preprocessed["query_lang"])

    # 验证 prompt 包含繁体中文回答指令
    assert "请用繁体中文回答。" in prompt
    # 验证 context 包含 original_text（繁体原文）
    assert "供應鏈管理是企業競爭力的核心" in prompt
    assert "供應鏈管理的最佳實踐包括需求預測與庫存控制" in prompt


def test_e2e_simplified_query_flow():
    """简体查询完整流程：验证 prompt 包含「请用简体中文回答。」"""
    query = "供应链管理的最佳实践"

    # Step 1: 查询预处理
    preprocessed = preprocess_query(query)
    assert preprocessed["query_lang"] == LANG_ZH_SIMPLIFIED
    assert preprocessed["query_simplified"] == query

    # Step 2: Mock retrieval 返回简体 chunks
    chunks = _build_simplified_chunks()

    # Step 3: Rerank
    mock_model = _build_mock_rerank_model([0.8])
    reranked = rerank(preprocessed["query_simplified"], chunks, top_k=3, rerank_model=mock_model)
    assert len(reranked) == 1

    # Step 4: Build prompt
    prompt = build_prompt(query, reranked, query_lang=preprocessed["query_lang"])

    # 验证 prompt 包含简体中文回答指令
    assert "请用简体中文回答。" in prompt
    assert "供应链管理是企业竞争力的核心" in prompt


def test_e2e_english_query_flow():
    """英文查询完整流程：验证 prompt 包含「Please answer in English.」"""
    query = "What are the best practices in supply chain management?"

    # Step 1: 查询预处理
    preprocessed = preprocess_query(query)
    assert preprocessed["query_lang"] == LANG_EN
    assert preprocessed["query_simplified"] == query

    # Step 2: Mock retrieval 返回英文 chunks
    chunks = _build_english_chunks()

    # Step 3: Rerank
    mock_model = _build_mock_rerank_model([0.85])
    reranked = rerank(preprocessed["query_simplified"], chunks, top_k=3, rerank_model=mock_model)
    assert len(reranked) == 1

    # Step 4: Build prompt
    prompt = build_prompt(query, reranked, query_lang=preprocessed["query_lang"])

    # 验证 prompt 包含英文回答指令
    assert "Please answer in English." in prompt
    assert "Supply chain management is the core competitiveness of enterprises." in prompt


def test_e2e_rerank_uses_search_text():
    """验证 rerank 阶段 pairs 使用 search_text（简体）而非 original_text（繁体）。

    构造 chunks: original_text=繁体, search_text=简体。
    验证 rerank_model.score_pairs 接收的 pair 第二个元素是 search_text。
    """
    query = "供應鏈管理的最佳實踐"
    preprocessed = preprocess_query(query)
    query_simplified = preprocessed["query_simplified"]

    # 构造 chunks: original_text=繁体, search_text=简体
    chunks = [
        {
            "original_text": "供應鏈管理是企業競爭力的核心",
            "search_text": "供应链管理是企业竞争力的核心",
            "original_lang": LANG_ZH_TRADITIONAL,
            "content": "fallback content",
        }
    ]

    mock_model = _build_mock_rerank_model([0.7])
    reranker = MultilingualReranker(rerank_model=mock_model)
    reranker.rerank(query_simplified, chunks, top_k=10)

    # 验证 score_pairs 接收的 pair 第二个元素是 search_text（简体）
    pairs_arg = mock_model.score_pairs.call_args.args[0]
    assert len(pairs_arg) == 1
    assert pairs_arg[0][0] == query_simplified
    assert pairs_arg[0][1] == "供应链管理是企业竞争力的核心"
    # 不应是繁体 original_text
    assert pairs_arg[0][1] != "供應鏈管理是企業競爭力的核心"


def test_e2e_prompt_uses_original_text():
    """验证 prompt 中 context 使用 original_text（繁体）而非 search_text（简体）。

    构造 chunks: original_text=繁体, search_text=简体。
    验证 prompt 中包含繁体 original_text，不包含简体 search_text。
    """
    query = "供應鏈管理的最佳實踐"
    preprocessed = preprocess_query(query)

    # 构造 chunks: original_text=繁体, search_text=简体
    chunks = [
        {
            "original_text": "供應鏈管理是企業競爭力的核心",
            "search_text": "供应链管理是企业竞争力的核心",
            "original_lang": LANG_ZH_TRADITIONAL,
            "content": "fallback content",
        }
    ]

    prompt = build_prompt(query, chunks, query_lang=preprocessed["query_lang"])

    # 验证 prompt 中包含繁体 original_text
    assert "供應鏈管理是企業競爭力的核心" in prompt
    # 验证 context 部分（【1】之后）使用 original_text
    assert "【1】 供應鏈管理是企業競爭力的核心" in prompt
    # search_text「供应链管理是企业竞争力的核心」不应出现在 prompt 中
    # 注意：query 本身是繁体「供應鏈管理的最佳實踐」，不会引入「供应链」字样
    assert "供应链管理是企业竞争力的核心" not in prompt


def test_e2e_chunk_preprocessor_integration():
    """验证 preprocess_chunk → rerank → prompt 全链路字段流转。

    流程：
    1. 原始 chunk（只有 content）经 preprocess_chunk 生成双字段
    2. 查询预处理得到 query_simplified
    3. rerank 使用 preprocess_chunk 产出的 chunk
    4. build_prompt 使用 rerank 后的 chunks
    """
    # Step 1: 原始 chunk（只有 content）经 preprocess_chunk 生成双字段
    raw_chunk = {"content": "供應鏈管理是企業競爭力的核心", "doc_id": "doc1"}
    processed_chunk = preprocess_chunk(raw_chunk)

    # 验证预处理后包含所有新字段
    assert "original_text" in processed_chunk
    assert "original_lang" in processed_chunk
    assert "search_text" in processed_chunk
    assert processed_chunk["original_text"] == "供應鏈管理是企業競爭力的核心"
    assert processed_chunk["original_lang"] == LANG_ZH_TRADITIONAL
    assert processed_chunk["search_text"] == "供应链管理是企业竞争力的核心"
    # 保留原字段
    assert processed_chunk["doc_id"] == "doc1"

    # Step 2: 查询预处理
    query = "供應鏈管理的最佳實踐"
    preprocessed = preprocess_query(query)
    query_simplified = preprocessed["query_simplified"]

    # Step 3: Rerank 使用 preprocess_chunk 产出的 chunk
    mock_model = _build_mock_rerank_model([0.6])
    reranked = rerank(query_simplified, [processed_chunk], top_k=5, rerank_model=mock_model)
    assert len(reranked) == 1
    assert reranked[0]["rerank_score"] == pytest.approx(0.6)
    # rerank 不应破坏原有字段
    assert reranked[0]["original_text"] == "供應鏈管理是企業競爭力的核心"
    assert reranked[0]["search_text"] == "供应链管理是企业竞争力的核心"

    # Step 4: Build prompt 使用 rerank 后的 chunks
    prompt = build_prompt(query, reranked, query_lang=preprocessed["query_lang"])
    assert "请用繁体中文回答。" in prompt
    assert "供應鏈管理是企業競爭力的核心" in prompt


def test_e2e_retrieval_results_contain_dual_fields():
    """验证检索结果包含 original_text 与 original_lang 字段。

    模拟检索链路返回的 chunks 应包含 original_text / original_lang / search_text 字段，
    经过 rerank 后字段仍应保留，build_prompt 时字段能正确流转。
    """
    query = "供應鏈管理的最佳實踐"
    preprocessed = preprocess_query(query)

    # Mock retrieval 返回的 chunks（模拟真实检索链路返回的结构）
    mock_retrieval_chunks = _build_traditional_chunks()

    # 验证每个 chunk 都包含 original_text 与 original_lang 字段
    for chunk in mock_retrieval_chunks:
        assert "original_text" in chunk
        assert "original_lang" in chunk
        assert "search_text" in chunk
        assert chunk["original_lang"] == LANG_ZH_TRADITIONAL

    # 经过 rerank 后字段仍应保留
    mock_model = _build_mock_rerank_model([0.5, 0.9])
    reranked = rerank(preprocessed["query_simplified"], mock_retrieval_chunks, top_k=3, rerank_model=mock_model)

    for chunk in reranked:
        assert "original_text" in chunk
        assert "original_lang" in chunk
        assert "search_text" in chunk
        assert "rerank_score" in chunk

    # Build prompt 时使用 reranked chunks，验证字段流转
    prompt = build_prompt(query, reranked, query_lang=preprocessed["query_lang"])
    assert "供應鏈管理是企業競爭力的核心" in prompt
    assert "供應鏈管理的最佳實踐包括需求預測與庫存控制" in prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
