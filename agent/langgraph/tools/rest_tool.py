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
"""RestTool — 通过 MCP 服务调用 ERP 系统的 REST API 适配层。

RestTool 作为 Agent 侧的轻量协议适配层，负责：
1. 接收 Planner 选中的函数名和参数 → 映射到具体的 REST 端点
2. 通过 RestRuntime（MCP Client）发送请求到 MCP 服务
3. 解析 MCP 响应，标准化为 RestToolOutput
4. 质量评分 + 空结果处理

不负责：
- 权限校验（MCP 服务负责）
- 业务逻辑聚合（MCP 服务负责）
- 直接访问 ERP 数据库（MCP 服务负责）

设计文档: docs/RestTool与MCP服务对接设计方案.md
"""

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, TypedDict

from agent.langgraph.tools.rest_runtime import get_rest_runtime

logger = logging.getLogger(__name__)


# ============================================================================
# RestSkillConfig — RestTool 内部映射配置
# ============================================================================


@dataclass
class RestSkillConfig:
    """RestTool 内部配置：映射 function name → REST 端点。

    对 Planner 完全透明。Planner 只看到 function declaration，
    不知道 endpoint 路径、MCP 协议等实现细节。

    Attributes:
        rest_endpoint: REST 端点路径
        rest_method: HTTP 方法
        erp_domain: ERP 领域
        description: 功能描述（用于生成 function declaration）
        parameters_schema: 参数 JSON Schema（用于生成 function declaration）
        required_params: 必填参数列表
        payload_mapper: 将 function_params 映射为 REST payload 的函数
        fallback_strategy: 降级策略
        pre_query: 前置 DB 查询配置（None 表示无前置查询）
        enhanced_payload_mapper: 增强版 payload_mapper，接收 DB 结果 + 用户参数
    """

    rest_endpoint: str
    rest_method: str = "GET"
    erp_domain: str = ""
    description: str = ""
    parameters_schema: dict = field(default_factory=dict)
    required_params: list[str] = field(default_factory=list)
    payload_mapper: Callable = lambda params: params
    fallback_strategy: str = "empty"  # empty / cache / default_value

    # ======== 前置查询字段 ========
    pre_query: dict | None = None
    """
    前置 DB 查询配置。为 None 时表示无前置查询（与现有行为一致）。

    结构:
    {
        "sql": "SELECT employee_id FROM dim_employee WHERE employee_name = :employee_name",
        "params_mapping": {
            "employee_name": "employee_name"  # SQL参数名 → function_params 中的 key
        },
        "output_fields": ["employee_id"],     # 需要从 DB 结果中提取的字段
        "db_id": "boss_ai_data",              # 目标数据库 ID（必填）
        "db_schema_config": "conf/db_schema.yaml",  # DB 配置路径（可选）
        "mcp_server_name": "database_mcp_server",    # MCP 服务名（可选）
        "allow_empty": False,                 # 是否允许 DB 查不到结果（默认 False）
    }
    """

    enhanced_payload_mapper: Callable | None = None
    """
    增强版 payload_mapper，接收 DB 查询结果 + 用户参数，返回 REST payload。

    签名: (db_result: dict, function_params: dict) -> dict

    当 pre_query 不为 None 时，此字段必须提供。
    当 pre_query 为 None 时，使用原有的 payload_mapper。
    """


# ============================================================================
# TypedDict I/O
# ============================================================================


class RestToolInput(TypedDict, total=False):
    """RestTool 输入参数。

    Attributes:
        query: 用户原始查询（自然语言）
        query_simplified: 简化后的查询
        query_lang: 查询语言
        tenant_id: 租户 ID
        llm_id: LLM 模型 ID
        mcp_server_name: MCP 服务名（默认 "erp_mcp_server"）
        function: 函数名（Planner 选中的函数名）
        function_params: 函数参数
        rest_endpoint: REST 端点路径（兼容直接调用方式）
        rest_method: HTTP 方法（兼容直接调用方式）
        rest_payload: 请求体（兼容直接调用方式）
        rest_headers: 额外请求头
        timeout_ms: 超时时间（默认 30000）
        max_retries: 最大重试次数（默认 2）
        erp_domain: ERP 领域
        extra: 额外参数
    """

    query: str
    query_simplified: str
    query_lang: str
    tenant_id: str
    llm_id: str
    mcp_server_name: str
    function: str
    function_params: dict
    rest_endpoint: str
    rest_method: str
    rest_payload: dict
    rest_headers: dict
    timeout_ms: int
    max_retries: int
    erp_domain: str
    extra: dict


