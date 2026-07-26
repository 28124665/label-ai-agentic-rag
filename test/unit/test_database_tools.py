"""
Database Tools 单元测试

测试三个独立的数据库工具：
- ListTablesTool: 表清单查询
- DescribeTableTool: 表结构查询
- ExecuteSQLTool: SQL 执行
- DatabaseToolBase: 共享基类（SQL 校验、结果格式化）
"""
import json
import pytest
from unittest.mock import Mock, MagicMock, patch
from agent.tools.database_list_tables import ListTablesTool, ListTablesParam
from agent.tools.database_describe_table import DescribeTableTool, DescribeTableParam
from agent.tools.database_execute_sql import ExecuteSQLTool, ExecuteSQLParam


# ============================================================
# Fixtures
# ============================================================

def _create_mock_mcp_session():
    """创建模拟的 MCP 会话"""
    mock_session = Mock()
    mock_session.get_tools.return_value = [
        Mock(name="query_mes_prod", description="查询生产制造数据：产量、工单、设备OEE"),
        Mock(name="list_tables_mes_prod", description="列出 mes_prod 数据库的表"),
        Mock(name="describe_table_mes_prod", description="获取 mes_prod 数据库表结构"),
        Mock(name="query_erp_prod", description="查询财务供应链数据：采购、成本、库存"),
        Mock(name="list_tables_erp_prod", description="列出 erp_prod 数据库的表"),
        Mock(name="describe_table_erp_prod", description="获取 erp_prod 数据库表结构"),
    ]
    return mock_session


@pytest.fixture
def list_tables_tool():
    """创建 ListTablesTool 测试实例"""
    canvas = Mock()
    canvas.get_tenant_id.return_value = "test_tenant"
    
    param = ListTablesParam()
    param.mcp_server_name = "test_mcp_server"
    param.db_schema_config = "conf/db_schema.yaml"
    
    with patch('agent.tools.database_common.MCPServerService') as mock_mcp_service, \
         patch('agent.tools.database_common.MCPToolCallSession') as mock_session_cls:
        mock_mcp_service.get_by_name_and_tenant.return_value = (None, Mock())
        mock_session_cls.return_value = _create_mock_mcp_session()
        tool = ListTablesTool(canvas, "test_id", param)
        return tool


@pytest.fixture
def describe_table_tool():
    """创建 DescribeTableTool 测试实例"""
    canvas = Mock()
    canvas.get_tenant_id.return_value = "test_tenant"
    
    param = DescribeTableParam()
    param.mcp_server_name = "test_mcp_server"
    param.db_schema_config = "conf/db_schema.yaml"
    
    with patch('agent.tools.database_common.MCPServerService') as mock_mcp_service, \
         patch('agent.tools.database_common.MCPToolCallSession') as mock_session_cls:
        mock_mcp_service.get_by_name_and_tenant.return_value = (None, Mock())
        mock_session_cls.return_value = _create_mock_mcp_session()
        tool = DescribeTableTool(canvas, "test_id", param)
        return tool


@pytest.fixture
def execute_sql_tool():
    """创建 ExecuteSQLTool 测试实例"""
    canvas = Mock()
    canvas.get_tenant_id.return_value = "test_tenant"
    
    param = ExecuteSQLParam()
    param.mcp_server_name = "test_mcp_server"
    param.db_schema_config = "conf/db_schema.yaml"
    
    with patch('agent.tools.database_common.MCPServerService') as mock_mcp_service, \
         patch('agent.tools.database_common.MCPToolCallSession') as mock_session_cls:
        mock_mcp_service.get_by_name_and_tenant.return_value = (None, Mock())
        mock_session_cls.return_value = _create_mock_mcp_session()
        tool = ExecuteSQLTool(canvas, "test_id", param)
        return tool


# ============================================================
# ListTablesTool 测试
# ============================================================

