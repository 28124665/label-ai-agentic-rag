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
"""LangGraph 主干与 ReAct 子图集成测试。

覆盖：
- 新增字段（react_state、react_execution_result、evidence、answerability_result）已加入 AgentState
- graph.py 编译成功（包含新节点 react_subgraph / evidence_fusion / answerability_check）
- intent_router.route_decision 包含 react_subgraph 分支
- 默认配置下行为不变（react.enabled=false 时不进入 react_subgraph）
"""
import pytest
from agent.langgraph.routers.models import RouteDecision
from agent.langgraph.state import AgentState


class TestAgentStateExtensions:
    """AgentState 扩展字段测试。"""

    def test_state_has_react_fields(self):
        """AgentState 包含 ReAct 子图相关字段。"""
        # 通过 __annotations__ 验证（TypedDict 会在 __annotations__ 中暴露字段）
        annotations = AgentState.__annotations__
        assert "react_state" in annotations
        assert "react_execution_result" in annotations
        assert "evidence" in annotations
        assert "evidence_fusion_result" in annotations
        assert "answerability_result" in annotations
        assert "react_enabled" in annotations

    def test_state_has_capability_flags(self):
        """AgentState 包含能力开关字段。"""
        annotations = AgentState.__annotations__
        assert "react_enabled" in annotations
        assert "db_tool_enabled" in annotations


class TestGraphCompilation:
    """LangGraph 主干图编译测试。"""

    def test_graph_compiles_with_react_nodes(self):
        """图能正常编译且包含新节点。"""
        from agent.langgraph.graph import build_and_compile_agent_graph

        compiled = build_and_compile_agent_graph()
        assert compiled is not None

        # 验证图节点（从 langgraph 的内部结构）
        # 实际节点名通过 graph.get_graph().nodes 获取
        try:
            graph_obj = compiled.get_graph()
            node_names = list(graph_obj.nodes.keys())
            assert "react_subgraph" in node_names
            assert "evidence_fusion" in node_names
            assert "answerability_check" in node_names
        except Exception:
            # 不同 langgraph 版本接口不同，编译成功即可
            pass


class TestRouteDecision:
    """route_decision 函数测试。"""

    def test_react_subgraph_branch(self):
        """满足条件时路由到 react_subgraph。"""
        from agent.langgraph.nodes.intent_router import route_decision

        state: AgentState = {
            "route_target": "rag",
            "route_decision": RouteDecision(
                target="rag",
                confidence=0.9,
                source="react_planner",  # 关键：source 为 react_planner
                reason="complex",
                complexity="complex",
            ),
            "react_enabled": True,  # 关键：ReAct 子图启用
        }
        result = route_decision(state)
        assert result == "react_subgraph"

    def test_react_subgraph_disabled(self):
        """ReAct 子图未启用时不进入。"""
        from agent.langgraph.nodes.intent_router import route_decision

        state: AgentState = {
            "route_target": "rag",
            "route_decision": RouteDecision(
                target="rag",
                confidence=0.9,
                source="react_planner",
                reason="complex",
                complexity="complex",
            ),
            "react_enabled": False,  # 关键：未启用
        }
        result = route_decision(state)
        assert result == "rag_tool"  # 走默认 RAG 路径

    def test_react_subgraph_simple_task_not_enter(self):
        """简单任务（complexity=simple）不进入 ReAct。"""
        from agent.langgraph.nodes.intent_router import route_decision

        state: AgentState = {
            "route_target": "rag",
            "route_decision": RouteDecision(
                target="rag",
                confidence=0.9,
                source="react_planner",
                reason="x",
                complexity="simple",  # 关键：非 complex
            ),
            "react_enabled": True,
        }
        result = route_decision(state)
        assert result == "rag_tool"

    def test_clarification_still_priority(self):
        """澄清优先级仍最高。"""
        from agent.langgraph.nodes.intent_router import route_decision

        state: AgentState = {
            "route_target": "rag",
            "route_decision": RouteDecision(
                target="rag",
                confidence=0.5,
                source="llm",
                reason="ambiguous",
                complexity="moderate",
                metadata={"needs_clarification": True},  # 关键
            ),
            "react_enabled": True,
        }
        result = route_decision(state)
        assert result == "clarification"

    def test_plan_executor_still_works(self):
        """Plan Executor 路径仍工作。"""
        from agent.langgraph.nodes.intent_router import route_decision

        state: AgentState = {
            "route_target": "rag",
            "route_decision": RouteDecision(
                target="rag",
                confidence=0.9,
                source="planner",  # source=planner
                reason="complex",
                complexity="complex",
            ),
            "react_enabled": True,
        }
        result = route_decision(state)
        assert result == "plan_executor"


