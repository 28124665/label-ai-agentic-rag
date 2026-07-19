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
import asyncio
import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# The agent.component package auto-imports every submodule on import, which
# pulls in heavy optional dependencies during test collection. Replace the
# package with a minimal stub that only exposes the modules Grader depends on.
import agent  # noqa: F401

_component_pkg = types.ModuleType("agent.component")
_component_pkg.__path__ = [str(Path(__file__).parents[3] / "agent" / "component")]
sys.modules["agent.component"] = _component_pkg

# The base component class imports Graph from agent.canvas at runtime.  Replace
# the real canvas module with a lightweight stub so that instantiating Grader
# does not pull in Redis/DB dependencies.
_canvas_mod = types.ModuleType("agent.canvas")


class Graph:
    pass


_canvas_mod.Graph = Graph
sys.modules["agent.canvas"] = _canvas_mod

# The Grader imports LLM/Rerank service classes inside its methods.  Providing
# lightweight stub modules lets the unit tests avoid pulling in the full model
# service stack (xgboost, deepdoc, litellm, etc.).
_llm_service_stub = types.ModuleType("api.db.services.llm_service")
_llm_service_stub.LLMBundle = type("LLMBundle", (), {})
sys.modules["api.db.services.llm_service"] = _llm_service_stub

_tenant_model_stub = types.ModuleType("api.db.joint_services.tenant_model_service")


def _stub_model_config(*args, **kwargs):
    return {}


_tenant_model_stub.get_model_config_by_type_and_name = _stub_model_config
_tenant_model_stub.get_tenant_default_model_by_type = _stub_model_config
sys.modules["api.db.joint_services.tenant_model_service"] = _tenant_model_stub

from agent.component.grader import Grader, GraderParam


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
        "sys.retrieved_docs": [],
    }

    def _get_variable_value(key):
        return canvas.globals.get(key, "")

    canvas.get_variable_value.side_effect = _get_variable_value
    return canvas


@pytest.fixture
def sample_docs():
    return [
        {"content": "This is a relevant document.", "rerank_score": 0.9},
        {"content": "This is completely unrelated.", "rerank_score": 0.1},
    ]


@pytest.fixture
def patch_llm_deps():
    # Patch the bundle factory methods on Grader so the tests do not need to
    # import the real model service stack.
    with patch.object(Grader, "_create_llm_bundle") as mock_create_llm, patch.object(Grader, "_create_rerank_bundle") as mock_create_rerank:
        bundle = MagicMock()
        bundle.async_chat = AsyncMock(return_value="[]")
        bundle.similarity = MagicMock(return_value=([], 0))
        mock_create_llm.return_value = bundle
        mock_create_rerank.return_value = bundle
        yield mock_create_llm, bundle, mock_create_rerank


@pytest.fixture(autouse=True)
def patch_tokenizer():
    # Use a simple length-based tokenizer for deterministic splitting in tests.
    # Avoids network-dependent tiktoken loading during unit tests.
    with patch("agent.component.grader.num_tokens_from_string", side_effect=len):
        yield


def make_param(**kwargs):
    param = GraderParam()
    param.llm_id = "test_llm"
    for k, v in kwargs.items():
        setattr(param, k, v)
    param.check()
    return param


class TestGraderParam:
    def test_default_values(self):
        param = GraderParam()
        assert param.batch_size == 5
        assert param.max_batch_size == 10
        assert param.max_eval_tokens == 2000
        assert param.timeout_seconds == 10
        assert param.fallback_on_failure is True
        assert param.max_retry_on_parse_error == 1
        assert param.evaluator_model == "llm"
        assert param.relevance_threshold == 0.5

    def test_batch_size_validation(self):
        param = GraderParam()
        param.llm_id = "test"
        param.batch_size = 12
        with pytest.raises(ValueError):
            param.check()

    def test_evaluator_model_validation(self):
        param = GraderParam()
        param.llm_id = "test"
        param.evaluator_model = "unknown"
        with pytest.raises(ValueError):
            param.check()

    def test_llm_id_required_for_llm_mode(self):
        param = GraderParam()
        param.llm_id = ""
        with pytest.raises(ValueError):
            param.check()


