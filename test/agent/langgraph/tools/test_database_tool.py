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
"""DatabaseTool 单元测试。

测试意图路由、模板匹配、Schema 发现、SQL 解析、安全校验、结果格式化等
不依赖 MCP 会话的纯逻辑能力。
"""

import os
import tempfile
import unittest

from agent.langgraph.tools.database_tool import (
    DatabaseTool,
    ExploreCounter,
    TemplateMatcher,
)


class TestTemplateMatcher(unittest.TestCase):
    """测试预定义查询模板匹配。"""

    def test_match_with_keywords_and_params(self):
        """测试关键词匹配和参数提取。"""
        config = {
            "templates": [
                {
                    "name": "query_by_sku",
                    "description": "按 SKU 查询",
                    "keywords": ["sku", "库存"],
                    "sql": "SELECT * FROM inventory WHERE sku_id = :sku_id",
                    "parameters": [
                        {"name": "sku_id", "type": "string", "required": True}
                    ],
                }
            ]
        }

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            import yaml
            yaml.dump(config, f)
            config_path = f.name

        try:
            matcher = TemplateMatcher(config_path)
            match = matcher.match("查询 SKU ABC-123 的库存")
            self.assertIsNotNone(match)
            self.assertIn("ABC-123", match.sql)
            self.assertEqual(match.params.get("sku_id"), "ABC-123")
        finally:
            os.unlink(config_path)

    def test_no_match(self):
        """测试无匹配模板。"""
        config = {"templates": []}
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            import yaml
            yaml.dump(config, f)
            config_path = f.name

        try:
            matcher = TemplateMatcher(config_path)
            match = matcher.match("随便问一个问题")
            self.assertIsNone(match)
        finally:
            os.unlink(config_path)


class TestExploreCounter(unittest.TestCase):
    """测试 Schema 探查频次限制。"""

    def test_limit_enforced(self):
        """测试次数上限。"""
        counter = ExploreCounter(max_list_tables=2, max_describe_table=1)
        self.assertTrue(counter.can_explore("db1", "list_tables"))
        counter.increment("db1", "list_tables")
        counter.increment("db1", "list_tables")
        self.assertFalse(counter.can_explore("db1", "list_tables"))
        self.assertTrue(counter.can_explore("db2", "list_tables"))


class TestDatabaseToolLogic(unittest.TestCase):
    """测试 DatabaseTool 内部逻辑。"""

    def test_filter_relevant_tables(self):
        """测试表筛选。"""
        tool = DatabaseTool()
        tables = ["inventory", "production_output", "sales_order", "users"]
        result = tool._filter_relevant_tables("昨天的产量是多少", tables, 2)
        self.assertIn("production_output", result)

    def test_extract_sql_from_markdown(self):
        """测试从 LLM 响应中提取 SQL。"""
        tool = DatabaseTool()
        text = "```sql\nSELECT * FROM orders LIMIT 10\n```"
        self.assertEqual(tool._extract_sql(text), "SELECT * FROM orders LIMIT 10")

    def test_validate_sql_passes_select(self):
        """测试合法 SELECT 通过校验。"""
        tool = DatabaseTool()
        try:
            tool._validate_sql("SELECT * FROM orders LIMIT 10")
        except Exception as e:
            self.fail(f"合法 SELECT 不应报错: {e}")

    def test_validate_sql_rejects_dangerous(self):
        """测试危险 SQL 被拦截。"""
        tool = DatabaseTool()
        with self.assertRaises(Exception):
            tool._validate_sql("DROP TABLE orders")
        with self.assertRaises(Exception):
            tool._validate_sql("DELETE FROM orders WHERE id = 1")

    def test_format_result(self):
        """测试结果格式化。"""
        tool = DatabaseTool()
        rows = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        formatted = tool._format_result(rows, "SELECT * FROM users", "erp", ["users"], "zh_CN")
        self.assertIn("Alice", formatted)
        self.assertIn("users", formatted)
        self.assertIn("SELECT * FROM users", formatted)

    def test_route_by_intent(self):
        """测试意图路由。"""
        tool = DatabaseTool()
        tool._db_tools = {
            "query_erp": {"description": "ERP 销售订单数据", "db_id": "erp"},
            "query_mes": {"description": "MES 生产产量数据", "db_id": "mes"},
        }
        self.assertEqual(tool._route_by_intent("昨天的产量"), "mes")
        self.assertEqual(tool._route_by_intent("查询订单"), "erp")


if __name__ == "__main__":
    unittest.main()
