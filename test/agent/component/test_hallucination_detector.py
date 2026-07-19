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
"""Unit tests for the HallucinationDetector component."""

from __future__ import annotations

import sys
import types

import pytest

# Stub ``agent.canvas`` before importing components to avoid side effects such as
# Redis connections that are triggered when the real Canvas module is loaded.
_canvas_stub = types.ModuleType("agent.canvas")


class Graph:
    """Minimal Canvas/Graph stand-in for component instantiation."""

    def __init__(self, dsl=None, tenant_id=None, task_id=None, custom_header=None):
        self._tenant_id = tenant_id
        self.task_id = task_id
        self.components = {}
        self.path = []
        self.custom_header = custom_header
        self.dsl = {"components": {}}


_canvas_stub.Graph = Graph
_canvas_stub.Canvas = Graph
sys.modules["agent.canvas"] = _canvas_stub

from agent.component.hallucination_detector import (
    Claim,
    HallucinationDetector,
    HallucinationDetectorParam,
)


class MockCanvas(Graph):
    """Minimal Canvas subclass for unit testing a single component."""

    def __init__(self, globals_dict=None, tenant_id="tenant_1"):
        self.globals = globals_dict or {}
        self._tenant_id = tenant_id
        self.task_id = "task_1"
        self.components = {}
        self.path = []
        self.custom_header = None
        self.dsl = {"components": {}}

    def is_canceled(self) -> bool:
        return False

    def get_tenant_id(self):
        return self._tenant_id

    def get_history(self, window_size):
        return []

    def get_reference(self):
        return {"chunks": [], "doc_aggs": []}

    def is_reff(self, exp: str) -> bool:
        exp = exp.strip("{").strip("}").strip()
        if exp.find("@") < 0:
            return exp in self.globals
        arr = exp.split("@")
        if len(arr) != 2:
            return False
        return arr[0] in self.components

    def get_variable_value(self, exp: str):
        exp = exp.strip("{").strip("}").strip()
        if exp.find("@") < 0:
            return self.globals.get(exp)
        cpn_id, var_nm = exp.split("@")
        cpn = self.components.get(cpn_id)
        if not cpn:
            raise Exception(f"Can't find variable: '{exp}'")
        return cpn["obj"].output(var_nm)


def make_component(globals_dict=None, param_overrides=None):
    # Import Graph dynamically so the canvas instance uses the same class
    # ComponentBase sees at runtime, regardless of test module import order.
    from agent.canvas import Graph as StubGraph

    canvas = MockCanvas.__new__(type("DynamicMockCanvas", (MockCanvas, StubGraph), {}))
    canvas.__init__(globals_dict=globals_dict)
    param = HallucinationDetectorParam()
    # Disable LLM by default so tests are deterministic and cheap.
    param.use_llm = False
    param.use_nli = False
    param.llm_id = ""
    if param_overrides:
        for k, v in param_overrides.items():
            setattr(param, k, v)
    param.check()
    return HallucinationDetector(canvas, "hd_0", param)


class TestClaimDecomposition:
    def test_split_sentences(self):
        cpn = make_component()
        text = "今天天气很好。我们去公园。"
        assert cpn._split_sentences(text) == ["今天天气很好。", "我们去公园。"]

    def test_split_compound_sentences(self):
        cpn = make_component()
        text = "因为下雨了，所以比赛取消了"
        result = cpn._split_compound_sentences(text)
        assert "比赛取消了" in result
        assert any("下雨了" in r for r in result)

    def test_split_list_items(self):
        cpn = make_component()
        text = "1. 苹果\n2. 香蕉\n3. 橙子"
        items = cpn._split_list_items(text)
        assert len(items) == 3
        assert "苹果" in items[0]

    def test_merge_numerical_claims(self):
        cpn = make_component()
        claims = ["2024年Q3", "营收增长15.3%"]
        merged = cpn._merge_numerical_claims(claims)
        assert len(merged) == 1
        assert "2024年Q3" in merged[0]
        assert "15.3%" in merged[0]

    def test_decompose_claims_with_list_and_compound(self):
        cpn = make_component()
        answer = "公司2024年Q3营收增长15.3%。因为需求上升，所以利润增加。主要产品有：1. 产品A 2. 产品B。"
        claims = cpn._decompose_claims(answer)
        texts = [c.text for c in claims]
        assert any("15.3%" in t for t in texts)
        assert any("需求上升" in t for t in texts)
        assert any("产品A" in t for t in texts)
        assert any("产品B" in t for t in texts)


class TestRuleVerification:
    def test_supported_numerical_claim(self):
        docs = [{"content": "公司2024年第三季度营收同比增长15.3%。"}]
        cpn = make_component()
        claim = Claim(text="2024年Q3营收增长15.3%")
        cpn._verify_claims([claim], docs)
        assert claim.rule_result == "SUPPORTED"
        assert claim.score == 1.0

    def test_contradicted_numerical_claim(self):
        docs = [{"content": "公司2024年Q3营收增长12.1%。"}]
        cpn = make_component()
        claim = Claim(text="2024年Q3营收增长15.3%")
        cpn._verify_claims([claim], docs)
        assert claim.rule_result == "CONTRADICTED"
        assert claim.score == 0.0

    def test_contradicted_quarter_claim(self):
        docs = [{"content": "公司2024年Q3营收增长12.1%。"}]
        cpn = make_component()
        claim = Claim(text="2024年Q4营收增长10%")
        cpn._verify_claims([claim], docs)
        # A quarter-specific claim that conflicts with the retrieved quarter is
        # treated as a contradiction by the rule layer.
        assert claim.rule_result == "CONTRADICTED"
        assert claim.score == 0.0

    def test_aggregate_score_with_contradiction_cap(self):
        docs = [{"content": "公司2024年Q3营收增长12.1%。"}]
        cpn = make_component()
        claim = Claim(text="2024年Q3营收增长15.3%")
        cpn._verify_claims([claim], docs)
        score, count = cpn._aggregate_scores([claim])
        assert score <= 0.5
        assert count == 1


