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
"""
Database Execute SQL Tool - 数据库 SQL 执行工具

Agent 画布可独立调用此工具执行 SQL 查询。
这是渐进式 Schema 发现的第三步。
"""
import json
import logging
from typing import Any, Dict, List, Optional
from agent.tools.base import ToolParamBase, ToolBase
from agent.tools.database_common import DatabaseToolBase


class ExecuteSQLParam(ToolParamBase):
    """
    ExecuteSQL 工具参数定义
    """

    def __init__(self):
        self.meta = {
            "name": "execute_sql",
            "description": "在指定数据库中执行 SQL 查询。只允许执行 SELECT 语句，返回查询结果。",
            "parameters": {
                "db_id": {
                    "type": "string",
                    "description": "数据库 ID（如 mes_prod、erp_prod）",
                    "required": True
                },
                "sql": {
                    "type": "string",
                    "description": "要执行的 SQL 查询语句（仅允许 SELECT）",
                    "required": True
                }
            }
        }
        super().__init__()
        # MCP Server 名称（从配置获取）
        self.mcp_server_name = "database_mcp_server"
        # 数据库配置文件路径
        self.db_schema_config = "conf/db_schema.yaml"

    def check(self):
        """参数校验"""
        self.check_empty(self.mcp_server_name, "MCP Server 名称")
        self.check_empty(self.db_schema_config, "数据库配置文件路径")


class ExecuteSQLTool(ToolBase, DatabaseToolBase):
    """
    SQL 执行工具
    
    Agent 画布可独立调用此工具，执行 SQL 查询。
    这是渐进式 Schema 发现的第三步：
    1. Agent 调用 list_tables 获取表名列表
    2. Agent 根据表名选择相关表
    3. Agent 调用 describe_table 获取字段详情
    4. Agent 根据字段详情生成 SQL
    5. Agent 调用 execute_sql 执行查询
    """
    
    component_name = "ExecuteSQLTool"
    
    def __init__(self, canvas, id, param: ExecuteSQLParam):
        ToolBase.__init__(self, canvas, id, param)
        DatabaseToolBase.__init__(self, canvas, param)
    
    def _invoke(self, **kwargs) -> str:
        """
        主执行流程
        
        Args:
            db_id: 数据库 ID（如 mes_prod）
            sql: 要执行的 SQL 查询语句
        
        Returns:
            查询结果（JSON 格式）
        """
        db_id = kwargs.get("db_id")
        sql = kwargs.get("sql")
        
        if not db_id:
            error_msg = "db_id 参数不能为空"
            self.set_output("formalized_content", error_msg)
            return error_msg
        
        if not sql:
            error_msg = "sql 参数不能为空"
            self.set_output("formalized_content", error_msg)
            return error_msg
        
        try:
            # 验证数据库是否存在
            query_tool_name = f"query_{db_id}"
            if query_tool_name not in self._db_tools:
                error_msg = f"数据库 {db_id} 不存在或无权访问"
                self.set_output("formalized_content", error_msg)
                return error_msg
            
            # SQL 安全校验
            self.validate_sql(sql)
            
            # 调用 MCP Tool 执行 SQL
            result = self._mcp_session.tool_call(
                name=query_tool_name,
                arguments={"sql": sql}
            )
            
            # 检查是否为错误信息
            if result.startswith("MCP server error") or result.startswith("Error"):
                error_msg = f"SQL 执行失败: {result}"
                self.set_output("formalized_content", error_msg)
                return error_msg
            
            # 格式化输出
            result_text = self.format_result(result)
            
            # 设置输出
            self.set_output("json", result)
            self.set_output("formalized_content", result_text)
            
            return result_text
        
        except Exception as e:
            error_msg = f"查询失败：{str(e)}"
            logging.exception(error_msg)
            self.set_output("formalized_content", error_msg)
            return error_msg
    
    def thoughts(self) -> str:
        return "正在执行 SQL 查询..."
