"""ReportTool 单元测试 — Storage 模块。"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from agent.langgraph.tools.report.storage import (
    LocalArtifactStorage,
    _is_safe_id,
    generate_report_id,
    get_default_storage,
)


@pytest.fixture
def temp_storage():
    """临时存储 fixture。"""
    tmpdir = tempfile.mkdtemp()
    storage = LocalArtifactStorage(root_path=tmpdir)
    yield storage
    shutil.rmtree(tmpdir, ignore_errors=True)


class TestSafeId:
    def test_safe_id_valid(self):
        """合法 ID。"""
        assert _is_safe_id("tenant_001") is True
        assert _is_safe_id("rpt_abc123") is True
        assert _is_safe_id("a") is True

    def test_safe_id_rejects_path_traversal(self):
        """拒绝路径穿越。"""
        assert _is_safe_id("../etc/passwd") is False
        assert _is_safe_id("..") is False
        assert _is_safe_id("foo/../bar") is False
        assert _is_safe_id("foo\\bar") is False
        assert _is_safe_id("a/b") is False

    def test_safe_id_rejects_empty(self):
        """拒绝空 ID。"""
        assert _is_safe_id("") is False
        assert _is_safe_id(None) is False  # type: ignore


class TestLocalArtifactStorage:
    def test_save_and_read(self, temp_storage):
        """保存并读取。"""
        result = temp_storage.save(
            tenant_id="tenant_001",
            report_id="rpt_001",
            content="# 测试报告",
        )
        assert result["artifact_id"] == "rpt_001"
        assert result["file_size_bytes"] > 0
        assert result["storage_backend"] == "local"
        assert "rpt_001" in result["file_uri"]
        assert "download_url" in result

        # 读取
        content = temp_storage.read("tenant_001", "rpt_001")
        assert content == "# 测试报告"

    def test_save_rejects_unsafe_tenant_id(self, temp_storage):
        """拒绝非法的 tenant_id。"""
        with pytest.raises(ValueError):
            temp_storage.save(
                tenant_id="../etc",
                report_id="rpt_001",
                content="test",
            )

    def test_save_rejects_unsafe_report_id(self, temp_storage):
        """拒绝非法的 report_id。"""
        with pytest.raises(ValueError):
            temp_storage.save(
                tenant_id="tenant_001",
                report_id="../../malicious",
                content="test",
            )

    def test_read_nonexistent(self, temp_storage):
        """读取不存在的文件。"""
        with pytest.raises(FileNotFoundError):
            temp_storage.read("tenant_001", "rpt_notexist")

    def test_exists(self, temp_storage):
        """检查文件存在。"""
        assert temp_storage.exists("tenant_001", "rpt_001") is False
        temp_storage.save("tenant_001", "rpt_001", "content")
        assert temp_storage.exists("tenant_001", "rpt_001") is True

    def test_download_url_has_expiry(self, temp_storage):
        """下载链接包含过期时间戳。"""
        result = temp_storage.save("tenant_001", "rpt_001", "content")
        assert "expires=" in result["download_url"]

    def test_tenant_isolation(self, temp_storage):
        """租户隔离。"""
        temp_storage.save("tenant_001", "rpt_001", "content_A")
        temp_storage.save("tenant_002", "rpt_001", "content_B")
        assert temp_storage.read("tenant_001", "rpt_001") == "content_A"
        assert temp_storage.read("tenant_002", "rpt_001") == "content_B"


class TestGenerateReportId:
    def test_generated_id_format(self):
        """生成的 ID 格式正确。"""
        rid = generate_report_id()
        assert rid.startswith("rpt_")
        assert _is_safe_id(rid)

    def test_generated_id_unique(self):
        """生成的 ID 唯一。"""
        ids = {generate_report_id() for _ in range(100)}
        assert len(ids) == 100


class TestGetDefaultStorage:
    def test_default_storage_singleton(self):
        """默认存储单例。"""
        s1 = get_default_storage()
        s2 = get_default_storage()
        assert s1 is s2
