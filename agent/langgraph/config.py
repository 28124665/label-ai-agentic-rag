"""RAG 增强能力配置加载器。

对应设计文档：docs/rag_enhancement_integration_design.md §5 配置驱动开关。

职责
----
1. 加载 ``conf/rag_enhancement.yaml`` 配置文件；
2. 支持环境变量覆盖（前缀 ``RAG_ENHANCEMENT__``，双下划线分隔层级）；
3. 文件缺失或解析失败时使用内置默认配置兜底；
4. 模块级单例缓存，避免重复读取文件。

环境变量覆盖规则
----------------
- 前缀：``RAG_ENHANCEMENT__``
- 层级分隔符：双下划线 ``__``（配置键内部的单下划线保留不变）
- 类型推断：``true``/``false`` → 布尔；纯数字 → int/float；
  ``[...]``/``{...}`` → JSON 解析；其余 → 字符串

示例::

    RAG_ENHANCEMENT__GATEWAY__VERIFIER_MODE=remote
    RAG_ENHANCEMENT__HALLUCINATION__ENABLED=false
    RAG_ENHANCEMENT__GRADER__TRIGGER_RANGE=[0.3,0.8]
    RAG_ENHANCEMENT__ROLLOUT__PERCENTAGE=50
"""

from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# 配置文件名（位于项目根目录 conf/ 下）
_CONFIG_FILENAME = "rag_enhancement.yaml"

# 环境变量覆盖前缀
_ENV_PREFIX = "RAG_ENHANCEMENT__"

# 默认配置：文件缺失、解析失败或字段缺失时兜底。
# 结构与 conf/rag_enhancement.yaml 保持一致。
_DEFAULT_CONFIG: dict[str, Any] = {
    "gateway": {
        "verifier_mode": "local",
        "remote_base_url": "http://localhost:9380",
        "remote_timeout": 30,
        "shadow_compare_only": True,
    },
    "hallucination": {
        "enabled": True,
        "pass_threshold": 0.85,
        "filter_threshold": 0.6,
        "regenerate_threshold": 0.3,
        "max_regenerates": 2,
        "rule_weight": 0.4,
        "nli_weight": 0.4,
        "llm_weight": 0.2,
    },
    "grader": {
        "enabled": True,
        "evaluator_model": "cross_encoder",
        "trigger_range": [0.4, 0.7],
        "fallback_on_failure": True,
        "score_merge_ratio": 0.4,
        "thresholds": {
            "base": 0.7,
            "grader_merged": 0.65,
        },
    },
    "circuit_breaker": {
        "retrieval": {
            "failure_threshold": 3,
            "recovery_timeout": 30,
            "half_open_max_calls": 2,
            "call_timeout": 10,
        },
    },
    "query_rewrite": {
        "enabled": True,
        "sub_query_decompose": {
            "enabled": True,
            "rrf_k": 60,
        },
        "hyde": {
            "enabled": True,
            "max_hypothetical_length": 200,
            "temperature": 0.3,
        },
    },
    "retry": {
        "max_retries": 3,
        "max_retry_tokens": 2000,
        "min_relevant_docs": 2,
    },
    "rollout": {
        "strategy": "percentage",
        "percentage": 100,
        "tenant_whitelist": [],
    },
}

# 模块级单例缓存（仅缓存默认路径的加载结果，避免重复读取文件）
_config_cache: dict[str, Any] | None = None


def _get_default_config_path() -> Path:
    """获取默认配置文件路径。

    基于本文件位置推导项目根目录，避免依赖进程工作目录。
    本文件位于 ``<project_root>/agent/langgraph/config.py``，
    故 ``parents[2]`` 即项目根目录。
    """
    project_root = Path(__file__).resolve().parents[2]
    return project_root / "conf" / _CONFIG_FILENAME


