#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#
#      http://www.apache.org/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""Skill 依赖图（设计文档 v1.1 §9.5）。

维护 Skill 之间的依赖关系，支持拓扑排序与环检测，
用于组合 Skill 的执行顺序编排与依赖健康检查。

设计文档 §9.5：
    - 邻接表（skill_id → 其依赖的 skills）+ 反向邻接表（skill_id → 依赖它的 skills）
    - ``topological_sort`` 使用 Kahn 算法（BFS 入度消减）
    - ``detect_cycle`` 使用 DFS 三色标记法（WHITE / GRAY / BLACK）
    - ``resolve_version_constraint`` 支持 PEP 440 / caret(^) / tilde(~) 三类版本约束

类比 Java：
    ``SkillDependencyGraph`` ≈ ``@Component`` 领域服务，
    内部维护双向索引（``Map<String, List<SkillDependency>>``）；
    ``resolve_version_constraint`` ≈ ``@Utility`` 静态工具方法。

注意：
    本模块的 ``SkillDependency`` 较 ``catalog_models.SkillDependency`` 多了
    ``linked_skill_id`` / ``role`` / ``selection_mode`` 字段，专用于依赖图建模，
    两者并存不冲突（不互相导入）。
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

# ========== packaging 降级处理 ==========
# packaging 不在项目硬依赖中，缺失时降级到简易 semver 元组比较。
try:
    from packaging.specifiers import SpecifierSet
    from packaging.version import InvalidVersion, Version

    _HAS_PACKAGING = True
except ImportError:  # pragma: no cover - 降级路径
    _HAS_PACKAGING = False
    SpecifierSet = None  # type: ignore[assignment]
    Version = None  # type: ignore[assignment]
    InvalidVersion = ValueError  # type: ignore[assignment]


# ========== 类型别名 ==========
DependencyRole = Literal["data_source", "evidence_provider", "report_parent", "companion"]
SelectionMode = Literal["required", "preferred", "fallback"]


class SkillDependency(BaseModel):
    """Skill 依赖声明（设计文档 §9.1 / §9.5）。

    类比 Java 中的 ``@ManyToOne`` 关系声明，带 semver 版本约束。

    Attributes:
        skill_id: 依赖方 Skill ID（声明依赖的 Skill）
        linked_skill_id: 被依赖的 Skill ID（依赖目标）
        version_constraint: semver 约束，如 ">=1.0,<2.0" / "^1.4" / "~1.4.0" / "*"
        required: 是否必需依赖（False = 软依赖，缺失不影响主链路）
        role: 依赖角色（data_source / evidence_provider / report_parent / companion）
        selection_mode: 选择模式（required / preferred / fallback）
    """

    model_config = ConfigDict(extra="forbid")

    skill_id: str
    linked_skill_id: str
    version_constraint: str = "*"
    required: bool = True
    role: DependencyRole = "data_source"
    selection_mode: SelectionMode = "required"


# ========== 版本约束解析（§9.5） ==========
def _parse_semver(version: str) -> tuple[int, int, int]:
    """将版本字符串解析为 (major, minor, patch) 元组（降级路径使用）。"""
    parts = version.strip().split(".")
    nums: list[int] = []
    for part in parts[:3]:
        dig = ""
        for ch in part:
            if ch.isdigit():
                dig += ch
            else:
                break
        nums.append(int(dig) if dig else 0)
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def _caret_to_pep440(ver: str) -> str:
    """caret 约束转 PEP 440。

    - ``^1.4``   → >=1.4.0,<2.0.0（major>0 锁定 major）
    - ``^0.2.3`` → >=0.2.3,<0.3.0（0.x 锁定 minor）
    - ``^0.0.3`` → >=0.0.3,<0.0.4（0.0.x 锁定 patch）
    """
    major, minor, patch = _parse_semver(ver)
    if major > 0:
        upper = (major + 1, 0, 0)
    elif minor > 0:
        upper = (major, minor + 1, 0)
    else:
        upper = (major, minor, patch + 1)
    return f">={major}.{minor}.{patch},<{upper[0]}.{upper[1]}.{upper[2]}"


def _tilde_to_pep440(ver: str) -> str:
    """tilde 约束转 PEP 440。

    - ``~1.4``   → >=1.4.0,<1.5.0（含 minor 时锁定 minor，允许 patch 更新）
    - ``~1.4.0`` → >=1.4.0,<1.5.0
    - ``~1``     → >=1.0.0,<2.0.0（仅 major 时锁定 major）
    """
    parts = ver.strip().split(".")
    major, minor, patch = _parse_semver(ver)
    if len(parts) >= 2:
        upper = (major, minor + 1, 0)
    else:
        upper = (major + 1, 0, 0)
    return f">={major}.{minor}.{patch},<{upper[0]}.{upper[1]}.{upper[2]}"


