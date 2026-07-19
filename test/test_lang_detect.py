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
"""rag.nlp.lang_detect 模块的单元测试。"""

import pytest

from rag.nlp.lang_detect import detect_language, LANG_ZH_SIMPLIFIED, LANG_ZH_TRADITIONAL, LANG_EN


def test_detect_traditional_chinese():
    """检测繁体中文文本应返回 zh-traditional。"""
    assert detect_language("供應鏈管理的最佳實踐") == LANG_ZH_TRADITIONAL


def test_detect_simplified_chinese():
    """检测简体中文文本应返回 zh-simplified。"""
    assert detect_language("供应链管理的最佳实践") == LANG_ZH_SIMPLIFIED


def test_detect_english():
    """检测英文文本应返回 en。"""
    assert detect_language("Supply chain management best practices") == LANG_EN


def test_detect_empty_string():
    """空字符串应返回默认值 zh-simplified。"""
    assert detect_language("") == LANG_ZH_SIMPLIFIED


def test_detect_whitespace_only():
    """纯空白字符串应返回默认值 zh-simplified。"""
    assert detect_language("   \t\n") == LANG_ZH_SIMPLIFIED


def test_detect_mixed_chinese_english_majority_chinese():
    """中英混合、中文字符占多数时应返回 zh-simplified。

    需保证中文字符数 > 英文字母数，使 ``en_chars / (en_chars + total_cjk) <= 0.5``，
    否则按规则会被判定为英文。本用例中 management 为 10 个英文字母，
    中文 12 个字符，比例约 0.45，故判为简体中文。
    """
    assert detect_language("供应链管理优化的核心实践 management") == LANG_ZH_SIMPLIFIED


def test_detect_mixed_chinese_english_majority_english():
    """中英混合、英文字母占多数时应返回 en。"""
    assert detect_language("Supply chain management 供应链") == LANG_EN


def test_detect_mixed_traditional_simplified():
    """繁简混合、繁体特征字占比 > 30% 时应返回 zh-traditional。"""
    assert detect_language("供应链管理是企業競爭力的核心") == LANG_ZH_TRADITIONAL


def test_detect_pure_numbers():
    """纯数字无中英文字符，应返回默认值 zh-simplified。"""
    assert detect_language("1234567890") == LANG_ZH_SIMPLIFIED


def test_detect_mixed_with_punctuation():
    """带标点的繁体文本应返回 zh-traditional。"""
    assert detect_language("供應鏈、企業競爭力、最佳實踐。") == LANG_ZH_TRADITIONAL


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
