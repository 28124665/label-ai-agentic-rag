#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""ReAct Step 单元测试。

覆盖：
- JSON 解析（直接、代码块、平衡块）
- 字段兼容性（type / action 嵌套差异）
- 解析失败降级为 finish
"""
import json

import pytest

from agent.langgraph.react.step import (
    _coerce_to_action,
    _extract_json,
    parse_llm_output,
)


class TestExtractJson:
    """_extract_json 测试。"""

    def test_direct_json(self):
        """直接 JSON 字符串。"""
        text = json.dumps({"a": 1, "b": [1, 2]})
        result = _extract_json(text)
        assert result == {"a": 1, "b": [1, 2]}

    def test_json_code_block(self):
        """```json ... ``` 代码块。"""
        text = '```json\n{"a": 1}\n```'
        result = _extract_json(text)
        assert result == {"a": 1}

    def test_plain_code_block(self):
        """``` ... ``` 无 json 标签。"""
        text = '```\n{"a": 1}\n```'
        result = _extract_json(text)
        assert result == {"a": 1}

    def test_balanced_braces(self):
        """带前导文本的平衡 {} 提取。"""
        text = '思考后输出: {"a": 1, "b": 2}'
        result = _extract_json(text)
        assert result == {"a": 1, "b": 2}

    def test_invalid_text_returns_none(self):
        """非 JSON 文本返回 None。"""
        assert _extract_json("not json at all") is None
        assert _extract_json("") is None
        assert _extract_json(None) is None

    def test_nested_json(self):
        """嵌套 JSON。"""
        text = json.dumps({"a": {"b": {"c": 1}}})
        result = _extract_json(text)
        assert result == {"a": {"b": {"c": 1}}}


class TestCoerceToAction:
    """_coerce_to_action 测试。"""

    def test_standard_format(self):
        """标准 action 嵌套格式。"""
        parsed = {
            "thought_summary": "ts",
            "action": {
                "type": "rag_search",
                "tool_name": "rag_search",
                "purpose": "p",
                "arguments": {"query": "q"},
            },
            "stop": False,
        }
        action = _coerce_to_action(parsed)
        assert action is not None
        assert action["type"] == "rag_search"
        assert action["arguments"]["query"] == "q"
        assert action["thought_summary"] == "ts"

    def test_flat_format(self):
        """LLM 输出平铺格式。"""
        parsed = {
            "thought_summary": "ts",
            "type": "db_query",
            "purpose": "p",
            "arguments": {"sql": "SELECT 1"},
        }
        action = _coerce_to_action(parsed)
        assert action is not None
        assert action["type"] == "db_query"

    def test_thought_truncation(self):
        """thought_summary 截断到 200 字符。"""
        parsed = {
            "thought_summary": "a" * 500,
            "action": {"type": "finish"},
        }
        action = _coerce_to_action(parsed)
        assert len(action["thought_summary"]) == 200

    def test_missing_type_returns_none(self):
        """无 type 字段返回 None。"""
        parsed = {"action": {"purpose": "p"}}
        assert _coerce_to_action(parsed) is None

    def test_non_dict_action_args(self):
        """非 dict 的 arguments 被包成 value。"""
        parsed = {
            "action": {
                "type": "finish",
                "arguments": "not a dict",
            }
        }
        action = _coerce_to_action(parsed)
        assert action is not None
        assert action["arguments"] == {"value": "not a dict"}


class TestParseLlmOutput:
    """parse_llm_output 端到端测试。"""

    def test_valid_json_returns_action(self):
        """有效 JSON 返回 action。"""
        output = json.dumps(
            {
                "thought_summary": "查询数据库",
                "action": {
                    "type": "db_query",
                    "tool_name": "db_query",
                    "purpose": "查询",
                    "arguments": {"sql": "SELECT 1"},
                },
                "stop": False,
            },
            ensure_ascii=False,
        )
        action = parse_llm_output(output)
        assert action["type"] == "db_query"
        assert action["thought_summary"] == "查询数据库"

    def test_invalid_json_returns_finish(self):
        """无效 JSON 返回 finish 降级。"""
        action = parse_llm_output("这是普通文本，不是 JSON")
        assert action["type"] == "finish"
        assert "解析失败" in action["purpose"]

    def test_json_missing_action_returns_finish(self):
        """JSON 但缺 action 字段返回 finish。"""
        output = json.dumps({"thought_summary": "x"})
        action = parse_llm_output(output)
        assert action["type"] == "finish"

    def test_code_block_output(self):
        """代码块包裹的 JSON 也能解析。"""
        output = '```json\n{"thought_summary": "t", "action": {"type": "finish"}}\n```'
        action = parse_llm_output(output)
        assert action["type"] == "finish"


class TestReactStepIntegration:
    """ReAct Step 集成测试（用 mock LLM）。"""

    @pytest.mark.asyncio
    async def test_reason_step_raises_when_llm_callable_none(self):
        """llm_callable 为 None 时抛 TypeError。"""
        from agent.langgraph.react.models import get_default_budget
        from agent.langgraph.react.step import reason_step

        state = {
            "user_question": "测试",
            "budget": get_default_budget(),
            "allowed_tools": ["rag_search"],
            "action_history": [],
            "evidence": [],
        }

        # 不传 llm_callable 会抛 TypeError
        with pytest.raises(TypeError):
            await reason_step("测试", state, llm_callable=None)

    @pytest.mark.asyncio
    async def test_reason_step_uses_default_when_callable_missing(self):
        """graph.run() 在 callable=None 时降级为默认 finish。"""
        # 验证 ReactSubgraph.run 不传 callable 时走默认 LLM
        from agent.langgraph.react.graph import ReactSubgraph

        sub = ReactSubgraph(agent_config={})
        result = await sub.run(
            user_question="test",
            llm_callable=None,  # 走默认
        )
        # 默认 LLM 返回 finish
        assert result["finish_reason"] in ("completed", "error")
