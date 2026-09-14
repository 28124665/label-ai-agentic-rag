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

状态图流程（v2.1 §3.2 Claim 级可追溯幻觉检测改造后 + ReAct 子图接入 + RestTool）：
  question_input → intent_router → [条件路由]
    ├── clarification → intent_router (用户回答后重新路由)
    ├── rag_tool → [条件路由]
    │     ├── db_tool (hybrid 串行) → evidence_fusion
    │     └── evidence_fusion (非 hybrid)
    ├── db_tool → evidence_fusion
    ├── react_subgraph → evidence_fusion (DB+RAG 交叉推理)
    ├── rest_tool → evidence_fusion (ERP 系统 REST API 调用)
    ├── plan_executor → evidence_fusion
    └── prompt_assembly (chitchat)
  web_tool → evidence_fusion
  evidence_fusion → reflection → quality_check → [条件路由]
    ├── prompt_assembly (pass)
    ├── rag_tool / db_tool (retry) → evidence_fusion
    ├── web_tool (fallback_web) → evidence_fusion
    └── rest_tool (retry) → evidence_fusion
  prompt_assembly → llm_generate → hallucination → [条件路由]
    ├── answer_renderer → observability → answer_output → END (pass/filter)
    ├── prompt_assembly (regenerate)
    └── observability → answer_output → END (reject/exhausted)

