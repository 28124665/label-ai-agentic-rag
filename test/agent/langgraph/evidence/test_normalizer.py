#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""Evidence Normalizer 单元测试。

覆盖：
- RAG 文档 → Evidence 转换
- DB 结果 → Evidence 转换
- Web 文档 → Evidence 转换
- REST 结果 → Evidence 转换
- 工具名映射（rag_search / db_query / web_search / rest）
- 脱敏（手机号、身份证）
- 截断长内容
- 去重
"""
import pytest

from agent.langgraph.evidence.models import (
    Evidence,
    normalize_db_evidence,
    normalize_rag_evidence,
    normalize_rest_evidence,
    normalize_tool_result,
    normalize_web_evidence,
)


class TestNormalizeRAG:
    """RAG 标准化测试。"""

    def test_basic_rag_doc(self):
        """基本 RAG 文档标准化。"""
        rag_docs = [
            {
                "content": "质量异常处理应当按照 ISO9001 规范执行",
                "score": 0.85,
                "kb_id": "kb_1",
                "doc_id": "doc_1",
                "chunk_id": "chunk_1",
                "page": 12,
            }
        ]
        evidences = normalize_rag_evidence(rag_docs, tenant_id="t1", query="质量异常")
        assert len(evidences) == 1
        ev = evidences[0]
        assert ev["source_type"] == "rag"
        assert ev["source_uri"] == "kb://kb_1/doc_1/chunk_1"
        assert ev["relevance_score"] == 0.85
        assert ev["authority_score"] == 0.80
        assert "ISO9001" in ev["content"]
        assert ev["metadata"]["kb_id"] == "kb_1"

    def test_empty_rag_docs(self):
        """空列表返回空。"""
        assert normalize_rag_evidence([]) == []

    def test_truncation_long_content(self):
        """超长内容被截断。"""
        long_content = "测试内容。" * 1000
        rag_docs = [{"content": long_content, "score": 0.5, "chunk_id": "c1"}]
        evidences = normalize_rag_evidence(rag_docs)
        assert evidences[0]["metadata"]["truncated"] is True
        assert len(evidences[0]["content"]) <= 2010  # 2000 + 省略号

    def test_sensitive_scrub_phone(self):
        """手机号脱敏。"""
        rag_docs = [{"content": "联系 13812345678 处理", "score": 0.5}]
        evidences = normalize_rag_evidence(rag_docs)
        assert "13812345678" not in evidences[0]["content"]
        assert "1XX" in evidences[0]["content"]


class TestNormalizeDB:
    """DB 标准化测试。"""

    def test_basic_db_result(self):
        """基本 DB 结果标准化。"""
        db_result = {
            "sql": "SELECT * FROM users LIMIT 10",
            "rows": [{"id": 1, "name": "张三"}, {"id": 2, "name": "李四"}],
            "row_count": 2,
            "source": "nl_to_sql",
            "tables": ["users"],
            "execution_time_ms": 120,
        }
        evidences = normalize_db_evidence(db_result, tenant_id="t1", query="查询所有用户")
        assert len(evidences) == 1
        ev = evidences[0]
        assert ev["source_type"] == "db"
        assert "SELECT" in ev["metadata"]["sql"]
        assert ev["metadata"]["row_count"] == 2
        assert ev["metadata"]["tables"] == ["users"]
        assert ev["authority_score"] == 0.95

    def test_empty_db_result(self):
        """空 DB 结果返回空列表。"""
        assert normalize_db_evidence({}) == []

    def test_db_with_formatted_result(self):
        """有 formatted_result 时优先使用。"""
        db_result = {
            "sql": "SELECT 1",
            "rows": [{"x": 1}],
            "row_count": 1,
            "formatted_result": "查询结果：1 行",
        }
        evidences = normalize_db_evidence(db_result)
        assert "查询结果：1 行" in evidences[0]["content"]


class TestNormalizeWeb:
    """Web 标准化测试。"""

    def test_basic_web_doc(self):
        """基本 Web 文档标准化。"""
        web_docs = [
            {
                "content": "这是 Web 搜索的摘要",
                "url": "https://example.com/article",
                "title": "示例文章",
                "score": 0.7,
                "published_at": "2026-01-01",
            }
        ]
        evidences = normalize_web_evidence(web_docs, tenant_id="t1", query="示例")
        assert len(evidences) == 1
        ev = evidences[0]
        assert ev["source_type"] == "web"
        assert ev["source_uri"] == "https://example.com/article"
        assert ev["authority_score"] == 0.50  # Web 权威性最低
        assert ev["metadata"]["url"] == "https://example.com/article"

    def test_empty_web_docs(self):
        """空列表返回空。"""
        assert normalize_web_evidence([]) == []


class TestNormalizeRest:
    """REST/ERP 标准化测试。"""

    def test_basic_rest_doc(self):
        """基本 REST 文档标准化。"""
        rest_docs = [
            {
                "content": "张三 2024年年假剩余: 5天",
                "title": "年假余额",
                "quality_score": 0.88,
                "function": "query_annual_leave_balance",
                "endpoint": "/api/hr/leave/balance",
                "method": "GET",
                "erp_domain": "hr",
            }
        ]
        evidences = normalize_rest_evidence(
            rest_docs, tenant_id="t1", query="年假查询", erp_domain="hr"
        )
        assert len(evidences) == 1
        ev = evidences[0]
        assert ev["source_type"] == "rest"
        assert ev["authority_score"] == 0.90
        assert ev["freshness_score"] == 1.0
        assert "年假" in ev["content"]
        assert ev["metadata"]["function"] == "query_annual_leave_balance"
        assert ev["metadata"]["erp_domain"] == "hr"
        assert ev["metadata"]["method"] == "GET"

    def test_rest_source_uri_format(self):
        """测试 source_uri 格式。"""
        rest_docs = [
            {
                "content": "采购订单详情",
                "function": "query_purchase_order",
                "endpoint": "/api/supply_chain/orders",
                "erp_domain": "supply_chain",
            }
        ]
        evidences = normalize_rest_evidence(rest_docs, erp_domain="supply_chain")
        ev = evidences[0]
        assert ev["source_uri"] == "rest://supply_chain/query_purchase_order//api/supply_chain/orders"

    def test_rest_empty_docs(self):
        """空列表返回空。"""
        assert normalize_rest_evidence([]) == []

    def test_rest_dict_content(self):
        """测试 dict 类型 content 自动序列化。"""
        rest_docs = [
            {
                "content": {"balance": 5, "employee": "张三"},
                "function": "query_annual_leave_balance",
                "endpoint": "/api/hr/leave/balance",
            }
        ]
        evidences = normalize_rest_evidence(rest_docs, erp_domain="hr")
        assert len(evidences) == 1
        assert "balance" in evidences[0]["content"]
        assert "张三" in evidences[0]["content"]

    def test_rest_structured_data(self):
        """测试 structured_data 保留。"""
        rest_docs = [
            {
                "content": "测试数据",
                "data": {"field1": "value1", "field2": "value2"},
                "function": "test_func",
                "endpoint": "/api/test",
            }
        ]
        evidences = normalize_rest_evidence(rest_docs)
        ev = evidences[0]
        assert ev["structured_data"] == {"field1": "value1", "field2": "value2"}

    def test_rest_authority_between_db_and_rag(self):
        """测试 rest 权威性介于 db 和 rag 之间。"""
        rest_docs = [
            {
                "content": "测试",
                "function": "test",
                "endpoint": "/api/test",
            }
        ]
        evidences = normalize_rest_evidence(rest_docs)
        auth = evidences[0]["authority_score"]
        assert auth == 0.90
        assert auth > 0.80  # 高于 rag
        assert auth < 0.95  # 低于 db


class TestNormalizeToolResult:
    """统一入口 normalizer 测试。"""

    def test_dispatch_rag(self):
        """rag_search 分发到 RAG normalizer。"""
        result = {"docs": [{"content": "测试", "score": 0.8, "chunk_id": "c1"}]}
        evidences = normalize_tool_result("rag_search", result, tenant_id="t1")
        assert len(evidences) == 1
        assert evidences[0]["source_type"] == "rag"

    def test_dispatch_db(self):
        """db_query 分发到 DB normalizer。"""
        result = {"sql": "SELECT 1", "rows": [], "row_count": 0}
        evidences = normalize_tool_result("db_query", result)
        assert len(evidences) == 1
        assert evidences[0]["source_type"] == "db"

    def test_dispatch_web(self):
        """web_search 分发到 Web normalizer。"""
        result = {"docs": [{"content": "测试", "url": "https://x.com", "score": 0.5}]}
        evidences = normalize_tool_result("web_search", result)
        assert len(evidences) == 1
        assert evidences[0]["source_type"] == "web"

    def test_dispatch_rest(self):
        """rest 分发到 REST normalizer。"""
        result = {
            "docs": [
                {
                    "content": "年假余额: 5天",
                    "function": "query_annual_leave_balance",
                    "endpoint": "/api/hr/leave/balance",
                    "erp_domain": "hr",
                }
            ],
            "erp_domain": "hr",
        }
        evidences = normalize_tool_result("rest", result, tenant_id="t1")
        assert len(evidences) == 1
        assert evidences[0]["source_type"] == "rest"
        assert evidences[0]["authority_score"] == 0.90

    def test_dispatch_rest_search(self):
        """rest_search 分发到 REST normalizer。"""
        result = {
            "docs": [
                {
                    "content": "考勤记录",
                    "function": "query_attendance",
                    "endpoint": "/api/hr/attendance",
                }
            ],
            "erp_domain": "hr",
        }
        evidences = normalize_tool_result("rest_search", result)
        assert len(evidences) == 1
        assert evidences[0]["source_type"] == "rest"

    def test_unknown_tool_returns_empty(self):
        """未知工具返回空列表。"""
        assert normalize_tool_result("unknown_tool", {}) == []


class TestEvidenceFusion:
    """Evidence 融合测试。"""

    def test_fuse_dedupes_by_evidence_id(self):
        """按 evidence_id 去重。"""
        from agent.langgraph.evidence.fusion import fuse_evidences

        ev: Evidence = {
            "evidence_id": "ev_1",
            "source_type": "rag",
            "title": "t",
            "content": "c",
            "source_uri": "u",
            "confidence": 0.8,
            "relevance_score": 0.8,
            "authority_score": 0.8,
            "freshness_score": 0.8,
        }
        result = fuse_evidences([ev, ev, ev])
        assert result["fused_count"] == 1

    def test_fuse_sorts_by_weight(self):
        """按权重降序排序。"""
        from agent.langgraph.evidence.fusion import fuse_evidences

        ev_low: Evidence = {
            "evidence_id": "ev_low",
            "source_type": "web",
            "title": "low",
            "content": "c",
            "source_uri": "u1",
            "relevance_score": 0.3,
            "authority_score": 0.3,
            "freshness_score": 0.3,
        }
        ev_high: Evidence = {
            "evidence_id": "ev_high",
            "source_type": "db",
            "title": "high",
            "content": "c",
            "source_uri": "u2",
            "relevance_score": 0.9,
            "authority_score": 0.95,
            "freshness_score": 0.9,
        }
        result = fuse_evidences([ev_low, ev_high])
        assert result["fused"][0]["evidence_id"] == "ev_high"

    def test_fuse_truncates_to_max_count(self):
        """超过 max_count 被丢弃。"""
        from agent.langgraph.evidence.fusion import fuse_evidences

        evs = [
            {
                "evidence_id": f"ev_{i}",
                "source_type": "rag",
                "title": f"t{i}",
                "content": "c",
                "source_uri": f"u{i}",
                "relevance_score": 0.5,
                "authority_score": 0.5,
                "freshness_score": 0.5,
            }
            for i in range(10)
        ]
        result = fuse_evidences(evs, max_count=3)
        assert result["fused_count"] == 3
        assert result["dropped_count"] == 7

    def test_fuse_detects_db_web_conflict(self):
        """DB + Web 同时存在时检测冲突。"""
        from agent.langgraph.evidence.fusion import fuse_evidences

        ev_db: Evidence = {
            "evidence_id": "ev_db",
            "source_type": "db",
            "title": "db result",
            "content": "DB content for users 100 records",
            "source_uri": "db://users",
            "relevance_score": 0.8,
            "authority_score": 0.95,
            "freshness_score": 1.0,
        }
        ev_web: Evidence = {
            "evidence_id": "ev_web",
            "source_type": "web",
            "title": "web result",
            "content": "完全不同的内容 0 1 2 3 4 5 6 7 8 9",
            "source_uri": "https://other.com",
            "relevance_score": 0.7,
            "authority_score": 0.5,
            "freshness_score": 0.7,
        }
        result = fuse_evidences([ev_db, ev_web])
        assert len(result["conflicts"]) >= 1

    def test_fuse_detects_db_rest_conflict(self):
        """DB + REST 同时存在时检测冲突。"""
        from agent.langgraph.evidence.fusion import fuse_evidences

        ev_db: Evidence = {
            "evidence_id": "ev_db",
            "source_type": "db",
            "title": "db result",
            "content": "DB content for users 100 records",
            "source_uri": "db://users",
            "relevance_score": 0.8,
            "authority_score": 0.95,
            "freshness_score": 1.0,
        }
        ev_rest: Evidence = {
            "evidence_id": "ev_rest",
            "source_type": "rest",
            "title": "erp result",
            "content": "完全不同的ERP数据 0 1 2 3 4 5 6 7 8 9",
            "source_uri": "rest://hr/query",
            "relevance_score": 0.85,
            "authority_score": 0.90,
            "freshness_score": 1.0,
        }
        result = fuse_evidences([ev_db, ev_rest])
        conflict_types = [c["type"] for c in result["conflicts"]]
        assert "db_rest_low_overlap" in conflict_types

    def test_fuse_detects_rest_web_conflict(self):
        """REST + Web 同时存在时检测冲突。"""
        from agent.langgraph.evidence.fusion import fuse_evidences

        ev_rest: Evidence = {
            "evidence_id": "ev_rest",
            "source_type": "rest",
            "title": "erp result",
            "content": "ERP数据 0 1 2 3 4 5 6 7 8 9",
            "source_uri": "rest://hr/query",
            "relevance_score": 0.85,
            "authority_score": 0.90,
            "freshness_score": 1.0,
        }
        ev_web: Evidence = {
            "evidence_id": "ev_web",
            "source_type": "web",
            "title": "web result",
            "content": "完全不同的内容 0 1 2 3 4 5 6 7 8 9",
            "source_uri": "https://other.com",
            "relevance_score": 0.7,
            "authority_score": 0.5,
            "freshness_score": 0.7,
        }
        result = fuse_evidences([ev_rest, ev_web])
        conflict_types = [c["type"] for c in result["conflicts"]]
        assert "rest_web_low_overlap" in conflict_types


class TestAnswerability:
    """Answerability Check 测试。"""

    def test_empty_evidences_not_answerable(self):
        """空证据不可答。"""
        from agent.langgraph.evidence.answerability import check_answerability

        result = check_answerability([])
        assert result["answerable"] is False
        assert result["coverage_score"] == 0.0
        assert result["recommended_action"] in ("ask_clarification", "react_continue")

    def test_high_quality_evidences_answerable(self):
        """高质量证据可答。"""
        from agent.langgraph.evidence.answerability import check_answerability

        evs = [
            {
                "source_type": "db",
                "relevance_score": 0.9,
                "authority_score": 0.95,
                "freshness_score": 1.0,
            },
            {
                "source_type": "rag",
                "relevance_score": 0.8,
                "authority_score": 0.8,
                "freshness_score": 1.0,
            },
        ]
        result = check_answerability(evs)
        assert result["answerable"] is True
        assert result["recommended_action"] in ("generate", "partial_answer")

    def test_low_relevance_partial_answer(self):
        """低相关性走 partial_answer。"""
        from agent.langgraph.evidence.answerability import check_answerability

        evs = [
            {
                "source_type": "rag",
                "relevance_score": 0.4,
                "authority_score": 0.5,
                "freshness_score": 0.5,
            }
        ]
        result = check_answerability(evs)
        assert result["answerable"] is True
        assert result["recommended_action"] == "partial_answer"
