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

    def test_injects_conversation_history_into_prompt(self):
        """测试对话历史被注入到最终 Prompt 中。"""
        state: AgentState = {
            "user_question": "它有哪些最佳实践？",
            "query_lang": "zh_CN",
            "rag_docs": [
                {
                    "content": "供应链最佳实践包括...",
                    "score": 0.9,
                    "source": "doc1",
                }
            ],
            "route_target": "rag",
            "conversation_history": [
                {"role": "user", "content": "什么是供应链管理？"},
                {"role": "assistant", "content": "供应链管理是..."},
            ],
        }
        result = prompt_assembly_node(state)
        self.assertIn("【对话历史】", result["final_prompt"])
        self.assertIn("什么是供应链管理？", result["final_prompt"])
        self.assertIn("供应链管理是...", result["final_prompt"])

    def test_chitchat_with_history_includes_history_block(self):
        """测试闲聊模式下也注入对话历史。"""
        state: AgentState = {
            "user_question": "继续说说",
            "query_lang": "zh_CN",
            "route_target": "chitchat",
            "conversation_history": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好！有什么可以帮你？"},
            ],
        }
        result = prompt_assembly_node(state)
        self.assertIn("【对话历史】", result["final_prompt"])
        self.assertIn("你好！有什么可以帮你？", result["final_prompt"])

    def test_empty_history_omits_history_block(self):
        """测试空对话历史不注入历史段落。"""
        state: AgentState = {
            "user_question": "测试",
            "query_lang": "zh_CN",
            "route_target": "chitchat",
            "conversation_history": [],
        }
        result = prompt_assembly_node(state)
        self.assertNotIn("【对话历史】", result["final_prompt"])

    def test_history_truncated_to_recent_ten(self):
        """测试对话历史仅保留最近 10 条。"""
        # 构造 15 条历史
        history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"消息{i}"} for i in range(15)]
        state: AgentState = {
            "user_question": "最新问题",
            "query_lang": "zh_CN",
            "route_target": "chitchat",
            "conversation_history": history,
        }
        result = prompt_assembly_node(state)
        # 消息0~4 应被截断，消息5~14 应保留
        self.assertNotIn("消息0", result["final_prompt"])
        self.assertNotIn("消息4", result["final_prompt"])
        self.assertIn("消息5", result["final_prompt"])
        self.assertIn("消息14", result["final_prompt"])


if __name__ == "__main__":
    unittest.main()
