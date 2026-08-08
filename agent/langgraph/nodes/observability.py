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
"""可观测性节点（v2.1 §3.2 改造点 8 + P1-F 修复）。

作为所有终态（pass/filter/regenerate/reject/exhausted）的必经节点，
统一记录全局指标、结构化日志与脱敏后的审计记录。

参考原有实现：
- api/utils/metrics.py: Prometheus 指标记录
- api/utils/structured_logger.py: 结构化日志
"""

import logging
import re
import time
from typing import Any

from agent.langgraph.state import AgentState

logger = logging.getLogger(__name__)


# 敏感字段集合：包含用户原始问题或外部内容，需 PII 脱敏后才能落审计存储
# - user_question：用户原始输入，可能含手机号/身份证/邮箱
# - content：Evidence/文档内容片段
# - source_uri：可能携带 token/签名的资源链接
_REDACT_FIELDS = frozenset({"user_question", "content", "source_uri"})

# 终态集合：所有终态都必须流经 observability 节点，确保审计/指标统一落盘
# - pass：质量通过，直接输出
# - filter：低置信过滤
# - regenerate：触发重试
# - reject：ENFORCED 模式下拒答
# - exhausted：重试预算耗尽
_TERMINAL_ACTIONS = frozenset({"pass", "filter", "regenerate", "reject", "exhausted"})