class TestListTablesTool:
    """ListTablesTool 单元测试"""
    
    def test_invoke_success(self, list_tables_tool):
        """测试成功获取表清单"""
        # 模拟 MCP 返回
        tables_json = json.dumps({"tables": ["production_order", "output_record", "equipment_status"]})
        list_tables_tool._mcp_session.tool_call.return_value = tables_json
        
        result = list_tables_tool._invoke(db_id="mes_prod")
        
        assert "production_order" in result
        assert "output_record" in result
        assert "共 3 张表" in result
    
    def test_invoke_missing_db_id(self, list_tables_tool):
        """测试缺少 db_id 参数"""
        result = list_tables_tool._invoke()
        assert "不能为空" in result
    
    def test_invoke_db_not_found(self, list_tables_tool):
        """测试数据库不存在"""
        result = list_tables_tool._invoke(db_id="nonexistent_db")
        assert "不存在" in result
    
    def test_invoke_empty_tables(self, list_tables_tool):
        """测试数据库中没有表"""
        list_tables_tool._mcp_session.tool_call.return_value = json.dumps({"tables": []})
        result = list_tables_tool._invoke(db_id="mes_prod")
        assert "没有可访问的表" in result
    
    def test_invoke_mcp_error(self, list_tables_tool):
        """测试 MCP 调用失败"""
        list_tables_tool._mcp_session.tool_call.return_value = "MCP server error: connection refused"
        result = list_tables_tool._invoke(db_id="mes_prod")
        assert "获取表清单失败" in result
    
    def test_thoughts(self, list_tables_tool):
        """测试 thoughts 方法"""
        assert "表清单" in list_tables_tool.thoughts()


# ============================================================
# DescribeTableTool 测试
# ============================================================

class TestDescribeTableTool:
    """DescribeTableTool 单元测试"""
    
    def test_invoke_success(self, describe_table_tool):
        """测试成功获取表结构"""
        schema_json = json.dumps({
            "columns": [
                {"name": "id", "type": "INTEGER", "comment": "主键"},
                {"name": "factory_code", "type": "VARCHAR(20)", "comment": "厂区代码"},
                {"name": "quantity", "type": "INTEGER", "comment": "产量"},
            ]
        })
        describe_table_tool._mcp_session.tool_call.return_value = schema_json
        
        result = describe_table_tool._invoke(db_id="mes_prod", table_name="output_record")
        
        assert "factory_code" in result
        assert "quantity" in result
        assert "3 个字段" in result
    
    def test_invoke_missing_db_id(self, describe_table_tool):
        """测试缺少 db_id 参数"""
        result = describe_table_tool._invoke(table_name="output_record")
        assert "不能为空" in result
    
    def test_invoke_missing_table_name(self, describe_table_tool):
        """测试缺少 table_name 参数"""
        result = describe_table_tool._invoke(db_id="mes_prod")
        assert "不能为空" in result
    
    def test_invoke_db_not_found(self, describe_table_tool):
        """测试数据库不存在"""
        result = describe_table_tool._invoke(db_id="nonexistent_db", table_name="test")
        assert "不存在" in result
    
    def test_thoughts(self, describe_table_tool):
        """测试 thoughts 方法"""
        assert "表结构" in describe_table_tool.thoughts()


# ============================================================
# ExecuteSQLTool 测试
# ============================================================