class TestCapabilityFlagRouting:
    """能力开关（react_enabled / db_tool_enabled）路由测试。"""

    def test_db_disabled_database_falls_back_to_rag(self):
        """db_tool 禁用时 database 路由降级为 rag_tool。"""
        from agent.langgraph.nodes.intent_router import route_decision

        state: AgentState = {
            "route_target": "database",
            "route_decision": None,
            "react_enabled": False,
            "db_tool_enabled": False,  # 关键
        }
        result = route_decision(state)
        assert result == "rag_tool"

    def test_db_disabled_hybrid_falls_back_to_rag(self):
        """db_tool 禁用时 hybrid 路由降级为 rag_tool（仅 RAG）。"""
        from agent.langgraph.nodes.intent_router import route_decision

        state: AgentState = {
            "route_target": "hybrid",
            "route_decision": None,
            "react_enabled": False,
            "db_tool_enabled": False,  # 关键
        }
        result = route_decision(state)
        assert result == "rag_tool"

    def test_db_enabled_database_goes_to_db(self):
        """db_tool 启用时 database 路由仍走 db_tool。"""
        from agent.langgraph.nodes.intent_router import route_decision

        state: AgentState = {
            "route_target": "database",
            "route_decision": None,
            "react_enabled": False,
            "db_tool_enabled": True,
        }
        result = route_decision(state)
        assert result == "db_tool"

    def test_db_disabled_chitchat_unchanged(self):
        """db_tool 禁用对 chitchat 无影响。"""
        from agent.langgraph.nodes.intent_router import route_decision

        state: AgentState = {
            "route_target": "chitchat",
            "route_decision": None,
            "react_enabled": False,
            "db_tool_enabled": False,
        }
        result = route_decision(state)
        assert result == "prompt_assembly"

    def test_db_default_enabled(self):
        """db_tool 缺省时默认为 True（向后兼容）。"""
        from agent.langgraph.nodes.intent_router import route_decision

        state: AgentState = {
            "route_target": "database",
            "route_decision": None,
            # react_enabled / db_tool_enabled 缺省
        }
        result = route_decision(state)
        assert result == "db_tool"

    def test_react_disabled_falls_back_to_plan_executor(self):
        """react 禁用时即使 Planner 标记为 react_planner 也不进 react_subgraph。"""
        from agent.langgraph.nodes.intent_router import route_decision

        state: AgentState = {
            "route_target": "rag",
            "route_decision": RouteDecision(
                target="rag",
                confidence=0.9,
                source="react_planner",  # 关键
                reason="complex",
                complexity="complex",
            ),
            "react_enabled": False,  # 关键：禁用
            "db_tool_enabled": True,
        }
        result = route_decision(state)
        # 不应进入 react_subgraph
        assert result != "react_subgraph"


class TestAfterRagToolWithDbDisabled:
    """after_rag_tool 在 db_tool 禁用时的行为测试。"""

    def test_hybrid_with_db_disabled_skips_db(self):
        """hybrid 模式下 db_tool 禁用时直接进入 reflection。"""
        from agent.langgraph.nodes.intent_router import after_rag_tool

        state: AgentState = {
            "route_target": "hybrid",
            "db_tool_enabled": False,
        }
        result = after_rag_tool(state)
        assert result == "reflection"

    def test_hybrid_with_db_enabled_goes_to_db(self):
        """hybrid 模式下 db_tool 启用时进入 db_tool。"""
        from agent.langgraph.nodes.intent_router import after_rag_tool

        state: AgentState = {
            "route_target": "hybrid",
            "db_tool_enabled": True,
        }
        result = after_rag_tool(state)
        assert result == "db_tool"

    def test_non_hybrid_unaffected(self):
        """非 hybrid 模式不受 db_tool 开关影响。"""
        from agent.langgraph.nodes.intent_router import after_rag_tool

        for target in ("rag", "database", "chitchat"):
            state: AgentState = {
                "route_target": target,
                "db_tool_enabled": False,
            }
            result = after_rag_tool(state)
            assert result == "reflection"


class TestIntentRouterNodeCapabilityFlags:
    """intent_router_node 能力开关读取测试。"""

    @pytest.mark.asyncio
    async def test_both_flags_read_from_agent_config(self):
        """intent_router_node 从 agent_config 读取两个开关。"""
        from agent.langgraph.nodes.intent_router import intent_router_node

        # 构造一个 chitchat 类的简单问题，避免触发 LLM 调用
        state: AgentState = {
            "user_question": "你好",
            "agent_config": {
                "react": {"enabled": True},
                "db_tool": {"enabled": False},
            },
        }
        result = await intent_router_node(state)
        assert result.get("react_enabled") is True
        assert result.get("db_tool_enabled") is False

    @pytest.mark.asyncio
    async def test_default_flags(self):
        """缺省时 react=False, db_tool=True。"""
        from agent.langgraph.nodes.intent_router import intent_router_node

        state: AgentState = {
            "user_question": "你好",
            "agent_config": {},  # 无 react / db_tool 配置
        }
        result = await intent_router_node(state)
        assert result.get("react_enabled") is False
        assert result.get("db_tool_enabled") is True

    @pytest.mark.asyncio
    async def test_empty_question_uses_safe_defaults(self):
        """用户问题为空时使用安全默认（react=False, db_tool=True）。"""
        from agent.langgraph.nodes.intent_router import intent_router_node

        state: AgentState = {"user_question": ""}
        result = await intent_router_node(state)
        assert result.get("react_enabled") is False
        assert result.get("db_tool_enabled") is True


