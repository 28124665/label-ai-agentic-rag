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
"""clarification_node 单元测试。

验证主动澄清交互节点的行为：
- 无 route_decision 时返回 chitchat
- 有 needs_clarification 元数据时通过 langgraph interrupt 暂停并合并用户回答
- interrupt 不可用（抛 ImportError）时降级为原路由目标
- clarification_request 被正确构建

clarification_node 内部通过 ``from langgraph.types import interrupt`` 拉起 interrupt，
且 langgraph 在测试环境已安装，因此用例 patch 的目标是 ``langgraph.types.interrupt``
（patch 该属性后，函数内的 from-import 会拿到 mock）。
"""

import pytest
from unittest.mock import patch

from agent.langgraph.nodes.clarification import clarification_node
from agent.langgraph.routers.models import RouteDecision


class TestClarificationNode:
    """clarification_node 异步节点测试。"""

    @pytest.mark.asyncio
    async def test_no_route_decision(self):
        """无 route_decision 时返回 chitchat。"""
        state = {"user_question": "test"}
        result = await clarification_node(state)
        assert result["route_target"] == "chitchat"

    @pytest.mark.asyncio
    async def test_with_clarification_metadata(self):
        """有澄清元数据时触发 interrupt 并合并用户回答。"""
        route_decision = RouteDecision(
            target="hybrid",
            confidence=0.5,
            source="llm",
            reason="low confidence",
            metadata={
                "needs_clarification": True,
                "clarification_question": "您是想查询什么？",
                "clarification_options": ["rag", "database"],
            },
        )
        state = {
            "user_question": "原始问题",
            "route_decision": route_decision,
        }
        # mock interrupt 返回用户回答
        with patch("langgraph.types.interrupt", return_value="用户回答"):
            result = await clarification_node(state)
            assert result["user_question"] != "原始问题"
            assert "用户回答" in result["user_question"]
            assert result["clarification_context"] == "用户回答"
            assert result["route_target"] == ""
            assert result["route_decision"] is None

    @pytest.mark.asyncio
    async def test_interrupt_unavailable_fallback(self):
        """interrupt 抛出 ImportError 时降级为原路由目标。"""
        route_decision = RouteDecision(
            target="rag",
            confidence=0.5,
            source="llm",
            reason="low confidence",
            metadata={"needs_clarification": True},
        )
        state = {
            "user_question": "test",
            "route_decision": route_decision,
        }
        with patch("langgraph.types.interrupt", side_effect=ImportError("no interrupt")):
            result = await clarification_node(state)
            # 降级时使用原 route_decision 的 target
            assert result["route_target"] == "rag"

    @pytest.mark.asyncio
    async def test_clarification_request_built(self):
        """验证 clarification_request 被正确构建。"""
        route_decision = RouteDecision(
            target="hybrid",
            confidence=0.4,
            source="llm",
            reason="ambiguous",
            metadata={
                "needs_clarification": True,
                "clarification_question": "请选择数据源",
                "clarification_options": ["rag", "database"],
            },
        )
        state = {
            "user_question": "查询数据",
            "route_decision": route_decision,
        }
        with patch("langgraph.types.interrupt", return_value="数据库"):
            result = await clarification_node(state)
            assert result["clarification_request"]["question"] == "请选择数据源"
            assert result["clarification_request"]["options"] == ["rag", "database"]
            assert result["clarification_request"]["original_question"] == "查询数据"
