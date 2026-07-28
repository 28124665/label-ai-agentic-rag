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
"""Prompt 组装节点测试。"""

import unittest

from agent.langgraph.nodes.prompt_assembly import prompt_assembly_node
from agent.langgraph.state import AgentState


class TestPromptAssemblyNode(unittest.TestCase):
    """测试 prompt_assembly_node 使用 original_text。"""

    def test_uses_original_text(self):
        """测试参考资料优先使用 original_text 而非 search_text。"""
        state: AgentState = {
            "user_question": "什麼是供應鏈管理？",
            "query_lang": "zh_TW",
            "rag_docs": [
                {
                    "original_text": "供應鏈管理是企業競爭力的核心。",
                    "search_text": "供应链管理是企业竞争力的核心。",
                    "content": "供应链管理是企业竞争力的核心。",
                    "score": 0.9,
                    "source": "doc1",
                }
            ],
            "route_target": "rag",
        }
        result = prompt_assembly_node(state)
        self.assertIn("供應鏈管理是企業競爭力的核心", result["merged_context"])
        self.assertNotIn("供应链管理是企业竞争力的核心", result["merged_context"])
        self.assertIn("請使用繁體中文回答", result["final_prompt"])

    def test_fallback_to_content(self):
        """测试无 original_text 时 fallback 到 content。"""
        state: AgentState = {
            "user_question": "什么是供应链管理？",
            "query_lang": "zh_CN",
            "rag_docs": [
                {
                    "content": "供应链管理是企业竞争力的核心。",
                    "score": 0.9,
                }
            ],
            "route_target": "rag",
        }
        result = prompt_assembly_node(state)
        self.assertIn("供应链管理是企业竞争力的核心", result["merged_context"])


if __name__ == "__main__":
    unittest.main()
