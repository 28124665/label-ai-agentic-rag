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
"""SQL Agent 子图状态定义。"""

import operator
from typing import Annotated, Any, Literal, TypedDict

# 探索终态（docs §3.3 收敛与 verdict）
VERDICT_DATA_FOUND = "data_found"
VERDICT_NO_DATA_CONFIRMED = "no_data_confirmed"
VERDICT_EXPLORATION_FAILED = "exploration_failed"
VERDICT_BUDGET_EXHAUSTED = "budget_exhausted"

# Agent 动作类型
ACTION_LIST_DATABASES = "list_databases"
ACTION_LIST_TABLES = "list_tables"
ACTION_DESCRIBE_TABLE = "describe_table"
ACTION_EXECUTE_SQL = "execute_sql"
ACTION_FINISH = "finish"
ACTION_GIVE_UP = "give_up"

TOOL_ACTIONS = {
    ACTION_LIST_DATABASES,
    ACTION_LIST_TABLES,
    ACTION_DESCRIBE_TABLE,
    ACTION_EXECUTE_SQL,
}


class SqlAgentState(TypedDict, total=False):
    """SQL Agent 子图状态。

    step_trace / observations 使用 append reducer，其余字段覆盖写。
    """

    # ---- 输入 ----
    query: str                      # 用户问题（简体化后）
    query_lang: str                 # 查询语言
    db_id: str                      # 调用方指定的库（空表示需 Agent 自主选库）
    exploration_hints: dict[str, Any]  # DataSkill exploration_hints 注入

    # ---- 轨迹（append） ----
    step_trace: Annotated[list[dict[str, Any]], operator.add]
    observations: Annotated[list[dict[str, Any]], operator.add]

    # ---- 预算计数 ----
    step_count: int
    llm_call_count: int
    sql_exec_count: int
    describe_count: int
    budget_exhausted: bool
    budget_stop_reason: str

    # ---- Agent 决策 ----
    pending_action: dict[str, Any]  # agent_llm 解析出的当前动作
    finish_summary: str
    give_up_reason: str
    give_up_type: Literal["no_data", "error"]
    llm_error: str

    # ---- 结果 ----
    final_db_id: str                # Agent 最终选定的库
    final_sql: str
    final_rows: list[dict]
    final_tables: list[str]
    sql_succeeded: bool             # 是否至少成功执行过一次 SQL
    exploration_verdict: str        # 终态（见 VERDICT_*）

    # ---- 内部字段（不透出到 db_result） ----
    _system_prompt: str             # 子图运行期 System Prompt
    _parse_failures: int            # LLM 输出解析失败计数
