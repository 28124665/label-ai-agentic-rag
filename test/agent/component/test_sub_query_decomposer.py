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
"""Unit tests for the SubQueryDecomposer component and result merge utility."""

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agent  # noqa: F401

_component_pkg = types.ModuleType("agent.component")
_component_pkg.__path__ = [str(Path(__file__).parents[3] / "agent" / "component")]
sys.modules["agent.component"] = _component_pkg

_canvas_mod = types.ModuleType("agent.canvas")


class Graph:
    pass


_canvas_mod.Graph = Graph
sys.modules["agent.canvas"] = _canvas_mod

_llm_service_stub = types.ModuleType("api.db.services.llm_service")
_llm_service_stub.LLMBundle = type("LLMBundle", (), {})
sys.modules["api.db.services.llm_service"] = _llm_service_stub

_tenant_model_stub = types.ModuleType("api.db.joint_services.tenant_model_service")


def _stub_model_config(*args, **kwargs):
    return {}


_tenant_model_stub.get_model_config_by_type_and_name = _stub_model_config
_tenant_model_stub.get_tenant_default_model_by_type = _stub_model_config
sys.modules["api.db.joint_services.tenant_model_service"] = _tenant_model_stub

from agent.component.sub_query_decomposer import (
    DEFAULT_DEDUP_THRESHOLD,
    DEFAULT_SUB_QUERY_TOP_K,
    SubQueryDecomposer,
    SubQueryDecomposerParam,
    merge_sub_query_results,
)


@pytest.fixture
def mock_canvas():
    # Import the Graph class dynamically so it matches the one ComponentBase
    # sees at test execution time (other test files also stub agent.canvas).
    from agent.canvas import Graph as StubGraph

    MockCanvas = type("MockCanvas", (StubGraph, MagicMock), {})
    canvas = MockCanvas()
    canvas.get_tenant_id.return_value = "tenant_1"
    canvas.is_canceled.return_value = False
    canvas.globals = {"sys.query": "RAGFlow 和 LangChain 有什么区别"}

    def _get_variable_value(key):
        return canvas.globals.get(key)

    canvas.get_variable_value.side_effect = _get_variable_value
    return canvas


@pytest.fixture
def patch_llm_deps():
    with patch.object(SubQueryDecomposer, "_create_llm_bundle") as mock_create_llm:
        bundle = MagicMock()
        bundle.async_chat = AsyncMock(return_value="[]")
        mock_create_llm.return_value = bundle
        yield mock_create_llm, bundle


def make_param(**kwargs):
    param = SubQueryDecomposerParam()
    param.llm_id = "test_llm"
    for k, v in kwargs.items():
        setattr(param, k, v)
    param.check()
    return param


class TestSubQueryDecomposerParam:
    def test_default_values(self):
        param = SubQueryDecomposerParam()
        param.llm_id = "test"
        param.check()
        assert param.min_count == 2
        assert param.max_count == 5
        assert param.top_k == DEFAULT_SUB_QUERY_TOP_K
        assert param.dedup_threshold == DEFAULT_DEDUP_THRESHOLD
        assert param.rrf_k == 60

    def test_invalid_count_range(self):
        param = SubQueryDecomposerParam()
        param.llm_id = "test"
        param.min_count = 6
        param.max_count = 5
        with pytest.raises(ValueError):
            param.check()

    def test_llm_id_required(self):
        param = SubQueryDecomposerParam()
        with pytest.raises(ValueError):
            param.check()