def _normalize_constraint(constraint: str) -> str | None:
    """将 caret / tilde 约束归一化为 PEP 440 约束串。"""
    constraint = constraint.strip()
    if constraint.startswith("^"):
        return _caret_to_pep440(constraint[1:])
    if constraint.startswith("~"):
        return _tilde_to_pep440(constraint[1:])
    return constraint  # 原样视为 PEP 440


def _max_version(versions: list[str]) -> str | None:
    """返回列表中最高版本。"""
    if not versions:
        return None
    if _HAS_PACKAGING:
        valid: list[tuple[Any, str]] = []
        for v in versions:
            try:
                valid.append((Version(v), v))
            except InvalidVersion:
                continue
        if not valid:
            return None
        valid.sort(key=lambda x: x[0])
        return valid[-1][1]
    return max(versions, key=_parse_semver)


def _safe_contains(spec: Any, version_str: str) -> bool:
    """安全判断版本是否满足 SpecifierSet（非法版本返回 False）。"""
    try:
        return Version(version_str) in spec  # type: ignore[operator]
    except InvalidVersion:
        return False


def _cmp(a: tuple[int, int, int], op: str, b: tuple[int, int, int]) -> bool:
    """元组比较（降级路径使用）。"""
    if op == ">=":
        return a >= b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    if op == "<":
        return a < b
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    return False


def _fallback_resolve(available: list[str], pep440_constraint: str) -> str | None:
    """packaging 不可用时的降级解析：按元组比较过滤候选版本。"""
    parsed_specs: list[tuple[str, tuple[int, int, int]]] = []
    for s in (x.strip() for x in pep440_constraint.split(",") if x.strip()):
        for op in (">=", "<=", "==", "!=", ">", "<"):
            if s.startswith(op):
                parsed_specs.append((op, _parse_semver(s[len(op):])))
                break
    candidates = [
        v for v in available
        if all(_cmp(_parse_semver(v), op, ver) for op, ver in parsed_specs)
    ]
    if not candidates:
        return None
    return max(candidates, key=_parse_semver)


def resolve_version_constraint(
    available_versions: list[str],
    constraint: str,
) -> str | None:
    """解析版本约束，返回满足约束的最高版本（设计文档 §9.5）。

    支持三类约束语法：
        1. PEP 440 标准约束：``>=1.0,<2.0`` / ``==1.4.0`` / ``*``
        2. caret 约束 ``^1.4``：兼容更新（major>0 锁 major，0.x 锁 minor，0.0.x 锁 patch）
        3. tilde 约束 ``~1.4.0``：允许 patch 更新（含 minor 锁 minor，仅 major 锁 major）

    Args:
        available_versions: 可选版本列表，如 ["1.3.0", "1.4.0", "1.4.2", "2.0.0"]
        constraint: 版本约束字符串

    Returns:
        满足约束的最高版本；无匹配或约束非法时返回 None
    """
    if not available_versions:
        return None
    constraint = (constraint or "*").strip()
    if constraint in ("", "*"):
        return _max_version(available_versions)

    pep440_constraint = _normalize_constraint(constraint)
    if pep440_constraint is None:
        logger.warning("[SkillDependencyGraph] 无法解析版本约束: %s", constraint)
        return None

    if _HAS_PACKAGING:
        try:
            spec = SpecifierSet(pep440_constraint)
            candidates = [v for v in available_versions if _safe_contains(spec, v)]
            if not candidates:
                return None
            return _max_version(candidates)
        except Exception as exc:  # SpecifierSet 解析失败
            logger.warning(
                "[SkillDependencyGraph] SpecifierSet 解析失败 %s: %s",
                pep440_constraint, exc,
            )
            return None

    return _fallback_resolve(available_versions, pep440_constraint)


