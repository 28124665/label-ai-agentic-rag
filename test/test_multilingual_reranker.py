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
"""api.utils.multilingual_reranker 模块的单元测试。

覆盖 mock rerank_model 注入、pairs 使用 search_text、top_k 截取、无模型兜底、
空结果、模块级单例、模块级 rerank 函数等场景。

注意：``multilingual_reranker`` 模块顶部依赖 ``api/utils/chunk_preprocessor.py``
（由并行子代理实现）。若该依赖尚未实现，本测试文件中所有依赖
``multilingual_reranker`` 的用例会被自动跳过，但代码中导入语句必须保留。
"""

from unittest.mock import MagicMock

import pytest

# api/utils/chunk_preprocessor.py 由并行子代理实现，可能尚未存在；
# 由于 multilingual_reranker 在模块加载阶段就 import chunk_preprocessor，
# 因此 chunk_preprocessor 缺失时 multilingual_reranker 也无法导入。
# 此时跳过依赖该模块的测试用例，但代码中导入语句必须保留。
_RERANKER_AVAILABLE = True
try:
    from api.utils.multilingual_reranker import MultilingualReranker, default_reranker, rerank as module_rerank
except ImportError:
    _RERANKER_AVAILABLE = False

skip_if_no_reranker = pytest.mark.skipif(
    not _RERANKER_AVAILABLE,
    reason="api/utils/chunk_preprocessor.py 或 api/utils/multilingual_reranker.py 尚未实现，跳过依赖该模块的测试",
)


def _build_mock_model(scores):
    """构造一个 mock rerank_model，其 score_pairs(pairs) 返回预设的 scores 列表。"""
    model = MagicMock()
    model.score_pairs = MagicMock(return_value=list(scores))
    return model


@skip_if_no_reranker
def test_rerank_with_mock_model():
    """Mock rerank_model 应构造正确的 pairs，并按分数降序返回结果。"""
    query = "供应链"
    results = [
        {"content": "A", "search_text": "供应链A"},
        {"content": "B", "search_text": "供应链B"},
    ]
    # mock 给 B 高分，A 低分，期望 B 排前
    model = _build_mock_model([0.5, 0.9])
    reranker = MultilingualReranker(rerank_model=model)

    output = reranker.rerank(query, results, top_k=10)

    # 验证 score_pairs 被调用一次，且 pairs 构造正确
    assert model.score_pairs.call_count == 1
    pairs_arg = model.score_pairs.call_args.args[0]
    assert pairs_arg == [
        ("供应链", "供应链A"),
        ("供应链", "供应链B"),
    ]

    # 验证按分数降序：B(0.9) 在前，A(0.5) 在后
    assert len(output) == 2
    assert output[0]["content"] == "B"
    assert output[1]["content"] == "A"

    # 验证每个 result 有 rerank_score 字段
    assert output[0]["rerank_score"] == pytest.approx(0.9)
    assert output[1]["rerank_score"] == pytest.approx(0.5)


@skip_if_no_reranker
def test_rerank_pairs_use_search_text():
    """pairs 必须用 search_text 而非 original_text。"""
    query = "供应链"
    results = [{"original_text": "供應鏈", "search_text": "供应链", "content": "fallback"}]
    model = _build_mock_model([0.8])
    reranker = MultilingualReranker(rerank_model=model)

    reranker.rerank(query, results, top_k=10)

    pairs_arg = model.score_pairs.call_args.args[0]
    # pair 的第二个元素应该是 search_text（简体「供应链」），而非 original_text（繁体「供應鏈」）
    assert pairs_arg == [("供应链", "供应链")]
    assert pairs_arg[0][1] == "供应链"
    assert pairs_arg[0][1] != "供應鏈"


@skip_if_no_reranker
def test_rerank_top_k():
    """top_k 应截取前 N 条结果。"""
    query = "供应链"
    results = [{"content": f"chunk_{i}", "search_text": f"供应链{i}"} for i in range(5)]
    # 给最后一条最高分，确保排序后 top_k 仍能正确截取
    model = _build_mock_model([0.1, 0.2, 0.3, 0.4, 0.5])
    reranker = MultilingualReranker(rerank_model=model)

    output = reranker.rerank(query, results, top_k=3)

    assert len(output) == 3
    # 排序后应该是分数最高的三条：chunk_4(0.5)、chunk_3(0.4)、chunk_2(0.3)
    assert output[0]["content"] == "chunk_4"
    assert output[1]["content"] == "chunk_3"
    assert output[2]["content"] == "chunk_2"


@skip_if_no_reranker
def test_rerank_no_model():
    """rerank_model=None 时按原顺序返回前 top_k 条，不调用 score_pairs。"""
    query = "供应链"
    results = [
        {"content": "A", "search_text": "供应链A"},
        {"content": "B", "search_text": "供应链B"},
        {"content": "C", "search_text": "供应链C"},
    ]
    reranker = MultilingualReranker(rerank_model=None)

    output = reranker.rerank(query, results, top_k=2)

    assert len(output) == 2
    # 按原顺序返回前 2 条
    assert output[0]["content"] == "A"
    assert output[1]["content"] == "B"


@skip_if_no_reranker
def test_rerank_empty_results():
    """空 results 列表应返回空列表，不调用模型。"""
    query = "供应链"
    model = _build_mock_model([])
    reranker = MultilingualReranker(rerank_model=model)

    output = reranker.rerank(query, [], top_k=10)

    assert output == []
    # 空列表应直接返回，不调用 score_pairs
    model.score_pairs.assert_not_called()


@skip_if_no_reranker
def test_default_reranker_singleton():
    """模块级单例 default_reranker 应可访问，默认 rerank_model 为 None。"""
    assert default_reranker is not None
    assert isinstance(default_reranker, MultilingualReranker)
    assert default_reranker.rerank_model is None


@skip_if_no_reranker
def test_module_level_rerank_function():
    """模块级 rerank() 函数应可用，传入 rerank_model 时按模型评分排序。"""
    query = "供应链"
    results = [
        {"content": "A", "search_text": "供应链A"},
        {"content": "B", "search_text": "供应链B"},
    ]
    model = _build_mock_model([0.5, 0.9])

    output = module_rerank(query, results, top_k=10, rerank_model=model)

    assert len(output) == 2
    assert output[0]["content"] == "B"
    assert output[1]["content"] == "A"

    # 不传 rerank_model 时使用 default_reranker（rerank_model=None），按原顺序返回前 top_k
    output_default = module_rerank(query, results, top_k=1)
    assert len(output_default) == 1
    assert output_default[0]["content"] == "A"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
