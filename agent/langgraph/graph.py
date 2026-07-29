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
"""LangGraph 状态图构建模块。

构建 Agent 工作流的状态图，包括节点注册、边连接和条件路由。

状态图流程：
  question_input → intent_router → [条件路由]
    ├── clarification → intent_router (用户回答后重新路由)
    ├── react_subgraph (ReAct 启用 + 复杂任务) → evidence_fusion → answerability_check
    ├── rag_tool → [条件路由]
    │     ├── db_tool (hybrid 串行) → reflection
    │     └── reflection (非 hybrid)
    ├── db_tool → reflection
    ├── plan_executor → reflection
    ├── web_tool → reflection
    └── prompt_assembly (chitchat)
  reflection → quality_check → [条件路由]
    ├── prompt_assembly (pass)
    ├── rag_tool / db_tool (retry)
    └── web_tool (fallback_web)
  prompt_assembly → llm_generate → hallucination → [条件路由]
    ├── observability → answer_output → END (pass/filter)
    ├── prompt_assembly (regenerate)
    └── answer_output → END (reject/exhausted)
"""

import logging

from langgraph.graph import END, StateGraph

from agent.langgraph.state import AgentState
from agent.langgraph.nodes.user_question import user_question_node
from agent.langgraph.nodes.intent_router import (
    after_rag_tool,
    intent_router_node,
    route_decision,
)
from agent.langgraph.nodes.rag_tool_node import rag_tool_node
from agent.langgraph.nodes.db_tool_node import db_tool_node
from agent.langgraph.nodes.web_tool_node import web_tool_node
from agent.langgraph.nodes.plan_executor import plan_executor_node
from agent.langgraph.nodes.reflection import reflection_node
from agent.langgraph.nodes.clarification import clarification_node
from agent.langgraph.nodes.quality_check import quality_check_node, quality_check_decision
from agent.langgraph.nodes.prompt_assembly import prompt_assembly_node
from agent.langgraph.nodes.llm_generate import llm_generate_node
from agent.langgraph.nodes.hallucination import hallucination_node, hallucination_decision
from agent.langgraph.nodes.observability import observability_node
from agent.langgraph.nodes.final_answer import final_answer_node
from agent.langgraph.nodes.react_subgraph import react_subgraph_node
from agent.langgraph.nodes.evidence_fusion import evidence_fusion_node
from agent.langgraph.nodes.answerability_check import (
    answerability_check_node,
    answerability_routing,
)

logger = logging.getLogger(__name__)


def build_agent_graph() -> StateGraph:
    """构建 Agent 工作流状态图。

    按照 LangGraph 的 API 构建 StateGraph：
    1. 创建 StateGraph 实例，绑定 AgentState
    2. 添加所有节点
    3. 设置入口点和边
    4. 添加条件路由

    注意：intent_router_node 现在是 async 函数，LangGraph 支持 async 节点，
    但需要使用 ainvoke 而不是 invoke 来执行图。

    Returns:
        StateGraph: 编译后的状态图
    """
    # 1. 创建状态图
    graph = StateGraph(AgentState)

    # 2. 添加节点（节点 ID 不能与 state key 重名）
    # intent_router_node 现在是 async 函数，LangGraph 支持 async 节点
    graph.add_node("question_input", user_question_node)
    graph.add_node("intent_router", intent_router_node)
    graph.add_node("clarification", clarification_node)
    graph.add_node("rag_tool", rag_tool_node)
    graph.add_node("db_tool", db_tool_node)
    graph.add_node("web_tool", web_tool_node)
    graph.add_node("plan_executor", plan_executor_node)
    graph.add_node("react_subgraph", react_subgraph_node)
    graph.add_node("evidence_fusion", evidence_fusion_node)
    graph.add_node("answerability_check", answerability_check_node)
    graph.add_node("reflection", reflection_node)
    graph.add_node("quality_check", quality_check_node)
    graph.add_node("prompt_assembly", prompt_assembly_node)
    graph.add_node("llm_generate", llm_generate_node)
    graph.add_node("hallucination", hallucination_node)
    graph.add_node("observability", observability_node)
    graph.add_node("answer_output", final_answer_node)

    # 3. 设置入口点
    graph.set_entry_point("question_input")

    # 4. 添加边

    # question_input → intent_router
    graph.add_edge("question_input", "intent_router")

    # intent_router → 条件路由（clarification / rag_tool / db_tool / plan_executor / react_subgraph / prompt_assembly）
    graph.add_conditional_edges(
        "intent_router",
        route_decision,
        {
            "clarification": "clarification",
            "rag_tool": "rag_tool",
            "db_tool": "db_tool",
            "plan_executor": "plan_executor",
            "react_subgraph": "react_subgraph",
            "prompt_assembly": "prompt_assembly",
        },
    )

    # clarification → intent_router（用户回答后重新路由）
    graph.add_edge("clarification", "intent_router")

    # ReAct 子图 → evidence_fusion → answerability_check → quality_check
    graph.add_edge("react_subgraph", "evidence_fusion")
    graph.add_conditional_edges(
        "answerability_check",
        answerability_routing,
        {
            "quality_check": "quality_check",
        },
    )
    graph.add_edge("evidence_fusion", "answerability_check")

    # rag_tool → hybrid 串行 db_tool，否则 reflection
    graph.add_conditional_edges(
        "rag_tool",
        after_rag_tool,
        {
            "db_tool": "db_tool",
            "reflection": "reflection",
        },
    )

    # db_tool → reflection（含 hybrid 串行完成后的汇合）
    graph.add_edge("db_tool", "reflection")

    # plan_executor → reflection（并行执行完所有步骤后进入反思）
    graph.add_edge("plan_executor", "reflection")

    # web_tool → reflection
    graph.add_edge("web_tool", "reflection")

    # reflection → quality_check
    graph.add_edge("reflection", "quality_check")

    # quality_check → 条件路由（prompt_assembly / rag_tool / db_tool / web_tool）
    graph.add_conditional_edges(
        "quality_check",
        quality_check_decision,
        {
            "prompt_assembly": "prompt_assembly",
            "rag_tool": "rag_tool",
            "db_tool": "db_tool",
            "web_tool": "web_tool",
        },
    )

    # prompt_assembly → llm_generate
    graph.add_edge("prompt_assembly", "llm_generate")

    # llm_generate → hallucination
    graph.add_edge("llm_generate", "hallucination")

    # hallucination → 条件路由（observability / prompt_assembly / answer_output）
    graph.add_conditional_edges(
        "hallucination",
        hallucination_decision,
        {
            "observability": "observability",
            "prompt_assembly": "prompt_assembly",
            "final_answer": "answer_output",
        },
    )

    # observability → answer_output
    graph.add_edge("observability", "answer_output")

    # answer_output → END
    graph.add_edge("answer_output", END)

    logger.info("[build_agent_graph] Agent 工作流状态图构建完成")

    return graph


def build_and_compile_agent_graph():
    """构建并编译 Agent 工作流状态图。

    Returns:
        CompiledGraph: 编译后的状态图，可直接执行
    """
    graph = build_agent_graph()
    compiled = graph.compile()
    logger.info("[build_and_compile_agent_graph] Agent 工作流状态图编译完成")
    return compiled
