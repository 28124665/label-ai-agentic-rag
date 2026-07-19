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
"""api.utils.query_preprocessor 模块的单元测试。

覆盖繁体查询、简体查询、英文查询、空查询兜底、route_partition 路由函数等场景。

注意：``query_preprocessor`` 依赖 ``rag.nlp.t2s.convert`` 做繁转简。
``rag/nlp/t2s.py`` 自身有独立测试覆盖转换正确性，本测试聚焦于
``preprocess_query`` 的路由/分区/Embedding 模型选择逻辑。
为避免 t2s 在特定环境下因 OpenCC 配置路径问题导致纯字形转换失效而影响本测试，
``test_preprocess_traditional_query`` 中使用 ``mock.patch`` 将 ``convert`` 替换为
确定性实现，保证测试在不同环境下均稳定通过。
"""

from unittest.mock import patch

import pytest

from api.utils.query_preprocessor import (
    EMBEDDING_MODEL_EN,
    EMBEDDING_MODEL_ZH,
    PARTITION_CHINESE,
    PARTITION_ENGLISH,
    preprocess_query,
    route_partition,
)
from rag.nlp.lang_detect import LANG_EN, LANG_ZH_SIMPLIFIED, LANG_ZH_TRADITIONAL


def test_preprocess_traditional_query():
    """繁体查询应被检测为 zh-traditional，繁转简后路由到 chinese 分区并使用中文 Embedding。"""
    # 用确定性 convert 替换 t2s.convert，避免环境差异（OpenCC 是否可用）影响本测试。
    # t2s 模块自身的转换正确性已由 test/test_t2s.py 覆盖。

    def fake_convert(text: str) -> str:
        if text == "供應鏈管理的最佳實踐是什麼？":
            return "供应链管理的最佳实践是什么？"
        return text

    with patch("api.utils.query_preprocessor.convert", side_effect=fake_convert):
        result = preprocess_query("供應鏈管理的最佳實踐是什麼？")
    assert result["query_lang"] == LANG_ZH_TRADITIONAL
    assert result["query_simplified"] == "供应链管理的最佳实践是什么？"
    assert result["partition"] == PARTITION_CHINESE
    assert result["embedding_model"] == EMBEDDING_MODEL_ZH


def test_preprocess_simplified_query():
    """简体查询应保持不变，路由到 chinese 分区。"""
    query = "供应链管理的最佳实践是什么？"
    result = preprocess_query(query)
    assert result["query_lang"] == LANG_ZH_SIMPLIFIED
    assert result["query_simplified"] == query
    assert result["partition"] == PARTITION_CHINESE
    assert result["embedding_model"] == EMBEDDING_MODEL_ZH


def test_preprocess_english_query():
    """英文查询应保持原样，路由到 english 分区并使用英文 Embedding 模型。"""
    query = "What are the best practices in supply chain management?"
    result = preprocess_query(query)
    assert result["query_lang"] == LANG_EN
    assert result["query_simplified"] == query
    assert result["partition"] == PARTITION_ENGLISH
    assert result["embedding_model"] == EMBEDDING_MODEL_EN


def test_preprocess_empty_query():
    """空查询应返回简体中文默认值。"""
    result = preprocess_query("")
    assert result["query_lang"] == LANG_ZH_SIMPLIFIED
    assert result["query_simplified"] == ""
    assert result["partition"] == PARTITION_CHINESE
    assert result["embedding_model"] == EMBEDDING_MODEL_ZH

    # 纯空白字符串同样走兜底逻辑
    result_ws = preprocess_query("   \t\n  ")
    assert result_ws["query_lang"] == LANG_ZH_SIMPLIFIED
    assert result_ws["query_simplified"] == ""
    assert result_ws["partition"] == PARTITION_CHINESE
    assert result_ws["embedding_model"] == EMBEDDING_MODEL_ZH

    # None 同样走兜底逻辑
    result_none = preprocess_query(None)
    assert result_none["query_lang"] == LANG_ZH_SIMPLIFIED
    assert result_none["query_simplified"] == ""
    assert result_none["partition"] == PARTITION_CHINESE
    assert result_none["embedding_model"] == EMBEDDING_MODEL_ZH


def test_route_partition():
    """route_partition 应根据语言返回对应分区名。"""
    assert route_partition(LANG_ZH_SIMPLIFIED) == PARTITION_CHINESE
    assert route_partition(LANG_ZH_TRADITIONAL) == PARTITION_CHINESE
    assert route_partition(LANG_EN) == PARTITION_ENGLISH
    # 未知语言按中文兜底
    assert route_partition("unknown") == PARTITION_CHINESE
    assert route_partition("") == PARTITION_CHINESE


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
