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
Database Common - 数据库工具共享模块

提供三个独立 Tool 的共享功能：
- MCP 会话管理
- SQL 校验
- 结果格式化
- 数据库配置加载
"""
import json
import logging
import os
import re
import yaml
from typing import Any, Dict, List, Optional
from common.mcp_tool_call_conn import MCPToolCallSession
from api.db.services.mcp_server_service import MCPServerService


class DatabaseToolBase:
    """
    数据库工具基类
    
    提供三个独立 Tool（ListTables/DescribeTable/ExecuteSQL）的共享功能：
    - MCP 会话管理
    - 数据库配置加载
    - SQL 校验
    - 结果格式化
    """
    
    def __init__(self, canvas, param):
        """
        初始化共享资源
        
        Args:
            canvas: Agent 画布实例
            param: 工具参数
        """
        self._canvas = canvas
        self._param = param
        self._mcp_session: Optional[MCPToolCallSession] = None
        self._db_tools: Dict[str, Dict[str, Any]] = {}  # tool_name -> {description, db_id}
        self._db_config: Dict[str, Any] = {}
        
        # 加载配置
        self._load_db_config()
        
        # 初始化 MCP 会话
        self._init_mcp_session()
    
    def _load_db_config(self):
        """加载数据库配置文件"""
        config_path = self._param.db_schema_config
        if not os.path.exists(config_path):
            logging.warning(f"Database config file not found: {config_path}")
            return
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                self._db_config = config.get("databases", {})
                logging.info(f"Loaded database config: {list(self._db_config.keys())}")
        except Exception as e:
            logging.error(f"Failed to load database config: {e}")
    
    def _init_mcp_session(self):
        """初始化 MCP 会话并获取可用 Tool 列表"""
        try:
            # 获取 MCP Server 配置
            _, mcp_server = MCPServerService.get_by_name_and_tenant(
                self._param.mcp_server_name,
                self._canvas.get_tenant_id()
            )
            
            if not mcp_server:
                logging.warning(f"MCP Server not found: {self._param.mcp_server_name}")
                return
            
            # 创建 MCP 会话
            self._mcp_session = MCPToolCallSession(mcp_server)
            
            # 获取可用 Tool 列表
            tools = self._mcp_session.get_tools()
            for tool in tools:
                # 识别数据库相关 Tool（query_*, list_tables_*, describe_table_*）
                if tool.name.startswith("query_") or \
                   tool.name.startswith("list_tables_") or \
                   tool.name.startswith("describe_table_"):
                    # 提取 db_id（如 query_mes_prod -> mes_prod）
                    parts = tool.name.split("_", 1)
                    if len(parts) > 1:
                        db_id = parts[1]
                        self._db_tools[tool.name] = {
                            "description": tool.description,
                            "db_id": db_id
                        }
            
            logging.info(f"Loaded {len(self._db_tools)} database tools from MCP Server")
        
        except Exception as e:
            logging.error(f"Failed to initialize MCP session: {e}")
    
    def validate_sql(self, sql: str) -> None:
        """
        校验 SQL 只读性
        
        拦截策略：
        1. 关键词黑名单
        2. 必须以 SELECT/WITH 开头
        """
        sql_upper = sql.strip().upper()
        
        # 必须以 SELECT 或 WITH 开头
        if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
            raise Exception(f"仅允许 SELECT/WITH 查询，当前 SQL 以 {sql_upper.split()[0]} 开头")
        
        # 关键词黑名单
        dangerous_keywords = [
            "DROP", "DELETE", "UPDATE", "INSERT", "ALTER",
            "TRUNCATE", "CREATE", "EXEC", "EXECUTE", "MERGE"
        ]
        
        sql_words = set(sql_upper.split())
        for keyword in dangerous_keywords:
            if keyword in sql_words:
                raise Exception(f"SQL 包含禁止关键词: {keyword}")
    
    def format_result(self, raw_result: str) -> str:
        """
        格式化查询结果为 Markdown 表格
        
        Args:
            raw_result: 原始查询结果（JSON 字符串）
        
        Returns:
            Markdown 格式的结果
        """
        try:
            # 尝试解析 JSON
            data = json.loads(raw_result)
            
            if isinstance(data, list) and len(data) > 0:
                # 转换为 Markdown 表格
                import pandas as pd
                df = pd.DataFrame(data)
                markdown = df.to_markdown(index=False)
                
                # 添加查询说明
                result_text = f"**查询结果**（共 {len(data)} 条记录）\n\n{markdown}"
                return result_text
            
            elif isinstance(data, dict):
                # 单个对象
                return f"**查询结果**\n\n```json\n{json.dumps(data, ensure_ascii=False, indent=2)}\n```"
            
            else:
                return f"**查询结果**\n\n{raw_result}"
        
        except Exception:
            # 如果不是 JSON，直接返回原始结果
            return f"**查询结果**\n\n{raw_result}"
    
    def get_available_databases(self) -> List[str]:
        """
        获取可用的数据库 ID 列表
        
        Returns:
            数据库 ID 列表（如 ["mes_prod", "erp_prod"]）
        """
        db_ids = set()
        for tool_name, tool_info in self._db_tools.items():
            if tool_name.startswith("query_"):
                db_ids.add(tool_info["db_id"])
        return list(db_ids)
    
    def get_database_description(self, db_id: str) -> Optional[str]:
        """
        获取数据库的描述信息
        
        Args:
            db_id: 数据库 ID
        
        Returns:
            数据库描述，不存在返回 None
        """
        query_tool_name = f"query_{db_id}"
        if query_tool_name in self._db_tools:
            return self._db_tools[query_tool_name]["description"]
        return None
