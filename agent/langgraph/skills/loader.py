"""Skill Catalog 加载器（P1/P2，§7.1 / §7.4 / §12.4）。

设计文档 §7.1 约束：
    2. reload 在后台构建新 Snapshot，完成全量校验和索引后原子替换指针。
    3. 构建失败继续使用上一 Snapshot，禁止部分更新。

P2 阶段扩展（§7.4 / §8.4）：
    - 构建 SkillCard（调用 CatalogBuilder，§8.4）
    - 构建 BM25 索引（调用 BM25Retriever，§7.4.1）
    - Embedding 索引异步构建（不阻塞 Snapshot 可用性，§7.4.2）

本模块职责：
    1. 从 YAML 目录加载 ReportSkill / DataSkill / RetrievalSkill
    2. 调用 validator.py 进行全量校验（O(N)）
    3. 调用 indexes.py 构建精确索引
    4. P2：调用 CatalogBuilder 构建 SkillCard
    5. P2：调用 BM25Retriever 构建 BM25 索引
    6. 生成 revision + checksum
    7. 返回不可变 SkillCatalogSnapshot

构建失败时抛出异常，由调用方（SkillRegistry.reload）决定是否保留旧 Snapshot。

类比 Java：
    ``CatalogLoader`` ≈ ``@Service`` 的工厂类，封装对象构建的复杂流程。
    ``load_catalog`` ≈ 工厂方法，返回不可变值对象。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml

from agent.langgraph.skills.catalog import (
    SkillCatalogSnapshot,
    compute_checksum,
    generate_revision,
)
from agent.langgraph.skills.indexes import build_indexes
from agent.langgraph.skills.models import DataSkill, ReportSkill, RetrievalSkill, SkillBase
from agent.langgraph.skills.validator import SkillValidationError, validate_skills

logger = logging.getLogger(__name__)


def load_catalog(root: Path | None = None) -> SkillCatalogSnapshot:
    """从 YAML 目录加载并构建不可变 Catalog Snapshot。

    流程：
        1. 加载 report/data/retrieval 三类 Skill YAML
        2. 全量校验（O(N) 重复检测 + 交叉引用校验）
        3. 构建精确索引（O(N) 一次性建立所有索引）
        4. P2：构建 SkillCard（CatalogBuilder 推导，§8.4）
        5. P2：构建 BM25 索引（§7.4.1）
        6. 生成 revision + checksum
        7. 返回 SkillCatalogSnapshot

    失败处理（§7.1 约束3）：
        - YAML 解析失败、Schema 校验失败、交叉引用校验失败 → 抛出异常
        - P2 SkillCard/BM25 构建失败 → 记录告警但不阻塞（降级为 P1 行为）
        - 调用方（SkillRegistry.reload）捕获异常后保留旧 Snapshot

    Args:
        root: Skill 配置根目录（默认 ``config/report_skills``）

    Returns:
        SkillCatalogSnapshot: 不可变快照

    Raises:
        SkillValidationError: 配置校验失败
        ValueError: YAML 格式错误
    """
    config_root = root or _default_config_root()
    skills = _load_all_skills(config_root)
    validate_skills(skills)
    return _build_snapshot(skills)


def _default_config_root() -> Path:
    """默认配置根目录：``config/report_skills``。"""
    return Path(__file__).resolve().parents[3] / "config" / "report_skills"


def _load_all_skills(root: Path) -> list[SkillBase]:
    """加载 report/data/retrieval 三类 Skill YAML。

    保持与原 SkillRegistry._load_directory 相同的加载顺序：
        1. root/*.yaml → ReportSkill
        2. root/data/*.yaml → DataSkill
        3. root/retrieval/*.yaml → RetrievalSkill

    顺序影响 keyword 路径同分时的候选排序（稳定排序保持加载顺序）。
    """
    skills: list[SkillBase] = []
    skills.extend(_load_directory(root, "report", ReportSkill))
    skills.extend(_load_directory(root / "data", "data", DataSkill))
    skills.extend(_load_directory(root / "retrieval", "retrieval", RetrievalSkill))
    return skills


def _load_directory(
    directory: Path, skill_type: str, model_type: type[SkillBase]
) -> list[SkillBase]:
    """加载目录下所有 YAML 文件为指定类型的 Skill 模型。

    保持与原 SkillRegistry._load_directory 相同的行为：
        - 目录不存在时返回空列表
        - 文件按文件名排序加载（确保跨平台一致性）
        - 自动注入 skill_type 字段
    """
    if not directory.exists():
        return []
    skills: list[SkillBase] = []
    for path in sorted(directory.glob("*.yaml")):
        with path.open(encoding="utf-8") as config_file:
            payload = yaml.safe_load(config_file) or {}
        if not isinstance(payload, dict):
            raise ValueError(f"Skill configuration '{path}' must be a YAML mapping")
        payload.setdefault("skill_type", skill_type)
        skills.append(model_type.model_validate(payload))
    return skills


def _build_snapshot(skills: list[SkillBase]) -> SkillCatalogSnapshot:
    """从已校验的 Skill 列表构建不可变 Snapshot。

    步骤：
        1. 构建精确索引（O(N)）
        2. P2：构建 SkillCard（CatalogBuilder 推导，§8.4）
        3. P2：构建 BM25 索引（§7.4.1）
        4. 生成 revision（时间戳）
        5. 计算 checksum（SHA256 of skill_id:version）
        6. 包装为不可变 Mapping（MappingProxyType）

    P2 构建失败处理：
        - SkillCard 构建失败（如未知 report_type）→ 记录告警，cards_by_key=None（降级为 P1）
        - BM25 索引构建失败 → 记录告警，bm25_index=None（降级为 keyword 匹配）
        - 精确索引/校验失败 → 抛出异常（不降级）
    """
    indexes = build_indexes(skills)
    revision = generate_revision()
    checksum = compute_checksum(skills)
    created_at = datetime.now(timezone.utc)

    # ===== P2: 构建 SkillCard（§8.4）=====
    cards_by_key = _build_skill_cards(skills, indexes)

    # ===== P2: 构建 BM25 索引（§7.4.1）=====
    bm25_index = _build_bm25_index(cards_by_key)

    logger.info(
        "[CatalogLoader] Snapshot 构建: revision=%s, checksum=%s, skills=%d, "
        "cards=%d, bm25=%s",
        revision,
        checksum,
        len(skills),
        len(cards_by_key) if cards_by_key else 0,
        "available" if bm25_index is not None else "unavailable",
    )

    return SkillCatalogSnapshot(
        revision=revision,
        created_at=created_at,
        checksum=checksum,
        skills_by_id=indexes.skills_by_id,
        report_type_index=indexes.report_type_index,
        data_skill_index=indexes.data_skill_index,
        retrieval_skill_index=indexes.retrieval_skill_index,
        skill_count=len(skills),
        # P2 新增字段
        cards_by_key=cards_by_key,
        bm25_index=bm25_index,
        embedding_index=None,  # 异步构建，初始为 None
    )


def _build_skill_cards(skills: list[SkillBase], indexes) -> dict:
    """P2：构建 SkillCard 字典（§8.4 CatalogBuilder 推导）。

    失败时返回空字典（降级为 P1 行为，不阻塞 Snapshot 构建）。
    返回空字典而非 None 确保上游始终获得可遍历的 dict。
    """
    try:
        from agent.langgraph.skills.card import build_cards_from_skills

        # 将 indexes 的 dict 转换为普通 dict（NamedTuple 字段）
        data_by_report = dict(indexes.data_skill_index) if indexes.data_skill_index else {}
        retrieval_by_report = dict(indexes.retrieval_skill_index) if indexes.retrieval_skill_index else {}

        return build_cards_from_skills(
            skills,
            data_skills_by_report=data_by_report,
            retrieval_skills_by_report=retrieval_by_report,
        )
    except Exception as e:
        logger.warning(
            "[CatalogLoader] SkillCard 构建失败，降级为 P1 模式（无语义索引）: %s",
            e,
            exc_info=True,
        )
        return {}


def _build_bm25_index(cards_by_key: dict | None):
    """P2：构建 BM25 索引（§7.4.1）。

    cards_by_key 为空时返回空 BM25Index（search 返回空列表，触发 keyword 降级）。
    失败时返回 None（降级为 keyword 匹配）。
    """
    if not cards_by_key:
        # 空语料：返回空 BM25Index（search 返回空列表，非 None 避免上游判断 None）
        from agent.langgraph.skills.retrievers import BM25Retriever
        try:
            retriever = BM25Retriever()
            return retriever.build([])
        except Exception:
            return None

    try:
        from agent.langgraph.skills.retrievers import BM25Retriever

        cards = list(cards_by_key.values())
        retriever = BM25Retriever()
        return retriever.build(cards)
    except Exception as e:
        logger.warning(
            "[CatalogLoader] BM25 索引构建失败，降级为 keyword 匹配: %s",
            e,
            exc_info=True,
        )
        return None
