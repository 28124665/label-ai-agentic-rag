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
"""Unit tests for the QueryRewriter component and complexity analyzer."""

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# The agent.component package auto-imports every submodule on import, which
# pulls in heavy optional dependencies during test collection. Replace the
# package with a minimal stub that only exposes the modules QueryRewriter
# depends on.
import agent  # noqa: F401

_component_pkg = types.ModuleType("agent.component")
_component_pkg.__path__ = [str(Path(__file__).parents[3] / "agent" / "component")]
sys.modules["agent.component"] = _component_pkg

# The base component class imports Graph from agent.canvas at runtime.
# Replace the real canvas module with a lightweight stub.
_canvas_mod = types.ModuleType("agent.canvas")


class Graph:
    pass


_canvas_mod.Graph = Graph
sys.modules["agent.canvas"] = _canvas_mod

# QueryRewriter imports LLM service classes inside its methods. Provide
# lightweight stub modules to avoid pulling in the full model service stack.
_llm_service_stub = types.ModuleType("api.db.services.llm_service")
_llm_service_stub.LLMBundle = type("LLMBundle", (), {})
sys.modules["api.db.services.llm_service"] = _llm_service_stub

_tenant_model_stub = types.ModuleType("api.db.joint_services.tenant_model_service")


def _stub_model_config(*args, **kwargs):
    return {}


_tenant_model_stub.get_model_config_by_type_and_name = _stub_model_config
_tenant_model_stub.get_tenant_default_model_by_type = _stub_model_config
sys.modules["api.db.joint_services.tenant_model_service"] = _tenant_model_stub

from agent.component.query_rewriter import (
    COMPLEXITY_BOOLEAN,
    COMPLEXITY_COMPLEX,
    COMPLEXITY_FACTUAL,
    COMPLEXITY_MULTI_ASPECT,
    COMPLEXITY_SIMPLE,
    STRATEGY_HYDE,
    STRATEGY_SUB_QUERY_DECOMPOSE,
    STRATEGY_SYNONYM_REWRITE,
    QueryRewriter,
    QueryRewriterParam,
    analyze_query_complexity,
)


@pytest.fixture
def mock_canvas():
    # Import Graph dynamically so the fixture uses the same class ComponentBase
    # sees at runtime, regardless of test module import order.
    from agent.canvas import Graph as StubGraph

    MockCanvas = type("MockCanvas", (StubGraph, MagicMock), {})
    canvas = MockCanvas()
    canvas.get_tenant_id.return_value = "tenant_1"
    canvas.is_canceled.return_value = False
    canvas.globals = {
        "sys.query": "test query",
        "sys.retry_count": 0,
    }

    def _get_variable_value(key):
        return canvas.globals.get(key)

    canvas.get_variable_value.side_effect = _get_variable_value
    return canvas


@pytest.fixture
def patch_llm_deps():
    with patch.object(QueryRewriter, "_create_llm_bundle") as mock_create_llm:
        bundle = MagicMock()
        bundle.async_chat = AsyncMock(return_value="")
        mock_create_llm.return_value = bundle
        yield mock_create_llm, bundle


def make_param(**kwargs):
    param = QueryRewriterParam()
    param.llm_id = "test_llm"
    for k, v in kwargs.items():
        setattr(param, k, v)
    param.check()
    return param


