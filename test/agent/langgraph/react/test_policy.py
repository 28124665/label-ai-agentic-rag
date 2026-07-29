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
"""Policy Guard 单元测试。

覆盖：
- 工具白名单检查
- DB 工具危险 SQL 拦截
- DB 工具 readonly 写操作拦截
- DB 工具 LIMIT 检查（needs_approval）
- Web 工具 URL 注入拦截
- Web 工具内网地址拦截
- RAG 工具知识库白名单
- finish / ask_clarification 始终允许
"""
import pytest

from agent.langgraph.react.policy import (
    DEFAULT_ALLOWED_TOOLS,
    PolicyContext,
    PolicyDecisionType,
    PolicyGuard,
)


class TestPolicyGuardWhitelist:
    """白名单校验测试。"""

    def test_finish_always_allowed(self):
        """finish 始终允许。"""
        guard = PolicyGuard()
        ctx = PolicyContext(action_type="finish", arguments={})
        result = guard.evaluate(ctx)
        assert result.decision == PolicyDecisionType.ALLOW

    def test_ask_clarification_always_allowed(self):
        """ask_clarification 始终允许。"""
        guard = PolicyGuard()
        ctx = PolicyContext(action_type="ask_clarification", arguments={"question": "q"})
        result = guard.evaluate(ctx)
        assert result.decision == PolicyDecisionType.ALLOW

    def test_tool_not_in_whitelist_denied(self):
        """不在白名单的工具被拒绝。"""
        guard = PolicyGuard(agent_config={"react": {"allowed_tools": ["rag_search"]}})
        ctx = PolicyContext(action_type="db_query", arguments={"query": "SELECT 1"})
        result = guard.evaluate(ctx)
        assert result.decision == PolicyDecisionType.DENY
        assert "db_query" in result.reason

    def test_tool_in_whitelist_proceeds(self):
        """在白名单的工具进入专项检查。"""
        guard = PolicyGuard(agent_config={"react": {"allowed_tools": ["rag_search"]}})
        ctx = PolicyContext(action_type="rag_search", arguments={"query": "test"})
        result = guard.evaluate(ctx)
        assert result.decision == PolicyDecisionType.ALLOW


class TestPolicyGuardDBQuery:
    """DB 工具策略测试。"""

    def test_dangerous_sql_keyword_denied(self):
        """包含高风险关键词的 SQL 被拒绝。"""
        guard = PolicyGuard()
        ctx = PolicyContext(
            action_type="db_query",
            arguments={"sql": "DROP TABLE users"},
        )
        result = guard.evaluate(ctx)
        assert result.decision == PolicyDecisionType.DENY
        assert result.risk_level == "high"

    def test_readonly_blocks_insert(self):
        """readonly 模式禁止 INSERT。"""
        guard = PolicyGuard(
            agent_config={"react": {"policy": {"db_query": {"readonly": True}}}}
        )
        ctx = PolicyContext(
            action_type="db_query",
            arguments={"sql": "INSERT INTO users VALUES (1)"},
        )
        result = guard.evaluate(ctx)
        assert result.decision == PolicyDecisionType.DENY
        # 既可能命中高风险关键词，也可能命中 readonly 写操作检查
        assert "readonly" in result.reason or "高风险" in result.reason

    def test_readonly_blocks_update(self):
        """readonly 模式禁止 UPDATE。"""
        guard = PolicyGuard(
            agent_config={"react": {"policy": {"db_query": {"readonly": True}}}}
        )
        ctx = PolicyContext(
            action_type="db_query",
            arguments={"sql": "UPDATE users SET name = 'x'"},
        )
        result = guard.evaluate(ctx)
        assert result.decision == PolicyDecisionType.DENY

    def test_select_without_limit_needs_approval(self):
        """SELECT 缺少 LIMIT 需要审批。"""
        guard = PolicyGuard()
        ctx = PolicyContext(
            action_type="db_query",
            arguments={"sql": "SELECT * FROM users"},
        )
        result = guard.evaluate(ctx)
        assert result.decision == PolicyDecisionType.NEEDS_APPROVAL
        assert result.requires_approval is True

    def test_select_with_limit_allowed(self):
        """SELECT 包含 LIMIT 通过校验。"""
        guard = PolicyGuard()
        ctx = PolicyContext(
            action_type="db_query",
            arguments={"sql": "SELECT * FROM users LIMIT 10"},
        )
        result = guard.evaluate(ctx)
        assert result.decision == PolicyDecisionType.ALLOW

    def test_db_id_not_in_whitelist_denied(self):
        """db_id 不在白名单被拒绝。"""
        guard = PolicyGuard(
            agent_config={
                "react": {
                    "policy": {"db_query": {"allowed_db_ids": ["db_a"]}}
                }
            }
        )
        ctx = PolicyContext(
            action_type="db_query",
            arguments={"sql": "SELECT 1 LIMIT 1", "db_id": "db_b"},
        )
        result = guard.evaluate(ctx)
        assert result.decision == PolicyDecisionType.DENY


