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
Database Query Tool 单元测试
"""
import json
import pytest
from unittest.mock import Mock, MagicMock, patch
from agent.tools.database_query import DatabaseQuery, DatabaseQueryParam


class TestDatabaseQueryParam:
    """测试 DatabaseQueryParam 类"""
    
    def test_init(self):
        """测试参数初始化"""
        param = DatabaseQueryParam()
        assert param.meta["name"] == "database_query"
        assert "query" in param.meta["parameters"]
        assert param.mcp_server_name == "database_mcp_server"
    
    def test_check_valid(self):
        """测试参数校验 - 正常情况"""
        param = DatabaseQueryParam()
        # 应该不抛出异常
        param.check()
    
    def test_check_invalid(self):
        """测试参数校验 - 缺少必填参数"""
        param = DatabaseQueryParam()
        param.mcp_server_name = ""
        with pytest.raises(Exception):
            param.check()


class TestDatabaseQuery:
    """测试 DatabaseQuery 类"""
    
    @pytest.fixture
    def mock_canvas(self):
        """模拟 Canvas 对象"""
        canvas = Mock()
        canvas.get_tenant_id.return_value = "test_tenant"
        return canvas
    
    @pytest.fixture
    def mock_param(self):
        """模拟参数对象"""
        param = Mock(spec=DatabaseQueryParam)
        param.mcp_server_name = "database_mcp_server"
        param.db_schema_config = "conf/db_schema.yaml"
        param.query_templates_config = "conf/query_templates.yaml"
        return param
    
    @pytest.fixture
    def database_query(self, mock_canvas, mock_param):
        """创建 DatabaseQuery 实例（不初始化 MCP）"""
        with patch('agent.tools.database_query.MCPServerService') as mock_mcp_service:
            mock_mcp_service.get_by_name_and_tenant.return_value = (False, None)
            query = DatabaseQuery(mock_canvas, "test_id", mock_param)
        return query
    
    def test_init(self, database_query):
        """测试初始化"""
        assert database_query.component_name == "DatabaseQuery"
        assert database_query._mcp_session is None
    
    def test_load_db_config(self, database_query):
        """测试加载数据库配置"""
        database_query._load_db_config()
        # 应该加载了配置（即使文件不存在也不报错）
        assert isinstance(database_query._db_config, dict)
    
    def test_load_query_templates(self, database_query):
        """测试加载查询模板"""
        database_query._load_query_templates()
        # 应该加载了模板（即使文件不存在也不报错）
        assert isinstance(database_query._query_templates, list)
    
    def test_route_by_keyword_mes(self, database_query):
        """测试关键词路由 - MES 数据库"""
        # 模拟已加载的 Tool 列表
        database_query._db_tools = {
            "query_mes_prod": {"description": "查询生产制造数据", "db_id": "mes_prod"},
            "query_erp_prod": {"description": "查询财务供应链数据", "db_id": "erp_prod"}
        }
        
        # 测试产量相关查询
        result = database_query._route_by_keyword("Q3 各厂区产量是多少？")
        assert result == "mes_prod"
        
        # 测试设备相关查询
        result = database_query._route_by_keyword("A 产线设备 OEE 是多少？")
        assert result == "mes_prod"
        
        # 测试效率相关查询（新增关键词）
        result = database_query._route_by_keyword("生产线效率如何？")
        assert result == "mes_prod"
    
    def test_route_by_intent_erp(self, database_query):
        """测试意图路由 - ERP 数据库"""
        database_query._db_tools = {
            "query_mes_prod": {"description": "查询生产制造数据", "db_id": "mes_prod"},
            "query_erp_prod": {"description": "查询财务供应链数据", "db_id": "erp_prod"}
        }
        
        # 测试库存相关查询
        result = database_query._route_by_intent("SKU-1001 当前库存是多少？")
        assert result == "erp_prod"
        
        # 测试采购相关查询
        result = database_query._route_by_intent("主要供应商的交货准时率是多少？")
        assert result == "erp_prod"
    
    def test_route_by_intent_no_match(self, database_query):
        """测试意图路由 - 无匹配"""
        database_query._db_tools = {
            "query_mes_prod": {"description": "查询生产制造数据", "db_id": "mes_prod"}
        }
        
        # 测试无关查询
        result = database_query._route_by_intent("今天天气怎么样？")
        assert result is None
    
    def test_filter_relevant_tables(self, database_query):
        """测试筛选相关表"""
        all_tables = [
            "production_order",
            "output_record",
            "equipment_status",
            "inventory_current",
            "purchase_order"
        ]
        
        # 测试产量相关查询
        result = database_query._filter_relevant_tables(
            "Q3 各厂区产量是多少？",
            all_tables,
            max_tables=2
        )
        assert len(result) == 2
        # output_record 应该排在前面
        assert "output_record" in result
    
    def test_validate_sql_valid(self, database_query):
        """测试 SQL 校验 - 合法 SQL"""
        # 应该不抛出异常
        database_query._validate_sql("SELECT * FROM output_record LIMIT 100")
        database_query._validate_sql("WITH cte AS (SELECT * FROM t) SELECT * FROM cte")
    
    def test_validate_sql_invalid(self, database_query):
        """测试 SQL 校验 - 非法 SQL"""
        # 测试非 SELECT 语句
        with pytest.raises(Exception, match="仅允许 SELECT/WITH 查询"):
            database_query._validate_sql("INSERT INTO t VALUES (1)")
        
        # 测试包含危险关键词
        with pytest.raises(Exception, match="SQL 包含禁止关键词"):
            database_query._validate_sql("SELECT * FROM t; DROP TABLE t")
        
        with pytest.raises(Exception, match="SQL 包含禁止关键词"):
            database_query._validate_sql("DELETE FROM t WHERE id = 1")
    
    def test_format_result_json_list(self, database_query):
        """测试结果格式化 - JSON 列表"""
        raw_result = json.dumps([
            {"factory_code": "SZ", "total_output": 15000},
            {"factory_code": "KS", "total_output": 12000}
        ])
        
        result = database_query._format_result(raw_result, "查询产量")
        assert "查询结果" in result
        assert "2 条记录" in result
        assert "SZ" in result
        assert "KS" in result
    
    def test_format_result_json_dict(self, database_query):
        """测试结果格式化 - JSON 对象"""
        raw_result = json.dumps({"count": 100})
        
        result = database_query._format_result(raw_result, "查询数量")
        assert "查询结果" in result
        assert "100" in result
    
    def test_format_result_plain_text(self, database_query):
        """测试结果格式化 - 纯文本"""
        raw_result = "No record in the database!"
        
        result = database_query._format_result(raw_result, "查询")
        assert "查询结果" in result
        assert "No record" in result
    
    def test_match_template(self, database_query):
        """测试模板匹配"""
        # 模拟加载的模板
        database_query._query_templates = [
            {
                "name": "query_inventory",
                "description": "查询库存",
                "keywords": ["库存", "存量"],
                "database": "erp_prod",
                "sql": "SELECT * FROM inventory_current WHERE sku_id = :sku_id",
                "parameters": [
                    {"name": "sku_id", "type": "string", "required": True}
                ]
            }
        ]
        
        # 测试匹配成功
        result = database_query._match_template("SKU-1001 的库存是多少？", "erp_prod")
        assert result is not None
        assert "sql" in result
        assert "params" in result
        assert result["params"]["sku_id"] == "SKU-1001"
        
        # 测试数据库不匹配
        result = database_query._match_template("SKU-1001 的库存是多少？", "mes_prod")
        assert result is None
        
        # 测试关键词不匹配
        result = database_query._match_template("今天天气怎么样？", "erp_prod")
        assert result is None


class TestDatabaseQueryIntegration:
    """集成测试（需要 MCP Server 环境）"""
    
    @pytest.mark.skip(reason="需要 MCP Server 环境")
    def test_full_flow(self):
        """测试完整流程"""
        # TODO: 实现集成测试
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
