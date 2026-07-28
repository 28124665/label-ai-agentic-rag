#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
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
"""RAGTool 单元测试。"""

import pytest
from agent.langgraph.tools.rag_tool import RAGTool, RAGToolInput


class TestRAGTool:
    """RAGTool 测试类。"""

    def test_init(self):
        """测试 RAGTool 初始化。"""
        tool = RAGTool()
        assert tool.component_name == "RAGTool"

    def test_evaluate_quality_with_high_scores(self):
        """测试质量评估 - 高分场景。"""
        tool = RAGTool()
        chunks = [
            {"content": "test1", "similarity": 0.9},
            {"content": "test2", "similarity": 0.85},
            {"content": "test3", "similarity": 0.8},
        ]
        quality_score, has_relevant, relevant_count, top_score = tool._evaluate_quality(chunks)
        
        assert quality_score == pytest.approx(0.85, rel=1e-2)
        assert has_relevant is True
        assert relevant_count == 3
        assert top_score == 0.9

    def test_evaluate_quality_with_low_scores(self):
        """测试质量评估 - 低分场景。"""
        tool = RAGTool()
        chunks = [
            {"content": "test1", "similarity": 0.3},
            {"content": "test2", "similarity": 0.2},
        ]
        quality_score, has_relevant, relevant_count, top_score = tool._evaluate_quality(chunks)
        
        assert quality_score == pytest.approx(0.25, rel=1e-2)
        assert has_relevant is False
        assert relevant_count == 0
        assert top_score == 0.3

    def test_evaluate_quality_with_mixed_scores(self):
        """测试质量评估 - 混合分数场景。"""
        tool = RAGTool()
        chunks = [
            {"content": "test1", "similarity": 0.9},
            {"content": "test2", "similarity": 0.4},
            {"content": "test3", "similarity": 0.3},
        ]
        quality_score, has_relevant, relevant_count, top_score = tool._evaluate_quality(chunks)
        
        assert quality_score == pytest.approx(0.533, rel=1e-2)
        assert has_relevant is True
        assert relevant_count == 1
        assert top_score == 0.9

    def test_evaluate_quality_empty_chunks(self):
        """测试质量评估 - 空 chunks 场景。"""
        tool = RAGTool()
        chunks = []
        quality_score, has_relevant, relevant_count, top_score = tool._evaluate_quality(chunks)
        
        assert quality_score == 0.0
        assert has_relevant is False
        assert relevant_count == 0
        assert top_score == 0.0

    def test_format_docs(self):
        """测试文档格式化。"""
        tool = RAGTool()
        chunks = [
            {
                "content_with_weight": "test content 1",
                "similarity": 0.9,
                "docnm_kwd": "doc1.pdf",
                "chunk_id": "chunk1",
                "doc_id": "doc1",
            },
            {
                "content": "test content 2",  # 测试 fallback 到 content
                "score": 0.8,  # 测试 fallback 到 score
                "docnm_kwd": "doc2.pdf",
                "chunk_id": "chunk2",
                "doc_id": "doc2",
            },
        ]
        docs = tool._format_docs(chunks)
        
        assert len(docs) == 2
        assert docs[0]["content"] == "test content 1"
        assert docs[0]["score"] == 0.9
        assert docs[0]["source"] == "doc1.pdf"
        assert docs[0]["chunk_id"] == "chunk1"
        assert docs[0]["doc_id"] == "doc1"
        
        assert docs[1]["content"] == "test content 2"
        assert docs[1]["score"] == 0.8
        assert docs[1]["source"] == "doc2.pdf"

    def test_empty_result(self):
        """测试空结果返回。"""
        import time
        tool = RAGTool()
        start_time = time.time()
        result = tool._empty_result(start_time)
        
        assert isinstance(result, dict)
        assert result["docs"] == []
        assert result["quality_score"] == 0.0
        assert result["has_relevant"] is False
        assert result["relevant_count"] == 0
        assert result["top_score"] == 0.0
        assert result["rewrite_history"] == []
        assert result["query_simplified"] == ""
        assert result["retrieval_time_ms"] >= 0

    def test_get_rag_tool_singleton(self):
        """测试单例模式。"""
        from agent.langgraph.tools.rag_tool import get_rag_tool
        
        tool1 = get_rag_tool()
        tool2 = get_rag_tool()
        
        assert tool1 is tool2
        assert isinstance(tool1, RAGTool)

    @pytest.mark.asyncio
    async def test_invoke_empty_query(self):
        """测试空查询调用。"""
        tool = RAGTool()
        input_data = RAGToolInput(query="", kb_ids=["kb1"])
        result = await tool.invoke(input_data)
        
        assert result["docs"] == []
        assert result["quality_score"] == 0.0
        assert result["has_relevant"] is False

    @pytest.mark.asyncio
    async def test_invoke_empty_kb_ids(self):
        """测试空知识库 ID 调用。"""
        tool = RAGTool()
        input_data = RAGToolInput(query="test query", kb_ids=[])
        result = await tool.invoke(input_data)
        
        assert result["docs"] == []
        assert result["quality_score"] == 0.0
        assert result["has_relevant"] is False