class TestPolicyGuardWebSearch:
    """Web 工具策略测试。"""

    def test_empty_query_denied(self):
        """空 query 被拒绝。"""
        guard = PolicyGuard()
        ctx = PolicyContext(action_type="web_search", arguments={"query": ""})
        result = guard.evaluate(ctx)
        assert result.decision == PolicyDecisionType.DENY

    def test_javascript_injection_denied(self):
        """javascript: 注入被拒绝。"""
        guard = PolicyGuard()
        ctx = PolicyContext(
            action_type="web_search",
            arguments={"query": "javascript:alert(1)"},
        )
        result = guard.evaluate(ctx)
        assert result.decision == PolicyDecisionType.DENY
        assert result.risk_level == "high"

    def test_private_ip_denied(self):
        """内网 IP 访问被拒绝。"""
        guard = PolicyGuard()
        ctx = PolicyContext(
            action_type="web_search",
            arguments={"query": "visit 192.168.1.1"},
        )
        result = guard.evaluate(ctx)
        assert result.decision == PolicyDecisionType.DENY

    def test_normal_query_allowed(self):
        """正常 query 通过校验。"""
        guard = PolicyGuard()
        ctx = PolicyContext(
            action_type="web_search",
            arguments={"query": "Python 教程"},
        )
        result = guard.evaluate(ctx)
        assert result.decision == PolicyDecisionType.ALLOW


class TestPolicyGuardRAGSearch:
    """RAG 工具策略测试。"""

    def test_empty_query_needs_clarification(self):
        """空 query 需要澄清。"""
        guard = PolicyGuard()
        ctx = PolicyContext(action_type="rag_search", arguments={"query": ""})
        result = guard.evaluate(ctx)
        assert result.decision == PolicyDecisionType.NEEDS_CLARIFICATION

    def test_kb_id_not_in_whitelist_denied(self):
        """kb_id 不在白名单被拒绝。"""
        guard = PolicyGuard(
            agent_config={
                "react": {
                    "policy": {"rag_search": {"allowed_kb_ids": ["kb_a"]}}
                }
            }
        )
        ctx = PolicyContext(
            action_type="rag_search",
            arguments={"query": "test", "kb_ids": ["kb_b"]},
        )
        result = guard.evaluate(ctx)
        assert result.decision == PolicyDecisionType.DENY

    def test_normal_rag_query_allowed(self):
        """正常 RAG query 通过。"""
        guard = PolicyGuard()
        ctx = PolicyContext(
            action_type="rag_search",
            arguments={"query": "质量异常处理规范"},
        )
        result = guard.evaluate(ctx)
        assert result.decision == PolicyDecisionType.ALLOW


class TestPolicyGuardCustomConfig:
    """Policy Guard 自定义配置测试。"""

    def test_default_allowed_tools(self):
        """默认白名单含三个核心工具。"""
        guard = PolicyGuard()
        for tool in ("rag_search", "db_query", "web_search"):
            assert tool in guard.allowed_tools

    def test_custom_allowed_tools_override(self):
        """自定义白名单覆盖默认。"""
        guard = PolicyGuard(agent_config={"react": {"allowed_tools": ["rag_search"]}})
        assert "rag_search" in guard.allowed_tools
        assert "db_query" not in guard.allowed_tools
