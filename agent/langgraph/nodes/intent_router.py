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
"""意图路由节点。

根据用户问题判断意图，路由到不同的工具（RAG / Database / Web / Chitchat）。
"""

import logging
import time
from typing import Any

from agent.langgraph.state import AgentState

logger = logging.getLogger(__name__)


def intent_router_node(state: AgentState) -> dict[str, Any]:
    """意图路由节点。

    根据用户问题判断意图，设置 route_target 字段。

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段
    """
    start_time = time.time()
    
    user_question = state.get("user_question", "")
    
    if not user_question:
        logger.warning("[intent_router] 用户问题为空，路由到 chitchat")
        return {
            "route_target": "chitchat",
            "node_timings": {"intent_router": int((time.time() - start_time) * 1000)},
        }
    
    # 意图路由逻辑
    route_target = _route_intent(user_question)
    
    logger.info(f"[intent_router] 路由决策: '{user_question}' -> {route_target}")
    
    return {
        "route_target": route_target,
        "node_timings": {"intent_router": int((time.time() - start_time) * 1000)},
    }


def _route_intent(query: str) -> str:
    """根据查询内容判断意图。

    路由策略：
    - 包含聚合词（总计、汇总、平均等） -> database
    - 包含精确实体（表名、字段名等） -> database
    - 包含概念词（什么是、如何、为什么等） -> rag
    - 混合意图 -> hybrid
    - 其他 -> chitchat

    Args:
        query: 用户查询

    Returns:
        str: 路由目标（rag / database / hybrid / chitchat）
    """
    query_lower = query.lower()
    
    # 聚合词（数据库查询特征）
    aggregation_keywords = ["总计", "汇总", "平均", "求和", "统计", "计算", "total", "sum", "average", "count"]
    
    # 精确实体（数据库查询特征）
    # TODO: 集成实际的表名和字段名列表
    entity_keywords = ["表", "字段", "记录", "数据", "table", "field", "record", "data"]
    
    # 概念词（知识库查询特征）
    concept_keywords = ["什么是", "如何", "为什么", "怎么", "请问", "what", "how", "why", "explain"]
    
    # 检查聚合词
    has_aggregation = any(kw in query_lower for kw in aggregation_keywords)
    
    # 检查精确实体
    has_entity = any(kw in query_lower for kw in entity_keywords)
    
    # 检查概念词
    has_concept = any(kw in query_lower for kw in concept_keywords)
    
    # 路由决策
    if has_aggregation or has_entity:
        # 包含聚合词或精确实体 -> database
        if has_concept:
            # 同时包含概念词 -> hybrid
            return "hybrid"
        return "database"
    
    if has_concept:
        # 包含概念词 -> rag
        return "rag"
    
    # 默认 -> chitchat
    return "chitchat"


def route_decision(state: AgentState) -> str:
    """路由决策函数，用于 LangGraph 条件边。

    Args:
        state: 当前 AgentState

    Returns:
        str: 下一个节点名称
    """
    route_target = state.get("route_target", "chitchat")
    
    if route_target == "rag":
        return "rag_tool"
    elif route_target == "database":
        return "db_tool"
    elif route_target == "hybrid":
        # 混合模式：同时调用 RAG 和 Database
        # TODO: 实现并行调用
        return "rag_tool"
    else:
        # chitchat 或其他 -> 直接到 prompt_assembly
        return "prompt_assembly"