class TestGraderLLMEvaluation:
    def test_llm_success(self, mock_canvas, sample_docs, patch_llm_deps):
        _, bundle, _ = patch_llm_deps
        bundle.async_chat.return_value = json.dumps(
            [
                {"index": 0, "relevance": "relevant", "score": 0.92, "reason": "Matches query"},
                {"index": 1, "relevance": "not_relevant", "score": 0.12, "reason": "No match"},
            ]
        )
        mock_canvas.globals["sys.retrieved_docs"] = sample_docs

        param = make_param()
        grader = Grader(mock_canvas, "grader_0", param)
        grader.invoke()

        graded = grader.output("graded_docs")
        assert len(graded) == 2
        assert graded[0]["relevance"] == "relevant"
        assert graded[0]["score"] == pytest.approx(0.92)
        assert graded[0]["graded_by"] == "llm"
        assert graded[1]["relevance"] == "not_relevant"
        assert grader.output("has_relevant") is True
        assert grader.output("relevant_count") == 1
        assert mock_canvas.globals["sys.has_relevant"] is True

    def test_llm_timeout_fallback(self, mock_canvas, sample_docs, patch_llm_deps):
        _, bundle, _ = patch_llm_deps
        bundle.async_chat.side_effect = asyncio.TimeoutError()
        mock_canvas.globals["sys.retrieved_docs"] = sample_docs

        param = make_param()
        grader = Grader(mock_canvas, "grader_0", param)
        grader.invoke()

        graded = grader.output("graded_docs")
        assert graded[0]["graded_by"] == "rerank_fallback"
        assert graded[0]["fallback_reason"] == "llm_timeout"
        assert graded[0]["score"] == pytest.approx(0.9)

    def test_llm_quota_exceeded_with_backup(self, mock_canvas, sample_docs, patch_llm_deps):
        mock_create_llm, bundle, _ = patch_llm_deps
        main_bundle = MagicMock()
        main_bundle.async_chat = AsyncMock(return_value="**ERROR** quota exceeded")
        backup_bundle = MagicMock()
        backup_bundle.async_chat = AsyncMock(
            return_value=json.dumps(
                [
                    {"index": 0, "relevance": "relevant", "score": 0.95, "reason": "Good"},
                    {"index": 1, "relevance": "not_relevant", "score": 0.05, "reason": "Bad"},
                ]
            )
        )
        mock_create_llm.side_effect = [main_bundle, backup_bundle]
        mock_canvas.globals["sys.retrieved_docs"] = sample_docs

        param = make_param(backup_llm_model="backup_model")
        grader = Grader(mock_canvas, "grader_0", param)
        grader.invoke()

        assert mock_create_llm.call_count == 2
        graded = grader.output("graded_docs")
        assert graded[0]["graded_by"] == "backup_llm"
        assert graded[0]["score"] == pytest.approx(0.95)

    def test_llm_quota_exceeded_without_backup(self, mock_canvas, sample_docs, patch_llm_deps):
        _, bundle, _ = patch_llm_deps
        bundle.async_chat.return_value = "**ERROR** rate limit"
        mock_canvas.globals["sys.retrieved_docs"] = sample_docs

        param = make_param()
        grader = Grader(mock_canvas, "grader_0", param)
        grader.invoke()

        graded = grader.output("graded_docs")
        assert graded[0]["graded_by"] == "rerank_fallback"
        assert graded[0]["fallback_reason"] == "llm_quota_exceeded"

    def test_llm_parse_error_retry_then_fallback(self, mock_canvas, sample_docs, patch_llm_deps):
        _, bundle, _ = patch_llm_deps
        bundle.async_chat.return_value = "not valid json"
        mock_canvas.globals["sys.retrieved_docs"] = sample_docs

        param = make_param(max_retry_on_parse_error=1)
        grader = Grader(mock_canvas, "grader_0", param)
        grader.invoke()

        assert bundle.async_chat.call_count == 2
        graded = grader.output("graded_docs")
        assert graded[0]["graded_by"] == "rerank_fallback"
        assert graded[0]["fallback_reason"] == "parse_error"

    def test_llm_parse_error_retry_success(self, mock_canvas, sample_docs, patch_llm_deps):
        _, bundle, _ = patch_llm_deps
        bundle.async_chat.side_effect = [
            "not valid json",
            json.dumps(
                [
                    {"index": 0, "relevance": "relevant", "score": 0.88, "reason": "Good"},
                    {"index": 1, "relevance": "not_relevant", "score": 0.11, "reason": "Bad"},
                ]
            ),
        ]
        mock_canvas.globals["sys.retrieved_docs"] = sample_docs

        param = make_param(max_retry_on_parse_error=1)
        grader = Grader(mock_canvas, "grader_0", param)
        grader.invoke()

        assert bundle.async_chat.call_count == 2
        graded = grader.output("graded_docs")
        assert graded[0]["graded_by"] == "llm"
        assert graded[0]["score"] == pytest.approx(0.88)

    def test_llm_service_unavailable_fallback(self, mock_canvas, sample_docs, patch_llm_deps):
        _, bundle, _ = patch_llm_deps
        bundle.async_chat.return_value = "**ERROR** service unavailable 503"
        mock_canvas.globals["sys.retrieved_docs"] = sample_docs

        param = make_param()
        grader = Grader(mock_canvas, "grader_0", param)
        grader.invoke()

        graded = grader.output("graded_docs")
        assert graded[0]["graded_by"] == "rerank_fallback"
        assert graded[0]["fallback_reason"] == "service_unavailable"