class TestSubQueryDecomposerComponent:
    def test_decompose_json_list(self, mock_canvas, patch_llm_deps):
        _, bundle = patch_llm_deps
        bundle.async_chat.return_value = (
            '["RAGFlow 检索增强生成实现", "LangChain 检索增强生成实现", "RAGFlow 与 LangChain 对比"]'
        )
        param = make_param()
        decomposer = SubQueryDecomposer(mock_canvas, "sub_0", param)
        decomposer.invoke()

        sub_queries = decomposer.output("sub_queries")
        assert len(sub_queries) == 3
        assert all(isinstance(q, str) for q in sub_queries)
        assert mock_canvas.globals["sys.sub_queries"] == sub_queries

    def test_decompose_deduplicates(self, mock_canvas, patch_llm_deps):
        _, bundle = patch_llm_deps
        bundle.async_chat.return_value = (
            '["Query one", "Query one", "Query two"]'
        )
        param = make_param()
        decomposer = SubQueryDecomposer(mock_canvas, "sub_0", param)
        decomposer.invoke()

        sub_queries = decomposer.output("sub_queries")
        assert len(sub_queries) == 2
        assert "Query one" in sub_queries
        assert "Query two" in sub_queries

    def test_decompose_clamps_to_max_count(self, mock_canvas, patch_llm_deps):
        _, bundle = patch_llm_deps
        bundle.async_chat.return_value = (
            '["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"]'
        )
        param = make_param()
        decomposer = SubQueryDecomposer(mock_canvas, "sub_0", param)
        decomposer.invoke()

        assert len(decomposer.output("sub_queries")) == 5

    def test_decompose_fallback_to_original_when_too_few(self, mock_canvas, patch_llm_deps):
        _, bundle = patch_llm_deps
        bundle.async_chat.return_value = '["Only one"]'
        param = make_param()
        decomposer = SubQueryDecomposer(mock_canvas, "sub_0", param)
        decomposer.invoke()

        sub_queries = decomposer.output("sub_queries")
        assert len(sub_queries) >= 1
        assert mock_canvas.globals["sys.query"] in sub_queries

    def test_decompose_llm_error_returns_empty(self, mock_canvas, patch_llm_deps):
        _, bundle = patch_llm_deps
        bundle.async_chat.return_value = "**ERROR** quota exceeded"
        param = make_param()
        decomposer = SubQueryDecomposer(mock_canvas, "sub_0", param)
        decomposer.invoke()

        assert decomposer.output("sub_queries") == []
        assert decomposer.output("sub_query_count") == 0

    def test_component_canceled(self, mock_canvas):
        mock_canvas.is_canceled.return_value = True
        param = make_param()
        decomposer = SubQueryDecomposer(mock_canvas, "sub_0", param)
        result = decomposer.invoke()

        assert result.get("_ERROR") == "Task has been canceled"


class TestMergeSubQueryResults:
    def test_empty_input(self):
        assert merge_sub_query_results([]) == []

    def test_simple_rrf_merge(self):
        results = [
            [{"id": "doc1", "content": "A"}, {"id": "doc2", "content": "B"}],
            [{"id": "doc2", "content": "B"}, {"id": "doc3", "content": "C"}],
        ]
        merged = merge_sub_query_results(results)
        assert len(merged) == 3
        # doc2 appears in both lists so has highest RRF score.
        assert merged[0]["id"] == "doc2"
        assert set(merged[0]["sub_query_sources"]) == {0, 1}

    def test_top_k_truncation(self):
        results = [
            [{"id": f"doc{i}", "content": f"content {i}"} for i in range(5)],
            [{"id": f"doc{i+3}", "content": f"content {i+3}"} for i in range(5)],
        ]
        merged = merge_sub_query_results(results, top_k=3)
        assert len(merged) == 3

    def test_content_deduplication_without_embeddings(self):
        results = [
            [{"id": "a", "content": "same"}, {"id": "b", "content": "other"}],
            [{"id": "c", "content": "same"}],
        ]
        merged = merge_sub_query_results(results)
        assert len(merged) == 2
        # The duplicate "same" doc from sub-query 1 should merge sources.
        same_doc = next(d for d in merged if d["content"] == "same")
        assert set(same_doc["sub_query_sources"]) == {0, 1}

    def test_embedding_deduplication(self):
        def embedding_fn(doc):
            # Deterministic embedding based on content.
            content = doc.get("content", "")
            if "same" in content:
                return [1.0, 0.0, 0.0]
            return [0.0, 1.0, float(hash(content) % 100) / 100]

        results = [
            [{"id": "a", "content": "same content"}, {"id": "b", "content": "unique 1"}],
            [{"id": "c", "content": "same content too"}, {"id": "d", "content": "unique 2"}],
        ]
        merged = merge_sub_query_results(
            results,
            embedding_fn=embedding_fn,
            dedup_threshold=0.92,
        )
        contents = {d["content"] for d in merged}
        assert "same content" in contents
        # The near-duplicate should be removed by embedding dedup.
        assert "same content too" not in contents
