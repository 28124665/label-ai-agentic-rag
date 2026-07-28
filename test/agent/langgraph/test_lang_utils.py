#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
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
"""语言工具模块测试。"""

import unittest

from agent.langgraph.utils.lang_utils import (
    detect_query_language,
    get_query_simplified,
    to_historical_lang,
    to_langgraph_lang,
)


class TestLangUtils(unittest.TestCase):
    """测试语言标识映射与检测。"""

    def test_to_historical_lang(self):
        """测试 LangGraph -> 历史标识映射。"""
        self.assertEqual(to_historical_lang("zh_CN"), "zh-simplified")
        self.assertEqual(to_historical_lang("zh_TW"), "zh-traditional")
        self.assertEqual(to_historical_lang("en"), "en")
        self.assertEqual(to_historical_lang("unknown"), "zh-simplified")

    def test_to_langgraph_lang(self):
        """测试历史 -> LangGraph 标识映射。"""
        self.assertEqual(to_langgraph_lang("zh-simplified"), "zh_CN")
        self.assertEqual(to_langgraph_lang("zh-traditional"), "zh_TW")
        self.assertEqual(to_langgraph_lang("en"), "en")
        self.assertEqual(to_langgraph_lang("fr"), "zh_CN")

    def test_detect_query_language_traditional(self):
        """测试繁体中文检测。"""
        langgraph, historical = detect_query_language("供應鏈管理的最佳實踐")
        self.assertEqual(historical, "zh-traditional")
        self.assertEqual(langgraph, "zh_TW")

    def test_detect_query_language_simplified(self):
        """测试简体中文检测。"""
        langgraph, historical = detect_query_language("供应链管理的最佳实践")
        self.assertEqual(historical, "zh-simplified")
        self.assertEqual(langgraph, "zh_CN")

    def test_detect_query_language_english(self):
        """测试英文检测。"""
        langgraph, historical = detect_query_language("Supply chain best practices")
        self.assertEqual(historical, "en")
        self.assertEqual(langgraph, "en")

    def test_get_query_simplified(self):
        """测试繁转简。"""
        self.assertEqual(
            get_query_simplified("供應鏈管理的最佳實踐", "zh-traditional"),
            "供应链管理的最佳实践",
        )
        self.assertEqual(
            get_query_simplified("供应链管理的最佳实践", "zh-simplified"),
            "供应链管理的最佳实践",
        )
        self.assertEqual(
            get_query_simplified("Hello world", "en"),
            "Hello world",
        )


if __name__ == "__main__":
    unittest.main()