def _convert_env_value(value: str) -> Any:
    """将环境变量字符串转换为合适的 Python 类型。

    转换优先级：布尔 → int → float → JSON（列表/字典）→ 字符串。

    Args:
        value: 环境变量原始字符串。

    Returns:
        转换后的值。
    """
    low = value.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    # int（同时排除 "1"/"0" 被误判为布尔的情况，交由数值路径处理）
    try:
        return int(value)
    except ValueError:
        pass
    # float
    try:
        return float(value)
    except ValueError:
        pass
    # JSON 列表/字典
    if value.startswith(("[", "{")):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            pass
    return value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """深度合并两个配置字典，override 中的值优先。

    用于将文件配置叠加到默认配置之上，保证字段缺失时有默认值兜底。

    Args:
        base: 基础配置（默认配置）。
        override: 覆盖配置（文件配置）。

    Returns:
        合并后的新字典（不修改入参）。
    """
    result = deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _apply_env_overrides(config: dict[str, Any]) -> dict[str, Any]:
    """将环境变量覆盖应用到配置字典（就地修改）。

    扫描所有以 ``RAG_ENHANCEMENT__`` 开头的环境变量，
    按双下划线拆分层级并写入配置。

    Args:
        config: 待覆盖的配置字典（会被就地修改）。

    Returns:
        应用了环境变量覆盖后的配置字典。
    """
    for env_key, env_value in os.environ.items():
        if not env_key.startswith(_ENV_PREFIX):
            continue
        # 去掉前缀后按双下划线拆分层级路径（键内部的单下划线保留）
        path = env_key[len(_ENV_PREFIX) :]
        parts = path.lower().split("__")
        if not parts or not parts[-1]:
            continue
        # 逐层定位/创建嵌套字典
        current = config
        for part in parts[:-1]:
            if not isinstance(current.get(part), dict):
                current[part] = {}
            current = current[part]
        current[parts[-1]] = _convert_env_value(env_value)
        logger.debug(f"[rag_enhancement] 环境变量覆盖: {'.'.join(parts)} = {env_value!r}")
    return config


