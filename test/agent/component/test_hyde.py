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
"""Unit tests for the HyDE component."""

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

from agent.component.hyde import DEFAULT_HYDE_TEMPERATURE, HyDE, HyDEParam


@pytest.fixture
def mock_canvas():
    # Import Graph dynamically so the fixture uses the same class ComponentBase
    # sees at runtime, regardless of test module import order.
    from agent.canvas import Graph as StubGraph

    MockCanvas = type("MockCanvas", (StubGraph, MagicMock), {})
    canvas = MockCanvas()
    canvas.get_tenant_id.return_value = "tenant_1"
    canvas.is_canceled.return_value = False
    canvas.globals = {"sys.query": "公司 2024 年 Q3 的营收增长率是多少"}

    def _get_variable_value(key):
        return canvas.globals.get(key)

    canvas.get_variable_value.side_effect = _get_variable_value
    return canvas


@pytest.fixture
def patch_llm_deps():
    with patch.object(HyDE, "_create_llm_bundle") as mock_create_llm:
        bundle = MagicMock()
        bundle.async_chat = AsyncMock(return_value="")
        mock_create_llm.return_value = bundle
        yield mock_create_llm, bundle


def make_param(**kwargs):
    param = HyDEParam()
    param.llm_id = "test_llm"
    for k, v in kwargs.items():
        setattr(param, k, v)
    param.check()
    return param


class TestHyDEParam:
    def test_default_values(self):
        param = HyDEParam()
        param.llm_id = "test"
        param.check()
        assert param.enable_hyde is False
        assert param.temperature == DEFAULT_HYDE_TEMPERATURE
        assert param.max_tokens == 256

    def test_enable_hyde_validation(self):
        param = HyDEParam()
        param.llm_id = "test"
        param.enable_hyde = "not_bool"
        with pytest.raises(ValueError):
            param.check()


class TestHyDEComponent:
    def test_hyde_disabled_does_not_call_llm(self, mock_canvas, patch_llm_deps):
        _, bundle = patch_llm_deps
        param = make_param(enable_hyde=False)
        hyde = HyDE(mock_canvas, "hyde_0", param)
        hyde.invoke()

        assert not bundle.async_chat.called
        assert hyde.output("hypothetical_answer") == ""
        assert hyde.output("retrieval_query") == mock_canvas.globals["sys.query"]
        assert hyde.output("enable_hyde") is False

    def test_hyde_generates_hypothetical_answer(self, mock_canvas, patch_llm_deps):
        _, bundle = patch_llm_deps
        bundle.async_chat.return_value = "公司 2024 年 Q3 营收增长率为 15.3%。"
        param = make_param(enable_hyde=True)
        hyde = HyDE(mock_canvas, "hyde_0", param)
        hyde.invoke()

        assert bundle.async_chat.called
        assert "15.3%" in hyde.output("hypothetical_answer")
        assert hyde.output("retrieval_query") == hyde.output("hypothetical_answer")
        assert mock_canvas.globals["sys.hypothetical_answer"] == hyde.output("hypothetical_answer")

    def test_hyde_uses_low_temperature(self, mock_canvas, patch_llm_deps):
        _, bundle = patch_llm_deps
        bundle.async_chat.return_value = "假设答案"
        param = make_param(enable_hyde=True, temperature=0.3)
        hyde = HyDE(mock_canvas, "hyde_0", param)
        hyde.invoke()

        call_args, _ = bundle.async_chat.call_args
        assert call_args[2]["temperature"] == pytest.approx(0.3)

    def test_hyde_llm_error_falls_back_to_query(self, mock_canvas, patch_llm_deps):
        _, bundle = patch_llm_deps
        bundle.async_chat.return_value = "**ERROR** service unavailable"
        param = make_param(enable_hyde=True)
        hyde = HyDE(mock_canvas, "hyde_0", param)
        hyde.invoke()

        assert hyde.output("hypothetical_answer") == ""
        assert hyde.output("retrieval_query") == mock_canvas.globals["sys.query"]

    def test_hyde_retrieval_query_not_final_answer(self, mock_canvas, patch_llm_deps):
        _, bundle = patch_llm_deps
        bundle.async_chat.return_value = "这是假设答案，仅用于检索。"
        param = make_param(enable_hyde=True)
        hyde = HyDE(mock_canvas, "hyde_0", param)
        hyde.invoke()

        # HyDE should never write to final_answer or answer_with_citations.
        assert "final_answer" not in hyde.output()
        assert "answer_with_citations" not in hyde.output()
        assert hyde.output("hypothetical_answer") != ""

    def test_component_canceled(self, mock_canvas):
        mock_canvas.is_canceled.return_value = True
        param = make_param(enable_hyde=False)
        hyde = HyDE(mock_canvas, "hyde_0", param)
        result = hyde.invoke()

        assert result.get("_ERROR") == "Task has been canceled"
