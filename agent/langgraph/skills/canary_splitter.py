#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#
#      http://www.apache.org/licenses-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""灰度流量分配（设计文档 §19 阶段3）。

在新版 Skill Router 上线时，按租户维度灰度放量，控制爆炸半径。

分配策略（§19 阶段3，优先级从高到低）：
    1. canary_tenants 白名单 → 命中即走新 Router
    2. rollout_percentage 百分比放量 → 基于 md5(tenant_id + catalog_revision) 哈希分桶
    3. 其余 → 走旧 Router（返回 False）

一致性保证：
    同一 tenant_id + catalog_revision 组合始终映射到同一桶，
    因此灰度结果在 Catalog 版本不变时稳定可复现。

类比 Java：
    ``CanaryTrafficSplitter`` ≈ ``@Component`` 路由策略，
    ``CanaryConfig`` ≈ ``@Value`` 配置值对象（从 governance 配置加载）。
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CanaryConfig(BaseModel):
    """灰度配置（§19 阶段3）。

    通常从 ``SkillGovernance`` 的灰度字段加载。

    Attributes:
        canary_tenants: 灰度白名单租户（命中即走新 Router）
        rollout_percentage: 百分比放量（0.0-100.0），0 表示完全不放量
        enabled: 灰度总开关（False 时一律走旧 Router）
    """

    canary_tenants: list[str] = Field(default_factory=list)
    rollout_percentage: float = 0.0
    enabled: bool = True


class CanaryTrafficSplitter:
    """灰度流量分配器（§19 阶段3）。

    类比 Java 中的 ``@Component``：无状态，可单例复用。
    """

    def should_use_new_router(
        self,
        tenant_id: str,
        catalog_revision: str,
        canary_config: CanaryConfig | dict[str, Any],
    ) -> bool:
        """判定指定租户在指定 Catalog 版本下是否走新 Router。

        Args:
            tenant_id: 租户 ID
            catalog_revision: 当前 Catalog 版本号（参与哈希以保证版本内一致）
            canary_config: 灰度配置（``CanaryConfig`` 或等价 dict）

        Returns:
            True 走新 Router，False 走旧 Router
        """
        config = self._normalize_config(canary_config)
        if not config.enabled:
            return False

        # 策略1：白名单优先
        if tenant_id and tenant_id in config.canary_tenants:
            return True

        # 策略2：百分比放量
        pct = config.rollout_percentage
        if pct <= 0:
            return False
        if pct >= 100:
            return True

        # md5(tenant_id + revision) 哈希分桶，保证同一组合结果稳定
        key = f"{tenant_id}:{catalog_revision}"
        digest = hashlib.md5(key.encode("utf-8")).hexdigest()
        # 取前 8 位十六进制（32bit）映射到 0.00-99.99 的桶
        bucket = (int(digest[:8], 16) % 10000) / 100.0
        use_new = bucket < pct
        if use_new:
            logger.debug(
                "[Canary] 租户走新 Router: tenant=%s, revision=%s, bucket=%.2f, pct=%.2f",
                tenant_id, catalog_revision, bucket, pct,
            )
        return use_new

    @staticmethod
    def _normalize_config(
        canary_config: CanaryConfig | dict[str, Any],
    ) -> CanaryConfig:
        """归一化配置：容忍 dict 传入。"""
        if isinstance(canary_config, CanaryConfig):
            return canary_config
        if isinstance(canary_config, dict):
            return CanaryConfig(**canary_config)
        logger.warning(
            "[Canary] 无法识别的灰度配置类型 %s，按关闭处理",
            type(canary_config).__name__,
        )
        return CanaryConfig(enabled=False)