class RestToolOutput(TypedDict, total=False):
    """RestTool 输出结果。

    Attributes:
        success: 是否成功
        error: 错误信息
        error_code: 错误码
        rest_status_code: HTTP 状态码
        rest_response: 原始响应（dict）
        rest_response_text: 原始响应文本
        rest_latency_ms: MCP 调用耗时
        docs: 标准化后的文档列表
        quality_score: 质量评分
        result_count: 结果数量
        rest_endpoint: 实际调用的端点
        rest_method: 实际使用的 HTTP 方法
        erp_domain: ERP 领域
        mcp_server_name: MCP 服务名
        rest_query_signature: REST 查询签名（SHA-256）
        rest_evidence_signature: 响应数据签名
        circuit_breaker_open: 熔断器是否打开
        fallback_used: 是否使用了降级策略
    """

    success: bool
    error: str
    error_code: str
    rest_status_code: int
    rest_response: dict
    rest_response_text: str
    rest_latency_ms: int
    docs: list[dict]
    quality_score: float
    result_count: int
    rest_endpoint: str
    rest_method: str
    erp_domain: str
    mcp_server_name: str
    rest_query_signature: str
    rest_evidence_signature: str
    circuit_breaker_open: bool
    fallback_used: bool
    pre_query_used: bool
    pre_query_latency_ms: int


# ============================================================================
# RestTool
# ============================================================================