v2.1 P0-1 拓扑修复要点：
  - 所有工具产出（rag_tool/db_tool/plan_executor/web_tool）必须经过 evidence_fusion
    构建 EvidenceSnapshot，保证 prompt_assembly/verifier/references 复用同一不可变快照
  - 不再有无条件 quality_check → prompt_assembly 边，避免与条件边并行执行
  - hallucination 的 pass/filter 路径先经 answer_renderer 渲染（fail-closed，
    仅输出 supported Claim），再到 observability，杜绝未验证 Claim 泄露
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
from agent.langgraph.nodes.evidence_fusion import evidence_fusion_node
from agent.langgraph.nodes.quality_check import quality_check_node, quality_check_decision
from agent.langgraph.nodes.prompt_assembly import prompt_assembly_node
from agent.langgraph.nodes.llm_generate import llm_generate_node
from agent.langgraph.nodes.hallucination import hallucination_node, hallucination_decision
from agent.langgraph.nodes.answer_renderer import answer_renderer_node
from agent.langgraph.nodes.observability import observability_node
from agent.langgraph.nodes.final_answer import final_answer_node
from agent.langgraph.nodes.fallback import fallback_node
from agent.langgraph.nodes.react_subgraph import react_subgraph_node
from agent.langgraph.nodes.rest_tool_node import rest_tool_node
from agent.langgraph.nodes.graph_tool_node import graph_tool_node

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
    # ★ ReAct 子图节点：DB+RAG 交叉推理（启用需 agent_config.react.enabled = True）
    # 路由条件：react_enabled + complex + needs_multi_tool + target in (hybrid, database)
    graph.add_node("react_subgraph", react_subgraph_node)
    # ★ RestTool 节点：通过 MCP 服务调用 ERP 系统 REST API
    # 路由条件：RuleRouter 命中 ERP 关键词 + LLMRouter 确认 target="rest"
    graph.add_node("rest_tool", rest_tool_node)
    # ★ GraphTool 节点：通过 HTTP 调用 ontology 图问答服务（GraphRAG）
    # 路由条件：@graph 指令 / RuleRouter 图谱关键词命中 target="graph"
    graph.add_node("graph_tool", graph_tool_node)
    # ★ v2.1 §3.2 新增：Evidence 融合节点（工具→fusion→reflection→quality_check）
    # 不可变 EvidenceSnapshot 在此构建，确保 prompt_assembly/verifier/references 复用同一快照
    graph.add_node("evidence_fusion", evidence_fusion_node)
    graph.add_node("reflection", reflection_node)
    graph.add_node("quality_check", quality_check_node)
    graph.add_node("prompt_assembly", prompt_assembly_node)
    graph.add_node("llm_generate", llm_generate_node)
    graph.add_node("hallucination", hallucination_node)
    # ★ v2.1 §3.2 新增：结构化答案渲染节点（fail-closed，仅输出 supported Claim）
    # 位于 hallucination 之后、observability 之前，过滤未验证 Claim
    graph.add_node("answer_renderer", answer_renderer_node)
    graph.add_node("observability", observability_node)
    graph.add_node("answer_output", final_answer_node)
    # ★ P0 修正：统一终止兜底节点
    graph.add_node("fallback", fallback_node)

    # 3. 设置入口点
    graph.set_entry_point("question_input")

    # 4. 添加边

    # question_input → intent_router
    graph.add_edge("question_input", "intent_router")

    # intent_router → 条件路由（clarification / rag_tool / db_tool / react_subgraph / plan_executor / prompt_assembly）
    # ★ ReAct 子图路由：react_enabled + complex + needs_multi_tool + target in (hybrid, database)
    graph.add_conditional_edges(
        "intent_router",
        route_decision,
        {
            "clarification": "clarification",
            "rag_tool": "rag_tool",
            "db_tool": "db_tool",
            "react_subgraph": "react_subgraph",
            "plan_executor": "plan_executor",
            "rest_tool": "rest_tool",
            "web_tool": "web_tool",
            "graph_tool": "graph_tool",
            "prompt_assembly": "prompt_assembly",
        },
    )

    # clarification → intent_router（用户回答后重新路由）
    graph.add_edge("clarification", "intent_router")

    # rag_tool → hybrid 串行 db_tool，否则 evidence_fusion
    # ★ v2.1 P0-1 修复：非 hybrid 路径直接进 evidence_fusion（不再跳过融合直连 reflection）
    # 拓扑约束：所有工具产出必须经过 evidence_fusion 构建 snapshot，
    #           保证 prompt_assembly/verifier/references 复用同一不可变快照
    graph.add_conditional_edges(
        "rag_tool",
        after_rag_tool,
        {
            "db_tool": "db_tool",
            "evidence_fusion": "evidence_fusion",
        },
    )

    # db_tool → evidence_fusion（含 hybrid 串行完成后的汇合）
    # ★ v2.1 P0-1 修复：原 db_tool → reflection 改为 db_tool → evidence_fusion，
    #                   确保数据库结果也进入 snapshot
    graph.add_edge("db_tool", "evidence_fusion")

    # plan_executor → evidence_fusion（并行执行完所有步骤后进入融合）
    # ★ v2.1 P0-1 修复：原 plan_executor → reflection 改为 → evidence_fusion
    graph.add_edge("plan_executor", "evidence_fusion")

    # web_tool → evidence_fusion
    # ★ v2.1 P0-1 修复：原 web_tool → reflection 改为 → evidence_fusion
    graph.add_edge("web_tool", "evidence_fusion")

    # react_subgraph → evidence_fusion（ReAct 子图完成 DB+RAG 交叉推理后进入融合）
    graph.add_edge("react_subgraph", "evidence_fusion")

    # rest_tool → evidence_fusion（REST 调用结果进入融合）
    graph.add_edge("rest_tool", "evidence_fusion")

    # graph_tool → evidence_fusion（图问答结果进入融合）
    graph.add_edge("graph_tool", "evidence_fusion")

    # evidence_fusion → reflection（融合后进入反思）
    # ★ v2.1 §3.2 新增边：snapshot 构建完成后才进入 reflection 评估检索质量
    graph.add_edge("evidence_fusion", "reflection")

    # reflection → quality_check
    graph.add_edge("reflection", "quality_check")

    # quality_check → 条件路由（prompt_assembly / rag_tool / db_tool / web_tool / fallback）
    # ★ P0 修正：新增 fallback 路由，termination_reason 非空时走兜底节点
    graph.add_conditional_edges(
        "quality_check",
        quality_check_decision,
        {
            "prompt_assembly": "prompt_assembly",
            "rag_tool": "rag_tool",
            "db_tool": "db_tool",
            "web_tool": "web_tool",
            "rest_tool": "rest_tool",
            "graph_tool": "graph_tool",
            "fallback": "fallback",
        },
    )

    # prompt_assembly → llm_generate
    graph.add_edge("prompt_assembly", "llm_generate")

    # llm_generate → hallucination
    graph.add_edge("llm_generate", "hallucination")

    # hallucination → 条件路由（answer_renderer / observability / prompt_assembly）
    # ★ v2.1 §3.2 + P0-3 修复：路由映射与 hallucination_decision 返回值对齐
    # - pass/filter → answer_renderer：结构化渲染，仅输出 supported Claim（fail-closed）
    # - reject/exhausted → observability：跳过渲染，直接输出拒答/保守答案
    # - regenerate → prompt_assembly：回到提示词组装重新生成
    graph.add_conditional_edges(
        "hallucination",
        hallucination_decision,
        {
            "answer_renderer": "answer_renderer",
            "observability": "observability",
            "prompt_assembly": "prompt_assembly",
        },
    )

    # answer_renderer → observability（渲染完成后再记录可观测性指标）
    # ★ v2.1 §3.2 新增边：渲染后的 final_answer 进入 observability 统一埋点
    graph.add_edge("answer_renderer", "observability")

    # fallback → observability（兜底回答直接进入可观测性记录，跳过 claim 渲染）
    # ★ P0 修正：兜底节点产出的是简单文本答案，无需 claim 级验证
    graph.add_edge("fallback", "observability")

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
