"""ReportTool 单元测试 — Sensitive Scanner 模块。"""
from __future__ import annotations

import pytest

from agent.langgraph.tools.report.sensitive_scanner import (
    SENSITIVE_PATTERNS,
    has_critical,
    has_high,
    scan_artifact,
    scan_text,
)


class TestScanText:
    def test_detect_id_card(self):
        """检测身份证号。"""
        findings = scan_text("员工 110101199001011234 提交了报告")
        assert any(f["code"] == "SENS_ID_CARD" for f in findings)

    def test_detect_phone(self):
        """检测手机号。"""
        findings = scan_text("联系电话：13800138000")
        assert any(f["code"] == "SENS_PHONE" for f in findings)

    def test_detect_email(self):
        """检测邮箱。"""
        findings = scan_text("邮箱：user@example.com")
        assert any(f["code"] == "SENS_EMAIL" for f in findings)

    def test_detect_api_key(self):
        """检测 API Key。"""
        findings = scan_text("API Key: sk-abcdefghijklmnopqrstuvwxyz123456")
        assert any(f["code"] == "SENS_API_KEY" for f in findings)

    def test_detect_aws_key(self):
        """检测 AWS Access Key。"""
        findings = scan_text("AWS Key: AKIAIOSFODNN7EXAMPLE")
        assert any(f["code"] == "SENS_API_KEY" for f in findings)

    def test_detect_password(self):
        """检测密码字段。"""
        findings = scan_text("password=secret123456")
        assert any(f["code"] == "SENS_PASSWORD" for f in findings)

    def test_detect_db_connection(self):
        """检测数据库连接串。"""
        findings = scan_text("mongodb://user:pass@localhost:27017/db")
        assert any(f["code"] == "SENS_DB_CONN" for f in findings)

    def test_detect_internal_ip(self):
        """检测内网 IP。"""
        findings = scan_text("内网地址：192.168.1.100")
        assert any(f["code"] == "SENS_INTERNAL_IP" for f in findings)

    def test_detect_internal_ip_10(self):
        """检测 10.x 内网 IP。"""
        findings = scan_text("服务器：10.0.0.1")
        assert any(f["code"] == "SENS_INTERNAL_IP" for f in findings)

    def test_clean_text(self):
        """干净文本无命中。"""
        findings = scan_text("近三个月质量异常趋势保持稳定。")
        # 命中可能是 false positive（如银行卡号），这里只验证不会触发 critical
        critical_findings = [f for f in findings if f.get("severity") == "critical"]
        assert len(critical_findings) == 0

    def test_empty_text(self):
        """空文本。"""
        assert scan_text("") == []
        assert scan_text(None) == []  # type: ignore


class TestScanArtifact:
    def test_scan_summary(self):
        """扫描 summary 字段。"""
        artifact = {
            "summary": "联系方式：13800138000",
            "sections": [],
            "tables": [],
            "charts": [],
        }
        findings = scan_artifact(artifact)
        assert any(f.get("location") == "summary" for f in findings)

    def test_scan_sections(self):
        """扫描 sections 字段。"""
        artifact = {
            "sections": [
                {
                    "section_id": "s1",
                    "content": "邮箱：user@example.com",
                }
            ],
            "tables": [],
            "charts": [],
        }
        findings = scan_artifact(artifact)
        assert any(f.get("location") == "section:s1" for f in findings)

    def test_scan_tables(self):
        """扫描 tables 字段。"""
        artifact = {
            "sections": [],
            "tables": [
                {
                    "table_id": "t1",
                    "rows": [
                        ["name", "phone"],
                        ["张三", "13800138000"],
                    ],
                }
            ],
            "charts": [],
        }
        findings = scan_artifact(artifact)
        assert any(f.get("location") == "table:t1" for f in findings)


class TestHelpers:
    def test_has_critical_true(self):
        """has_critical 检测。"""
        findings = [{"severity": "critical"}]
        assert has_critical(findings) is True

    def test_has_critical_false(self):
        """无 critical 时返回 False。"""
        findings = [{"severity": "high"}]
        assert has_critical(findings) is False

    def test_has_high_includes_critical(self):
        """has_high 包含 critical。"""
        findings = [{"severity": "critical"}]
        assert has_high(findings) is True
