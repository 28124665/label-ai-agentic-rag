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
"""幻觉检测 exhausted 动作与 final_answer 对齐。"""

import unittest

from agent.langgraph.nodes.final_answer import final_answer_node
from agent.langgraph.nodes.hallucination import (
    MAX_REGENERATES,
    _dispose,
    hallucination_decision,
)
from agent.langgraph.state import AgentState


class TestHallucinationExhausted(unittest.TestCase):
    """测试 regenerate 用尽 → exhausted → 保守答案。"""

    def test_dispose_returns_exhausted_when_max_reached(self):
        """分数落在 regenerate 区间且次数用尽时返回 exhausted。"""
        score = 0.45  # REGENERATE_THRESHOLD <= score < FILTER_THRESHOLD
        self.assertEqual(_dispose(score, MAX_REGENERATES), "exhausted")

    def test_dispose_returns_regenerate_when_retries_remain(self):
        """次数未用尽时仍返回 regenerate。"""
        score = 0.45
        self.assertEqual(_dispose(score, 0), "regenerate")

    def test_decision_exhausted_routes_to_final_answer(self):
        """exhausted 路由到 final_answer（answer_output）。"""
        state: AgentState = {"hallucination_action": "exhausted"}
        self.assertEqual(hallucination_decision(state), "final_answer")

    def test_decision_regenerate_routes_to_prompt_assembly(self):
        """regenerate 仍回 prompt_assembly，不到 final_answer。"""
        state: AgentState = {"hallucination_action": "regenerate"}
        self.assertEqual(hallucination_decision(state), "prompt_assembly")

    def test_final_answer_exhausted_returns_conservative(self):
        """exhausted 时返回基于上下文的保守答案，而非拒答。"""
        state: AgentState = {
            "hallucination_action": "exhausted",
            "generated_answer": "编造的答案",
            "merged_context": "供应链管理是企业竞争力的核心。",
            "query_lang": "zh_CN",
        }
        result = final_answer_node(state)
        self.assertIn("根据现有参考资料", result["final_answer"])
        self.assertIn("供应链管理是企业竞争力的核心", result["final_answer"])
        self.assertNotIn("无法回答", result["final_answer"])

    def test_final_answer_reject_still_rejects(self):
        """reject 仍返回拒答文案。"""
        state: AgentState = {
            "hallucination_action": "reject",
            "generated_answer": "任意",
            "query_lang": "zh_CN",
        }
        result = final_answer_node(state)
        self.assertIn("无法回答", result["final_answer"])


if __name__ == "__main__":
    unittest.main()