class RestTool:
    """RestTool：通过 MCP 服务调用 ERP 系统的 REST API。

    职责：
    1. 接收 Planner 选中的函数名和参数 → 映射到具体的 REST 端点
    2. 通过 RestRuntime（MCP Client）发送请求到 MCP 服务
    3. 解析 MCP 响应，标准化为 RestToolOutput
    4. 质量评分 + 空结果处理

    支持两种调用方式：
    1. 函数调用方式（推荐）：传入 function + function_params，
       RestTool 内部查 _FUNCTION_SKILL_MAP 映射到具体 endpoint
    2. 直接调用方式（兼容）：传入 rest_endpoint + rest_method + rest_payload
    """

    # 函数名 → RestSkill 配置的映射表（对 Planner 透明）
    _FUNCTION_SKILL_MAP: dict[str, RestSkillConfig] = {
        "query_annual_leave_balance": RestSkillConfig(
            rest_endpoint="/api/hr/leave/balance",
            rest_method="GET",
            erp_domain="hr",
            description="查询员工的年假剩余天数",
            parameters_schema={
                "employee_name": {"type": "string", "description": "员工姓名"},
                "year": {"type": "integer", "description": "查询年份，默认当年"},
            },
            required_params=["employee_name"],
            payload_mapper=lambda params: {
                "employee_name": params["employee_name"],
                "year": params.get("year", 2024),
            },
        ),
        "query_attendance": RestSkillConfig(
            rest_endpoint="/api/hr/attendance",
            rest_method="GET",
            erp_domain="hr",
            description="查询员工考勤记录（出勤/迟到/早退/缺勤）",
            parameters_schema={
                "employee_name": {"type": "string", "description": "员工姓名"},
                "month": {"type": "string", "description": "查询月份，格式 YYYY-MM"},
            },
            required_params=["employee_name"],
            payload_mapper=lambda params: {
                "employee_name": params["employee_name"],
                "month": params.get("month", ""),
            },
        ),
        "query_purchase_order": RestSkillConfig(
            rest_endpoint="/api/supply_chain/orders",
            rest_method="GET",
            erp_domain="supply_chain",
            description="查询采购订单详情",
            parameters_schema={
                "order_id": {"type": "string", "description": "订单编号"},
            },
            required_params=["order_id"],
            payload_mapper=lambda params: {"order_id": params["order_id"]},
        ),
        "query_reimbursement_status": RestSkillConfig(
            rest_endpoint="/api/financial/reimbursement",
            rest_method="GET",
            erp_domain="financial",
            description="查询报销单审批状态",
            parameters_schema={
                "reimbursement_id": {"type": "string", "description": "报销单编号"},
            },
            required_params=["reimbursement_id"],
            payload_mapper=lambda params: {"reimbursement_id": params["reimbursement_id"]},
        ),
        # ★ 带前置查询的 Skill 示例：姓名 → 员工ID → 查采购订单
        "query_my_purchase_orders": RestSkillConfig(
            rest_endpoint="/api/supply_chain/orders/by-employee",
            rest_method="GET",
            erp_domain="supply_chain",
            description="查询指定员工的采购订单列表（需先通过员工姓名查 employee_id）",
            parameters_schema={
                "employee_name": {"type": "string", "description": "员工姓名"},
                "year": {"type": "integer", "description": "查询年份"},
            },
            required_params=["employee_name"],
            # 前置查询：姓名 → employee_id
            pre_query={
                "sql": "SELECT employee_id FROM dim_employee WHERE employee_name = :employee_name",
                "params_mapping": {"employee_name": "employee_name"},
                "output_fields": ["employee_id"],
                "db_id": "boss_ai_data",
                "allow_empty": False,
            },
            # 增强 payload_mapper：融合 DB 结果 + 用户参数
            enhanced_payload_mapper=lambda db_result, fn_params: {
                "employee_id": db_result["employee_id"],
                "year": fn_params.get("year", 2024),
            },
            # 普通 payload_mapper 作为降级（pre_query 为 None 或 enhanced_payload_mapper 为 None 时使用）
            payload_mapper=lambda params: {
                "employee_name": params["employee_name"],
                "year": params.get("year", 2024),
            },
        ),
    }

    # 有效的 erp_domain 值
    _VALID_ERP_DOMAINS = {"hr", "supply_chain", "financial", "finance"}

    # 有效的 HTTP 方法
    _VALID_HTTP_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}

    @classmethod
    def get_function_declarations(cls) -> list[dict]:
        """暴露给 Planner 和 LLMRouter 的函数声明列表。

        返回格式兼容 OpenAI function calling 规范。
        这些声明在 Planner 和 LLMRouter 的 system prompt 中动态注入。
        """
        return [
            {
                "name": name,
                "description": config.description,
                "parameters": {
                    "type": "object",
                    "properties": config.parameters_schema,
                    "required": config.required_params,
                },
            }
            for name, config in cls._FUNCTION_SKILL_MAP.items()
        ]

    # ======== Phase 4: 配置管理与验证 ========

    @classmethod
    def validate_config(cls) -> tuple[bool, list[str]]:
        """启动时校验所有 RestSkillConfig 的配置合法性。

        校验项：
        1. endpoint 格式：必须以 "/" 开头
        2. rest_method 必须是合法的 HTTP 方法
        3. erp_domain 必须是已知领域
        4. description 不能为空
        5. required_params 中的参数必须在 parameters_schema 中定义
        6. payload_mapper 必须可调用

        Returns:
            tuple[bool, list[str]]: (是否全部通过, 错误信息列表)
        """
        errors: list[str] = []

        for name, config in cls._FUNCTION_SKILL_MAP.items():
            prefix = f"[{name}]"
            errs = cls._validate_single_config(name, config)
            errors.extend(f"{prefix} {e}" for e in errs)

        if errors:
            logger.warning(
                f"[RestTool] 配置校验发现 {len(errors)} 个问题:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )
        else:
            logger.info(
                f"[RestTool] 配置校验通过，共 {len(cls._FUNCTION_SKILL_MAP)} 个函数"
            )

        return len(errors) == 0, errors

    @staticmethod
    def _validate_single_config(name: str, config: RestSkillConfig) -> list[str]:
        """校验单个 RestSkillConfig。

        Args:
            name: 函数名
            config: RestSkillConfig 实例

        Returns:
            list[str]: 错误信息列表（空列表表示无错误）
        """
        errors: list[str] = []

        # 1. endpoint 格式校验
        if not config.rest_endpoint:
            errors.append("rest_endpoint 不能为空")
        elif not config.rest_endpoint.startswith("/"):
            errors.append(
                f"rest_endpoint 必须以 '/' 开头，当前值: {config.rest_endpoint}"
            )

        # 2. HTTP 方法校验
        if config.rest_method.upper() not in RestTool._VALID_HTTP_METHODS:
            errors.append(
                f"rest_method 不合法: {config.rest_method}，"
                f"合法值: {RestTool._VALID_HTTP_METHODS}"
            )

        # 3. erp_domain 校验
        if config.erp_domain and config.erp_domain not in RestTool._VALID_ERP_DOMAINS:
            errors.append(
                f"erp_domain 未知: {config.erp_domain}，"
                f"合法值: {RestTool._VALID_ERP_DOMAINS}"
            )

        # 4. description 校验
        if not config.description:
            errors.append("description 不能为空（Planner 依赖描述选择函数）")

        # 5. required_params 与 parameters_schema 一致性校验
        schema_keys = set(config.parameters_schema.keys())
        for param in config.required_params:
            if param not in schema_keys:
                errors.append(
                    f"required_params 中的 '{param}' 未在 parameters_schema 中定义"
                )

        # 6. payload_mapper 可调用性校验
        if not callable(config.payload_mapper):
            errors.append("payload_mapper 不可调用")

        # 7. pre_query 与 enhanced_payload_mapper 一致性校验
        if config.pre_query is not None:
            if not config.enhanced_payload_mapper:
                errors.append("配置了 pre_query 但未提供 enhanced_payload_mapper")
            elif not callable(config.enhanced_payload_mapper):
                errors.append("enhanced_payload_mapper 不可调用")
            if not config.pre_query.get("sql"):
                errors.append("pre_query.sql 不能为空")
            if not config.pre_query.get("db_id"):
                errors.append("pre_query.db_id 不能为空")
            if not config.pre_query.get("output_fields"):
                errors.append("pre_query.output_fields 不能为空")

        return errors

    @classmethod
    def register_function(cls, name: str, config: RestSkillConfig) -> tuple[bool, str]:
        """运行时动态注册/更新函数配置（热加载）。

        支持新增函数和更新已有函数配置。
        注册前会进行配置校验，校验失败则拒绝注册。

        Args:
            name: 函数名
            config: RestSkillConfig 实例

        Returns:
            tuple[bool, str]: (是否成功, 消息)
        """
        # 校验配置
        errs = cls._validate_single_config(name, config)
        if errs:
            msg = f"配置校验失败: {'; '.join(errs)}"
            logger.error(f"[RestTool] register_function 失败: {msg}")
            return False, msg

        is_update = name in cls._FUNCTION_SKILL_MAP
        cls._FUNCTION_SKILL_MAP[name] = config

        if is_update:
            logger.info(
                f"[RestTool] 函数配置已更新: {name} → {config.rest_endpoint}"
            )
            return True, f"函数 '{name}' 已更新"
        else:
            logger.info(
                f"[RestTool] 函数已注册: {name} → {config.rest_endpoint}"
            )
            return True, f"函数 '{name}' 已注册"

    @classmethod
    def unregister_function(cls, name: str) -> tuple[bool, str]:
        """运行时移除函数配置。

        Args:
            name: 函数名

        Returns:
            tuple[bool, str]: (是否成功, 消息)
        """
        if name not in cls._FUNCTION_SKILL_MAP:
            return False, f"函数 '{name}' 不存在"

        del cls._FUNCTION_SKILL_MAP[name]
        logger.info(f"[RestTool] 函数已移除: {name}")
        return True, f"函数 '{name}' 已移除"

    @classmethod
    def list_functions(cls) -> list[dict]:
        """列出所有已注册的函数及其配置摘要。

        Returns:
            list[dict]: 函数摘要列表
        """
        return [
            {
                "name": name,
                "endpoint": config.rest_endpoint,
                "method": config.rest_method,
                "erp_domain": config.erp_domain,
                "description": config.description,
                "required_params": config.required_params,
            }
            for name, config in cls._FUNCTION_SKILL_MAP.items()
        ]

    async def invoke(self, input_data: dict) -> RestToolOutput:
        """执行 REST 调用。

        支持两种调用方式：
        1. 函数调用方式（推荐）：传入 function + function_params，
           RestTool 内部查 _FUNCTION_SKILL_MAP 映射到具体 endpoint
        2. 直接调用方式（兼容）：传入 rest_endpoint + rest_method + rest_payload

        流程：
        1. 参数校验与规范化
        2. 如果提供了 function，从 _FUNCTION_SKILL_MAP 解析 endpoint/payload
        3. 调用 RestRuntime.execute()
        4. 解析响应 → 标准化 docs
        5. 质量评分
        6. 返回 RestToolOutput
        """
        start_time = time.time()

        # 1. 参数校验
        input_data = self._validate_input(input_data)

        # 2. 解析调用参数：优先使用 function 方式
        function_name = input_data.get("function", "")
        function_params = input_data.get("function_params", {})
        pre_query_used = False
        pre_query_latency_ms = 0
        if function_name:
            config = self._FUNCTION_SKILL_MAP.get(function_name)
            if not config:
                return self._empty_result(
                    error=f"Unknown function: {function_name}",
                    error_code="UNKNOWN_FUNCTION",
                    input_data=input_data,
                )
            rest_endpoint = config.rest_endpoint
            rest_method = config.rest_method
            erp_domain = config.erp_domain

            # ★ 前置查询分支：如果配置了 pre_query，先执行 DB 查询
            if config.pre_query is not None and config.enhanced_payload_mapper is not None:
                pre_query_used = True
                pre_query_start = time.time()
                try:
                    db_result = await self._execute_pre_query(
                        config.pre_query, function_params, input_data
                    )
                except Exception as e:
                    logger.error(f"[RestTool] 前置查询失败: {e}")
                    return self._empty_result(
                        error=f"Pre-query failed: {e}",
                        error_code="PRE_QUERY_FAILED",
                        input_data=input_data,
                    )

                pre_query_latency_ms = int((time.time() - pre_query_start) * 1000)

                if db_result is None:
                    return self._empty_result(
                        error=f"No matching record found in pre-query for function: {function_name}",
                        error_code="PRE_QUERY_EMPTY",
                        input_data=input_data,
                    )

                try:
                    rest_payload = config.enhanced_payload_mapper(db_result, function_params)
                except Exception as e:
                    logger.error(f"[RestTool] enhanced_payload_mapper 执行失败: {e}")
                    return self._empty_result(
                        error=f"Enhanced payload mapper failed: {e}",
                        error_code="PAYLOAD_MAPPER_ERROR",
                        input_data=input_data,
                    )
            else:
                # 无前置查询：使用原有 payload_mapper
                try:
                    rest_payload = config.payload_mapper(function_params)
                except Exception as e:
                    logger.error(f"[RestTool] payload_mapper 执行失败: {e}")
                    return self._empty_result(
                        error=f"Function params mapping failed: {e}",
                        error_code="PAYLOAD_MAPPER_ERROR",
                        input_data=input_data,
                    )
        else:
            # 3. 直接调用方式（兼容）
            rest_endpoint = input_data.get("rest_endpoint", "")
            rest_method = input_data.get("rest_method", "GET")
            rest_payload = input_data.get("rest_payload")
            erp_domain = input_data.get("erp_domain", "")

        if not rest_endpoint:
            return self._empty_result(
                error="No REST endpoint specified",
                error_code="MISSING_ENDPOINT",
                input_data=input_data,
            )

        # 4. 调用 MCP 服务
        runtime = get_rest_runtime()
        try:
            response = await runtime.execute(
                endpoint=rest_endpoint,
                method=rest_method,
                payload=rest_payload,
                headers=input_data.get("rest_headers"),
                tenant_id=input_data.get("tenant_id", ""),
                mcp_server_name=input_data.get("mcp_server_name", "erp_mcp_server"),
                timeout_ms=input_data.get("timeout_ms", 30000),
                max_retries=input_data.get("max_retries", 2),
            )
        except Exception as e:
            logger.error(f"[RestTool] MCP 调用失败: {e}")
            return self._empty_result(
                error=str(e),
                error_code="MCP_CALL_FAILED",
                input_data=input_data,
            )

        # 5. 解析响应 → 标准化 docs
        docs = self._parse_response_to_docs(response)

        # 6. 质量评分
        quality = self._evaluate_quality(docs, response)

        latency_ms = int((time.time() - start_time) * 1000)

        # 7. 构建输出
        return RestToolOutput(
            success=True,
            rest_status_code=response.get("status_code", 200),
            rest_response=response.get("data", {}),
            rest_response_text=response.get("text", ""),
            rest_latency_ms=response.get("latency_ms", latency_ms),
            docs=docs,
            quality_score=quality,
            result_count=len(docs),
            rest_endpoint=rest_endpoint,
            rest_method=rest_method,
            erp_domain=erp_domain,
            mcp_server_name=input_data.get("mcp_server_name", "erp_mcp_server"),
            rest_query_signature=self._compute_query_signature(input_data),
            rest_evidence_signature=self._compute_evidence_signature(response),
            circuit_breaker_open=False,
            fallback_used=False,
            pre_query_used=pre_query_used,
            pre_query_latency_ms=pre_query_latency_ms,
        )

    def _validate_input(self, input_data: dict) -> dict:
        """参数校验与规范化，填充默认值。"""
        defaults = {
            "query": "",
            "query_simplified": "",
            "query_lang": "zh_CN",
            "tenant_id": "",
            "llm_id": "",
            "mcp_server_name": "erp_mcp_server",
            "function": "",
            "function_params": {},
            "rest_endpoint": "",
            "rest_method": "GET",
            "rest_payload": None,
            "rest_headers": None,
            "timeout_ms": 30000,
            "max_retries": 2,
            "erp_domain": "",
            "extra": {},
        }
        for key, default in defaults.items():
            if key not in input_data or input_data[key] is None:
                if isinstance(default, (dict, list)):
                    input_data[key] = default.copy()
                else:
                    input_data[key] = default
        return input_data

    def _empty_result(
        self,
        error: str = "",
        error_code: str = "",
        input_data: dict = None,
    ) -> RestToolOutput:
        """生成空结果（调用失败或空响应时）。"""
        if input_data is None:
            input_data = {}
        return RestToolOutput(
            success=False,
            error=error,
            error_code=error_code,
            rest_status_code=0,
            rest_response={},
            rest_response_text="",
            rest_latency_ms=0,
            docs=[],
            quality_score=0.0,
            result_count=0,
            rest_endpoint=input_data.get("rest_endpoint", ""),
            rest_method=input_data.get("rest_method", "GET"),
            erp_domain=input_data.get("erp_domain", ""),
            mcp_server_name=input_data.get("mcp_server_name", "erp_mcp_server"),
            rest_query_signature="",
            rest_evidence_signature="",
            circuit_breaker_open=False,
            fallback_used=False,
            pre_query_used=False,
            pre_query_latency_ms=0,
        )

    async def _execute_pre_query(
        self,
        pre_query: dict,
        function_params: dict,
        input_data: dict,
    ) -> dict | None:
        """执行前置 DB 查询，返回结果的第一行或 None。

        流程：
        1. 从 pre_query.params_mapping 构造 SQL 参数
        2. 通过 DbRuntime 执行 SQL 查询
        3. 提取 output_fields 指定的字段
        4. 返回 dict 或 None（无结果时）

        Args:
            pre_query: 前置查询配置
            function_params: 用户提供的函数参数
            input_data: RestTool 输入

        Returns:
            dict | None: 提取的字段字典，或 None（无结果且 allow_empty=False）

        Raises:
            Exception: DB 连接失败或 SQL 执行失败
        """
        from agent.langgraph.tools.database_tool import get_database_tool

        # 1. 构造 SQL 参数
        sql_params = {}
        for sql_param, fn_key in pre_query.get("params_mapping", {}).items():
            value = function_params.get(fn_key)
            if value is None:
                logger.warning(
                    f"[RestTool] pre_query 参数缺失: "
                    f"SQL 参数 '{sql_param}' 需要 function_params['{fn_key}']，"
                    f"但 function_params 中不存在"
                )
                continue
            sql_params[sql_param] = value

        # 2. 获取 DatabaseTool 单例，复用 DbRuntime
        db_tool = get_database_tool()
        runtime = db_tool._runtime

        db_schema_config = pre_query.get("db_schema_config", "conf/db_schema.yaml")
        tenant_id = input_data.get("tenant_id", "")
        mcp_server_name = pre_query.get("mcp_server_name", "database_mcp_server")
        db_id = pre_query.get("db_id", "")

        if not db_id:
            raise Exception("pre_query.db_id 不能为空")

        # 初始化 MCP 会话（如果尚未初始化或 server 不同）
        try:
            runtime.load_db_config(db_schema_config)
            runtime.init_session(tenant_id, mcp_server_name)
        except Exception as e:
            logger.error(f"[RestTool] pre_query MCP 会话初始化失败: {e}")
            raise

        # 3. 执行 SQL（替换参数占位符）
        sql = pre_query["sql"]
        for param_name, param_value in sql_params.items():
            sql = sql.replace(f":{param_name}", repr(param_value))

        logger.debug(
            f"[RestTool] pre_query 执行 SQL: {sql[:200]}"
        )

        rows = await runtime.execute_sql(db_id, sql)

        # 4. 提取 output_fields
        if not rows:
            if pre_query.get("allow_empty", False):
                return {}
            return None

        row = rows[0]
        output_fields = pre_query.get("output_fields", [])
        result = {field: row.get(field) for field in output_fields}

        logger.debug(
            f"[RestTool] pre_query 完成: "
            f"SQL 返回 {len(rows)} 行，"
            f"提取字段: {list(result.keys())}"
        )

        return result

    def _parse_response_to_docs(self, response: dict) -> list[dict]:
        """将 MCP 响应解析为标准化 docs 列表。

        策略：
        - 如果响应 data 是列表 → 每项一个 doc
        - 如果响应 data 是 dict → 整体一个 doc
        - 提取 title/content/structured_data 等字段
        """
        docs: list[dict] = []
        data = response.get("data", {})

        if not data:
            return docs

        if isinstance(data, list):
            for i, item in enumerate(data):
                if isinstance(item, dict):
                    doc = {
                        "id": item.get("id", str(i)),
                        "title": item.get("title", item.get("name", f"Result {i + 1}")),
                        "content": item.get("content", json.dumps(item, ensure_ascii=False)),
                        "structured_data": item,
                        "score": 0.9,
                    }
                    docs.append(doc)
        elif isinstance(data, dict):
            doc = {
                "id": data.get("id", "1"),
                "title": data.get("title", data.get("name", "ERP Query Result")),
                "content": data.get("content", json.dumps(data, ensure_ascii=False)),
                "structured_data": data,
                "score": 0.9,
            }
            docs.append(doc)

        return docs

    def _evaluate_quality(self, docs: list[dict], response: dict) -> float:
        """评估 REST 调用结果质量。

        质量评分策略：
        - 有有效数据：0.8 ~ 0.95（取决于数据完整度）
        - 空数据：0.1
        - MCP 返回 error：0.0
        """
        status_code = response.get("status_code", 0)

        if status_code >= 400:
            return 0.0

        if not docs:
            return 0.1

        # 有数据：基础分 0.8，按数据丰富度加分
        total_fields = 0
        for doc in docs:
            if doc.get("structured_data"):
                total_fields += len(doc["structured_data"])

        # 字段越多，分数越高，封顶 0.95
        bonus = min(total_fields * 0.02, 0.15)
        return min(0.8 + bonus, 0.95)

    def _compute_query_signature(self, input_data: dict) -> str:
        """计算查询签名（SHA-256），用于跨轮去重和缓存。"""
        canonical = json.dumps(
            {
                "endpoint": input_data.get("rest_endpoint", ""),
                "method": input_data.get("rest_method", "GET"),
                "payload": input_data.get("rest_payload"),
                "function": input_data.get("function", ""),
                "function_params": input_data.get("function_params", {}),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def _compute_evidence_signature(self, response: dict) -> str:
        """计算响应数据签名，用于 Evidence 稳定性判断。"""
        data = response.get("data", {})
        canonical = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# ============================================================================
# 单例工厂
# ============================================================================

_rest_tool_instance: RestTool | None = None


def get_rest_tool() -> RestTool:
    """获取 RestTool 单例实例。"""
    global _rest_tool_instance
    if _rest_tool_instance is None:
        _rest_tool_instance = RestTool()
    return _rest_tool_instance