#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
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
"""reflection_node 单元测试。

验证工具结果反思节点的行为：
- 无工具结果时返回 None
- 正常 LLM 反思路径
- LLM 超时/失败时降级
- 缺少 tenant_id 时降级
- 多种工具结果（RAG / DB / Web）收集

参考 test/agent/component/test_grader.py 的 stub 模式：真实 api.utils.reflection
模块在顶部 import json_repair（测试环境未安装），且 call_reflection_llm 内部
依赖 TenantLLMService / common.constants 等 DB 栈，故用轻量 stub 替换该模块，
使 reflection_node 内的 ``from api.utils.reflection import ...`` 拿到可控函数，
再由各用例按需 patch。
"""

import asyncio
import sys
import types
from unittest.mock import AsyncMock, patch

import pytest

# ---------- 模块 stub ----------
# 用轻量 stub 替换 api.utils.reflection，隔离 json_repair / rag.prompts / DB 栈依赖。
_reflection_stub = types.ModuleType("api.utils.reflection")


async def _default_call_reflection_llm(*args, **kwargs):
    # 默认返回合法 JSON 字符串；具体用例会 patch 覆盖
    return '{"key_findings": [], "info_sufficient": true}'


def _default_parse_reflection_result(llm_output: str) -> dict:
    return {
        "key_findings": [],
        "info_sufficient": True,
        "gaps": [],
        "next_action": "proceed_to_generate",
        "reflection_text": llm_output or "",
    }


def _default_fallback_reflection(tool_results: list[dict]) -> dict:
    # 与真实 fallback_reflection 行为一致：info_sufficient=True 让流程继续
    findings = []
    for r in tool_results or []:
        tool = r.get("tool", "unknown")
        content = r.get("content", "")
        if content:
            preview = content[:80] + ("..." if len(content) > 80 else "")
            findings.append(f"[{tool}] {preview}")
    return {
        "key_findings": findings,
        "info_sufficient": True,
        "gaps": [],
        "next_action": "proceed_to_generate",
        "reflection_text": "（降级反思：未调用 LLM，默认信息充分）",
    }


_reflection_stub.call_reflection_llm = _default_call_reflection_llm
_reflection_stub.parse_reflection_result = _default_parse_reflection_result
_reflection_stub.fallback_reflection = _default_fallback_reflection
sys.modules["api.utils.reflection"] = _reflection_stub

from agent.langgraph.nodes.reflection import (  # noqa: E402
    _collect_tool_results,
    _safe_fallback,
    reflection_node,
)


class TestCollectToolResults:
    """_collect_tool_results 纯函数测试。"""

    def test_empty_state(self):
        """无任何工具结果时返回空列表。"""
        state = {}
        assert _collect_tool_results(state) == []

    def test_rag_docs(self):
        """有 RAG 结果时收集。"""
        state = {"rag_docs": [{"content": "doc1"}, {"content": "doc2"}]}
        results = _collect_tool_results(state)
        assert len(results) == 1
        assert results[0]["tool"] == "rag"
        assert "doc1" in results[0]["content"]

    def test_db_result(self):
        """有 DB 结果时收集。"""
        state = {"db_result": {"rows": [{"id": 1}], "row_count": 1}}
        results = _collect_tool_results(state)
        assert len(results) == 1
        assert results[0]["tool"] == "database"

    def test_web_docs(self):
        """有 Web 结果时收集。"""
        state = {"web_docs": [{"content": "web result"}]}
        results = _collect_tool_results(state)
        assert len(results) == 1
        assert results[0]["tool"] == "web"

    def test_mixed_results(self):
        """多种工具结果混合收集。"""
        state = {
            "rag_docs": [{"content": "rag doc"}],
            "db_result": {"rows": [{"id": 1}]},
            "web_docs": [{"content": "web doc"}],
        }
        results = _collect_tool_results(state)
        assert len(results) == 3
        tools = {r["tool"] for r in results}
        assert tools == {"rag", "database", "web"}


class TestReflectionNode:
    """reflection_node 异步节点测试。"""

    @pytest.mark.asyncio
    async def test_no_tool_results(self):
        """无工具结果时返回 None。"""
        state = {"user_question": "test", "tenant_id": "t1", "llm_id": "l1"}
        result = await reflection_node(state)
        assert result["reflection_result"] is None

    @pytest.mark.asyncio
    async def test_successful_reflection(self):
        """正常反思：mock LLM 调用与结果解析。"""
        state = {
            "user_question": "什么是供应链？",
            "tenant_id": "t1",
            "llm_id": "l1",
            "rag_docs": [{"content": "供应链是指..."}],
        }
        with patch("api.utils.reflection.call_reflection_llm", new_callable=AsyncMock) as mock_call, \
             patch("api.utils.reflection.parse_reflection_result") as mock_parse:
            mock_call.return_value = '{"key_findings": ["test"], "info_sufficient": true}'
            mock_parse.return_value = {
                "key_findings": ["test"],
                "info_sufficient": True,
                "gaps": [],
                "next_action": "proceed_to_generate",
                "reflection_text": "test",
            }
            result = await reflection_node(state)
            assert result["reflection_result"]["info_sufficient"] is True
            mock_call.assert_called_once()
            mock_parse.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_timeout_fallback(self):
        """LLM 超时时降级（info_sufficient=True）。"""
        state = {
            "user_question": "test",
            "tenant_id": "t1",
            "llm_id": "l1",
            "rag_docs": [{"content": "doc"}],
        }
        with patch("api.utils.reflection.call_reflection_llm", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = asyncio.TimeoutError()
            result = await reflection_node(state)
            assert result["reflection_result"]["info_sufficient"] is True

    @pytest.mark.asyncio
    async def test_llm_failure_fallback(self):
        """LLM 失败时降级（info_sufficient=True）。"""
        state = {
            "user_question": "test",
            "tenant_id": "t1",
            "llm_id": "l1",
            "rag_docs": [{"content": "doc"}],
        }
        with patch("api.utils.reflection.call_reflection_llm", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = Exception("LLM error")
            result = await reflection_node(state)
            assert result["reflection_result"]["info_sufficient"] is True

    @pytest.mark.asyncio
    async def test_missing_tenant_id(self):
        """缺少 tenant_id 时使用本地降级（不依赖 api.utils.reflection）。"""
        state = {
            "user_question": "test",
            "rag_docs": [{"content": "doc"}],
        }
        result = await reflection_node(state)
        assert result["reflection_result"]["info_sufficient"] is True

    @pytest.mark.asyncio
    async def test_node_timings_present(self):
        """结果包含 node_timings.reflection。"""
        state = {"user_question": "test", "tenant_id": "t1", "llm_id": "l1"}
        result = await reflection_node(state)
        assert "node_timings" in result
        assert "reflection" in result["node_timings"]
