"""Shadow 路由器单元测试（设计文档 §19 阶段2）。"""
from __future__ import annotations

import asyncio

from agent.langgraph.skills.shadow_router import ShadowSkillRouter


# ========== 辅助路由函数 ==========


async def _slow_route(query, context):
    """模拟超时的异步路由。"""
    await asyncio.sleep(0.2)
    return {"skill_id": "late"}


async def _async_route(query, context):
    """异步路由返回 dict。"""
    return {"skill_id": "async_skill"}


def _boom_route(query, context):
    """同步抛异常的路由。"""
    raise RuntimeError("boom")


# ========== 测试 ==========


def test_shadow_route_no_route_fn():
    """route_fn=None 时不崩溃且不记录 diff。"""
    router = ShadowSkillRouter(route_fn=None)
    asyncio.run(router.shadow_route("query", {}, {"skill_id": "prod"}))
    assert router.diffs == []


def test_shadow_route_records_diff():
    """新旧结果不同时记录 divergent diff。"""
    router = ShadowSkillRouter(
        route_fn=lambda q, c: {"skill_id": "shadow_skill"}
    )
    asyncio.run(
        router.shadow_route("query", {}, {"skill_id": "prod_skill"})
    )
    diffs = router.diffs
    assert len(diffs) == 1
    assert diffs[0]["is_divergent"] is True
    assert diffs[0]["shadow"]["skill_id"] == "shadow_skill"
    assert diffs[0]["production"]["skill_id"] == "prod_skill"


def test_shadow_route_timeout():
    """route_fn 超时即丢弃，不崩溃。"""
    router = ShadowSkillRouter(route_fn=_slow_route, timeout_ms=50)
    asyncio.run(router.shadow_route("query", {}, {"skill_id": "prod"}))
    assert router.diffs == []


def test_shadow_route_exception():
    """route_fn 抛异常时不崩溃、不记录 diff。"""
    router = ShadowSkillRouter(route_fn=_boom_route)
    asyncio.run(router.shadow_route("query", {}, {"skill_id": "prod"}))
    assert router.diffs == []


def test_shadow_route_sync_fn():
    """同步 route_fn 正常工作。"""
    router = ShadowSkillRouter(
        route_fn=lambda q, c: {"skill_id": "same"}
    )
    asyncio.run(router.shadow_route("query", {}, {"skill_id": "same"}))
    assert len(router.diffs) == 1
    assert router.diffs[0]["is_divergent"] is False


def test_shadow_route_async_fn():
    """异步 route_fn 正常工作。"""
    router = ShadowSkillRouter(route_fn=_async_route)
    asyncio.run(router.shadow_route("query", {}, {"skill_id": "prod"}))
    assert len(router.diffs) == 1
    assert router.diffs[0]["shadow"]["skill_id"] == "async_skill"


def test_get_diffs_returns_buffer():
    """diffs 属性返回已记录的差异列表。"""
    router = ShadowSkillRouter(
        route_fn=lambda q, c: {"skill_id": "shadow"}
    )
    for i in range(3):
        asyncio.run(router.shadow_route(f"q{i}", {}, {"skill_id": "prod"}))
    assert len(router.diffs) == 3
