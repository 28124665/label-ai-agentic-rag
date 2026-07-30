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
"""SQL Agent — 业界标准 Agent 驱动渐进式披露数据库查询。

模块结构（docs/数据库Tool渐进式披露LLM化落地设计.md）：
- state.py: SqlAgentState 子图状态
- budget.py: ExplorationBudget 预算硬约束
- tools.py: SqlAgentToolset 4 个细粒度工具（薄封装+独立护栏）
- prompts.py: System Prompt 构建与 LLM 输出解析
- subgraph.py: SQLAgentSubgraph 有界 ReAct 循环（LangGraph StateGraph）
- runner.py: SqlAgentRunner 对外入口（模板短路 + 模式切换 + db_result 契约）
"""

from agent.langgraph.tools.sql_agent.budget import ExplorationBudget
from agent.langgraph.tools.sql_agent.runner import SqlAgentRunner, get_sql_agent_runner
from agent.langgraph.tools.sql_agent.state import SqlAgentState
from agent.langgraph.tools.sql_agent.subgraph import SqlAgentSubgraph
from agent.langgraph.tools.sql_agent.tools import SqlAgentToolset

__all__ = [
    "ExplorationBudget",
    "SqlAgentRunner",
    "SqlAgentState",
    "SqlAgentSubgraph",
    "SqlAgentToolset",
    "get_sql_agent_runner",
]
