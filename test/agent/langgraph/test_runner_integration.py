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
"""LangGraphRunner 集成测试。

通过 mock 替换真实节点实现，验证 ``LangGraphRunner.arun_stream`` 能够：
1. 产出正确的 ``(node_name, node_output)`` 序列
2. 将 ``conversation_history`` 与 ``agent_config`` 正确注入 initial state
3. ``node_timings`` 在多节点间通过 reducer 正确累积，不被覆盖

注意：本测试不验证真实 RAG / DB / LLM 调用，仅验证 runner 编排逻辑
与 state 注入行为。真实节点逻辑由各自单元测试覆盖。

使用 ``unittest.IsolatedAsyncioTestCase`` 以避免对 pytest-asyncio 插件的
强依赖，保证在最小测试环境下也可运行。
"""

from __future__ import annotations

import time
import unittest
from typing import Any

from agent.langgraph.runner import LangGraphRunner
from agent.langgraph.state import merge_timings


# ========== merge_timings reducer 单元测试 ==========


class TestMergeTimingsReducer(unittest.TestCase):
    """验证 ``node_timings`` 的 reducer 行为（Bug C5 修复）。"""

    def test_merge_two_disjoint_dicts(self):
        left = {"question_input": 10}
        right = {"intent_router": 25}
        merged = merge_timings(left, right)
        self.assertEqual(merged, {"question_input": 10, "intent_router": 25})

    def test_merge_right_overrides_left_for_same_key(self):
        left = {"rag_tool": 100}
        right = {"rag_tool": 200}
        merged = merge_timings(left, right)
        self.assertEqual(merged["rag_tool"], 200)

    def test_merge_with_none_left(self):
        merged = merge_timings(None, {"a": 1})
        self.assertEqual(merged, {"a": 1})

    def test_merge_with_none_right(self):
        merged = merge_timings({"a": 1}, None)
        self.assertEqual(merged, {"a": 1})

    def test_merge_both_none(self):
        self.assertEqual(merge_timings(None, None), {})

    def test_merge_empty_dicts(self):
        self.assertEqual(merge_timings({}, {}), {})


# ========== Runner 集成测试（mock 编译图） ==========


class _FakeCompiledGraph:
    """模拟 LangGraph 编译图的 astream / ainvoke 行为。

    通过 ``stream_sequence`` 配置逐节点产出的 dict 列表，
    每个元素形如 ``{node_name: node_output}``。
    """

    def __init__(self, stream_sequence, capture_state=None):
        self._stream_sequence = stream_sequence
        self._capture_state = capture_state

    async def astream(self, initial_state):
        if self._capture_state is not None:
            self._capture_state.update(initial_state)
        for chunk in self._stream_sequence:
            yield chunk

    async def ainvoke(self, initial_state):
        if self._capture_state is not None:
            self._capture_state.update(initial_state)
        # 合并所有节点输出到最终状态
        final = dict(initial_state)
        for chunk in self._stream_sequence:
            for node_name, node_output in chunk.items():
                final.update(node_output)
        return final


class TestRunnerStreamIntegration(unittest.IsolatedAsyncioTestCase):
    """验证 ``LangGraphRunner.arun_stream`` 的编排与 state 注入。"""

    async def test_arun_stream_emits_node_sequence(self):
        """验证 arun_stream 产出正确的节点序列与参数注入。"""
        runner = LangGraphRunner()
        captured_initial_state: dict[str, Any] = {}

        sequence = [
            {"question_input": {"query_lang": "zh_CN", "graph_start_time": time.time(), "node_timings": {"question_input": 10}}},
            {"intent_router": {"route_target": "rag", "node_timings": {"intent_router": 25}}},
            {"answer_output": {"final_answer": "测试答案", "node_timings": {"answer_output": 50}}},
        ]
        runner._compiled_graph = _FakeCompiledGraph(sequence, capture_state=captured_initial_state)

        conversation_history = [
            {"role": "user", "content": "上一个问题"},
            {"role": "assistant", "content": "上一个回答"},
        ]
        agent_config = {
            "tools_config": {"tools": ["rag"], "kb_ids": ["kb1"]},
            "model_config": {"llm_id": "gpt-4"},
        }

        results: list[tuple[str, dict[str, Any]]] = []
        async for node_name, node_output in runner.arun_stream(
            user_question="测试问题",
            tenant_id="tenant_001",
            llm_id="gpt-4",
            kb_ids=["kb1"],
            db_id="db1",
            conversation_history=conversation_history,
            agent_config=agent_config,
        ):
            results.append((node_name, node_output))

        # 验证节点序列
        node_names = [name for name, _ in results]
        self.assertEqual(node_names, ["question_input", "intent_router", "answer_output"])

        # 验证最终答案被正确产出
        self.assertEqual(results[-1][1]["final_answer"], "测试答案")

        # 验证 conversation_history 注入 initial state
        self.assertEqual(
            captured_initial_state["conversation_history"],
            conversation_history,
        )

        # 验证 agent_config 注入 initial state
        self.assertEqual(captured_initial_state["agent_config"], agent_config)

        # 验证其他参数注入
        self.assertEqual(captured_initial_state["user_question"], "测试问题")
        self.assertEqual(captured_initial_state["tenant_id"], "tenant_001")
        self.assertEqual(captured_initial_state["kb_ids"], ["kb1"])
        self.assertEqual(captured_initial_state["db_id"], "db1")

    async def test_arun_stream_defaults_when_history_and_config_omitted(self):
        """省略 conversation_history / agent_config 时使用空兜底。"""
        runner = LangGraphRunner()
        captured: dict[str, Any] = {}

        runner._compiled_graph = _FakeCompiledGraph(
            [{"answer_output": {"final_answer": "x"}}],
            capture_state=captured,
        )

        results = []
        async for name, output in runner.arun_stream(user_question="hi"):
            results.append((name, output))

        self.assertEqual(captured["conversation_history"], [])
        self.assertEqual(captured["agent_config"], {})
        self.assertEqual(results[0][0], "answer_output")

    def test_node_timings_accumulate_via_reducer(self):
        """验证 node_timings 通过 reducer 累积，不被后续节点覆盖（Bug C5 回归）。"""
        timings_sequence = [
            {"question_input": 10},
            {"intent_router": 25},
            {"answer_output": 50},
        ]
        accumulated: dict[str, float] = {}
        for t in timings_sequence:
            accumulated = merge_timings(accumulated, t)

        self.assertEqual(
            accumulated,
            {
                "question_input": 10,
                "intent_router": 25,
                "answer_output": 50,
            },
        )

    async def test_arun_returns_final_state(self):
        """验证 arun 返回包含 final_answer 的最终状态。"""
        runner = LangGraphRunner()
        captured: dict[str, Any] = {}

        sequence = [
            {"question_input": {"node_timings": {"question_input": 10}}},
            {"answer_output": {"final_answer": "最终答案", "node_timings": {"answer_output": 50}}},
        ]
        runner._compiled_graph = _FakeCompiledGraph(sequence, capture_state=captured)

        final_state = await runner.arun(
            user_question="问题",
            conversation_history=[{"role": "user", "content": "历史"}],
            agent_config={"tools_config": {"tools": ["rag"]}},
        )

        self.assertEqual(final_state["final_answer"], "最终答案")
        self.assertEqual(
            final_state["conversation_history"],
            [{"role": "user", "content": "历史"}],
        )
        self.assertEqual(
            final_state["agent_config"],
            {"tools_config": {"tools": ["rag"]}},
        )


if __name__ == "__main__":
    unittest.main()