# ========== 依赖图（§9.5） ==========
class SkillDependencyGraph:
    """Skill 依赖图（设计文档 §9.5）。

    维护邻接表（skill_id → 其依赖的 skills）与反向邻接表
    （skill_id → 依赖它的 skills），支持拓扑排序与环检测。

    类比 Java 中的 ``@Component`` 领域服务：
        - 内部用 ``Map<String, List<SkillDependency>>`` 维护双向索引
        - ``topological_sort`` 用 Kahn 算法（BFS 入度消减）
        - ``detect_cycle`` 用 DFS 三色标记法（WHITE/GRAY/BLACK）
    """

    def __init__(self) -> None:
        # 正向邻接表：skill_id → 该 skill 的依赖边列表
        self._adj: dict[str, list[SkillDependency]] = {}
        # 反向邻接表：linked_skill_id → 依赖它的边列表
        self._radj: dict[str, list[SkillDependency]] = {}
        self._nodes: set[str] = set()

    @property
    def nodes(self) -> set[str]:
        """图中全部节点 ID（只读视图）。"""
        return set(self._nodes)

    def add_node(self, skill_id: str) -> None:
        """添加节点（幂等）。"""
        self._nodes.add(skill_id)
        self._adj.setdefault(skill_id, [])
        self._radj.setdefault(skill_id, [])

    def add_edge(self, dependency: SkillDependency) -> None:
        """添加依赖边（skill_id 依赖 linked_skill_id）。

        会自动确保两端节点存在。
        """
        self.add_node(dependency.skill_id)
        self.add_node(dependency.linked_skill_id)
        self._adj[dependency.skill_id].append(dependency)
        self._radj[dependency.linked_skill_id].append(dependency)

    def topological_sort(self) -> list[str]:
        """Kahn 算法拓扑排序（§9.5）。

        边方向：skill_id 依赖 linked_skill_id，因此 linked_skill_id 必须先执行。
        入度 = 该节点的未解析依赖数量；入度为 0（无依赖）的节点先输出。

        Returns:
            拓扑有序的 skill_id 列表（被依赖者在前）；
            存在环时返回已排序部分（环内节点不输出，可用 ``detect_cycle`` 定位）。
        """
        # 入度 = 每个节点的依赖数量
        in_degree: dict[str, int] = {n: len(self._adj.get(n, [])) for n in self._nodes}
        # 入度 0 的节点先入队（sorted 保证确定性）
        queue: deque[str] = deque(sorted(n for n in self._nodes if in_degree[n] == 0))
        order: list[str] = []
        while queue:
            node = queue.popleft()
            order.append(node)
            # 通知所有依赖 node 的 skill：它们的依赖减少一个
            for dep in self._radj.get(node, []):
                dependent = dep.skill_id
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)
        return order

    def detect_cycle(self) -> list[str] | None:
        """DFS 三色标记法环检测（§9.5）。

        - WHITE(0)：未访问
        - GRAY(1)：当前 DFS 栈中（正在访问）
        - BLACK(2)：已完成

        遇到 GRAY 节点即发现环。

        Returns:
            环路径（skill_id 列表，首尾相同）；无环时返回 None。
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {n: WHITE for n in self._nodes}
        cycle_holder: list[str] | None = None

        def dfs(node: str, path: list[str]) -> bool:
            nonlocal cycle_holder
            color[node] = GRAY
            path.append(node)
            for dep in self._adj.get(node, []):
                target = dep.linked_skill_id
                target_color = color.get(target, WHITE)
                if target_color == GRAY:
                    # 回边：从 target 在 path 中的位置到当前构成环
                    start = path.index(target)
                    cycle_holder = path[start:] + [target]
                    return True
                if target_color == WHITE:
                    if dfs(target, path):
                        return True
            path.pop()
            color[node] = BLACK
            return False

        for n in sorted(self._nodes):
            if color[n] == WHITE:
                if dfs(n, []):
                    return cycle_holder
        return None

    def get_dependencies(
        self, skill_id: str, required_only: bool = False
    ) -> list[str]:
        """获取 skill_id 直接依赖的 skill_id 列表。

        Args:
            skill_id: 起始节点
            required_only: True 时仅返回必需依赖（required=True）

        Returns:
            被 skill_id 依赖的 skill_id 列表
        """
        deps = self._adj.get(skill_id, [])
        if required_only:
            return [d.linked_skill_id for d in deps if d.required]
        return [d.linked_skill_id for d in deps]

    def get_dependents(self, skill_id: str) -> list[str]:
        """获取直接依赖 skill_id 的 skill_id 列表（反向查询）。"""
        return [d.skill_id for d in self._radj.get(skill_id, [])]

    def get_edges(self, skill_id: str) -> list[SkillDependency]:
        """获取 skill_id 的全部依赖边（含版本约束等元信息）。"""
        return list(self._adj.get(skill_id, []))
