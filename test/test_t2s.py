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
"""rag/nlp/t2s.py 模块单元测试。

覆盖繁简转换的核心场景：纯繁体字形、台湾用语词汇、专有名词保护、
简体原文保持不变、空字符串、中英混合、模块级便捷函数。
"""

import pytest

from rag.nlp.t2s import TraditionalToSimplifiedConverter, convert


def test_pure_traditional_character_conversion():
    """纯繁体字形转换：常见繁体字应被 OpenCC 转换为对应简体字。"""
    converter = TraditionalToSimplifiedConverter()
    assert converter.convert("供應鏈管理是企業競爭力的核心") == "供应链管理是企业竞争力的核心"


def test_taiwan_vocabulary_conversion():
    """台湾用语词汇转换：伺服器->服务器、軟體->软件。"""
    converter = TraditionalToSimplifiedConverter()
    assert converter.convert("伺服器的軟體需要更新") == "服务器的软件需要更新"


def test_proper_noun_protection():
    """专有名词保护：鴻海精密、iPhone 在白名单中应原样保留，供應鏈仍需转换。"""
    converter = TraditionalToSimplifiedConverter()
    result = converter.convert("鴻海精密的 iPhone 16 供應鏈")
    assert result == "鴻海精密的 iPhone 16 供应链"
    # 鴻海 不应被转换为 鸿海
    assert "鸿海" not in result
    assert "鴻海" in result


def test_simplified_text_unchanged():
    """已经是简体的文本应保持不变。"""
    converter = TraditionalToSimplifiedConverter()
    assert converter.convert("供应链管理优化方案") == "供应链管理优化方案"


def test_empty_string():
    """空字符串应原样返回，不抛异常。"""
    converter = TraditionalToSimplifiedConverter()
    assert converter.convert("") == ""


def test_mixed_chinese_english():
    """中英文混合：iPhone 作为专有名词保留，繁体供應鏈转换为简体。"""
    converter = TraditionalToSimplifiedConverter()
    assert converter.convert("iPhone 16 的供應鏈管理") == "iPhone 16 的供应链管理"


def test_module_level_convert_function():
    """模块级 convert 函数应等价于 default_converter.convert。"""
    assert convert("供應鏈管理是企業競爭力的核心") == "供应链管理是企业竞争力的核心"
    assert convert("伺服器的軟體需要更新") == "服务器的软件需要更新"
    assert convert("") == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
