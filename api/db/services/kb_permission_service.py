"""知识库用户权限服务。

提供细粒度的用户-知识库访问控制，补充 RAGFlow 原生 me/team 二元权限模型。
"""

import logging
from typing import Optional

from api.db.db_models import KBUserPermission, Knowledgebase
from common.misc_utils import get_uuid

logger = logging.getLogger(__name__)


class KBPermissionService:
    """知识库用户权限服务。

    提供白名单模式的用户-KB 权限管理：
    - 查询用户有权限的知识库列表
    - 过滤知识库 ID 列表
    - 授予/撤销权限
    - 按知识库查询授权用户
    """

    @staticmethod
    def get_permitted_kb_ids(user_id: str) -> list[str]:
        """获取用户有显式权限的所有知识库 ID。

        只返回 kb_user_permission 表中授予的权限，
        不包含 KB 创建者（创建者通过原生 me/team 模型处理）。

        Args:
            user_id: 用户 ID

        Returns:
            list[str]: 知识库 ID 列表
        """
        try:
            rows = (
                KBUserPermission.select(KBUserPermission.kb_id)
                .where(KBUserPermission.user_id == user_id)
                .dicts()
            )
            return [row["kb_id"] for row in rows]
        except Exception as e:
            logger.error(f"[KBPermissionService] 查询用户权限失败: user_id={user_id}, error={e}")
            return []

    @staticmethod
    def filter_permitted_kb_ids(kb_ids: list[str], user_id: str) -> list[str]:
        """过滤知识库 ID 列表，只保留用户有权限的。

        当前阶段：如果 kb_ids 为空则返回全量有权限的 kb_ids，
        否则只过滤传入列表中有权限的。

        Args:
            kb_ids: 待过滤的知识库 ID 列表
            user_id: 用户 ID

        Returns:
            list[str]: 过滤后的知识库 ID 列表
        """
        if not kb_ids:
            return KBPermissionService.get_permitted_kb_ids(user_id)

        permitted = set(KBPermissionService.get_permitted_kb_ids(user_id))
        return [kb_id for kb_id in kb_ids if kb_id in permitted]

    @staticmethod
    def grant(kb_id: str, user_id: str, granted_by: str) -> bool:
        """授予用户对知识库的访问权限。

        Args:
            kb_id: 知识库 ID
            user_id: 被授权用户 ID
            granted_by: 授权人 ID

        Returns:
            bool: 是否成功
        """
        try:
            KBUserPermission.create(
                id=get_uuid(),
                kb_id=kb_id,
                user_id=user_id,
                granted_by=granted_by,
            )
            logger.info(
                f"[KBPermissionService] 权限授予成功: kb_id={kb_id}, "
                f"user_id={user_id}, granted_by={granted_by}"
            )
            return True
        except Exception as e:
            logger.error(f"[KBPermissionService] 权限授予失败: {e}")
            return False

    @staticmethod
    def revoke(kb_id: str, user_id: str) -> bool:
        """撤销用户对知识库的访问权限。

        Args:
            kb_id: 知识库 ID
            user_id: 用户 ID

        Returns:
            bool: 是否成功
        """
        try:
            deleted = (
                KBUserPermission.delete()
                .where(
                    (KBUserPermission.kb_id == kb_id)
                    & (KBUserPermission.user_id == user_id)
                )
                .execute()
            )
            logger.info(
                f"[KBPermissionService] 权限撤销: kb_id={kb_id}, "
                f"user_id={user_id}, deleted={deleted}"
            )
            return deleted > 0
        except Exception as e:
            logger.error(f"[KBPermissionService] 权限撤销失败: {e}")
            return False

    @staticmethod
    def get_users_by_kb(kb_id: str) -> list[dict]:
        """获取指定知识库的所有授权用户。

        Args:
            kb_id: 知识库 ID

        Returns:
            list[dict]: 授权用户列表，包含 user_id、granted_by、create_time
        """
        try:
            rows = (
                KBUserPermission.select()
                .where(KBUserPermission.kb_id == kb_id)
                .dicts()
            )
            return list(rows)
        except Exception as e:
            logger.error(f"[KBPermissionService] 查询知识库授权用户失败: kb_id={kb_id}, error={e}")
            return []