class TestAnalyzeQueryComplexity:
    def test_empty_query_defaults_to_simple(self):
        complexity, strategies = analyze_query_complexity("")
        assert complexity == COMPLEXITY_SIMPLE
        assert strategies == [STRATEGY_SYNONYM_REWRITE]

    def test_simple_query(self):
        complexity, strategies = analyze_query_complexity("RAGFlow 是什么")
        assert complexity == COMPLEXITY_SIMPLE
        assert strategies == [STRATEGY_SYNONYM_REWRITE]

    def test_simple_query_short_chinese(self):
        complexity, strategies = analyze_query_complexity("公司地址")
        assert complexity == COMPLEXITY_SIMPLE
        assert strategies == [STRATEGY_SYNONYM_REWRITE]

    def test_multi_aspect_query(self):
        complexity, strategies = analyze_query_complexity("RAGFlow 和 LangChain 有什么区别")
        assert complexity == COMPLEXITY_MULTI_ASPECT
        assert strategies == [STRATEGY_SUB_QUERY_DECOMPOSE]

    def test_multi_aspect_query_with_vs(self):
        complexity, strategies = analyze_query_complexity("RAGFlow vs LangChain")
        assert complexity == COMPLEXITY_MULTI_ASPECT
        assert strategies == [STRATEGY_SUB_QUERY_DECOMPOSE]

    def test_factual_query_with_year(self):
        complexity, strategies = analyze_query_complexity("2024年Q3营收增长多少")
        assert complexity == COMPLEXITY_FACTUAL
        assert strategies == [STRATEGY_HYDE]

    def test_factual_query_with_number(self):
        complexity, strategies = analyze_query_complexity("2024年员工人数是多少")
        assert complexity == COMPLEXITY_FACTUAL
        assert strategies == [STRATEGY_HYDE]

    def test_boolean_query(self):
        complexity, strategies = analyze_query_complexity("RAGFlow 是开源的吗")
        assert complexity == COMPLEXITY_BOOLEAN
        assert strategies == [STRATEGY_SYNONYM_REWRITE]

    def test_boolean_query_with_prefix(self):
        complexity, strategies = analyze_query_complexity("是否支持多租户")
        assert complexity == COMPLEXITY_BOOLEAN
        assert strategies == [STRATEGY_SYNONYM_REWRITE]

    def test_complex_query_multiple_entities(self):
        complexity, strategies = analyze_query_complexity("RAGFlow、LangChain 和 LlamaIndex 各自的优势是什么")
        assert complexity == COMPLEXITY_COMPLEX
        assert STRATEGY_SYNONYM_REWRITE in strategies
        assert STRATEGY_SUB_QUERY_DECOMPOSE in strategies
        assert STRATEGY_HYDE in strategies

    def test_complex_query_long(self):
        complexity, strategies = analyze_query_complexity("请详细说明 RAGFlow 的部署方式、系统要求以及性能调优方法")
        assert complexity == COMPLEXITY_COMPLEX
        assert strategies == [
            STRATEGY_SYNONYM_REWRITE,
            STRATEGY_SUB_QUERY_DECOMPOSE,
            STRATEGY_HYDE,
        ]

    def test_multi_aspect_overrides_complex(self):
        # Two entities but explicit comparison keyword should classify as multi_aspect.
        complexity, strategies = analyze_query_complexity("A 和 B 的差异")
        assert complexity == COMPLEXITY_MULTI_ASPECT
        assert strategies == [STRATEGY_SUB_QUERY_DECOMPOSE]

    def test_factual_overrides_boolean(self):
        # Contains a number and a boolean ending; factual signal is checked first.
        complexity, strategies = analyze_query_complexity("2024年营收增长15%吗")
        assert complexity == COMPLEXITY_FACTUAL
        assert strategies == [STRATEGY_HYDE]


class TestQueryRewriterParam:
    def test_default_values(self):
        param = QueryRewriterParam()
        assert param.query == "sys.query"
        assert param.retry_count == "sys.retry_count"
        assert param.enable_llm_fallback is True
        assert param.max_retry_tokens == 2000
        assert param.max_retries == 3

    def test_validation_passes(self):
        param = QueryRewriterParam()
        param.llm_id = "test_llm"
        param.check()

    def test_invalid_max_retry_tokens(self):
        param = QueryRewriterParam()
        param.max_retry_tokens = -1
        with pytest.raises(ValueError):
            param.check()


