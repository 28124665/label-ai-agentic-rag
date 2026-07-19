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
"""api.utils.chunk_preprocessor 模块单元测试。

覆盖 9 个场景：繁体 / 简体 / 英文 chunk 处理、原字段保留、批量处理、
search_text 与 original_text 的 fallback 提取、content_with_weight 字段支持、
空 content 兜底处理。
"""

import pytest

from api.utils.chunk_preprocessor import (
    extract_original_text,
    extract_search_text,
    preprocess_chunk,
    preprocess_chunks,
)


def test_preprocess_traditional_chunk():
    """繁体 chunk：original_text 保留繁体原文，search_text 转为简体，保留 doc_id。"""
    chunk = {"content": "供應鏈管理是企業競爭力的核心", "doc_id": "doc1"}
    result = preprocess_chunk(chunk)

    assert result["original_text"] == "供應鏈管理是企業競爭力的核心"
    assert result["original_lang"] == "zh-traditional"
    assert result["search_text"] == "供应链管理是企业竞争力的核心"
    # 保留原字段
    assert result["doc_id"] == "doc1"
    # 不修改入参
    assert chunk == {"content": "供應鏈管理是企業競爭力的核心", "doc_id": "doc1"}


def test_preprocess_simplified_chunk():
    """简体 chunk：original_text 与 search_text 相同，均为原文。

    Note:
        输入文本需确保 :func:`rag.nlp.lang_detect.detect_language` 判定为简体
        （繁体特征字比例 <= 0.3）。此处采用与 test/test_lang_detect.py 中
        ``test_detect_simplified_chinese`` 一致的输入。
    """
    chunk = {"content": "供应链管理的最佳实践"}
    result = preprocess_chunk(chunk)

    assert result["original_text"] == "供应链管理的最佳实践"
    assert result["search_text"] == "供应链管理的最佳实践"
    assert result["original_text"] == result["search_text"]
    assert result["original_lang"] == "zh-simplified"


def test_preprocess_english_chunk():
    """英文 chunk：original_text 与 search_text 相同，均为原文。"""
    chunk = {"content": "Supply chain management best practices"}
    result = preprocess_chunk(chunk)

    assert result["original_text"] == "Supply chain management best practices"
    assert result["search_text"] == "Supply chain management best practices"
    assert result["original_text"] == result["search_text"]
    assert result["original_lang"] == "en"


def test_preprocess_chunk_preserves_original_fields():
    """保留 chunk 中所有原有字段（doc_id、page_number、metadata）。"""
    chunk = {
        "content": "供應鏈管理是企業競爭力的核心",
        "doc_id": "d1",
        "page_number": 5,
        "metadata": {"source": "report"},
    }
    result = preprocess_chunk(chunk)

    # 保留原有字段
    assert result["doc_id"] == "d1"
    assert result["page_number"] == 5
    assert result["metadata"] == {"source": "report"}
    # 新增字段
    assert "original_text" in result
    assert "original_lang" in result
    assert "search_text" in result
    # 不修改入参
    assert chunk["metadata"] == {"source": "report"}
    assert chunk["doc_id"] == "d1"


def test_preprocess_chunks_batch():
    """批量处理：返回正确数量，每条都新增预处理字段。"""
    chunks = [
        {"content": "供應鏈管理是企業競爭力的核心", "doc_id": "doc1"},
        {"content": "供应链管理的最佳实践", "doc_id": "doc2"},
        {"content": "Supply chain management best practices", "doc_id": "doc3"},
    ]
    results = preprocess_chunks(chunks)

    assert len(results) == 3
    # 第一条：繁体 -> 简体
    assert results[0]["original_lang"] == "zh-traditional"
    assert results[0]["search_text"] == "供应链管理是企业竞争力的核心"
    assert results[0]["doc_id"] == "doc1"
    # 第二条：简体保持
    assert results[1]["original_lang"] == "zh-simplified"
    assert results[1]["search_text"] == "供应链管理的最佳实践"
    assert results[1]["doc_id"] == "doc2"
    # 第三条：英文保持
    assert results[2]["original_lang"] == "en"
    assert results[2]["search_text"] == "Supply chain management best practices"
    assert results[2]["doc_id"] == "doc3"
    # 不修改入参
    assert chunks[0] == {"content": "供應鏈管理是企業競爭力的核心", "doc_id": "doc1"}


def test_extract_search_text_fallback():
    """当 chunk 没有 search_text 字段时，extract_search_text fallback 到 content。"""
    chunk = {"content": "供应链管理优化方案", "doc_id": "doc1"}
    assert extract_search_text(chunk) == "供应链管理优化方案"

    # 已预处理的 chunk 优先返回 search_text
    processed = preprocess_chunk({"content": "供應鏈管理是企業競爭力的核心"})
    assert extract_search_text(processed) == "供应链管理是企业竞争力的核心"

    # 既没有 search_text 也没有 content -> 空字符串
    assert extract_search_text({"doc_id": "x"}) == ""

    # 非字典输入 -> 空字符串
    assert extract_search_text(None) == ""  # type: ignore[arg-type]


def test_extract_original_text_fallback():
    """当 chunk 没有 original_text 字段时，extract_original_text fallback 到 content。"""
    chunk = {"content": "供应链管理优化方案", "doc_id": "doc1"}
    assert extract_original_text(chunk) == "供应链管理优化方案"

    # 已预处理的 chunk 优先返回 original_text（保留繁体原文）
    processed = preprocess_chunk({"content": "供應鏈管理是企業競爭力的核心"})
    assert extract_original_text(processed) == "供應鏈管理是企業競爭力的核心"

    # 既没有 original_text 也没有 content -> 空字符串
    assert extract_original_text({"doc_id": "x"}) == ""

    # 非字典输入 -> 空字符串
    assert extract_original_text(None) == ""  # type: ignore[arg-type]


def test_preprocess_chunk_with_content_with_weight_field():
    """当 chunk 用 content_with_weight 字段而非 content 时能正确处理。"""
    chunk = {"content_with_weight": "供應鏈管理是企業競爭力的核心", "doc_id": "doc1"}
    result = preprocess_chunk(chunk)

    assert result["original_text"] == "供應鏈管理是企業競爭力的核心"
    assert result["original_lang"] == "zh-traditional"
    assert result["search_text"] == "供应链管理是企业竞争力的核心"
    assert result["doc_id"] == "doc1"
    # 不应新增 content 字段
    assert "content" not in result


def test_preprocess_chunk_empty_content():
    """空 content 的兜底处理：不抛异常，返回结构完整的字段。"""
    # content 为空字符串
    chunk = {"content": "", "doc_id": "doc1"}
    result = preprocess_chunk(chunk)
    assert result["original_text"] == ""
    assert result["search_text"] == ""
    assert result["original_lang"] == "zh-simplified"
    assert result["metadata"] == {}
    assert result["doc_id"] == "doc1"

    # chunk 中没有任何 content 类字段
    chunk2 = {"doc_id": "doc2"}
    result2 = preprocess_chunk(chunk2)
    assert result2["original_text"] == ""
    assert result2["search_text"] == ""
    assert result2["original_lang"] == "zh-simplified"
    assert result2["metadata"] == {}
    assert result2["doc_id"] == "doc2"

    # 空 chunks 列表
    assert preprocess_chunks([]) == []

    # metadata 字段为 None 时应被置为空字典
    chunk3 = {"content": "供应链管理", "metadata": None}
    result3 = preprocess_chunk(chunk3)
    assert result3["metadata"] == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
