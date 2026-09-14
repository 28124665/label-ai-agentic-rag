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
"""GraphTool — 通过 HTTP 调用 ontology 图问答（GraphRAG）服务的适配层。

GraphTool 作为 Agent 侧的轻量 HTTP 防腐层，负责：
1. 接收路由/Planner 选中的图查询参数
2. 通过 httpx 调用下游薄 HTTP 服务 ``POST /graph/ask``
3. 解析 GraphQAResult，映射为 GraphToolOutput
4. 质量评分 + 空结果/异常兜底

不负责：
- 图数据库连接（下游 GraphQAPipeline 负责）
- Cypher 生成与修复（下游负责）
- 权限校验（下游服务负责）

设计文档: docs/GraphTool接入设计文档.md §4.1
上游契约: docs/GraphTool上游对接文档.md
"""

import hashlib
import json
import logging
import os
import time
from typing import TypedDict

logger = logging.getLogger(__name__)


# ============================================================================
# TypedDict I/O
# ============================================================================


class GraphToolInput(TypedDict, total=False):
    """GraphTool 输入参数。

    Attributes:
        query: 用户原始查询（自然语言）
        query_simplified: 简化后的查询
        source_type: 本体 Schema 子类型（默认 "Meeting"）
        max_rows: 最大返回行数（默认 20）
        max_result_chars: 最大结果字符数（默认 12000）
        enable_hybrid_retrieval: 是否启用 hybrid 向量上下文检索（默认 False）
        enable_pg: 是否启用 PostgreSQL 双源分支（默认 False）
        tenant_id: 租户 ID
        llm_id: LLM 模型 ID
        timeout_ms: 超时时间（默认 30000）
    """

    query: str
    query_simplified: str
    source_type: str
    max_rows: int
    max_result_chars: int
    enable_hybrid_retrieval: bool
    enable_pg: bool
    tenant_id: str
    llm_id: str
    timeout_ms: int


class GraphToolOutput(TypedDict, total=False):
    """GraphTool 输出结果。

    Attributes:
        success: 是否成功
        error: 错误信息（空字符串表示成功）
        error_code: 错误码（RETRIEVAL_SERVICE_ERROR 等）
        answer: 下游 pipeline 合成答案
        rows: 图查询原始结果行
        row_count: 结果行数
        quality_score: 质量评分（0.0 ~ 1.0）
        has_relevant: 是否存在相关结果
        relevant_count: 相关结果数量
        planner: PlannerOutput 序列化 dict
        cypher_query: CypherPlan.cypher
        cypher_params: CypherPlan.params
        repair_trace: Cypher 修复追踪
        query_stats: 查询统计（miss_rate / hit_rate 等）
        pg_rows: PostgreSQL 双源结果行
        pg_row_count: PG 结果行数
        pg_query_log: PG 查询日志
        pg_query_stats: PG 查询统计
        pg_repair_trace: PG 修复追踪
        latency_ms: 调用耗时
        query_signature: 查询签名（SHA-256）
        evidence_signature: 响应数据签名
    """

    success: bool
    error: str
    error_code: str
    answer: str
    rows: list[dict]
    row_count: int
    quality_score: float
    has_relevant: bool
    relevant_count: int
    planner: dict
    cypher_query: str
    cypher_params: dict
    repair_trace: list[str]
    query_stats: dict
    pg_rows: list[dict]
    pg_row_count: int
    pg_query_log: list[dict]
    pg_query_stats: dict
    pg_repair_trace: list[str]
    latency_ms: int
    query_signature: str
    evidence_signature: str


# ============================================================================
# GraphTool
# ============================================================================