def load_rag_enhancement_config(
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """加载 RAG 增强能力配置（带模块级单例缓存）。

    加载流程：
        1. 以内置默认配置为基础；
        2. 若配置文件存在且解析成功，深度合并覆盖默认值；
        3. 应用环境变量覆盖（优先级最高）；
        4. 缓存结果（仅缓存默认路径的加载，避免重复读文件）。

    Args:
        config_path: 配置文件路径。为 None 时使用默认路径
            ``<project_root>/conf/rag_enhancement.yaml``。
            传入自定义路径时不写入单例缓存（便于测试）。

    Returns:
        完整的配置字典。调用方应视为只读，切勿就地修改。
    """
    global _config_cache

    # 默认路径下命中缓存直接返回
    if config_path is None and _config_cache is not None:
        return _config_cache

    path = Path(config_path) if config_path else _get_default_config_path()

    # 1. 以默认配置为基础
    config = deepcopy(_DEFAULT_CONFIG)

    # 2. 读取并合并文件配置
    if path.exists():
        try:
            with path.open(encoding="utf-8") as f:
                file_config = yaml.safe_load(f) or {}
            if isinstance(file_config, dict):
                config = _deep_merge(config, file_config)
                logger.debug(f"[rag_enhancement] 配置文件加载成功: {path}")
            else:
                logger.warning(f"[rag_enhancement] 配置文件根节点非字典类型: {path}，使用默认配置")
        except yaml.YAMLError as e:
            logger.warning(f"[rag_enhancement] 配置文件解析失败: {path}，{e}，使用默认配置")
        except OSError as e:
            logger.warning(f"[rag_enhancement] 配置文件读取失败: {path}，{e}，使用默认配置")
    else:
        logger.info(f"[rag_enhancement] 配置文件不存在: {path}，使用默认配置")

    # 3. 应用环境变量覆盖（优先级最高）
    config = _apply_env_overrides(config)

    # 4. 缓存默认路径的加载结果
    if config_path is None:
        _config_cache = config

    return config


def get_config_section(section: str) -> dict[str, Any]:
    """获取指定的配置段。

    Args:
        section: 顶层配置段名称，如 ``"grader"``、``"hallucination"``。

    Returns:
        该配置段的字典；若段不存在或非字典类型，返回空字典 ``{}``。
    """
    config = load_rag_enhancement_config()
    value = config.get(section)
    if isinstance(value, dict):
        return value
    return {}


def is_feature_enabled(feature: str) -> bool:
    """检查某项增强功能是否启用。

    支持点号分层记法，便于查询嵌套开关：

    - ``is_feature_enabled("hallucination")``
      → 读取 ``hallucination.enabled``
    - ``is_feature_enabled("query_rewrite.sub_query_decompose")``
      → 读取 ``query_rewrite.sub_query_decompose.enabled``

    判定规则：
    - 定位到的节点为字典且含 ``enabled`` 字段 → 返回该布尔值；
    - 定位到的节点为字典但无 ``enabled`` 字段 → 视为存在即启用，返回 ``True``；
    - 定位到的节点为标量 → 返回其布尔值；
    - 节点不存在 → 返回 ``False``。

    Args:
        feature: 功能名称，支持点号分层（如 ``"query_rewrite.hyde"``）。

    Returns:
        是否启用。
    """
    config = load_rag_enhancement_config()
    parts = feature.split(".")
    current: Any = config
    for part in parts:
        if not isinstance(current, dict):
            return False
        current = current.get(part)
        if current is None:
            return False
    if isinstance(current, dict):
        if "enabled" in current:
            return bool(current["enabled"])
        return True
    return bool(current)


# ========== Claim 级可追溯幻觉检测配置（v2.1 §4.0 + §4.9） ==========

from dataclasses import dataclass, field  # noqa: E402
from enum import Enum  # noqa: E402


class EnforcementMode(str, Enum):
    """幻觉检测强制级别（v2.1 §4.0.1，P0-3 修复）。

    类比 Java 中的枚举单例，确保全局唯一且类型安全。

    - DISABLED: legacy 模式，明确不提供 Claim 可追溯保证
      响应返回 enforcement_mode=disabled，不保证引用可追溯
    - SHADOW: 影子模式，只记录差异不影响输出（灰度阶段使用）
    - ENFORCED: 强制模式，AST 解析失败重试后必须拒答，不输出未验证纯文本
      filter_soft 只能用于 shadow 内部比对，不能把"不支持"内容展示给最终用户
    """

    DISABLED = "disabled"   # legacy，不提供保证
    SHADOW = "shadow"       # 影子，只记录差异
    ENFORCED = "enforced"   # 强制，fail-closed


class RunMode(str, Enum):
    """业务运行模式（与 EnforcementMode 正交，v2.1 §4.0.1）。

    - CHITCHAT: 闲聊/创作，不需要可追溯
    - FACTUAL: 事实问答，需要 fail-closed
    - REPORT: 报告生成，需要可追溯但容忍度略高
    """

    CHITCHAT = "chitchat"
    FACTUAL = "factual"
    REPORT = "report"


@dataclass
class VerificationBudget:
    """整轮验证预算（v2.1 §4.9，任务二建议 3）。

    任一预算耗尽立即终止剩余验证，未验证 Claim 标记为 verifier_error。

    类比 Java 中的 ``@Builder`` 配置对象：
        ``VerificationBudget.builder().maxClaims(30).maxTotalPairs(150).build()``
    """

    # Claim 数量预算
    max_claims: int = 30                 # 单轮最大 Claim 数
    max_claims_per_section: int = 10     # 单章节最大 Claim 数

    # 引用数量预算
    max_citations_per_claim: int = 5     # 单 Claim 最大引用数
    max_total_pairs: int = 150           # 整轮最大 pair 数

    # verifier 调用预算
    max_llm_calls: int = 50              # 整轮最大 LLM verifier 调用数
    max_nli_calls: int = 100             # 整轮最大 NLI verifier 调用数

    # 超时预算
    per_pair_timeout_ms: int = 3000      # 单 pair 验证超时
    total_verification_timeout_ms: int = 30000  # 整轮验证超时

    # 批量调用
    batch_size: int = 10                 # 批量 verifier 调用批次大小
    batch_concurrency: int = 3           # 批量并发数

    # 缓存策略
    cache_enabled: bool = True           # 启用 pair 验证结果缓存
    cache_ttl_seconds: int = 3600        # 缓存 TTL

    @classmethod
    def from_config(cls, config: dict) -> "VerificationBudget":
        """从 agent_config 字典构建预算。

        Args:
            config: agent_config 字典（可能含 verification_budget 段）

        Returns:
            VerificationBudget 实例
        """
        budget_cfg = config.get("verification_budget", {}) if isinstance(config, dict) else {}
        return cls(
            max_claims=budget_cfg.get("max_claims", 30),
            max_citations_per_claim=budget_cfg.get("max_citations_per_claim", 5),
            max_total_pairs=budget_cfg.get("max_total_pairs", 150),
            max_llm_calls=budget_cfg.get("max_llm_calls", 50),
            max_nli_calls=budget_cfg.get("max_nli_calls", 100),
            per_pair_timeout_ms=budget_cfg.get("per_pair_timeout_ms", 3000),
            total_verification_timeout_ms=budget_cfg.get("total_verification_timeout_ms", 30000),
        )


def resolve_run_mode(
    route_target: str,
    agent_config: dict | None = None,
) -> RunMode:
    """解析业务运行模式（v2.1 §4.0.1）。

    解析规则：
    - route_target == "chitchat" → CHITCHAT
    - route_target in ("rag", "database", "hybrid") → FACTUAL
    - route_target == "web" → FACTUAL
    - agent_config 显式指定 run_mode 时优先

    Args:
        route_target: 路由目标（rag/database/hybrid/web/chitchat）
        agent_config: Agent 配置字典

    Returns:
        RunMode 枚举
    """
    if agent_config and agent_config.get("run_mode"):
        mode_str = agent_config["run_mode"]
        try:
            return RunMode(mode_str)
        except ValueError:
            pass

    if route_target == "chitchat":
        return RunMode.CHITCHAT
    return RunMode.FACTUAL


def resolve_enforcement(
    run_mode: RunMode,
    agent_config: dict | None = None,
) -> EnforcementMode:
    """解析强制级别（v2.1 §4.0.1，P0-3 修复）。

    解析规则：
    - CHITCHAT → DISABLED（闲聊不需要可追溯）
    - FACTUAL/REPORT + citation_enhancement.enabled=false → DISABLED（明确告知无保证）
    - FACTUAL/REPORT + enabled=true + rollout_phase=shadow → SHADOW
    - FACTUAL/REPORT + enabled=true + rollout_phase=full → ENFORCED

    Args:
        run_mode: 业务运行模式
        agent_config: Agent 配置字典

    Returns:
        EnforcementMode 枚举
    """
    # 闲聊模式：不需要可追溯
    if run_mode == RunMode.CHITCHAT:
        return EnforcementMode.DISABLED

    # 读取 citation_enhancement 配置
    config = agent_config or {}
    citation_cfg = config.get("citation_enhancement", {})
    if not citation_cfg.get("enabled", False):
        return EnforcementMode.DISABLED  # 明确不提供保证

    phase = citation_cfg.get("rollout_phase", "shadow")
    if phase == "shadow":
        return EnforcementMode.SHADOW
    return EnforcementMode.ENFORCED


def get_verification_budget(agent_config: dict | None = None) -> VerificationBudget:
    """获取验证预算配置。

    Args:
        agent_config: Agent 配置字典

    Returns:
        VerificationBudget 实例
    """
    return VerificationBudget.from_config(agent_config or {})

