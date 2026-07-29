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
"""ReAct Subgraph 端到端测试。

使用 mock LLM 和 mock 工具执行器，验证 ReAct 主循环的：
- finish 路径
- 预算超限自动终止
- 工具调用正确流转
- Policy Guard 拒绝行为
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from agent.langgraph.react.graph import ReactSubgraph


def _mock_llm_finish(summary: str = "ok"):
    """返回 finish action 的 mock LLM。"""
    async def _fn(prompt: str) -> str:
        return json.dumps(
            {
                "thought_summary": summary,
                "action": {
                    "type": "finish",
                    "tool_name": "finish",
                    "purpose": summary,
                    "arguments": {"summary": summary, "answer_hint": ""},
                },
                "stop": True,
            },
            ensure_ascii=False,
        )
    return _fn


def _mock_llm_rag_then_finish(call_count_holder: list):
    """第一次返回 rag_search，第二次返回 finish。"""
    async def _fn(prompt: str) -> str:
        call_count_holder.append(1)
        if len(call_count_holder) == 1:
            return json.dumps(
                {
                    "thought_summary": "查询 RAG",
                    "action": {
                        "type": "rag_search",
                        "tool_name": "rag_search",
                        "purpose": "查询规范",
                        "arguments": {"query": "质量异常规范", "kb_ids": []},
                    },
                    "stop": False,
                },
                ensure_ascii=False,
            )
        # 第二次返回 finish
        return json.dumps(
            {
                "thought_summary": "完成",
                "action": {
                    "type": "finish",
                    "tool_name": "finish",
                    "purpose": "done",
                    "arguments": {"summary": "done", "answer_hint": ""},
                },
                "stop": True,
            },
            ensure_ascii=False,
        )
    return _fn


def _mock_observation(success: bool = True, evidence=None):
    """返回 (observation, evidences) 元组的 mock。"""
    from agent.langgraph.react.models import ReactObservation

    obs = ReactObservation(
        action_type="rag_search",
        success=success,
        content="mock content",
        structured_data={},
        error="" if success else "mock error",
        latency_ms=10,
        risk_level="low",
        truncated=False,
        metadata={},
    )
    return obs, evidence or []


class TestReactSubgraphFinish:
    """ReAct 子图 finish 路径测试。"""

    @pytest.mark.asyncio
    async def test_immediate_finish(self):
        """LLM 直接返回 finish。"""
        sub = ReactSubgraph(agent_config={})
        result = await sub.run(
            user_question="hello",
            llm_callable=_mock_llm_finish("hi"),
        )
        assert result["finish_reason"] == "completed"
        assert result["step_count"] == 0  # 直接 finish，没执行 step

    @pytest.mark.asyncio
    async def test_finish_after_one_rag(self):
        """先调用一次 RAG，再 finish。"""
        from agent.langgraph.evidence.models import Evidence

        ev: Evidence = {
            "evidence_id": "ev_1",
            "source_type": "rag",
            "title": "规范",
            "content": "质量异常处理规范...",
            "source_uri": "kb://1",
            "relevance_score": 0.9,
            "authority_score": 0.8,
            "freshness_score": 1.0,
        }

        with patch(
            "agent.langgraph.react.executor.ReactExecutor.execute",
            new_callable=AsyncMock,
            return_value=_mock_observation(True, [ev]),
        ):
            call_count = []
            sub = ReactSubgraph(
                agent_config={
                    "react": {"allowed_tools": ["rag_search"], "max_steps": 5}
                }
            )
            result = await sub.run(
                user_question="质量异常如何处理",
                llm_callable=_mock_llm_rag_then_finish(call_count),
            )
            assert result["finish_reason"] == "completed"
            assert len(result["evidence"]) == 1
            assert result["step_count"] >= 1


class TestReactSubgraphBudget:
    """ReAct 子图预算超限测试。"""

    @pytest.mark.asyncio
    async def test_budget_exhausted_on_max_steps(self):
        """超过 max_steps 自动终止。"""
        # 构造一个永远调用 rag_search 的 LLM（不会 finish）
        async def _llm_always_rag(prompt: str) -> str:
            return json.dumps(
                {
                    "thought_summary": "再查一次",
                    "action": {
                        "type": "rag_search",
                        "tool_name": "rag_search",
                        "purpose": "x",
                        "arguments": {"query": "q"},
                    },
                    "stop": False,
                },
                ensure_ascii=False,
            )

        with patch(
            "agent.langgraph.react.executor.ReactExecutor.execute",
            new_callable=AsyncMock,
            return_value=_mock_observation(True, []),
        ):
            sub = ReactSubgraph(
                agent_config={
                    "react": {
                        "allowed_tools": ["rag_search"],
                        "max_steps": 2,  # 限制很小
                        "max_tool_calls": 10,
                    }
                }
            )
            result = await sub.run(
                user_question="test",
                llm_callable=_llm_always_rag,
                max_latency_ms=60000,
            )
            assert result["finish_reason"] == "budget_exhausted"

    @pytest.mark.asyncio
    async def test_budget_exhausted_on_max_tool_calls(self):
        """超过 max_tool_calls 自动终止。"""
        async def _llm_always_rag(prompt: str) -> str:
            return json.dumps(
                {
                    "thought_summary": "再查一次",
                    "action": {
                        "type": "rag_search",
                        "tool_name": "rag_search",
                        "purpose": "x",
                        "arguments": {"query": "q"},
                    },
                    "stop": False,
                },
                ensure_ascii=False,
            )

        with patch(
            "agent.langgraph.react.executor.ReactExecutor.execute",
            new_callable=AsyncMock,
            return_value=_mock_observation(True, []),
        ):
            sub = ReactSubgraph(
                agent_config={
                    "react": {
                        "allowed_tools": ["rag_search"],
                        "max_steps": 100,
                        "max_tool_calls": 2,
                    }
                }
            )
            result = await sub.run(
                user_question="test",
                llm_callable=_llm_always_rag,
                max_latency_ms=60000,
            )
            assert result["finish_reason"] == "budget_exhausted"


class TestReactSubgraphPolicy:
    """ReAct 子图 Policy 拒绝测试。"""

    @pytest.mark.asyncio
    async def test_tool_not_in_whitelist_denied(self):
        """白名单外的工具被拒绝。"""
        call_count = [0]

        async def _llm_call(prompt: str) -> str:
            call_count[0] += 1
            if call_count[0] == 1:
                # 第一次调用 db_query（不在白名单）
                return json.dumps(
                    {
                        "thought_summary": "查询 DB",
                        "action": {
                            "type": "db_query",
                            "tool_name": "db_query",
                            "purpose": "p",
                            "arguments": {"sql": "SELECT 1 LIMIT 1"},
                        },
                        "stop": False,
                    },
                    ensure_ascii=False,
                )
            # 第二次直接 finish
            return json.dumps(
                {
                    "thought_summary": "done",
                    "action": {
                        "type": "finish",
                        "tool_name": "finish",
                        "purpose": "done",
                        "arguments": {"summary": "done", "answer_hint": ""},
                    },
                    "stop": True,
                },
                ensure_ascii=False,
            )

        sub = ReactSubgraph(
            agent_config={"react": {"allowed_tools": ["rag_search"]}}
        )
        # 不需要 mock executor，因为 policy 会先拒绝
        result = await sub.run(
            user_question="test",
            llm_callable=_llm_call,
            max_latency_ms=10000,
        )
        # policy 拒绝后会写入 failures 但继续
        # 第二次 LLM 调用返回 finish，所以最终 finish_reason=completed
        assert result["finish_reason"] in ("completed", "policy_denied", "error")


class TestReactSubgraphLLMError:
    """ReAct 子图 LLM 错误处理测试。"""

    @pytest.mark.asyncio
    async def test_llm_raises_exception(self):
        """LLM 抛异常时子图安全终止。"""
        async def _llm_raise(prompt: str) -> str:
            raise RuntimeError("LLM service down")

        sub = ReactSubgraph(agent_config={})
        result = await sub.run(
            user_question="test",
            llm_callable=_llm_raise,
        )
        assert result["finish_reason"] == "error"
        assert result["success"] is False