class TestGraderOtherEvaluators:
    def test_cross_encoder_evaluation(self, mock_canvas, sample_docs, patch_llm_deps):
        _, bundle, _ = patch_llm_deps
        bundle.similarity.return_value = ([0.95, 0.05], 0)
        mock_canvas.globals["sys.retrieved_docs"] = sample_docs

        param = make_param(evaluator_model="cross_encoder", rerank_model_id="rerank_model")
        grader = Grader(mock_canvas, "grader_0", param)
        grader.invoke()

        graded = grader.output("graded_docs")
        assert graded[0]["relevance"] == "relevant"
        assert graded[0]["graded_by"] == "cross_encoder"
        assert graded[0]["score"] == pytest.approx(0.95)
        assert graded[1]["relevance"] == "not_relevant"

    def test_local_nli_evaluation(self, mock_canvas, sample_docs, patch_llm_deps):
        _, bundle, _ = patch_llm_deps
        bundle.async_chat.return_value = json.dumps(
            [
                {"index": 0, "relevance": "relevant", "score": 0.88, "reason": "Entailment"},
                {"index": 1, "relevance": "not_relevant", "score": 0.12, "reason": "Neutral"},
            ]
        )
        mock_canvas.globals["sys.retrieved_docs"] = sample_docs

        param = make_param(evaluator_model="local_nli")
        grader = Grader(mock_canvas, "grader_0", param)
        grader.invoke()

        graded = grader.output("graded_docs")
        assert graded[0]["graded_by"] == "local_nli"
        assert graded[0]["score"] == pytest.approx(0.88)


class TestGraderFallback:
    def test_all_llm_failed_mark_relevant(self, mock_canvas, sample_docs, patch_llm_deps):
        _, bundle, _ = patch_llm_deps
        bundle.async_chat.side_effect = Exception("unexpected failure")
        mock_canvas.globals["sys.retrieved_docs"] = sample_docs

        param = make_param(fallback_on_failure=False)
        grader = Grader(mock_canvas, "grader_0", param)
        grader.invoke()

        graded = grader.output("graded_docs")
        assert graded[0]["graded_by"] == "llm_all_failed"
        assert graded[0]["relevance"] == "relevant"
        assert graded[0]["fallback_reason"] == "llm_failure"

    def test_no_documents(self, mock_canvas, patch_llm_deps):
        param = make_param()
        grader = Grader(mock_canvas, "grader_0", param)
        grader.invoke()

        assert grader.output("graded_docs") == []
        assert grader.output("has_relevant") is False
        assert grader.output("relevant_count") == 0


class TestGraderLongDocument:
    def test_long_document_semantic_split(self, mock_canvas, patch_llm_deps, patch_tokenizer):
        _, bundle, _ = patch_llm_deps
        # Two semantic paragraphs, one relevant and one not.
        doc = {"content": "A" * 100 + "\n\n" + "B" * 100}
        mock_canvas.globals["sys.retrieved_docs"] = [doc]
        bundle.async_chat.return_value = json.dumps(
            [
                {"index": 0, "relevance": "relevant", "score": 0.9, "reason": "Matches"},
                {"index": 1, "relevance": "not_relevant", "score": 0.1, "reason": "No match"},
            ]
        )

        param = make_param(max_eval_tokens=80, batch_size=2)
        grader = Grader(mock_canvas, "grader_0", param)
        grader.invoke()

        graded = grader.output("graded_docs")
        assert len(graded) == 1
        assert graded[0]["score"] == pytest.approx(0.9)
        assert graded[0]["relevance"] == "relevant"
        assert graded[0]["graded_by"] == "llm"

    def test_batch_split_by_token_limit(self, mock_canvas, patch_llm_deps, patch_tokenizer):
        mock_create_llm, bundle, _ = patch_llm_deps
        docs = [{"content": f"doc{i}"} for i in range(4)]
        mock_canvas.globals["sys.retrieved_docs"] = docs
        # Return valid results for any batch size.
        bundle.async_chat.return_value = json.dumps(
            [
                {"index": 0, "relevance": "relevant", "score": 0.8, "reason": "OK"},
                {"index": 1, "relevance": "relevant", "score": 0.8, "reason": "OK"},
            ]
        )

        # With max_eval_tokens=20 and batch_size=4, the token budget forces the 4 docs
        # into two batches of size 2.
        param = make_param(max_eval_tokens=20, batch_size=4)
        grader = Grader(mock_canvas, "grader_0", param)
        grader.invoke()

        assert bundle.async_chat.call_count == 2
        graded = grader.output("graded_docs")
        assert len(graded) == 4