class GraphTool:
    """GraphTool：通过 HTTP 调用 ontology 图问答服务。

    职责：
    1. 拼装 HTTP 请求 → 调用下游 ``POST /graph/ask``
    2. 解析 GraphQAResult → GraphToolOutput
    3. 质量评分 + 空结果处理

    使用 httpx 异步客户端（延迟导入，与 HttpVerifierGateway 一致）。
    无内层重试（重试交给主流程 quality_check）。
    """

    def __init__(self, base_url: str, timeout: float = 30.0):
        """初始化 GraphTool。

        Args:
            base_url: 下游 graph QA 服务基础 URL（如 http://graph-service:8000）
            timeout: HTTP 请求超时时间（秒），默认 30.0
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def invoke(self, input_data: dict) -> GraphToolOutput:
        """调用下游 graph QA 服务并映射结果为 TypedDict 输出。

        流程：
        1. 参数校验与规范化（填充默认值）
        2. httpx POST /graph/ask
        3. 解析 GraphQAResult
        4. 质量评分
        5. 返回 GraphToolOutput
        """
        start_time = time.time()

        # 1. 参数校验
        input_data = self._validate_input(input_data)

        # 2. 拼装请求体（下游契约：request 用 "question" 字段）
        payload = {
            "question": input_data.get("query", ""),
            "source_type": input_data.get("source_type", "Meeting"),
            "max_rows": input_data.get("max_rows", 20),
            "max_result_chars": input_data.get("max_result_chars", 12000),
            "enable_hybrid_retrieval": input_data.get(
                "enable_hybrid_retrieval", False
            ),
            "enable_pg": input_data.get("enable_pg", False),
        }

        # 3. 调用下游服务（延迟导入 httpx，避免模块被 import 时报错）
        logger.info(
            f"[GraphTool] 开始调用 /graph/ask: source_type="
            f"{payload['source_type']}, max_rows={payload['max_rows']}"
        )
        try:
            import httpx
        except ImportError as e:
            logger.error(f"[GraphTool] httpx 未安装: {e}")
            return self._empty_result(
                error=f"httpx not installed: {e}",
                error_code="RETRIEVAL_SERVICE_ERROR",
                input_data=input_data,
            )

        timeout_seconds = input_data.get("timeout_ms", 30000) / 1000.0
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/graph/ask", json=payload
                )
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as e:
            logger.error(f"[GraphTool] 调用超时: {e}")
            return self._empty_result(
                error=f"Graph QA timeout: {e}",
                error_code="RETRIEVAL_SERVICE_ERROR",
                input_data=input_data,
            )
        except httpx.ConnectError as e:
            logger.error(f"[GraphTool] 连接失败: {e}")
            return self._empty_result(
                error=f"Graph QA connect error: {e}",
                error_code="RETRIEVAL_SERVICE_ERROR",
                input_data=input_data,
            )
        except httpx.HTTPStatusError as e:
            logger.error(f"[GraphTool] HTTP 状态错误: {e}")
            return self._empty_result(
                error=f"Graph QA HTTP error: {e}",
                error_code="RETRIEVAL_SERVICE_ERROR",
                input_data=input_data,
            )
        except Exception as e:
            logger.error(f"[GraphTool] 调用失败: {e}")
            return self._empty_result(
                error=str(e),
                error_code="RETRIEVAL_SERVICE_ERROR",
                input_data=input_data,
            )

        if not isinstance(data, dict):
            return self._empty_result(
                error="Graph QA 返回非法响应",
                error_code="RETRIEVAL_SERVICE_ERROR",
                input_data=input_data,
            )

        # 4. 质量评分
        quality = self._evaluate_quality(data)

        latency_ms = int((time.time() - start_time) * 1000)
        row_count = int(data.get("row_count", 0))
        error = data.get("error", "") or ""

        logger.info(
            f"[GraphTool] 调用完成: row_count={row_count}, "
            f"quality={quality}, latency={latency_ms}ms"
        )

        # 5. 构建输出（GraphQAResult → GraphToolOutput 映射）
        return GraphToolOutput(
            success=not error,
            error=error,
            error_code="" if not error else "RETRIEVAL_SERVICE_ERROR",
            answer=data.get("answer", ""),
            rows=data.get("rows", []),
            row_count=row_count,
            quality_score=quality,
            has_relevant=row_count > 0 and not error,
            relevant_count=row_count if not error else 0,
            planner=data.get("planner", {}),
            cypher_query=data.get("cypher_query", "") or data.get("cypher_plan", {}).get("cypher", ""),
            cypher_params=data.get("cypher_params", {}) or data.get("cypher_plan", {}).get("params", {}),
            repair_trace=data.get("repair_trace", []),
            query_stats=data.get("query_stats", {}),
            pg_rows=data.get("pg_rows", []),
            pg_row_count=int(data.get("pg_row_count", 0)),
            pg_query_log=data.get("pg_query_log", []),
            pg_query_stats=data.get("pg_query_stats", {}),
            pg_repair_trace=data.get("pg_repair_trace", []),
            latency_ms=latency_ms,
            query_signature=self._compute_query_signature(input_data),
            evidence_signature=self._compute_evidence_signature(data),
        )

    def _validate_input(self, input_data: dict) -> dict:
        """参数校验与规范化，填充默认值。"""
        defaults = {
            "query": "",
            "query_simplified": "",
            "source_type": "Meeting",
            "max_rows": 20,
            "max_result_chars": 12000,
            "enable_hybrid_retrieval": False,
            "enable_pg": False,
            "tenant_id": "",
            "llm_id": "",
            "timeout_ms": 30000,
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
    ) -> GraphToolOutput:
        """生成空结果（网络错误 / 超时 / 服务异常时）。"""
        if input_data is None:
            input_data = {}
        return GraphToolOutput(
            success=False,
            error=error,
            error_code=error_code,
            answer="",
            rows=[],
            row_count=0,
            quality_score=0.0,
            has_relevant=False,
            relevant_count=0,
            planner={},
            cypher_query="",
            cypher_params={},
            repair_trace=[],
            query_stats={},
            pg_rows=[],
            pg_row_count=0,
            pg_query_log=[],
            pg_query_stats={},
            pg_repair_trace=[],
            latency_ms=0,
            query_signature="",
            evidence_signature="",
        )

    def _evaluate_quality(self, output: dict) -> float:
        """评估图问答结果质量。

        质量评分策略（设计文档 §5.2）：
        - error 非空：0.0
        - row_count == 0：0.0（空结果 → 降级 RAG）
        - 否则 0.85（契约分数）
          - query_stats.miss_rate > 0.5：0.5（大量标签缺失）
          - repair_trace 非空：-0.1（修复过）
        """
        if output.get("error"):
            return 0.0

        if output.get("row_count", 0) == 0:
            return 0.0

        score = 0.85

        query_stats = output.get("query_stats") or {}
        try:
            miss_rate = float(query_stats.get("miss_rate", 0.0))
        except (TypeError, ValueError):
            miss_rate = 0.0
        if miss_rate > 0.5:
            score = 0.5

        if output.get("repair_trace"):
            score -= 0.1

        return score

    def _compute_query_signature(self, input_data: dict) -> str:
        """计算查询签名（SHA-256），用于跨轮去重和缓存。"""
        canonical = json.dumps(
            {
                "query": input_data.get("query", ""),
                "source_type": input_data.get("source_type", "Meeting"),
                "max_rows": input_data.get("max_rows", 20),
                "enable_hybrid_retrieval": input_data.get(
                    "enable_hybrid_retrieval", False
                ),
                "enable_pg": input_data.get("enable_pg", False),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def _compute_evidence_signature(self, data: dict) -> str:
        """计算响应数据签名，用于 Evidence 稳定性判断。"""
        rows = data.get("rows", [])
        canonical = json.dumps(rows, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# ============================================================================
# 单例工厂
# ============================================================================

_graph_tool_instance: GraphTool | None = None

_DEFAULT_GRAPH_BASE_URL = os.environ.get("GRAPH_QA_BASE_URL", "http://localhost:9380")


def get_graph_tool(base_url: str | None = None) -> GraphTool:
    """获取 GraphTool 单例实例。

    base_url 注入优先级：显式传入 > 环境变量 GRAPH_QA_BASE_URL > 默认值。
    单例在首次调用时创建，之后复用（避免重复初始化 httpx 客户端）。
    """
    global _graph_tool_instance
    if _graph_tool_instance is None:
        _graph_tool_instance = GraphTool(base_url or _DEFAULT_GRAPH_BASE_URL)
    return _graph_tool_instance