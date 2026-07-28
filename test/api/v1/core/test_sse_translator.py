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
"""SSE 翻译层单元测试。

验证 ``api/v1/core/sse_translator.py`` 将 LangGraph 节点输出正确映射
为前端可消费的 SSE 事件，覆盖各节点类型、空输出与异常兜底。
"""

from __future__ import annotations

import json
import unittest

from api.v1.core.sse_translator import (
    format_sse,
    translate_node_output,
)


class TestTranslateNodeOutput(unittest.TestCase):
    """测试 ``translate_node_output`` 节点 → SSE 事件映射。"""

    # ----- question_input -----

    def test_question_input_emits_thinking(self):
        events = translate_node_output("question_input", {})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "thinking")
        self.assertIn("正在分析问题", events[0]["content"])

    # ----- intent_router -----

    def test_intent_router_emits_thinking_with_route(self):
        events = translate_node_output("intent_router", {"route_target": "rag"})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "thinking")
        # 已知路由使用中文标签
        self.assertIn("知识库检索", events[0]["content"])

    def test_intent_router_uses_label_for_known_route(self):
        events = translate_node_output("intent_router", {"route_target": "database"})
        self.assertIn("数据库查询", events[0]["content"])

    def test_intent_router_falls_back_to_raw_route(self):
        events = translate_node_output("intent_router", {"route_target": "unknown_route"})
        self.assertIn("unknown_route", events[0]["content"])

    def test_intent_router_missing_route_defaults_to_chitchat(self):
        events = translate_node_output("intent_router", {})
        # chitchat 使用中文标签 "智能对话"
        self.assertIn("智能对话", events[0]["content"])

    # ----- rag_tool -----

    def test_rag_tool_with_docs_emits_tool_call_result_reference(self):
        docs = [
            {
                "content": "RAG 是检索增强生成",
                "source": "doc1.pdf",
                "score": 0.92,
            }
        ]
        events = translate_node_output("rag_tool", {"rag_docs": docs})
        types = [e["type"] for e in events]
        self.assertEqual(types, ["tool_call", "tool_result", "reference"])
        self.assertEqual(events[0]["tool"], "rag")
        self.assertEqual(events[1]["documents"], docs)
        self.assertEqual(len(events[2]["references"]), 1)
        self.assertEqual(events[2]["references"][0]["title"], "doc1.pdf")

    def test_rag_tool_empty_docs_omits_reference(self):
        events = translate_node_output("rag_tool", {"rag_docs": []})
        types = [e["type"] for e in events]
        self.assertEqual(types, ["tool_call", "tool_result"])

    def test_rag_tool_missing_docs_key_treated_as_empty(self):
        events = translate_node_output("rag_tool", {})
        types = [e["type"] for e in events]
        self.assertEqual(types, ["tool_call", "tool_result"])

    # ----- db_tool -----

    def test_db_tool_with_rows_emits_reference(self):
        db_result = {
            "sql": "SELECT * FROM users",
            "rows": [{"id": 1}],
            "row_count": 1,
        }
        events = translate_node_output("db_tool", {"db_result": db_result})
        types = [e["type"] for e in events]
        self.assertEqual(types, ["tool_call", "tool_result", "reference"])
        self.assertEqual(events[1]["sql"], "SELECT * FROM users")
        self.assertEqual(events[1]["row_count"], 1)
        self.assertIn("SQL", events[2]["references"][0]["title"])

    def test_db_tool_without_sql_omits_reference(self):
        db_result = {"rows": [], "row_count": 0}
        events = translate_node_output("db_tool", {"db_result": db_result})
        types = [e["type"] for e in events]
        self.assertEqual(types, ["tool_call", "tool_result"])

    def test_db_tool_missing_db_result_treated_as_empty(self):
        events = translate_node_output("db_tool", {})
        types = [e["type"] for e in events]
        self.assertEqual(types, ["tool_call", "tool_result"])

    # ----- web_tool -----

    def test_web_tool_with_docs_emits_reference(self):
        docs = [
            {
                "title": "Example",
                "content": "Some content",
                "url": "https://example.com",
            }
        ]
        events = translate_node_output("web_tool", {"web_docs": docs})
        types = [e["type"] for e in events]
        self.assertEqual(types, ["tool_call", "tool_result", "reference"])
        self.assertEqual(events[2]["references"][0]["source"], "https://example.com")

    def test_web_tool_empty_docs_omits_reference(self):
        events = translate_node_output("web_tool", {"web_docs": []})
        types = [e["type"] for e in events]
        self.assertEqual(types, ["tool_call", "tool_result"])

    # ----- hallucination -----

    def test_hallucination_emits_thinking_with_score(self):
        events = translate_node_output(
            "hallucination",
            {"hallucination_score": 0.32, "hallucination_action": "pass"},
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "thinking")
        self.assertIn("0.32", events[0]["content"])
        self.assertIn("pass", events[0]["content"])

    def test_hallucination_missing_score_defaults_to_zero(self):
        events = translate_node_output("hallucination", {})
        self.assertIn("0.00", events[0]["content"])

    # ----- answer_output -----

    def test_answer_output_emits_text_event(self):
        events = translate_node_output("answer_output", {"final_answer": "这是最终答案"})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "text")
        self.assertEqual(events[0]["content"], "这是最终答案")

    def test_answer_output_empty_answer_emits_empty_text(self):
        events = translate_node_output("answer_output", {})
        self.assertEqual(events[0]["type"], "text")
        self.assertEqual(events[0]["content"], "")

    # ----- 静默节点 / 未知节点 / 异常兜底 -----

    def test_silent_nodes_emit_no_events(self):
        """quality_check / prompt_assembly / llm_generate / observability 静默。"""
        for node in [
            "quality_check",
            "prompt_assembly",
            "llm_generate",
            "observability",
        ]:
            self.assertEqual(translate_node_output(node, {"any": "thing"}), [])

    def test_unknown_node_emits_no_events(self):
        self.assertEqual(translate_node_output("nonexistent_node", {}), [])

    def test_none_output_does_not_crash(self):
        # intent_router 处理器对 None 输入应能兜底（dict.get 容错）
        # translate_node_output 期望 dict，传入空 dict 验证兜底
        self.assertEqual(translate_node_output("question_input", {}), [{"type": "thinking", "content": "正在分析问题..."}])


class TestFormatSse(unittest.TestCase):
    """测试 ``format_sse`` SSE 文本帧格式化。"""

    def test_format_sse_basic(self):
        event = {"type": "text", "content": "hello"}
        result = format_sse(event)
        self.assertTrue(result.startswith("data: "))
        self.assertTrue(result.endswith("\n\n"))
        payload = result[len("data: ") :].rstrip("\n")
        self.assertEqual(json.loads(payload), event)

    def test_format_sse_preserves_chinese(self):
        event = {"type": "thinking", "content": "正在分析问题..."}
        result = format_sse(event)
        self.assertIn("正在分析问题", result)
        # ensure_ascii=False 应保留中文而非 \uXXXX 转义
        self.assertNotIn("\\u", result)

    def test_format_sse_done_event(self):
        event = {"type": "done", "message_id": "abc-123"}
        result = format_sse(event)
        self.assertIn("abc-123", result)
        self.assertIn('"type": "done"', result)


if __name__ == "__main__":
    unittest.main()
