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
Database List Tables Tool - 数据库表清单查询工具

Agent 画布可独立调用此工具获取指定数据库的表名清单。
这是渐进式 Schema 发现的第一步。
"""
import json
import logging
from typing import Any, Dict, List, Optional
from agent.tools.base import ToolParamBase, ToolBase
from agent.tools.database_common import DatabaseToolBase


class ListTablesParam(ToolParamBase):
    """
    ListTables 工具参数定义
    """

    def __init__(self):
        self.meta = {
            "name": "list_tables",
            "description": "查询指定数据库的表名清单。返回该数据库中所有可访问的表名列表（仅表名，不含字段细节）。",
            "parameters": {
                "db_id": {
                    "type": "string",
                    "description": "数据库 ID（如 mes_prod、erp_prod）",
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


class ListTablesTool(ToolBase, DatabaseToolBase):
    """
    表清单查询工具
    
    Agent 画布可独立调用此工具，获取指定数据库的表名清单。
    这是渐进式 Schema 发现的第一步：
    1. Agent 调用 list_tables 获取表名列表
    2. Agent 根据表名选择相关表
    3. Agent 调用 describe_table 获取字段详情
    """
    
    component_name = "ListTablesTool"
    
    def __init__(self, canvas, id, param: ListTablesParam):
        ToolBase.__init__(self, canvas, id, param)
        DatabaseToolBase.__init__(self, canvas, param)
    
    def _invoke(self, **kwargs) -> str:
        """
        主执行流程
        
        Args:
            db_id: 数据库 ID（如 mes_prod）
        
        Returns:
            表名清单（JSON 格式）
        """
        db_id = kwargs.get("db_id")
        if not db_id:
            error_msg = "db_id 参数不能为空"
            self.set_output("formalized_content", error_msg)
            return error_msg
        
        try:
            # 验证数据库是否存在
            list_tool_name = f"list_tables_{db_id}"
            if list_tool_name not in self._db_tools:
                error_msg = f"数据库 {db_id} 不存在或无权访问"
                self.set_output("formalized_content", error_msg)
                return error_msg
            
            # 调用 MCP Tool 获取表名清单
            tables_result = self._mcp_session.tool_call(
                name=list_tool_name,
                arguments={}
            )
            
            # 检查是否为错误信息
            if tables_result.startswith("MCP server error") or tables_result.startswith("Error"):
                error_msg = f"获取表清单失败: {tables_result}"
                self.set_output("formalized_content", error_msg)
                return error_msg
            
            # 解析结果
            try:
                tables_data = json.loads(tables_result)
                all_tables = tables_data.get("tables", [])
            except (json.JSONDecodeError, TypeError):
                # 如果解析失败，尝试按行分割
                all_tables = [line.strip() for line in tables_result.split("\n") if line.strip()]
            
            if not all_tables:
                error_msg = f"数据库 {db_id} 中没有可访问的表"
                self.set_output("formalized_content", error_msg)
                return error_msg
            
            # 格式化输出
            result_text = f"**数据库 {db_id} 的表清单**（共 {len(all_tables)} 张表）\n\n"
            for i, table_name in enumerate(all_tables, 1):
                result_text += f"{i}. {table_name}\n"
            
            # 设置输出
            self.set_output("json", tables_result)
            self.set_output("formalized_content", result_text)
            
            return result_text
        
        except Exception as e:
            error_msg = f"查询失败：{str(e)}"
            logging.exception(error_msg)
            self.set_output("formalized_content", error_msg)
            return error_msg
    
    def thoughts(self) -> str:
        return "正在获取数据库表清单..."