class TestQueryRewriterComponent:
    def test_component_outputs_rule_based_complexity(self, mock_canvas):
        param = make_param(enable_llm_fallback=False)
        mock_canvas.globals["sys.query"] = "RAGFlow 和 LangChain 有什么区别"
        rewriter = QueryRewriter(mock_canvas, "rewriter_0", param)
        rewriter.invoke()

        assert rewriter.output("query_complexity") == COMPLEXITY_MULTI_ASPECT
        assert rewriter.output("recommended_strategies") == [STRATEGY_SUB_QUERY_DECOMPOSE]
        assert rewriter.output("selected_strategy") == STRATEGY_SUB_QUERY_DECOMPOSE
        assert rewriter.output("rewritten_query") == "RAGFlow 和 LangChain 有什么区别"
        assert mock_canvas.globals["sys.query_complexity"] == COMPLEXITY_MULTI_ASPECT

    def test_component_selects_strategy_by_retry_count(self, mock_canvas):
        param = make_param(enable_llm_fallback=False)
        mock_canvas.globals["sys.query"] = "RAGFlow、LangChain 和 LlamaIndex 各自的优势"
        mock_canvas.globals["sys.retry_count"] = 1
        rewriter = QueryRewriter(mock_canvas, "rewriter_0", param)
        rewriter.invoke()

        assert rewriter.output("query_complexity") == COMPLEXITY_COMPLEX
        assert rewriter.output("selected_strategy") == STRATEGY_SUB_QUERY_DECOMPOSE

    def test_component_default_retry_count(self, mock_canvas):
        param = make_param(enable_llm_fallback=False)
        mock_canvas.globals["sys.query"] = "RAGFlow 是什么"
        del mock_canvas.globals["sys.retry_count"]
        rewriter = QueryRewriter(mock_canvas, "rewriter_0", param)
        rewriter.invoke()

        assert rewriter.output("selected_strategy") == STRATEGY_SYNONYM_REWRITE
        assert mock_canvas.globals["sys.retry_count"] == 0

    def test_component_uses_llm_fallback_for_unknown(self, mock_canvas, patch_llm_deps):
        _, bundle = patch_llm_deps
        bundle.async_chat.return_value = '{"complexity": "complex", "reason": "ambiguous"}'
        param = make_param(enable_llm_fallback=True, llm_id="test_llm")
        mock_canvas.globals["sys.query"] = "这个问题到底应该怎么理解才正确呢"
        rewriter = QueryRewriter(mock_canvas, "rewriter_0", param)
        rewriter.invoke()

        assert bundle.async_chat.called
        assert rewriter.output("query_complexity") == COMPLEXITY_COMPLEX

    def test_llm_fallback_parse_error_defaults_to_simple(self, mock_canvas, patch_llm_deps):
        _, bundle = patch_llm_deps
        bundle.async_chat.return_value = "not valid json"
        param = make_param(enable_llm_fallback=True, llm_id="test_llm")
        mock_canvas.globals["sys.query"] = "这个问题到底应该怎么理解才正确呢"
        rewriter = QueryRewriter(mock_canvas, "rewriter_0", param)
        rewriter.invoke()

        assert rewriter.output("query_complexity") == COMPLEXITY_SIMPLE

    def test_llm_fallback_keyword_extraction(self, mock_canvas, patch_llm_deps):
        _, bundle = patch_llm_deps
        bundle.async_chat.return_value = "The complexity is multi_aspect because of comparison."
        param = make_param(enable_llm_fallback=True, llm_id="test_llm")
        mock_canvas.globals["sys.query"] = "这个问题到底应该怎么理解才正确呢"
        rewriter = QueryRewriter(mock_canvas, "rewriter_0", param)
        rewriter.invoke()

        assert rewriter.output("query_complexity") == COMPLEXITY_MULTI_ASPECT

    def test_llm_disabled_uses_simple_default(self, mock_canvas, patch_llm_deps):
        _, bundle = patch_llm_deps
        param = make_param(enable_llm_fallback=False)
        mock_canvas.globals["sys.query"] = "这个问题到底应该怎么理解才正确呢"
        rewriter = QueryRewriter(mock_canvas, "rewriter_0", param)
        rewriter.invoke()

        assert not bundle.async_chat.called
        assert rewriter.output("query_complexity") == COMPLEXITY_SIMPLE

    def test_component_canceled(self, mock_canvas):
        mock_canvas.is_canceled.return_value = True
        param = make_param(enable_llm_fallback=False)
        rewriter = QueryRewriter(mock_canvas, "rewriter_0", param)
        result = rewriter.invoke()

        assert result.get("_ERROR") == "Task has been canceled"