class TestExecuteSQLTool:
    """ExecuteSQLTool 单元测试"""
    
    def test_invoke_success(self, execute_sql_tool):
        """测试成功执行 SQL"""
        result_json = json.dumps([
            {"factory_code": "SZ", "total_output": 15000},
            {"factory_code": "KS", "total_output": 12000},
        ])
        execute_sql_tool._mcp_session.tool_call.return_value = result_json
        
        result = execute_sql_tool._invoke(
            db_id="mes_prod",
            sql="SELECT factory_code, SUM(quantity) as total_output FROM output_record GROUP BY factory_code"
        )
        
        assert "查询结果" in result
        assert "2 条记录" in result
    
    def test_invoke_missing_db_id(self, execute_sql_tool):
        """测试缺少 db_id 参数"""
        result = execute_sql_tool._invoke(sql="SELECT 1")
        assert "不能为空" in result
    
    def test_invoke_missing_sql(self, execute_sql_tool):
        """测试缺少 sql 参数"""
        result = execute_sql_tool._invoke(db_id="mes_prod")
        assert "不能为空" in result
    
    def test_invoke_db_not_found(self, execute_sql_tool):
        """测试数据库不存在"""
        result = execute_sql_tool._invoke(db_id="nonexistent_db", sql="SELECT 1")
        assert "不存在" in result
    
    def test_validate_sql_blocks_drop(self, execute_sql_tool):
        """测试 SQL 校验拦截 DROP"""
        result = execute_sql_tool._invoke(db_id="mes_prod", sql="DROP TABLE output_record")
        assert "仅允许 SELECT" in result
    
    def test_validate_sql_blocks_delete(self, execute_sql_tool):
        """测试 SQL 校验拦截 DELETE"""
        result = execute_sql_tool._invoke(db_id="mes_prod", sql="DELETE FROM output_record WHERE id = 1")
        assert "仅允许 SELECT" in result
    
    def test_validate_sql_blocks_update(self, execute_sql_tool):
        """测试 SQL 校验拦截 UPDATE"""
        result = execute_sql_tool._invoke(db_id="mes_prod", sql="UPDATE output_record SET quantity = 0")
        assert "仅允许 SELECT" in result
    
    def test_validate_sql_blocks_insert(self, execute_sql_tool):
        """测试 SQL 校验拦截 INSERT"""
        result = execute_sql_tool._invoke(db_id="mes_prod", sql="INSERT INTO output_record VALUES (1, 100)")
        assert "仅允许 SELECT" in result
    
    def test_validate_sql_allows_select(self, execute_sql_tool):
        """测试 SQL 校验允许 SELECT"""
        execute_sql_tool._mcp_session.tool_call.return_value = "[]"
        result = execute_sql_tool._invoke(
            db_id="mes_prod",
            sql="SELECT * FROM output_record LIMIT 10"
        )
        assert "查询失败" not in result
    
    def test_validate_sql_allows_with(self, execute_sql_tool):
        """测试 SQL 校验允许 WITH（CTE）"""
        execute_sql_tool._mcp_session.tool_call.return_value = "[]"
        result = execute_sql_tool._invoke(
            db_id="mes_prod",
            sql="WITH cte AS (SELECT * FROM output_record) SELECT * FROM cte"
        )
        assert "查询失败" not in result
    
    def test_thoughts(self, execute_sql_tool):
        """测试 thoughts 方法"""
        assert "SQL" in execute_sql_tool.thoughts()


# ============================================================
# DatabaseToolBase 共享功能测试
# ============================================================

class TestDatabaseToolBase:
    """DatabaseToolBase 共享功能测试"""
    
    def test_get_available_databases(self, list_tables_tool):
        """测试获取可用数据库列表"""
        db_ids = list_tables_tool.get_available_databases()
        assert "mes_prod" in db_ids
        assert "erp_prod" in db_ids
    
    def test_get_database_description(self, list_tables_tool):
        """测试获取数据库描述"""
        desc = list_tables_tool.get_database_description("mes_prod")
        assert desc is not None
        assert "生产" in desc
    
    def test_get_database_description_not_found(self, list_tables_tool):
        """测试获取不存在的数据库描述"""
        desc = list_tables_tool.get_database_description("nonexistent")
        assert desc is None
    
    def test_validate_sql_pass(self, execute_sql_tool):
        """测试 SQL 校验通过"""
        # 不应抛出异常
        execute_sql_tool.validate_sql("SELECT * FROM test LIMIT 10")
    
    def test_validate_sql_fail(self, execute_sql_tool):
        """测试 SQL 校验失败"""
        with pytest.raises(Exception, match="仅允许 SELECT"):
            execute_sql_tool.validate_sql("DROP TABLE test")
    
    def test_format_result_list(self, execute_sql_tool):
        """测试格式化列表结果"""
        raw = json.dumps([{"a": 1, "b": 2}, {"a": 3, "b": 4}])
        result = execute_sql_tool.format_result(raw)
        assert "2 条记录" in result
    
    def test_format_result_dict(self, execute_sql_tool):
        """测试格式化字典结果"""
        raw = json.dumps({"key": "value"})
        result = execute_sql_tool.format_result(raw)
        assert "查询结果" in result
    
    def test_format_result_non_json(self, execute_sql_tool):
        """测试格式化非 JSON 结果"""
        result = execute_sql_tool.format_result("plain text result")
        assert "plain text result" in result