class TestScoringWeights:
    def test_claim_score_weights(self):
        cpn = make_component()
        claim = Claim(text="x", rule_result="SUPPORTED", nli_result="entailment", llm_result="SUPPORTED")
        assert cpn._claim_score(claim) == pytest.approx(1.0)

        claim2 = Claim(text="x", rule_result="NOT_SUPPORTED", nli_result="neutral", llm_result="NOT_SUPPORTED")
        expected = 0.4 * 0.0 + 0.4 * 0.5 + 0.2 * 0.3
        assert cpn._claim_score(claim2) == pytest.approx(expected)


class TestDisposalLogic:
    def test_pass_threshold(self):
        cpn = make_component()
        claims = [Claim(text="x", score=1.0), Claim(text="y", score=1.0)]
        action, answer, _, _ = cpn._dispose("original", "query", claims, 0.9, [])
        assert action == "pass"
        assert answer == "original"

    def test_filter_threshold(self):
        cpn = make_component()
        claims = [
            Claim(text="supported claim", score=1.0),
            Claim(text="unsupported claim", score=0.2),
        ]
        action, answer, _, _ = cpn._dispose("original", "query", claims, 0.7, [])
        assert action == "filter"
        assert "根据现有资料" in answer
        assert "supported claim" in answer
        assert "unsupported claim" not in answer

    def test_regenerate_threshold(self):
        docs = [{"content": " doc ", "score": 0.9, "relevance": "relevant"}]
        cpn = make_component()
        claims = [Claim(text="x", score=0.4)]
        action, answer, ctx, prompt = cpn._dispose("original", "query", claims, 0.4, docs)
        assert action == "regenerate"
        assert len(ctx) == 1
        assert "ONLY the provided documents" in prompt

    def test_refuse_threshold(self):
        cpn = make_component()
        claims = [Claim(text="x", score=0.0)]
        action, answer, _, _ = cpn._dispose("original", "query", claims, 0.2, [])
        assert action == "refuse"
        assert "抱歉" in answer


class TestComponentInvocation:
    def test_component_runs_with_supported_facts(self):
        globals_dict = {
            "sys.query": "Q3营收情况如何",
            "sys.answer_with_citations": "2024年Q3营收增长15.3%。",
            "sys.retrieved_docs": [{"content": "公司2024年第三季度营收同比增长15.3%。"}],
        }
        cpn = make_component(globals_dict=globals_dict)
        result = cpn.invoke()
        assert result["is_hallucination"] is False
        assert result["faithfulness_score"] >= 0.85
        assert result["hallucination_count"] == 0
        assert result["action"] == "pass"

    def test_component_detects_hallucination(self):
        globals_dict = {
            "sys.query": "Q3营收情况如何",
            "sys.answer_with_citations": "2024年Q3营收增长15.3%。",
            "sys.retrieved_docs": [{"content": "公司2024年Q3营收增长12.1%。"}],
        }
        cpn = make_component(globals_dict=globals_dict)
        result = cpn.invoke()
        assert result["is_hallucination"] is True
        assert result["hallucination_count"] >= 1
        assert result["action"] in {"filter", "refuse"}

    def test_component_handles_empty_answer(self):
        globals_dict = {
            "sys.query": "q",
            "sys.answer_with_citations": "",
            "sys.retrieved_docs": [],
        }
        cpn = make_component(globals_dict=globals_dict)
        result = cpn.invoke()
        assert result["is_hallucination"] is False
        assert result["faithfulness_score"] == 1.0
        assert result["action"] == "pass"


class TestInputCoercion:
    def test_coerce_docs_from_json_string(self):
        cpn = make_component()
        docs = cpn._coerce_docs('[{"content": "hello"}]')
        assert docs == [{"content": "hello"}]

    def test_coerce_docs_from_plain_string(self):
        cpn = make_component()
        docs = cpn._coerce_docs("hello world")
        assert docs == [{"content": "hello world"}]

    def test_parse_verification_answer_json(self):
        cpn = make_component()
        ans = '{"nli": "entailment", "llm": "SUPPORTED", "evidence": "doc1"}'
        parsed = cpn._parse_verification_answer(ans)
        assert parsed["nli"] == "entailment"
        assert parsed["llm"] == "SUPPORTED"
        assert parsed["evidence"] == "doc1"

    def test_parse_verification_answer_markdown(self):
        cpn = make_component()
        ans = '```json\n{"nli": "contradiction", "llm": "CONTRADICTED"}\n```'
        parsed = cpn._parse_verification_answer(ans)
        assert parsed["nli"] == "contradiction"
        assert parsed["llm"] == "CONTRADICTED"
