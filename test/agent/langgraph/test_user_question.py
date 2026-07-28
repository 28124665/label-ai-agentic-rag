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
"""用户问题入口节点测试。"""

import unittest

from agent.langgraph.nodes.user_question import user_question_node
from agent.langgraph.state import AgentState


class TestUserQuestionNode(unittest.TestCase):
    """测试 user_question_node 语言检测与繁转简。"""

    def test_traditional_chinese(self):
        """测试繁体中文输入被正确检测并转换。"""
        state: AgentState = {"user_question": "供應鏈管理的最佳實踐"}
        result = user_question_node(state)
        self.assertEqual(result["query_lang"], "zh_TW")
        self.assertEqual(result["query_simplified"], "供应链管理的最佳实践")
        self.assertEqual(result["user_question"], "供應鏈管理的最佳實踐")
        self.assertEqual(result["retry_count"], 0)

    def test_simplified_chinese(self):
        """测试简体中文输入。"""
        state: AgentState = {"user_question": "供应链管理的最佳实践"}
        result = user_question_node(state)
        self.assertEqual(result["query_lang"], "zh_CN")
        self.assertEqual(result["query_simplified"], "供应链管理的最佳实践")

    def test_english(self):
        """测试英文输入。"""
        state: AgentState = {"user_question": "Supply chain best practices"}
        result = user_question_node(state)
        self.assertEqual(result["query_lang"], "en")
        self.assertEqual(result["query_simplified"], "Supply chain best practices")

    def test_empty_question(self):
        """测试空输入兜底。"""
        state: AgentState = {"user_question": ""}
        result = user_question_node(state)
        self.assertEqual(result["query_lang"], "zh_CN")
        self.assertEqual(result["query_simplified"], "")
        self.assertEqual(result["route_target"], "chitchat")


if __name__ == "__main__":
    unittest.main()
