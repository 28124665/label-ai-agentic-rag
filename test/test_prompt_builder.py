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
"""api.utils.prompt_builder 模块的单元测试。

覆盖三种语言的输出指令、多 chunk 拼接、original_text vs search_text 选择、
未知语言兜底、max_chunks 截取、OUTPUT_LANG_INSTRUCTIONS 常量等场景。

注意：``prompt_builder`` 模块顶部依赖 ``api/utils/chunk_preprocessor.py``
（由并行子代理实现）。若该依赖尚未实现，本测试文件中所有依赖
``prompt_builder`` 的用例会被自动跳过，但代码中导入语句必须保留。
"""

import pytest

# api/utils/chunk_preprocessor.py 由并行子代理实现，可能尚未存在；
# 由于 prompt_builder 在模块加载阶段就 import chunk_preprocessor，
# 因此 chunk_preprocessor 缺失时 prompt_builder 也无法导入。
# 此时跳过依赖该模块的测试用例，但代码中导入语句必须保留。
_PROMPT_BUILDER_AVAILABLE = True
try:
    from api.utils.prompt_builder import OUTPUT_LANG_INSTRUCTIONS, build_context, build_prompt
except ImportError:
    _PROMPT_BUILDER_AVAILABLE = False

from rag.nlp.lang_detect import LANG_EN, LANG_ZH_SIMPLIFIED, LANG_ZH_TRADITIONAL

skip_if_no_prompt_builder = pytest.mark.skipif(
    not _PROMPT_BUILDER_AVAILABLE,
    reason="api/utils/chunk_preprocessor.py 或 api/utils/prompt_builder.py 尚未实现，跳过依赖该模块的测试",
)


@skip_if_no_prompt_builder
def test_build_prompt_traditional():
    """query_lang=zh-traditional 时 Prompt 应包含繁体中文回答指令及 original_text 内容。"""
    query = "供應鏈管理是什麼？"
    chunks = [{"original_text": "供應鏈管理是企業核心競爭力", "search_text": "供应链管理是企业核心竞争力"}]
    prompt = build_prompt(query, chunks, LANG_ZH_TRADITIONAL)

    assert "请用繁体中文回答。" in prompt
    # Prompt 应包含 original_text（繁体）而非 search_text（简体）
    assert "供應鏈管理是企業核心競爭力" in prompt
    assert "供应链管理是企业核心竞争力" not in prompt
    # 应包含 query 本身
    assert query in prompt


@skip_if_no_prompt_builder
def test_build_prompt_simplified():
    """query_lang=zh-simplified 时 Prompt 应包含简体中文回答指令。"""
    query = "供应链管理是什么？"
    chunks = [{"original_text": "供应链管理是企业核心竞争力", "search_text": "供应链管理是企业核心竞争力"}]
    prompt = build_prompt(query, chunks, LANG_ZH_SIMPLIFIED)

    assert "请用简体中文回答。" in prompt
    assert "供应链管理是企业核心竞争力" in prompt
    assert query in prompt


@skip_if_no_prompt_builder
def test_build_prompt_english():
    """query_lang=en 时 Prompt 应包含英文回答指令。"""
    query = "What is supply chain management?"
    chunks = [{"original_text": "Supply chain management is core competitiveness.", "search_text": "Supply chain management is core competitiveness."}]
    prompt = build_prompt(query, chunks, LANG_EN)

    assert "Please answer in English." in prompt
    assert "Supply chain management is core competitiveness." in prompt
    assert query in prompt


@skip_if_no_prompt_builder
def test_build_prompt_with_multiple_chunks():
    """多个 chunk 拼接时 context 中应有【1】【2】等序号。"""
    query = "供应链管理"
    chunks = [
        {"original_text": "第一条参考资料", "search_text": "第一条参考资料"},
        {"original_text": "第二条参考资料", "search_text": "第二条参考资料"},
        {"original_text": "第三条参考资料", "search_text": "第三条参考资料"},
    ]
    prompt = build_prompt(query, chunks, LANG_ZH_SIMPLIFIED)

    assert "【1】" in prompt
    assert "【2】" in prompt
    assert "【3】" in prompt
    assert "第一条参考资料" in prompt
    assert "第二条参考资料" in prompt
    assert "第三条参考资料" in prompt


@skip_if_no_prompt_builder
def test_build_prompt_uses_original_text():
    """Prompt 必须使用 original_text 而非 search_text。"""
    query = "供应链"
    chunks = [{"original_text": "供應鏈", "search_text": "供应链", "content": "fallback"}]
    prompt = build_prompt(query, chunks, LANG_ZH_SIMPLIFIED)

    # Prompt 应包含 original_text（繁体「供應鏈」），而非 search_text（简体「供应链」）
    assert "供應鏈" in prompt
    # 注意：query 本身是简体「供应链」，会出现在「用户问题」一栏，因此不能用
    # 「"供应链" not in prompt」断言，而应验证 context 部分（【1】之后）使用 original_text。
    assert "【1】 供應鏈" in prompt


@skip_if_no_prompt_builder
def test_build_prompt_unknown_lang_defaults_simplified():
    """未知语言应默认使用简体中文指令。"""
    query = "测试查询"
    chunks = [{"original_text": "参考资料", "search_text": "参考资料"}]
    prompt = build_prompt(query, chunks, "unknown-lang")

    assert "请用简体中文回答。" in prompt


@skip_if_no_prompt_builder
def test_build_context_max_chunks():
    """build_context 应按 max_chunks 截取前 N 条。"""
    chunks = [{"original_text": f"内容{i}", "search_text": f"内容{i}"} for i in range(5)]
    context = build_context(chunks, max_chunks=3)

    assert "【1】 内容0" in context
    assert "【2】 内容1" in context
    assert "【3】 内容2" in context
    # 第 4、5 条应被截断
    assert "【4】" not in context
    assert "【5】" not in context
    assert "内容3" not in context
    assert "内容4" not in context

    # 空列表返回空字符串
    assert build_context([], max_chunks=10) == ""

    # max_chunks=0 返回空字符串
    assert build_context(chunks, max_chunks=0) == ""


@skip_if_no_prompt_builder
def test_output_lang_instructions_constant():
    """OUTPUT_LANG_INSTRUCTIONS 常量应包含 3 种语言。"""
    assert isinstance(OUTPUT_LANG_INSTRUCTIONS, dict)
    assert LANG_ZH_TRADITIONAL in OUTPUT_LANG_INSTRUCTIONS
    assert LANG_ZH_SIMPLIFIED in OUTPUT_LANG_INSTRUCTIONS
    assert LANG_EN in OUTPUT_LANG_INSTRUCTIONS
    assert OUTPUT_LANG_INSTRUCTIONS[LANG_ZH_TRADITIONAL] == "请用繁体中文回答。"
    assert OUTPUT_LANG_INSTRUCTIONS[LANG_ZH_SIMPLIFIED] == "请用简体中文回答。"
    assert OUTPUT_LANG_INSTRUCTIONS[LANG_EN] == "Please answer in English."


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
