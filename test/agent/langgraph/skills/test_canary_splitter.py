"""灰度流量分配单元测试（设计文档 §19 阶段3）。"""
from __future__ import annotations

from agent.langgraph.skills.canary_splitter import (
    CanaryConfig,
    CanaryTrafficSplitter,
)


def test_disabled_returns_false():
    """enabled=False 时一律走旧 Router。"""
    splitter = CanaryTrafficSplitter()
    config = CanaryConfig(enabled=False, rollout_percentage=100.0)
    assert splitter.should_use_new_router("t1", "rev_1", config) is False


def test_canary_tenant_whitelist():
    """canary_tenants 白名单命中即走新 Router。"""
    splitter = CanaryTrafficSplitter()
    config = CanaryConfig(canary_tenants=["canary_t"], rollout_percentage=0.0)
    assert splitter.should_use_new_router("canary_t", "rev_1", config) is True
    assert splitter.should_use_new_router("normal_t", "rev_1", config) is False


def test_rollout_100_returns_true():
    """rollout_percentage=100 时全量放量。"""
    splitter = CanaryTrafficSplitter()
    config = CanaryConfig(rollout_percentage=100.0)
    assert splitter.should_use_new_router("any_tenant", "rev_1", config) is True


def test_rollout_0_returns_false():
    """rollout_percentage=0 时完全不放量。"""
    splitter = CanaryTrafficSplitter()
    config = CanaryConfig(rollout_percentage=0.0)
    assert splitter.should_use_new_router("any_tenant", "rev_1", config) is False


def test_rollout_percentage_deterministic():
    """同一 tenant + revision 组合结果稳定可复现。"""
    splitter = CanaryTrafficSplitter()
    config = CanaryConfig(rollout_percentage=50.0)
    results = [
        splitter.should_use_new_router("t1", "rev_1", config) for _ in range(5)
    ]
    assert len(set(results)) == 1  # 全部相同


def test_rollout_hash_stable():
    """同输入多次调用结果一致（md5 哈希稳定）。"""
    splitter = CanaryTrafficSplitter()
    config = CanaryConfig(rollout_percentage=30.0)
    first = splitter.should_use_new_router("tenant_x", "rev_2026", config)
    second = splitter.should_use_new_router("tenant_x", "rev_2026", config)
    assert first == second


def test_normalize_config_from_dict():
    """dict 配置归一化为 CanaryConfig。"""
    splitter = CanaryTrafficSplitter()
    config_dict = {"canary_tenants": ["t1"], "rollout_percentage": 100.0, "enabled": True}
    assert splitter.should_use_new_router("t1", "rev_1", config_dict) is True
    # enabled=False 的 dict
    assert splitter.should_use_new_router(
        "t1", "rev_1", {"enabled": False}
    ) is False
