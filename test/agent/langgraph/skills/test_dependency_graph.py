"""Skill 依赖图单元测试（设计文档 v1.1 §9.5）。"""
from __future__ import annotations

from agent.langgraph.skills.dependency_graph import (
    SkillDependency,
    SkillDependencyGraph,
)


def _dep(skill_id: str, linked_skill_id: str, **overrides) -> SkillDependency:
    """构造测试用 SkillDependency。"""
    defaults = dict(skill_id=skill_id, linked_skill_id=linked_skill_id)
    defaults.update(overrides)
    return SkillDependency(**defaults)


# ========== 节点 / 边管理 ==========


def test_add_node_idempotent():
    """重复添加同一节点幂等。"""
    graph = SkillDependencyGraph()
    graph.add_node("A")
    graph.add_node("A")
    assert graph.nodes == {"A"}


def test_add_edge_adds_both_nodes():
    """add_edge 自动确保两端节点存在。"""
    graph = SkillDependencyGraph()
    graph.add_edge(_dep("A", "B"))
    assert graph.nodes == {"A", "B"}


def test_nodes_property():
    """nodes 返回全部节点 ID（只读副本）。"""
    graph = SkillDependencyGraph()
    graph.add_node("A")
    graph.add_node("B")
    result = graph.nodes
    assert result == {"A", "B"}
    # 修改返回值不影响原图
    result.add("C")
    assert graph.nodes == {"A", "B"}


# ========== 拓扑排序 ==========


def test_topological_sort_no_deps():
    """无依赖时全部节点输出。"""
    graph = SkillDependencyGraph()
    for n in ("A", "B", "C"):
        graph.add_node(n)
    order = graph.topological_sort()
    assert set(order) == {"A", "B", "C"}
    assert len(order) == 3


def test_topological_sort_ordered():
    """被依赖者排在依赖者之前。"""
    graph = SkillDependencyGraph()
    # A 依赖 B → B 必须先执行
    graph.add_edge(_dep("A", "B"))
    order = graph.topological_sort()
    assert order.index("B") < order.index("A")


def test_topological_sort_with_cycle_returns_partial():
    """存在环时返回已排序部分（环内节点不输出）。"""
    graph = SkillDependencyGraph()
    # A ↔ B 构成环，C 独立
    graph.add_edge(_dep("A", "B"))
    graph.add_edge(_dep("B", "A"))
    graph.add_node("C")
    order = graph.topological_sort()
    assert "C" in order
    assert "A" not in order
    assert "B" not in order


# ========== 环检测 ==========


def test_detect_cycle_no_cycle():
    """无环时返回 None。"""
    graph = SkillDependencyGraph()
    graph.add_edge(_dep("A", "B"))
    graph.add_edge(_dep("B", "C"))
    assert graph.detect_cycle() is None


def test_detect_cycle_finds_cycle():
    """有环时返回环路径节点列表。"""
    graph = SkillDependencyGraph()
    graph.add_edge(_dep("A", "B"))
    graph.add_edge(_dep("B", "A"))
    cycle = graph.detect_cycle()
    assert cycle is not None
    assert len(cycle) >= 2
    # 环路径首尾相同
    assert cycle[0] == cycle[-1]


# ========== 依赖查询 ==========


def test_get_dependencies():
    """获取节点直接依赖的 skill_id 列表。"""
    graph = SkillDependencyGraph()
    graph.add_edge(_dep("A", "B"))
    graph.add_edge(_dep("A", "C"))
    assert set(graph.get_dependencies("A")) == {"B", "C"}


def test_get_dependents():
    """获取直接依赖某节点的 skill_id 列表（反向查询）。"""
    graph = SkillDependencyGraph()
    graph.add_edge(_dep("A", "B"))
    graph.add_edge(_dep("C", "B"))
    assert set(graph.get_dependents("B")) == {"A", "C"}
