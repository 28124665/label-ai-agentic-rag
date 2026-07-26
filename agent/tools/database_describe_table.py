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
Database Describe Table Tool - 数据库表结构查询工具

Agent 画布可独立调用此工具获取指定表的字段详情。
这是渐进式 Schema 发现的第二步。
"""
import json
import logging
from typing import Any, Dict, List, Optional
from agent.tools.base import ToolParamBase, ToolBase
from agent.tools.database_common import DatabaseToolBase


class DescribeTableParam(ToolParamBase):
    """
    DescribeTable 工具参数定义
    """

    def __init__(self):
        self.meta = {
            "name": "describe_table",
            "description": "查询指定数据库表的字段详情。返回该表的字段名、数据类型、注释等信息。",
            "parameters": {
                "db_id": {
                    "type": "string",
                    "description": "数据库 ID（如 mes_prod、erp_prod）",
                    "required": True
                },
                "table_name": {
                    "type": "string",
                    "description": "表名（如 output_record、inventory_current）",
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


class DescribeTableTool(ToolBase, DatabaseToolBase):
    """
    表结构查询工具
    
    Agent 画布可独立调用此工具，获取指定表的字段详情。
    这是渐进式 Schema 发现的第二步：
    1. Agent 调用 list_tables 获取表名列表
    2. Agent 根据表名选择相关表
    3. Agent 调用 describe_table 获取字段详情
    4. Agent 根据字段详情生成 SQL
    """
    
    component_name = "DescribeTableTool"
    
    def __init__(self, canvas, id, param: DescribeTableParam):
        ToolBase.__init__(self, canvas, id, param)
        DatabaseToolBase.__init__(self, canvas, param)
    
    def _invoke(self, **kwargs) -> str:
        """
        主执行流程
        
        Args:
            db_id: 数据库 ID（如 mes_prod）
            table_name: 表名（如 output_record）
        
        Returns:
            表结构详情（JSON 格式）
        """
        db_id = kwargs.get("db_id")
        table_name = kwargs.get("table_name")
        
        if not db_id:
            error_msg = "db_id 参数不能为空"
            self.set_output("formalized_content", error_msg)
            return error_msg
        
        if not table_name:
            error_msg = "table_name 参数不能为空"
            self.set_output("formalized_content", error_msg)
            return error_msg
        
        try:
            # 验证数据库是否存在
            describe_tool_name = f"describe_table_{db_id}"
            if describe_tool_name not in self._db_tools:
                error_msg = f"数据库 {db_id} 不存在或无权访问"
                self.set_output("formalized_content", error_msg)
                return error_msg
            
            # 调用 MCP Tool 获取表结构
            table_schema_result = self._mcp_session.tool_call(
                name=describe_tool_name,
                arguments={"table_name": table_name}
            )
            
            # 检查是否为错误信息
            if table_schema_result.startswith("MCP server error") or table_schema_result.startswith("Error"):
                error_msg = f"获取表结构失败: {table_schema_result}"
                self.set_output("formalized_content", error_msg)
                return error_msg
            
            # 解析结果
            try:
                schema_data = json.loads(table_schema_result)
            except (json.JSONDecodeError, TypeError):
                schema_data = {"raw": table_schema_result}
            
            # 格式化输出
            result_text = f"**表 {table_name} 的结构**\n\n"
            
            if "columns" in schema_data:
                columns = schema_data["columns"]
                result_text += f"共 {len(columns)} 个字段：\n\n"
                result_text += "| 字段名 | 数据类型 | 注释 |\n"
                result_text += "|--------|----------|------|\n"
                for col in columns:
                    col_name = col.get("name", "")
                    col_type = col.get("type", "")
                    col_comment = col.get("comment", "")
                    result_text += f"| {col_name} | {col_type} | {col_comment} |\n"
            else:
                result_text += f"```json\n{json.dumps(schema_data, ensure_ascii=False, indent=2)}\n```"
            
            # 设置输出
            self.set_output("json", table_schema_result)
            self.set_output("formalized_content", result_text)
            
            return result_text
        
        except Exception as e:
            error_msg = f"查询失败：{str(e)}"
            logging.exception(error_msg)
            self.set_output("formalized_content", error_msg)
            return error_msg
    
    def thoughts(self) -> str:
        return "正在获取表结构详情..."