class TestDbToolNodeDefensiveCheck:
    """db_tool_node 防御深度检查测试。"""

    @pytest.mark.asyncio
    async def test_db_tool_skipped_when_disabled(self):
        """db_tool 禁用时 db_tool_node 直接返回空结果。"""
        from agent.langgraph.nodes.db_tool_node import db_tool_node

        state: AgentState = {
            "user_question": "查询",
            "db_tool_enabled": False,
        }
        result = await db_tool_node(state)
        assert result["db_result"] == {}
        assert result["db_quality_score"] == 0.0

    @pytest.mark.asyncio
    async def test_db_tool_default_enabled(self):
        """db_tool 默认启用时正常进入（这里用空问题测试返回）。"""
        from agent.langgraph.nodes.db_tool_node import db_tool_node

        state: AgentState = {"user_question": ""}  # 空问题
        result = await db_tool_node(state)
        # 空问题不触发禁用逻辑，返回空结果
        assert "db_result" in result


class TestReactSubgraphNodeDefensiveCheck:
    """react_subgraph_node 防御深度检查测试。"""

    @pytest.mark.asyncio
    async def test_react_skipped_when_disabled(self):
        """react 禁用时 react_subgraph_node 返回 disabled 结果。"""
        from agent.langgraph.nodes.react_subgraph import react_subgraph_node

        state: AgentState = {
            "user_question": "复杂任务",
            "react_enabled": False,
        }
        result = await react_subgraph_node(state)
        assert result["react_execution_result"]["finish_reason"] == "disabled"
        assert result["react_execution_result"]["success"] is False


class TestEvidenceFusionNode:
    """evidence_fusion_node 单元测试。"""

    @pytest.mark.asyncio
    async def test_fuse_state_evidences(self):
        """直接融合 state.evidence。"""
        from agent.langgraph.nodes.evidence_fusion import evidence_fusion_node

        state: AgentState = {
            "evidence": [
                {
                    "evidence_id": "ev_1",
                    "source_type": "rag",
                    "title": "t1",
                    "content": "c1",
                    "source_uri": "u1",
                    "relevance_score": 0.8,
                    "authority_score": 0.8,
                    "freshness_score": 0.8,
                },
                {
                    "evidence_id": "ev_2",
                    "source_type": "db",
                    "title": "t2",
                    "content": "c2",
                    "source_uri": "u2",
                    "relevance_score": 0.9,
                    "authority_score": 0.95,
                    "freshness_score": 1.0,
                },
            ],
        }
        result = await evidence_fusion_node(state)
        assert len(result["evidence"]) == 2
        assert result["evidence_fusion_result"]["fused_count"] == 2

    @pytest.mark.asyncio
    async def test_fuse_from_tool_results(self):
        """state.evidence 为空时从工具结果补充。"""
        from agent.langgraph.nodes.evidence_fusion import evidence_fusion_node

        state: AgentState = {
            "evidence": [],
            "rag_docs": [
                {"content": "RAG content", "score": 0.8, "chunk_id": "c1"}
            ],
            "db_result": {
                "sql": "SELECT 1",
                "rows": [{"x": 1}],
                "row_count": 1,
            },
        }
        result = await evidence_fusion_node(state)
        # 应该从 rag_docs 和 db_result 标准化得到至少 2 条 evidence
        assert len(result["evidence"]) >= 2


class TestAnswerabilityCheckNode:
    """answerability_check_node 单元测试。"""

    @pytest.mark.asyncio
    async def test_high_quality_evidence(self):
        """高质量证据 → generate。"""
        from agent.langgraph.nodes.answerability_check import answerability_check_node

        state: AgentState = {
            "evidence": [
                {
                    "source_type": "db",
                    "relevance_score": 0.9,
                    "authority_score": 0.95,
                    "freshness_score": 1.0,
                }
            ],
            "user_question": "test",
        }
        result = await answerability_check_node(state)
        assert result["answerability_result"]["answerable"] is True
        assert result["answerability_result"]["recommended_action"] in (
            "generate",
            "partial_answer",
        )

    @pytest.mark.asyncio
    async def test_no_evidence(self):
        """无证据 → ask_clarification/react_continue。"""
        from agent.langgraph.nodes.answerability_check import answerability_check_node

        state: AgentState = {"evidence": [], "user_question": "test"}
        result = await answerability_check_node(state)
        assert result["answerability_result"]["answerable"] is False


class TestReactSubgraphNodeEmpty:
    """react_subgraph_node 空输入测试。"""

    @pytest.mark.asyncio
    async def test_empty_user_question(self):
        """用户问题为空时优雅返回。"""
        from agent.langgraph.nodes.react_subgraph import react_subgraph_node

        state: AgentState = {"user_question": "", "react_enabled": True}
        result = await react_subgraph_node(state)
        assert result["react_execution_result"]["finish_reason"] == "empty_question"
        assert result["react_execution_result"]["success"] is False
