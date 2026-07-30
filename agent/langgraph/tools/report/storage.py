"""Artifact Storage（报告产物存储）。

按 docs/报告生成Tool设计.md 第 6.11 节设计：
- 存储路径必须包含 tenant_id 和 report_id
- 阻止路径穿越
- 阶段一仅实现本地文件存储（生产期可扩展 MinIO/OSS）
- 返回 file_uri 和 download_url
"""
from __future__ import annotations

import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# 路径安全白名单字符
SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _is_safe_id(value: str) -> bool:
    """校验 ID 安全性（防止路径穿越）。"""
    if not value:
        return False
    # 禁止 . / \ .. 等
    if ".." in value or "/" in value or "\\" in value:
        return False
    return bool(SAFE_ID_PATTERN.match(value))


class LocalArtifactStorage:
    """本地文件存储后端。

    路径格式：{root_path}/{tenant_id}/{report_id}.{ext}
    特点：
    - 强制 tenant_id 和 report_id 路径校验
    - 阻止 ../ 路径穿越
    - 返回 file:// URI 和 HTTP 下载 URL
    """

    def __init__(
        self,
        root_path: str = "./reports",
        download_base_url: str = "/api/v1/reports",
    ):
        """初始化本地存储。

        Args:
            root_path: 根目录
            download_base_url: 下载 URL 前缀
        """
        self._root_path = Path(root_path).resolve()
        self._root_path.mkdir(parents=True, exist_ok=True)
        self._download_base_url = download_base_url.rstrip("/")
        logger.info(
            f"[LocalArtifactStorage] 初始化: root={self._root_path}, "
            f"download_base={self._download_base_url}"
        )

    def save(
        self,
        tenant_id: str,
        report_id: str,
        content: str,
        extension: str = "md",
    ) -> dict[str, Any]:
        """保存报告内容到本地。

        Args:
            tenant_id: 租户 ID（必须通过 _is_safe_id 校验）
            report_id: 报告 ID（必须通过 _is_safe_id 校验）
            content: 报告内容
            extension: 文件扩展名（md / html）

        Returns:
            dict: {
                artifact_id, file_uri, download_url, file_size_bytes
            }

        Raises:
            ValueError: 参数不安全
        """
        if not _is_safe_id(tenant_id):
            raise ValueError(f"非法的 tenant_id: {tenant_id!r}")
        if not _is_safe_id(report_id):
            raise ValueError(f"非法的 report_id: {report_id!r}")

        ext = extension.lstrip(".").lower()
        if ext not in ("md", "html", "txt"):
            raise ValueError(f"不支持的文件扩展名: {ext}")

        tenant_dir = self._root_path / tenant_id
        tenant_dir.mkdir(parents=True, exist_ok=True)

        file_path = tenant_dir / f"{report_id}.{ext}"
        file_path.write_text(content, encoding="utf-8")

        # 计算 file_uri
        file_uri = f"file://{file_path}"
        file_size = file_path.stat().st_size

        # 计算 download_url（含过期时间戳）
        expires_at = int(time.time()) + 600  # 10 分钟
        download_url = (
            f"{self._download_base_url}/{tenant_id}/{report_id}/download"
            f"?expires={expires_at}"
        )

        result = {
            "artifact_id": report_id,
            "file_uri": file_uri,
            "download_url": download_url,
            "storage_backend": "local",
            "file_size_bytes": file_size,
        }

        logger.info(
            f"[LocalArtifactStorage] 保存成功: tenant={tenant_id}, "
            f"report={report_id}, size={file_size}B, path={file_path}"
        )
        return result

    def read(self, tenant_id: str, report_id: str, extension: str = "md") -> str:
        """读取报告内容。

        Args:
            tenant_id: 租户 ID
            report_id: 报告 ID
            extension: 文件扩展名

        Returns:
            str: 文件内容

        Raises:
            ValueError: 参数不安全
            FileNotFoundError: 文件不存在
        """
        if not _is_safe_id(tenant_id):
            raise ValueError(f"非法的 tenant_id: {tenant_id!r}")
        if not _is_safe_id(report_id):
            raise ValueError(f"非法的 report_id: {report_id!r}")

        file_path = self._root_path / tenant_id / f"{report_id}.{extension.lstrip('.')}"
        if not file_path.exists():
            raise FileNotFoundError(f"报告文件不存在: {file_path}")

        return file_path.read_text(encoding="utf-8")

    def exists(self, tenant_id: str, report_id: str, extension: str = "md") -> bool:
        """检查报告文件是否存在。"""
        if not _is_safe_id(tenant_id) or not _is_safe_id(report_id):
            return False
        file_path = self._root_path / tenant_id / f"{report_id}.{extension.lstrip('.')}"
        return file_path.exists()


def generate_report_id() -> str:
    """生成报告 ID。"""
    return f"rpt_{uuid.uuid4().hex[:16]}"


# 全局默认存储实例
_default_storage: LocalArtifactStorage | None = None


def get_default_storage() -> LocalArtifactStorage:
    """获取默认存储实例（单例）。"""
    global _default_storage
    if _default_storage is None:
        _default_storage = LocalArtifactStorage()
    return _default_storage
