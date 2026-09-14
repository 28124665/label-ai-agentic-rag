#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""RestTool 单元测试。

测试 RestSkillConfig 配置管理、参数校验、响应解析、质量评分、
函数声明、热加载注册/注销等纯逻辑能力。
"""

import unittest

from agent.langgraph.tools.rest_tool import (
    RestSkillConfig,
    RestTool,
    RestToolInput,
    RestToolOutput,
    get_rest_tool,
)


class TestRestSkillConfig(unittest.TestCase):
    """测试 RestSkillConfig dataclass。"""

    def test_default_values(self):
        """测试默认值。"""
        config = RestSkillConfig(rest_endpoint="/api/test")
        self.assertEqual(config.rest_method, "GET")
        self.assertEqual(config.erp_domain, "")
        self.assertEqual(config.description, "")
        self.assertEqual(config.parameters_schema, {})
        self.assertEqual(config.required_params, [])
        self.assertEqual(config.fallback_strategy, "empty")

    def test_full_config(self):
        """测试完整配置。"""
        config = RestSkillConfig(
            rest_endpoint="/api/hr/leave/balance",
            rest_method="GET",
            erp_domain="hr",
            description="查询年假余额",
            parameters_schema={
                "employee_name": {"type": "string", "description": "员工姓名"},
                "year": {"type": "integer", "description": "年份"},
            },
            required_params=["employee_name"],
            payload_mapper=lambda params: {"employee_name": params["employee_name"]},
            fallback_strategy="empty",
        )
        self.assertEqual(config.rest_endpoint, "/api/hr/leave/balance")
        self.assertEqual(config.erp_domain, "hr")
        self.assertEqual(config.required_params, ["employee_name"])
        self.assertTrue(callable(config.payload_mapper))
        result = config.payload_mapper({"employee_name": "张三"})
        self.assertEqual(result, {"employee_name": "张三"})


class TestRestToolConfigValidation(unittest.TestCase):
    """测试 RestTool 配置校验。"""

    def setUp(self):
        """每个测试前保存原始配置。"""
        self._original_map = dict(RestTool._FUNCTION_SKILL_MAP)

    def tearDown(self):
        """每个测试后恢复原始配置。"""
        RestTool._FUNCTION_SKILL_MAP = self._original_map

    def test_validate_config_all_pass(self):
        """测试全部配置通过校验。"""
        ok, errors = RestTool.validate_config()
        self.assertTrue(ok)
        self.assertEqual(len(errors), 0)

    def test_validate_config_invalid_endpoint(self):
        """测试 endpoint 格式校验。"""
        RestTool._FUNCTION_SKILL_MAP = {
            "test_func": RestSkillConfig(
                rest_endpoint="api/no-slash",
                rest_method="GET",
                erp_domain="hr",
                description="测试函数",
                parameters_schema={"param": {"type": "string"}},
                required_params=["param"],
            )
        }
        ok, errors = RestTool.validate_config()
        self.assertFalse(ok)
        self.assertTrue(any("必须以 '/' 开头" in e for e in errors))

    def test_validate_config_empty_endpoint(self):
        """测试空 endpoint 校验。"""
        RestTool._FUNCTION_SKILL_MAP = {
            "test_func": RestSkillConfig(
                rest_endpoint="",
                rest_method="GET",
                erp_domain="hr",
                description="测试函数",
            )
        }
        ok, errors = RestTool.validate_config()
        self.assertFalse(ok)
        self.assertTrue(any("rest_endpoint 不能为空" in e for e in errors))

    def test_validate_config_invalid_method(self):
        """测试无效 HTTP 方法校验。"""
        RestTool._FUNCTION_SKILL_MAP = {
            "test_func": RestSkillConfig(
                rest_endpoint="/api/test",
                rest_method="INVALID",
                erp_domain="hr",
                description="测试函数",
            )
        }
        ok, errors = RestTool.validate_config()
        self.assertFalse(ok)
        self.assertTrue(any("rest_method 不合法" in e for e in errors))

    def test_validate_config_unknown_erp_domain(self):
        """测试未知 erp_domain 校验。"""
        RestTool._FUNCTION_SKILL_MAP = {
            "test_func": RestSkillConfig(
                rest_endpoint="/api/test",
                rest_method="GET",
                erp_domain="unknown_domain",
                description="测试函数",
            )
        }
        ok, errors = RestTool.validate_config()
        self.assertFalse(ok)
        self.assertTrue(any("erp_domain 未知" in e for e in errors))

    def test_validate_config_empty_description(self):
        """测试空 description 校验。"""
        RestTool._FUNCTION_SKILL_MAP = {
            "test_func": RestSkillConfig(
                rest_endpoint="/api/test",
                rest_method="GET",
                erp_domain="hr",
                description="",
            )
        }
        ok, errors = RestTool.validate_config()
        self.assertFalse(ok)
        self.assertTrue(any("description 不能为空" in e for e in errors))

    def test_validate_config_missing_required_param_in_schema(self):
        """测试 required_params 与 schema 不一致。"""
        RestTool._FUNCTION_SKILL_MAP = {
            "test_func": RestSkillConfig(
                rest_endpoint="/api/test",
                rest_method="GET",
                erp_domain="hr",
                description="测试函数",
                parameters_schema={"name": {"type": "string"}},
                required_params=["name", "missing_param"],
            )
        }
        ok, errors = RestTool.validate_config()
        self.assertFalse(ok)
        self.assertTrue(any("未在 parameters_schema 中定义" in e for e in errors))


class TestRestToolFunctionRegistration(unittest.TestCase):
    """测试函数注册/注销（热加载）。"""

    def setUp(self):
        self._original_map = dict(RestTool._FUNCTION_SKILL_MAP)

    def tearDown(self):
        RestTool._FUNCTION_SKILL_MAP = self._original_map

    def test_register_new_function(self):
        """测试注册新函数。"""
        config = RestSkillConfig(
            rest_endpoint="/api/test/new",
            rest_method="POST",
            erp_domain="supply_chain",
            description="测试新函数",
            parameters_schema={"id": {"type": "string"}},
            required_params=["id"],
        )
        ok, msg = RestTool.register_function("test_new_func", config)
        self.assertTrue(ok)
        self.assertIn("test_new_func", RestTool._FUNCTION_SKILL_MAP)
        self.assertEqual(
            RestTool._FUNCTION_SKILL_MAP["test_new_func"].rest_endpoint,
            "/api/test/new",
        )

    def test_register_update_existing(self):
        """测试更新已有函数。"""
        old_config = RestTool._FUNCTION_SKILL_MAP.get("query_attendance")
        config = RestSkillConfig(
            rest_endpoint="/api/hr/attendance/v2",
            rest_method="GET",
            erp_domain="hr",
            description="查询考勤记录（v2）",
            parameters_schema={"employee_name": {"type": "string"}},
            required_params=["employee_name"],
        )
        ok, msg = RestTool.register_function("query_attendance", config)
        self.assertTrue(ok)
        self.assertIn("已更新", msg)
        self.assertEqual(
            RestTool._FUNCTION_SKILL_MAP["query_attendance"].rest_endpoint,
            "/api/hr/attendance/v2",
        )

    def test_register_invalid_config_rejected(self):
        """测试无效配置被拒绝注册。"""
        config = RestSkillConfig(
            rest_endpoint="invalid-endpoint",  # 不以 / 开头
            rest_method="GET",
            erp_domain="hr",
            description="测试",
        )
        ok, msg = RestTool.register_function("bad_func", config)
        self.assertFalse(ok)
        self.assertIn("校验失败", msg)
        self.assertNotIn("bad_func", RestTool._FUNCTION_SKILL_MAP)

    def test_unregister_existing(self):
        """测试注销已有函数。"""
        # 先注册一个临时函数
        RestTool._FUNCTION_SKILL_MAP["temp_func"] = RestSkillConfig(
            rest_endpoint="/api/temp",
            rest_method="GET",
            erp_domain="hr",
            description="临时函数",
        )
        ok, msg = RestTool.unregister_function("temp_func")
        self.assertTrue(ok)
        self.assertNotIn("temp_func", RestTool._FUNCTION_SKILL_MAP)

    def test_unregister_nonexistent(self):
        """测试注销不存在的函数。"""
        ok, msg = RestTool.unregister_function("nonexistent_func")
        self.assertFalse(ok)
        self.assertIn("不存在", msg)


class TestRestToolFunctionDeclarations(unittest.TestCase):
    """测试函数声明生成。"""

    def test_get_function_declarations(self):
        """测试函数声明格式。"""
        declarations = RestTool.get_function_declarations()
        self.assertIsInstance(declarations, list)
        self.assertGreater(len(declarations), 0)

        for decl in declarations:
            self.assertIn("name", decl)
            self.assertIn("description", decl)
            self.assertIn("parameters", decl)
            self.assertEqual(decl["parameters"]["type"], "object")
            self.assertIn("properties", decl["parameters"])
            self.assertIn("required", decl["parameters"])

    def test_declarations_include_known_functions(self):
        """测试声明包含已知函数。"""
        declarations = RestTool.get_function_declarations()
        names = [d["name"] for d in declarations]
        self.assertIn("query_annual_leave_balance", names)
        self.assertIn("query_attendance", names)
        self.assertIn("query_purchase_order", names)
        self.assertIn("query_reimbursement_status", names)

    def test_list_functions(self):
        """测试 list_functions。"""
        funcs = RestTool.list_functions()
        self.assertIsInstance(funcs, list)
        for func in funcs:
            self.assertIn("name", func)
            self.assertIn("endpoint", func)
            self.assertIn("method", func)
            self.assertIn("erp_domain", func)
            self.assertIn("description", func)


class TestRestToolInputValidation(unittest.TestCase):
    """测试参数校验。"""

    def setUp(self):
        self.tool = get_rest_tool()

    def test_validate_input_fills_defaults(self):
        """测试 _validate_input 填充默认值。"""
        input_data = {}
        result = self.tool._validate_input(input_data)
        self.assertEqual(result["query"], "")
        self.assertEqual(result["query_lang"], "zh_CN")
        self.assertEqual(result["tenant_id"], "")
        self.assertEqual(result["mcp_server_name"], "erp_mcp_server")
        self.assertEqual(result["function"], "")
        self.assertEqual(result["function_params"], {})
        self.assertEqual(result["rest_endpoint"], "")
        self.assertEqual(result["rest_method"], "GET")
        self.assertEqual(result["timeout_ms"], 30000)
        self.assertEqual(result["max_retries"], 2)
        self.assertEqual(result["erp_domain"], "")

    def test_validate_input_preserves_provided(self):
        """测试 _validate_input 保留已提供的值。"""
        input_data = {
            "query": "查询年假",
            "tenant_id": "t001",
            "function": "query_annual_leave_balance",
            "function_params": {"employee_name": "张三"},
        }
        result = self.tool._validate_input(input_data)
        self.assertEqual(result["query"], "查询年假")
        self.assertEqual(result["tenant_id"], "t001")
        self.assertEqual(result["function"], "query_annual_leave_balance")
        self.assertEqual(result["function_params"], {"employee_name": "张三"})


class TestRestToolEmptyResult(unittest.TestCase):
    """测试空结果生成。"""

    def setUp(self):
        self.tool = get_rest_tool()

    def test_empty_result_default(self):
        """测试默认空结果。"""
        result = self.tool._empty_result()
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "")
        self.assertEqual(result["error_code"], "")
        self.assertEqual(result["rest_status_code"], 0)
        self.assertEqual(result["docs"], [])
        self.assertEqual(result["quality_score"], 0.0)
        self.assertEqual(result["result_count"], 0)

    def test_empty_result_with_error(self):
        """测试带错误信息的空结果。"""
        result = self.tool._empty_result(
            error="MCP 调用超时",
            error_code="TIMEOUT",
            input_data={"rest_endpoint": "/api/hr/test"},
        )
        self.assertEqual(result["error"], "MCP 调用超时")
        self.assertEqual(result["error_code"], "TIMEOUT")
        self.assertEqual(result["rest_endpoint"], "/api/hr/test")


class TestRestToolResponseParsing(unittest.TestCase):
    """测试响应解析。"""

    def setUp(self):
        self.tool = get_rest_tool()

    def test_parse_empty_data(self):
        """测试空数据响应。"""
        docs = self.tool._parse_response_to_docs({"data": {}})
        self.assertEqual(docs, [])

    def test_parse_dict_data(self):
        """测试 dict 类型数据。"""
        response = {
            "data": {
                "id": "001",
                "title": "年假余额",
                "balance": 5,
                "employee_name": "张三",
            }
        }
        docs = self.tool._parse_response_to_docs(response)
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["title"], "年假余额")
        self.assertEqual(docs[0]["structured_data"]["balance"], 5)

    def test_parse_list_data(self):
        """测试 list 类型数据。"""
        response = {
            "data": [
                {"id": "1", "name": "订单A", "amount": 100},
                {"id": "2", "name": "订单B", "amount": 200},
            ]
        }
        docs = self.tool._parse_response_to_docs(response)
        self.assertEqual(len(docs), 2)
        self.assertEqual(docs[0]["title"], "订单A")
        self.assertEqual(docs[1]["title"], "订单B")

    def test_parse_dict_without_title(self):
        """测试无 title 字段的 dict 数据。"""
        response = {"data": {"value": 42}}
        docs = self.tool._parse_response_to_docs(response)
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["title"], "ERP Query Result")


class TestRestToolQualityEvaluation(unittest.TestCase):
    """测试质量评分。"""

    def setUp(self):
        self.tool = get_rest_tool()

    def test_error_status_code(self):
        """测试 4xx/5xx 状态码返回 0.0。"""
        score = self.tool._evaluate_quality([], {"status_code": 500})
        self.assertEqual(score, 0.0)

        score = self.tool._evaluate_quality([], {"status_code": 404})
        self.assertEqual(score, 0.0)

    def test_empty_docs(self):
        """测试空 docs 返回 0.1。"""
        score = self.tool._evaluate_quality([], {"status_code": 200})
        self.assertEqual(score, 0.1)

    def test_with_data(self):
        """测试有数据时的评分。"""
        docs = [
            {
                "structured_data": {"a": 1, "b": 2, "c": 3},
            }
        ]
        score = self.tool._evaluate_quality(docs, {"status_code": 200})
        self.assertGreaterEqual(score, 0.8)
        self.assertLessEqual(score, 0.95)

    def test_score_capped(self):
        """测试评分封顶。"""
        docs = [{"structured_data": {str(i): i for i in range(20)}}]
        score = self.tool._evaluate_quality(docs, {"status_code": 200})
        self.assertLessEqual(score, 0.95)


class TestRestToolSignatures(unittest.TestCase):
    """测试查询签名和证据签名。"""

    def setUp(self):
        self.tool = get_rest_tool()

    def test_query_signature_deterministic(self):
        """测试查询签名确定性。"""
        input_data = {
            "rest_endpoint": "/api/hr/leave/balance",
            "rest_method": "GET",
            "rest_payload": {"employee_name": "张三"},
        }
        sig1 = self.tool._compute_query_signature(input_data)
        sig2 = self.tool._compute_query_signature(input_data)
        self.assertEqual(sig1, sig2)
        self.assertEqual(len(sig1), 16)

    def test_query_signature_different_inputs(self):
        """测试不同输入产生不同签名。"""
        sig1 = self.tool._compute_query_signature(
            {"rest_endpoint": "/api/a", "rest_method": "GET", "rest_payload": {}}
        )
        sig2 = self.tool._compute_query_signature(
            {"rest_endpoint": "/api/b", "rest_method": "GET", "rest_payload": {}}
        )
        self.assertNotEqual(sig1, sig2)

    def test_evidence_signature(self):
        """测试证据签名。"""
        response = {"data": {"balance": 5, "employee": "张三"}}
        sig = self.tool._compute_evidence_signature(response)
        self.assertEqual(len(sig), 16)
        self.assertIsInstance(sig, str)


class TestRestToolInvokeDirect(unittest.TestCase):
    """测试直接调用方式（不依赖 MCP 服务）。"""

    def setUp(self):
        self.tool = get_rest_tool()

    def test_invoke_missing_endpoint(self):
        """测试缺少 endpoint 时的错误处理。"""
        import asyncio

        async def _run():
            return await self.tool.invoke({
                "query": "测试",
                "tenant_id": "t001",
                # 不提供 function 也不提供 rest_endpoint
            })

        result = asyncio.run(_run())
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "MISSING_ENDPOINT")

    def test_invoke_unknown_function(self):
        """测试未知函数名。"""
        import asyncio

        async def _run():
            return await self.tool.invoke({
                "query": "测试",
                "tenant_id": "t001",
                "function": "unknown_function_name",
                "function_params": {},
            })

        result = asyncio.run(_run())
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "UNKNOWN_FUNCTION")


class TestRestToolSingleton(unittest.TestCase):
    """测试单例工厂。"""

    def test_get_rest_tool_singleton(self):
        """测试 get_rest_tool() 返回同一实例。"""
        tool1 = get_rest_tool()
        tool2 = get_rest_tool()
        self.assertIs(tool1, tool2)

    def test_rest_tool_is_callable(self):
        """测试 RestTool 实例可调用 invoke。"""
        tool = get_rest_tool()
        self.assertTrue(hasattr(tool, "invoke"))
        self.assertTrue(callable(tool.invoke))


# ============================================================================
# 前置查询（Pre-Query）测试
# ============================================================================


class TestPreQueryConfig(unittest.TestCase):
    """测试 RestSkillConfig 前置查询字段。"""

    def test_pre_query_default_none(self):
        """测试 pre_query 默认值为 None。"""
        config = RestSkillConfig(rest_endpoint="/api/test")
        self.assertIsNone(config.pre_query)
        self.assertIsNone(config.enhanced_payload_mapper)

    def test_pre_query_full_config(self):
        """测试完整的前置查询配置。"""
        config = RestSkillConfig(
            rest_endpoint="/api/supply_chain/orders",
            rest_method="GET",
            erp_domain="supply_chain",
            description="查询采购订单",
            parameters_schema={
                "employee_name": {"type": "string", "description": "员工姓名"},
            },
            required_params=["employee_name"],
            pre_query={
                "sql": "SELECT employee_id FROM dim_employee WHERE employee_name = :employee_name",
                "params_mapping": {"employee_name": "employee_name"},
                "output_fields": ["employee_id"],
                "db_id": "boss_ai_data",
                "allow_empty": False,
            },
            enhanced_payload_mapper=lambda db_result, fn_params: {
                "employee_id": db_result["employee_id"],
                "year": fn_params.get("year", 2024),
            },
        )
        self.assertIsNotNone(config.pre_query)
        self.assertEqual(config.pre_query["sql"], "SELECT employee_id FROM dim_employee WHERE employee_name = :employee_name")
        self.assertEqual(config.pre_query["db_id"], "boss_ai_data")
        self.assertEqual(config.pre_query["output_fields"], ["employee_id"])
        self.assertFalse(config.pre_query["allow_empty"])
        self.assertTrue(callable(config.enhanced_payload_mapper))

    def test_pre_query_config_validation_missing_enhanced_mapper(self):
        """测试pre_query存在但enhanced_payload_mapper缺失时校验失败。"""
        config = RestSkillConfig(
            rest_endpoint="/api/test",
            rest_method="GET",
            erp_domain="hr",
            description="测试",
            pre_query={
                "sql": "SELECT 1",
                "output_fields": ["x"],
                "db_id": "test_db",
            },
            # enhanced_payload_mapper 未提供
        )
        errors = RestTool._validate_single_config("test", config)
        self.assertTrue(any("未提供 enhanced_payload_mapper" in e for e in errors))

    def test_pre_query_config_validation_missing_sql(self):
        """测试pre_query中sql缺失时校验失败。"""
        config = RestSkillConfig(
            rest_endpoint="/api/test",
            rest_method="GET",
            erp_domain="hr",
            description="测试",
            pre_query={
                "output_fields": ["x"],
                "db_id": "test_db",
            },
            enhanced_payload_mapper=lambda db, params: params,
        )
        errors = RestTool._validate_single_config("test", config)
        self.assertTrue(any("pre_query.sql 不能为空" in e for e in errors))

    def test_pre_query_config_validation_missing_db_id(self):
        """测试pre_query中db_id缺失时校验失败。"""
        config = RestSkillConfig(
            rest_endpoint="/api/test",
            rest_method="GET",
            erp_domain="hr",
            description="测试",
            pre_query={
                "sql": "SELECT 1",
                "output_fields": ["x"],
            },
            enhanced_payload_mapper=lambda db, params: params,
        )
        errors = RestTool._validate_single_config("test", config)
        self.assertTrue(any("pre_query.db_id 不能为空" in e for e in errors))

    def test_pre_query_config_validation_missing_output_fields(self):
        """测试pre_query中output_fields缺失时校验失败。"""
        config = RestSkillConfig(
            rest_endpoint="/api/test",
            rest_method="GET",
            erp_domain="hr",
            description="测试",
            pre_query={
                "sql": "SELECT 1",
                "db_id": "test_db",
            },
            enhanced_payload_mapper=lambda db, params: params,
        )
        errors = RestTool._validate_single_config("test", config)
        self.assertTrue(any("pre_query.output_fields 不能为空" in e for e in errors))

    def test_pre_query_enhanced_mapper_not_callable(self):
        """测试enhanced_payload_mapper不可调用时校验失败。"""
        config = RestSkillConfig(
            rest_endpoint="/api/test",
            rest_method="GET",
            erp_domain="hr",
            description="测试",
            pre_query={
                "sql": "SELECT 1",
                "output_fields": ["x"],
                "db_id": "test_db",
            },
            enhanced_payload_mapper="not_callable",
        )
        errors = RestTool._validate_single_config("test", config)
        self.assertTrue(any("enhanced_payload_mapper 不可调用" in e for e in errors))


class TestPreQueryExecution(unittest.TestCase):
    """测试 _execute_pre_query 方法（使用 mock）。"""

    def setUp(self):
        self.tool = get_rest_tool()

    def test_execute_pre_query_success(self):
        """测试前置查询正常执行并提取字段。"""
        import asyncio

        async def _run():
            pre_query = {
                "sql": "SELECT employee_id, dept_name FROM dim_employee WHERE employee_name = :employee_name",
                "params_mapping": {"employee_name": "employee_name"},
                "output_fields": ["employee_id", "dept_name"],
                "db_id": "boss_ai_data",
                "allow_empty": False,
            }
            function_params = {"employee_name": "张三"}
            input_data = {"tenant_id": "t001"}

            # Mock DbRuntime.execute_sql
            from unittest.mock import AsyncMock, patch

            with patch(
                "agent.langgraph.tools.database_tool.get_database_tool"
            ) as mock_get_db:
                mock_runtime = AsyncMock()
                mock_runtime.execute_sql = AsyncMock(
                    return_value=[
                        {"employee_id": "E001", "dept_name": "技术部"},
                    ]
                )
                mock_db_tool = mock_get_db.return_value
                mock_db_tool._runtime = mock_runtime

                result = await self.tool._execute_pre_query(
                    pre_query, function_params, input_data
                )

            self.assertIsNotNone(result)
            self.assertEqual(result["employee_id"], "E001")
            self.assertEqual(result["dept_name"], "技术部")

        asyncio.run(_run())

    def test_execute_pre_query_empty_result_not_allowed(self):
        """测试前置查询无结果且 allow_empty=False 时返回 None。"""
        import asyncio

        async def _run():
            pre_query = {
                "sql": "SELECT employee_id FROM dim_employee WHERE employee_name = :employee_name",
                "params_mapping": {"employee_name": "employee_name"},
                "output_fields": ["employee_id"],
                "db_id": "boss_ai_data",
                "allow_empty": False,
            }
            function_params = {"employee_name": "不存在的人"}
            input_data = {"tenant_id": "t001"}

            from unittest.mock import AsyncMock, patch

            with patch(
                "agent.langgraph.tools.database_tool.get_database_tool"
            ) as mock_get_db:
                mock_runtime = AsyncMock()
                mock_runtime.execute_sql = AsyncMock(return_value=[])
                mock_db_tool = mock_get_db.return_value
                mock_db_tool._runtime = mock_runtime

                result = await self.tool._execute_pre_query(
                    pre_query, function_params, input_data
                )

            self.assertIsNone(result)

        asyncio.run(_run())

    def test_execute_pre_query_empty_result_allowed(self):
        """测试前置查询无结果且 allow_empty=True 时返回空 dict。"""
        import asyncio

        async def _run():
            pre_query = {
                "sql": "SELECT employee_id FROM dim_employee WHERE employee_name = :employee_name",
                "params_mapping": {"employee_name": "employee_name"},
                "output_fields": ["employee_id"],
                "db_id": "boss_ai_data",
                "allow_empty": True,
            }
            function_params = {"employee_name": "不存在的人"}
            input_data = {"tenant_id": "t001"}

            from unittest.mock import AsyncMock, patch

            with patch(
                "agent.langgraph.tools.database_tool.get_database_tool"
            ) as mock_get_db:
                mock_runtime = AsyncMock()
                mock_runtime.execute_sql = AsyncMock(return_value=[])
                mock_db_tool = mock_get_db.return_value
                mock_db_tool._runtime = mock_runtime

                result = await self.tool._execute_pre_query(
                    pre_query, function_params, input_data
                )

            self.assertEqual(result, {})

        asyncio.run(_run())

    def test_execute_pre_query_missing_db_id(self):
        """测试前置查询 db_id 为空时抛出异常。"""
        import asyncio

        async def _run():
            pre_query = {
                "sql": "SELECT 1",
                "output_fields": ["x"],
                "db_id": "",  # 空 db_id
            }
            function_params = {}
            input_data = {"tenant_id": "t001"}

            with self.assertRaises(Exception) as ctx:
                await self.tool._execute_pre_query(
                    pre_query, function_params, input_data
                )
            self.assertIn("db_id 不能为空", str(ctx.exception))

        asyncio.run(_run())

    def test_execute_pre_query_db_failure(self):
        """测试前置查询 DB 执行失败时抛出异常。"""
        import asyncio

        async def _run():
            pre_query = {
                "sql": "SELECT invalid_column FROM invalid_table",
                "params_mapping": {},
                "output_fields": ["x"],
                "db_id": "boss_ai_data",
            }
            function_params = {}
            input_data = {"tenant_id": "t001"}

            from unittest.mock import AsyncMock, patch

            with patch(
                "agent.langgraph.tools.database_tool.get_database_tool"
            ) as mock_get_db:
                mock_runtime = AsyncMock()
                mock_runtime.execute_sql = AsyncMock(
                    side_effect=Exception("SQL 执行失败: table not found")
                )
                mock_db_tool = mock_get_db.return_value
                mock_db_tool._runtime = mock_runtime

                with self.assertRaises(Exception) as ctx:
                    await self.tool._execute_pre_query(
                        pre_query, function_params, input_data
                    )
                self.assertIn("SQL 执行失败", str(ctx.exception))

        asyncio.run(_run())

    def test_execute_pre_query_parameter_substitution(self):
        """测试前置查询中 SQL 参数替换。"""
        import asyncio

        async def _run():
            pre_query = {
                "sql": "SELECT id FROM users WHERE name = :user_name AND dept = :dept_name",
                "params_mapping": {
                    "user_name": "employee_name",
                    "dept_name": "department",
                },
                "output_fields": ["id"],
                "db_id": "test_db",
                "allow_empty": False,
            }
            function_params = {"employee_name": "张三", "department": "技术部"}
            input_data = {"tenant_id": "t001"}

            from unittest.mock import AsyncMock, patch

            with patch(
                "agent.langgraph.tools.database_tool.get_database_tool"
            ) as mock_get_db:
                mock_runtime = AsyncMock()
                mock_runtime.execute_sql = AsyncMock(
                    return_value=[{"id": "U001"}]
                )
                mock_db_tool = mock_get_db.return_value
                mock_db_tool._runtime = mock_runtime

                result = await self.tool._execute_pre_query(
                    pre_query, function_params, input_data
                )

            self.assertIsNotNone(result)
            self.assertEqual(result["id"], "U001")
            # 验证 execute_sql 被调用时 SQL 中参数已被替换
            called_sql = mock_runtime.execute_sql.call_args[0][1]
            self.assertIn("'张三'", called_sql)
            self.assertIn("'技术部'", called_sql)

        asyncio.run(_run())


class TestPreQueryInvoke(unittest.TestCase):
    """测试 invoke() 方法中的前置查询分支（使用 mock）。"""

    def setUp(self):
        self._original_map = dict(RestTool._FUNCTION_SKILL_MAP)
        self.tool = get_rest_tool()

    def tearDown(self):
        RestTool._FUNCTION_SKILL_MAP = self._original_map

    def test_invoke_with_pre_query_success(self):
        """测试带前置查询的 invoke() 成功执行。"""
        import asyncio

        async def _run():
            from unittest.mock import AsyncMock, patch

            with patch(
                "agent.langgraph.tools.rest_tool.get_rest_runtime"
            ) as mock_get_runtime:
                # Mock RestRuntime
                mock_runtime = AsyncMock()
                mock_runtime.execute = AsyncMock(
                    return_value={
                        "status_code": 200,
                        "data": {
                            "orders": [
                                {"order_id": "PO001", "amount": 1000},
                            ],
                        },
                        "text": "",
                        "latency_ms": 50,
                    }
                )
                mock_get_runtime.return_value = mock_runtime

                # Mock DatabaseTool for pre_query
                with patch(
                    "agent.langgraph.tools.database_tool.get_database_tool"
                ) as mock_get_db:
                    mock_db_runtime = AsyncMock()
                    mock_db_runtime.execute_sql = AsyncMock(
                        return_value=[{"employee_id": "E001"}]
                    )
                    mock_db_tool = mock_get_db.return_value
                    mock_db_tool._runtime = mock_db_runtime

                    result = await self.tool.invoke({
                        "query": "张三的采购订单",
                        "tenant_id": "t001",
                        "function": "query_my_purchase_orders",
                        "function_params": {"employee_name": "张三"},
                    })

            self.assertTrue(result["success"])
            self.assertEqual(result["erp_domain"], "supply_chain")
            self.assertEqual(result["rest_endpoint"], "/api/supply_chain/orders/by-employee")
            self.assertEqual(result["rest_method"], "GET")
            self.assertTrue(result["pre_query_used"])
            self.assertGreater(result["pre_query_latency_ms"], 0)
            self.assertGreater(len(result["docs"]), 0)

            # 验证 REST 调用参数中包含 employee_id（来自前置查询）
            rest_payload = mock_runtime.execute.call_args[1]["payload"]
            self.assertEqual(rest_payload["employee_id"], "E001")
            self.assertEqual(rest_payload["year"], 2024)

        asyncio.run(_run())

    def test_invoke_with_pre_query_empty_result(self):
        """测试前置查询无结果时 invoke() 返回 PRE_QUERY_EMPTY。"""
        import asyncio

        async def _run():
            from unittest.mock import AsyncMock, patch

            with patch(
                "agent.langgraph.tools.database_tool.get_database_tool"
            ) as mock_get_db:
                mock_db_runtime = AsyncMock()
                mock_db_runtime.execute_sql = AsyncMock(
                    return_value=[]  # 空结果
                )
                mock_db_tool = mock_get_db.return_value
                mock_db_tool._runtime = mock_db_runtime

                result = await self.tool.invoke({
                    "query": "不存在的员工的采购订单",
                    "tenant_id": "t001",
                    "function": "query_my_purchase_orders",
                    "function_params": {"employee_name": "不存在的人"},
                })

            self.assertFalse(result["success"])
            self.assertEqual(result["error_code"], "PRE_QUERY_EMPTY")
            self.assertFalse(result["pre_query_used"])

        asyncio.run(_run())

    def test_invoke_without_pre_query_backward_compat(self):
        """测试无前置查询的 Skill 不受影响（向后兼容）。"""
        import asyncio

        async def _run():
            from unittest.mock import AsyncMock, patch

            with patch(
                "agent.langgraph.tools.rest_tool.get_rest_runtime"
            ) as mock_get_runtime:
                mock_runtime = AsyncMock()
                mock_runtime.execute = AsyncMock(
                    return_value={
                        "status_code": 200,
                        "data": {"balance": 5, "employee_name": "张三"},
                        "text": "",
                        "latency_ms": 30,
                    }
                )
                mock_get_runtime.return_value = mock_runtime

                result = await self.tool.invoke({
                    "query": "年假查询",
                    "tenant_id": "t001",
                    "function": "query_annual_leave_balance",
                    "function_params": {"employee_name": "张三"},
                })

            self.assertTrue(result["success"])
            self.assertFalse(result["pre_query_used"])
            self.assertEqual(result["pre_query_latency_ms"], 0)

            # 验证 payload 使用普通 payload_mapper（没有 employee_id）
            rest_payload = mock_runtime.execute.call_args[1]["payload"]
            self.assertEqual(rest_payload["employee_name"], "张三")
            self.assertNotIn("employee_id", rest_payload)

        asyncio.run(_run())

    def test_invoke_pre_query_mapper_error(self):
        """测试 enhanced_payload_mapper 执行失败时返回错误。"""
        import asyncio

        async def _run():
            from unittest.mock import AsyncMock, patch

            with patch(
                "agent.langgraph.tools.database_tool.get_database_tool"
            ) as mock_get_db:
                mock_db_runtime = AsyncMock()
                mock_db_runtime.execute_sql = AsyncMock(
                    return_value=[{"employee_id": "E001"}]
                )
                mock_db_tool = mock_get_db.return_value
                mock_db_tool._runtime = mock_db_runtime

                # 使用一个会抛出异常的 enhanced_payload_mapper
                bad_config = RestSkillConfig(
                    rest_endpoint="/api/test",
                    rest_method="GET",
                    erp_domain="hr",
                    description="测试",
                    pre_query={
                        "sql": "SELECT 1",
                        "output_fields": ["x"],
                        "db_id": "test_db",
                    },
                    enhanced_payload_mapper=lambda db, fn: db["nonexistent_key"],
                )
                RestTool._FUNCTION_SKILL_MAP = {"test_pre_query": bad_config}

                result = await self.tool.invoke({
                    "query": "测试",
                    "tenant_id": "t001",
                    "function": "test_pre_query",
                    "function_params": {},
                })

            self.assertFalse(result["success"])
            self.assertEqual(result["error_code"], "PAYLOAD_MAPPER_ERROR")

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()