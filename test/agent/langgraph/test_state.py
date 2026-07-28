#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""Unit tests for LangGraph AgentState definition."""

from __future__ import annotations

from agent.langgraph.state import AgentState


class TestAgentState:
    """Test AgentState TypedDict definition."""

    def test_state_creation_with_all_fields(self):
        """Test creating AgentState with all fields populated."""
        state: AgentState = {
            # 用户输入
            "user_question": "什么是 RAG？",
            "query_lang": "zh_CN",
            # 路由决策
            "route_target": "rag",
            # RAG Tool 输出
            "rag_docs": [
                {
                    "content": "RAG 是检索增强生成技术",
                    "score": 0.95,
                    "source": "doc1.pdf",
                    "chunk_id": "chunk_001",
                }
            ],
            "rag_quality_score": 0.92,
            "rag_has_relevant": True,
            "rag_relevant_count": 5,
            "rag_top_score": 0.95,
            # Database Tool 输出
            "db_result": {
                "sql": "SELECT * FROM users LIMIT 10",
                "rows": [{"id": 1, "name": "Alice"}],
                "row_count": 1,
                "source": "mysql",
            },
            "db_quality_score": 0.88,
            # Web Tool 输出
            "web_docs": [
                {
                    "content": "Web search result content",
                    "url": "https://example.com",
                    "title": "Example Title",
                }
            ],
            # 融合上下文
            "merged_context": "RAG 是检索增强生成技术...",
            # LLM 生成
            "generated_answer": "RAG（Retrieval-Augmented Generation）是一种...",
            # 幻觉检测
            "hallucination_score": 0.15,
            "hallucination_action": "pass",
            # 重试控制
            "retry_count": 0,
            "max_retries": 3,
            # 可观测性
            "trace_id": "trace_abc123",
            "node_timings": {
                "user_question": 10.5,
                "intent_router": 25.3,
                "rag_tool": 1250.8,
            },
        }

        # 验证所有字段都能正确访问
        assert state["user_question"] == "什么是 RAG？"
        assert state["query_lang"] == "zh_CN"
        assert state["route_target"] == "rag"
        assert len(state["rag_docs"]) == 1
        assert state["rag_quality_score"] == 0.92
        assert state["rag_has_relevant"] is True
        assert state["rag_relevant_count"] == 5
        assert state["rag_top_score"] == 0.95
        assert state["db_result"]["row_count"] == 1
        assert state["db_quality_score"] == 0.88
        assert len(state["web_docs"]) == 1
        assert state["merged_context"] == "RAG 是检索增强生成技术..."
        assert state["generated_answer"].startswith("RAG")
        assert state["hallucination_score"] == 0.15
        assert state["hallucination_action"] == "pass"
        assert state["retry_count"] == 0
        assert state["max_retries"] == 3
        assert state["trace_id"] == "trace_abc123"
        assert "rag_tool" in state["node_timings"]

    def test_state_partial_initialization(self):
        """Test creating AgentState with only required fields (total=False)."""
        state: AgentState = {
            "user_question": "测试问题",
            "query_lang": "en",
        }

        assert state["user_question"] == "测试问题"
        assert state["query_lang"] == "en"
        # 其他字段可以不存在
        assert "route_target" not in state
        assert "rag_docs" not in state

    def test_state_query_lang_variants(self):
        """Test different query_lang values."""
        for lang in ["zh_CN", "zh_TW", "en"]:
            state: AgentState = {
                "user_question": "test",
                "query_lang": lang,
            }
            assert state["query_lang"] == lang

    def test_state_route_target_variants(self):
        """Test different route_target values."""
        for target in ["rag", "database", "hybrid", "web", "chitchat"]:
            state: AgentState = {
                "route_target": target,
            }
            assert state["route_target"] == target

    def test_state_hallucination_action_variants(self):
        """Test different hallucination_action values."""
        for action in ["pass", "filter", "regenerate", "reject"]:
            state: AgentState = {
                "hallucination_action": action,
            }
            assert state["hallucination_action"] == action

    def test_state_rag_docs_structure(self):
        """Test rag_docs field structure."""
        state: AgentState = {
            "rag_docs": [
                {
                    "content": "文档内容 1",
                    "score": 0.9,
                    "source": "test.pdf",
                    "chunk_id": "chunk_001",
                },
                {
                    "content": "文档内容 2",
                    "score": 0.85,
                    "source": "test2.pdf",
                    "chunk_id": "chunk_002",
                },
            ]
        }

        assert len(state["rag_docs"]) == 2
        assert state["rag_docs"][0]["content"] == "文档内容 1"
        assert state["rag_docs"][1]["score"] == 0.85

    def test_state_db_result_structure(self):
        """Test db_result field structure."""
        state: AgentState = {
            "db_result": {
                "sql": "SELECT COUNT(*) FROM users",
                "rows": [{"count": 100}],
                "row_count": 1,
                "source": "postgresql",
            }
        }

        assert state["db_result"]["sql"] == "SELECT COUNT(*) FROM users"
        assert state["db_result"]["row_count"] == 1
        assert state["db_result"]["source"] == "postgresql"

    def test_state_web_docs_structure(self):
        """Test web_docs field structure."""
        state: AgentState = {
            "web_docs": [
                {
                    "content": "搜索结果内容",
                    "url": "https://example.com/article",
                    "title": "文章标题",
                }
            ]
        }

        assert len(state["web_docs"]) == 1
        assert state["web_docs"][0]["url"] == "https://example.com/article"

    def test_state_node_timings_structure(self):
        """Test node_timings field structure."""
        state: AgentState = {
            "node_timings": {
                "user_question": 5.2,
                "intent_router": 15.8,
                "rag_tool": 850.3,
                "quality_check": 10.1,
                "llm_generate": 1200.5,
            }
        }

        assert len(state["node_timings"]) == 5
        assert state["node_timings"]["rag_tool"] == 850.3

    def test_state_empty_collections(self):
        """Test state with empty collections."""
        state: AgentState = {
            "rag_docs": [],
            "web_docs": [],
            "node_timings": {},
        }

        assert state["rag_docs"] == []
        assert state["web_docs"] == []
        assert state["node_timings"] == {}

    def test_state_numeric_boundaries(self):
        """Test numeric fields at boundary values."""
        state: AgentState = {
            "rag_quality_score": 0.0,
            "db_quality_score": 1.0,
            "hallucination_score": 0.5,
            "retry_count": 0,
            "max_retries": 10,
        }

        assert state["rag_quality_score"] == 0.0
        assert state["db_quality_score"] == 1.0
        assert state["hallucination_score"] == 0.5
        assert state["retry_count"] == 0
        assert state["max_retries"] == 10