# PII 脱敏正则集合（按最常见模式覆盖，避免引入重型依赖）
# 1. 大陆手机号：1[3-9]\d{9}
# 2. 18 位身份证号（含末位 X）：[1-9]\d{17}[\dXx]
# 3. 邮箱：通用邮箱正则
_PII_PATTERNS = (
    (re.compile(r"1[3-9]\d{9}"), "[PHONE]"),
    (re.compile(r"[1-9]\d{16}[\dXx]"), "[IDCARD]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
)


def _should_redact(field_name: str) -> bool:
    """判断字段是否需要 PII 脱敏。

    设计决策：
    - 仅对存储到审计/离线分析的敏感字段进行脱敏，
      避免对结构化指标字段（如分数、计数）误处理；
    - 维护一个常量集合而非散落在调用点的 if 分支，
      便于审计合规清单演进。

    Args:
        field_name: 字段名

    Returns:
        bool: True 表示该字段需脱敏
    """
    return field_name in _REDACT_FIELDS


def _redact_value(value: str) -> str:
    """PII 脱敏：手机号/身份证/邮箱等。

    设计决策：
    - 使用正则替换为占位符而非完全擦除，保留可读性的同时移除敏感信息；
    - 对非字符串输入直接 str() 转换后处理，避免调用方类型判断；
    - 不引入第三方 PII 库（如 presidio），降低运行时依赖，
      满足当前合规场景的精度要求。

    Args:
        value: 原始值

    Returns:
        str: 脱敏后的值
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    redacted = value
    for pattern, placeholder in _PII_PATTERNS:
        redacted = pattern.sub(placeholder, redacted)
    return redacted


def _build_audit_record(state: AgentState) -> dict:
    """构建审计记录（用于离线分析和 PII 脱敏后的审计存储）。

    设计决策：
    - 仅采集合规审计所需的最小字段集，避免冗余 PII 入库；
    - 对敏感字段（user_question/content/source_uri）调用 _redact_value 脱敏；
    - 引用指标作为质量审计的核心量化信号，需完整保留；
    - trace_id/evidence_snapshot_id 作为关联主键，便于跨存储追溯。

    Args:
        state: 当前 AgentState

    Returns:
        dict: 脱敏后的审计记录
    """
    # 收集原始字段（仅合规审计所需最小集）
    raw_record = {
        "trace_id": state.get("trace_id", ""),
        "evidence_snapshot_id": state.get("evidence_snapshot_id", ""),
        "enforcement_mode": state.get("enforcement_mode", ""),
        "policy_action": state.get("policy_action", ""),
        "claim_verdicts": state.get("claim_verdicts", []),
        "citation_metrics": state.get("citation_metrics", {}) or {},
        # 敏感字段：用户原始问题（脱敏后入库）
        "user_question": state.get("user_question", ""),
    }

    # 对敏感字段执行 PII 脱敏，非敏感字段原样保留
    redacted_record: dict[str, Any] = {}
    for field_name, value in raw_record.items():
        if _should_redact(field_name) and isinstance(value, str):
            redacted_record[field_name] = _redact_value(value)
        else:
            redacted_record[field_name] = value

    return redacted_record


def observability_node(state: AgentState) -> dict[str, Any]:
    """可观测性节点（所有终态必经）。

    记录全局指标和结构化日志：
    - e2e_latency：端到端延迟
    - 各节点耗时：从 node_timings 中提取
    - 工具调用次数：根据 route_target 判断
    - 质量评分：rag_quality_score / db_quality_score
    - 幻觉检测分数：hallucination_score
    - 强制级别与策略决策：enforcement_mode / policy_action
    - Evidence/Claim/引用相关：evidence_snapshot_id / claim_verdicts / citation_catalog / citation_metrics
    - 验证器状态与拒答原因：verifier_status / reject_reason

    设计决策（v2.1 §3.2 改造点 8 + P1-F 修复）：
    - 所有终态（pass/filter/regenerate/reject/exhausted）统一经此节点，
      确保审计记录、Prometheus 指标、结构化日志三路落盘的一致性；
    - 不修改业务字段，仅追加 observability 节点的耗时；
    - 审计记录通过 _build_audit_record 单独构造并脱敏后日志输出。

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段（仅 node_timings，不修改业务状态）
    """
    start_time = time.time()

    trace_id = state.get("trace_id", "")
    node_timings = state.get("node_timings", {})
    route_target = state.get("route_target", "chitchat")
    retry_count = state.get("retry_count", 0)
    rag_quality_score = state.get("rag_quality_score", 0.0)
    db_quality_score = state.get("db_quality_score", 0.0)
    hallucination_score = state.get("hallucination_score", 0.0)
    hallucination_action = state.get("hallucination_action", "pass")
    generated_answer = state.get("generated_answer", "")
    graph_start_time = state.get("graph_start_time")

    # v2.1 §3.2 改造点 8 新增字段：终态必经，统一记录
    # - evidence_snapshot_id：Evidence 快照 ID，跨节点一致性硬约束 #4 的追溯主键
    # - enforcement_mode：强制级别（disabled/shadow/enforced）
    # - claim_verdicts：Claim 裁决列表，用于离线质量分析
    # - citation_catalog：引用目录，用于引用完整性审计
    # - citation_metrics：引用指标，量化信号（validity/correctness/completeness/faithfulness）
    # - policy_action：Policy Engine 决策（pass/filter/regenerate/reject）
    # - verifier_status：验证器状态（ok/timeout/error/mixed/unknown）
    # - reject_reason：拒答原因（ENFORCED 模式下 reject 时填充）
    evidence_snapshot_id = state.get("evidence_snapshot_id", "")
    enforcement_mode = state.get("enforcement_mode", "")
    claim_verdicts = state.get("claim_verdicts", [])
    citation_catalog = state.get("citation_catalog", [])
    citation_metrics = state.get("citation_metrics", {}) or {}
    policy_action = state.get("policy_action", "")
    verifier_status = state.get("verifier_status", "")
    reject_reason = state.get("reject_reason", "")

    # 计算端到端延迟：以 user_question_node 注入的 graph_start_time 为基准，
    # 而非本节点的 start_time，避免仅记录到 observability 自身耗时
    if graph_start_time:
        e2e_latency_ms = int((time.time() - graph_start_time) * 1000)
    else:
        # 兜底：graph_start_time 缺失时退化为节点耗时 + 各节点累计耗时
        total_node_latency = sum(node_timings.values()) if node_timings else 0
        e2e_latency_ms = int((time.time() - start_time) * 1000) + total_node_latency

    # 终态校验：policy_action 落在终态集合内才视为合法终态，
    # 出现未知值时仅告警不阻断，避免 observability 自身故障影响主流程
    if policy_action and policy_action not in _TERMINAL_ACTIONS:
        logger.warning(
            f"[observability] 未知 policy_action={policy_action}, trace_id={trace_id}, "
            f"期望值: {sorted(_TERMINAL_ACTIONS)}"
        )

    # 记录结构化日志（运行时指标，不含敏感 PII）
    # 注意：claim_verdicts/citation_catalog 仅记录数量，避免大对象污染日志
    log_data = {
        "trace_id": trace_id,
        "route_target": route_target,
        "retry_count": retry_count,
        "e2e_latency_ms": e2e_latency_ms,
        "node_timings": node_timings,
        "rag_quality_score": rag_quality_score,
        "db_quality_score": db_quality_score,
        "hallucination_score": hallucination_score,
        "hallucination_action": hallucination_action,
        "answer_length": len(generated_answer),
        # v2.1 §3.2 改造点 8 新增字段
        "evidence_snapshot_id": evidence_snapshot_id,
        "enforcement_mode": enforcement_mode,
        "claim_verdicts_count": len(claim_verdicts) if claim_verdicts else 0,
        "citation_catalog_count": len(citation_catalog) if citation_catalog else 0,
        "citation_metrics": citation_metrics,
        "policy_action": policy_action,
        "verifier_status": verifier_status,
        "reject_reason": reject_reason,
    }

    logger.info(f"[observability] 全局指标: {log_data}")

    # 构建并输出脱敏后的审计记录（用于离线分析和审计存储）
    # 设计决策：审计记录独立于运行时指标，单独走 PII 脱敏流程，
    # 避免敏感字段（user_question/content/source_uri）直接落审计存储
    audit_record = _build_audit_record(state)
    logger.info(f"[observability] 审计记录(已脱敏): {audit_record}")

    # 记录 Prometheus 指标（如果可用）
    try:
        from api.utils import metrics

        # 记录端到端延迟
        metrics.record_e2e_latency(e2e_latency_ms / 1000.0)

        # 记录幻觉检测分数
        if hallucination_score > 0:
            metrics.record_hallucination_score(hallucination_score, hallucination_action)
    except Exception as e:
        logger.warning(f"[observability] Prometheus 指标记录失败: {e}")

    # 记录结构化日志（如果可用）
    try:
        from api.utils.structured_logger import log_trace

        log_trace(
            trace_id=trace_id,
            duration_ms=e2e_latency_ms,
            status="success",
            metadata=log_data,
        )
    except Exception as e:
        logger.warning(f"[observability] 结构化日志记录失败: {e}")

    return {
        "node_timings": {
            **node_timings,
            "observability": int((time.time() - start_time) * 1000),
        },
    }
