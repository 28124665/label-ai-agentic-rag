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
"""hybrid 串行路由：RAG 完成后必须进入 db_tool。"""

import unittest

from agent.langgraph.nodes.intent_router import after_rag_tool, route_decision
from agent.langgraph.state import AgentState


class TestHybridRouting(unittest.TestCase):
    """测试 hybrid 模式串行 RAG → DB。"""

    def test_route_decision_hybrid_starts_at_rag(self):
        """hybrid 入口仍先走 rag_tool。"""
        state: AgentState = {"route_target": "hybrid"}
        self.assertEqual(route_decision(state), "rag_tool")

    def test_after_rag_hybrid_goes_to_db(self):
        """rag_tool 完成后，hybrid 必须进入 db_tool。"""
        state: AgentState = {"route_target": "hybrid"}
        self.assertEqual(after_rag_tool(state), "db_tool")

    def test_after_rag_non_hybrid_goes_to_quality_check(self):
        """非 hybrid 的 rag 完成后直接 quality_check。"""
        for target in ("rag", "database", "chitchat", "web"):
            state: AgentState = {"route_target": target}
            self.assertEqual(
                after_rag_tool(state),
                "quality_check",
                msg=f"route_target={target}",
            )


if __name__ == "__main__":
    unittest.main()
