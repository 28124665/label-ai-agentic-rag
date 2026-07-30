"""意图路由三层递进架构集成测试。

测试三层路由的完整流程：
- 第0层：前置过滤（安全拦截、显式指令、问候语、实体格式）
- 第1层：规则路由（范围词、词库、置信度、时效性）
- 第2层：LLM 语义路由（触发条件、结构化输出、降级机制）
- 第3层：Planner 复杂任务规划（任务拆解、并行标记）
"""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from agent.langgraph.routers.config_loader import load_config
from agent.langgraph.routers.llm_router import LLMRouter
from agent.langgraph.routers.models import RouteDecision
from agent.langgraph.routers.planner import Planner
from agent.langgraph.routers.pre_filter import PreFilter
from agent.langgraph.routers.rule_router import RuleRouter
from agent.langgraph.state import AgentState


class TestPreFilter:
    """测试第0层前置过滤器。"""

    def setup_method(self):
        """初始化测试环境。"""
        self.pre_filter = PreFilter()

    def test_sql_injection_detection(self):
        """测试 SQL 注入检测。"""
        malicious_queries = [
            "'; DROP TABLE users; --",
            "SELECT * FROM users WHERE id=1 UNION SELECT * FROM passwords",
            "1' OR '1'='1",
            "admin'--",
        ]
        
        for query in malicious_queries:
            decision = self.pre_filter.filter(query)
            assert decision is not None, f"Should detect SQL injection: {query}"
            assert decision.target == "chitchat"
            assert decision.confidence == 1.0
            assert decision.source == "prefilter"
            assert "安全拦截" in decision.reason

    def test_privilege_escalation_detection(self):
        """测试越权关键词检测。"""
        queries = [
            "删除所有用户数据",
            "获取管理员密码",
            "DROP DATABASE production",
        ]
        
        for query in queries:
            decision = self.pre_filter.filter(query)
            assert decision is not None, f"Should detect privilege escalation: {query}"
            assert decision.target == "chitchat"
            assert decision.confidence == 1.0
            assert "安全拦截" in decision.reason

    def test_explicit_external_search(self):
        """测试显式外部搜索指令。"""
        queries = [
            "百度搜索最新新闻",
            "谷歌搜索 Python 教程",
            "在网上查找",
        ]
        
        for query in queries:
            decision = self.pre_filter.filter(query)
            assert decision is not None, f"Should detect external search: {query}"
            assert decision.target == "web"
            assert decision.confidence == 1.0
            assert "显式外部搜索" in decision.reason

    def test_greeting_detection(self):
        """测试问候语检测。"""
        greetings = [
            "你好",
            "您好",
            "hello",
            "hi",
            "嗨",
        ]
        
        for greeting in greetings:
            decision = self.pre_filter.filter(greeting)
            assert decision is not None, f"Should detect greeting: {greeting}"
            assert decision.target == "chitchat"
            assert decision.confidence == 1.0
            assert "问候语" in decision.reason

    def test_entity_format_matching(self):
        """测试实体格式匹配。"""
        queries = [
            "SKU-12345",
            "ORD-67890",
            "工单号：123456",
            "订单号：789012",
        ]
        
        for query in queries:
            decision = self.pre_filter.filter(query)
            assert decision is not None, f"Should match entity format: {query}"
            assert decision.target == "database"
            assert decision.confidence == 1.0
            assert "实体格式" in decision.reason

    def test_no_match_returns_none(self):
        """测试无匹配时返回 None。"""
        normal_queries = [
            "Q3 华东区总产量",
            "数据治理是什么",
            "统计本月销售额",
        ]
        
        for query in normal_queries:
            decision = self.pre_filter.filter(query)
            assert decision is None, f"Should not match pre-filter: {query}"


class TestRuleRouter:
    """测试第1层规则路由器。"""

    def setup_method(self):
        """初始化测试环境。"""
        self.config = load_config()
        self.rule_router = RuleRouter(self.config)

    def test_database_intent_with_aggregation(self):
        """测试包含聚合词的数据库意图。"""
        queries = [
            "Q3 华东区总产量",
            "统计本月销售额",
            "计算平均订单金额",
            "汇总各部门业绩",
        ]
        
        for query in queries:
            decision = self.rule_router.route(query)
            assert decision.target == "database", f"Should route to database: {query}"
            assert decision.confidence >= 0.7, f"Confidence should be >= 0.7: {query}"
            assert decision.source == "rule"

    def test_rag_intent_with_concept(self):
        """测试包含概念词的 RAG 意图。"""
        queries = [
            "数据治理是什么",
            "如何配置系统",
            "解释一下 RAG 原理",
            "为什么需要检索增强",
        ]
        
        for query in queries:
            decision = self.rule_router.route(query)
            assert decision.target == "rag", f"Should route to rag: {query}"
            assert decision.confidence >= 0.7, f"Confidence should be >= 0.7: {query}"
            assert decision.source == "rule"

    def test_external_scope_detection(self):
        """测试外部范围词检测。"""
        queries = [
            "网上搜索最新新闻",
            "百度一下 Python 教程",
            "谷歌搜索",
        ]
        
        for query in queries:
            decision = self.rule_router.route(query)
            assert decision.target == "web", f"Should route to web: {query}"
            assert decision.confidence >= 0.8, f"Confidence should be >= 0.8: {query}"

    def test_freshness_detection(self):
        """测试时效性检测。"""
        queries = [
            "今天天气怎么样",
            "最新新闻",
            "实时数据",
        ]
        
        for query in queries:
            decision = self.rule_router.route(query)
            assert decision.target == "web", f"Should route to web: {query}"
            assert decision.confidence >= 0.75, f"Confidence should be >= 0.75: {query}"
            assert decision.metadata.get("freshness_required") is True

    def test_hybrid_intent(self):
        """测试混合意图。"""
        queries = [
            "对比 A 产线和 B 产线的 OEE，并分析差异原因",
            "查询销售数据并解释趋势",
        ]
        
        for query in queries:
            decision = self.rule_router.route(query)
            # 混合意图可能路由到 hybrid 或根据主要意图路由
            assert decision.target in ["hybrid", "database", "rag"], f"Should route to hybrid or main intent: {query}"
            assert decision.source == "rule"

    def test_low_confidence_triggers_llm(self):
        """测试低置信度触发 LLM 路由。"""
        # 模糊查询应该产生低置信度
        query = "这个怎么办"
        decision = self.rule_router.route(query)
        # 置信度应该 < 0.7，触发 LLM 路由
        assert decision.confidence < 0.7, f"Low confidence should trigger LLM: {query}"


class TestLLMRouter:
    """测试第2层 LLM 语义路由器。"""

    def setup_method(self):
        """初始化测试环境。"""
        self.config = load_config()
        self.llm_router = LLMRouter(self.config)

    @pytest.mark.asyncio
    async def test_llm_routing_with_mock(self):
        """测试 LLM 路由（Mock LLM 调用）。"""
        query = "数据治理是什么"
        rule_decision = RouteDecision(
            target="rag",
            confidence=0.65,  # 低置信度，触发 LLM
            source="rule",
            reason="包含概念词",
            complexity="simple",
            metadata={},
        )
        
        # Mock LLM 调用
        with patch.object(LLMRouter, '_call_llm', new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {
                "primary_intent": "rag",
                "confidence": 0.92,
                "reason": "询问概念定义",
                "complexity": "simple",
                "sub_intents": ["definition"],
                "entities": {"concept": "数据治理"},
                "needs_clarification": False,
                "clarification_question": "",
            }
            
            decision = await self.llm_router.route(query, rule_decision)
            assert decision.target == "rag"
            assert decision.confidence == 0.92
            assert decision.source == "llm"
            assert "概念定义" in decision.reason

    @pytest.mark.asyncio
    async def test_llm_timeout_fallback(self):
        """测试 LLM 超时降级。"""
        query = "数据治理是什么"
        rule_decision = RouteDecision(
            target="rag",
            confidence=0.65,
            source="rule",
            reason="包含概念词",
            complexity="simple",
            metadata={},
        )
        
        # Mock LLM 超时
        with patch.object(LLMRouter, '_call_llm', new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = asyncio.TimeoutError()
            
            decision = await self.llm_router.route(query, rule_decision)
            # 应该降级到规则路由结果
            assert decision.target == rule_decision.target
            assert decision.source == "rule"

    @pytest.mark.asyncio
    async def test_circuit_breaker(self):
        """测试熔断器机制。"""
        query = "数据治理是什么"
        rule_decision = RouteDecision(
            target="rag",
            confidence=0.65,
            source="rule",
            reason="包含概念词",
            complexity="simple",
            metadata={},
        )
        
        # Mock LLM 连续失败
        with patch.object(LLMRouter, '_call_llm', new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = Exception("LLM error")
            
            # 连续失败 3 次
            for _ in range(3):
                await self.llm_router.route(query, rule_decision)
            
            # 第 4 次应该触发熔断，直接返回规则路由结果
            decision = await self.llm_router.route(query, rule_decision)
            assert decision.target == rule_decision.target
            assert decision.source == "rule"

    @pytest.mark.asyncio
    async def test_confidence_thresholds(self):
        """测试置信度阈值判断。"""
        query = "测试查询"
        
        # 测试高置信度（>= 0.75）直接采纳
        with patch.object(LLMRouter, '_call_llm', new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {
                "primary_intent": "rag",
                "confidence": 0.85,
                "reason": "测试",
                "complexity": "simple",
                "sub_intents": [],
                "entities": {},
                "needs_clarification": False,
                "clarification_question": "",
            }
            
            rule_decision = RouteDecision(
                target="rag",
                confidence=0.65,
                source="rule",
                reason="",
                complexity="simple",
                metadata={},
            )
            
            decision = await self.llm_router.route(query, rule_decision)
            assert decision.confidence == 0.85
            assert "low_confidence_label" not in decision.metadata

        # 测试中等置信度（0.6-0.75）附标签
        with patch.object(LLMRouter, '_call_llm', new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {
                "primary_intent": "rag",
                "confidence": 0.70,
                "reason": "测试",
                "complexity": "simple",
                "sub_intents": [],
                "entities": {},
                "needs_clarification": False,
                "clarification_question": "",
            }
            
            decision = await self.llm_router.route(query, rule_decision)
            assert decision.confidence == 0.70
            assert decision.metadata.get("low_confidence_label") is True

        # 测试低置信度（< 0.6）触发澄清
        with patch.object(LLMRouter, '_call_llm', new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {
                "primary_intent": "rag",
                "confidence": 0.50,
                "reason": "测试",
                "complexity": "simple",
                "sub_intents": [],
                "entities": {},
                "needs_clarification": True,
                "clarification_question": "您是想查询数据还是了解概念定义？",
            }
            
            decision = await self.llm_router.route(query, rule_decision)
            assert decision.confidence == 0.50
            assert decision.metadata.get("needs_clarification") is True


class TestPlanner:
    """测试第3层 Planner 复杂任务规划器。"""

    def setup_method(self):
        """初始化测试环境。"""
        self.config = load_config()
        self.planner = Planner(self.config)

    @pytest.mark.asyncio
    async def test_complex_task_planning(self):
        """测试复杂任务规划。"""
        query = "对比 A 产线和 B 产线的 OEE，并分析差异原因"
        llm_decision = RouteDecision(
            target="hybrid",
            confidence=0.85,
            source="llm",
            reason="混合意图",
            complexity="complex",
            metadata={
                "sub_intents": ["database", "rag"],
                "entities": {"metric": "OEE", "lines": ["A", "B"]},
            },
        )
        
        # Mock Planner 调用
        with patch.object(Planner, '_call_planner', new_callable=AsyncMock) as mock_call:
            from agent.langgraph.routers.models import ExecutionPlan, PlanStep
            
            mock_plan = ExecutionPlan(
                plan_id="plan_001",
                steps=[
                    PlanStep(
                        step_id="step1",
                        tool="database",
                        args={"query": "A产线OEE数据"},
                        depends_on=[],
                        can_parallel=True,
                    ),
                    PlanStep(
                        step_id="step2",
                        tool="database",
                        args={"query": "B产线OEE数据"},
                        depends_on=[],
                        can_parallel=True,
                    ),
                    PlanStep(
                        step_id="step3",
                        tool="rag",
                        args={"query": "OEE差异原因分析"},
                        depends_on=["step1", "step2"],
                        can_parallel=False,
                    ),
                ],
                estimated_tokens=2000,
                fallback_strategy="sequential",
            )
            mock_call.return_value = mock_plan
            
            decision = await self.planner.plan(query, llm_decision)
            assert decision.target == "hybrid"
            assert decision.source == "planner"
            assert "plan" in decision.metadata
            assert len(decision.metadata["plan"]["steps"]) == 3

    @pytest.mark.asyncio
    async def test_planner_timeout_fallback(self):
        """测试 Planner 超时降级。"""
        query = "对比 A 产线和 B 产线的 OEE，并分析差异原因"
        llm_decision = RouteDecision(
            target="hybrid",
            confidence=0.85,
            source="llm",
            reason="混合意图",
            complexity="complex",
            metadata={},
        )
        
        # Mock Planner 超时
        with patch.object(Planner, '_call_planner', new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = asyncio.TimeoutError()
            
            decision = await self.planner.plan(query, llm_decision)
            # 应该降级到 LLM 路由结果
            assert decision.target == llm_decision.target
            assert decision.source == "llm"
            assert decision.metadata.get("planner_timeout") is True

    @pytest.mark.asyncio
    async def test_planner_parallel_marking(self):
        """测试并行标记识别。"""
        query = "对比 A 产线和 B 产线的 OEE"
        llm_decision = RouteDecision(
            target="hybrid",
            confidence=0.85,
            source="llm",
            reason="混合意图",
            complexity="complex",
            metadata={
                "sub_intents": ["database"],
                "entities": {"lines": ["A", "B"]},
            },
        )
        
        # Mock Planner 返回包含并行标记的计划
        with patch.object(Planner, '_call_planner', new_callable=AsyncMock) as mock_call:
            from agent.langgraph.routers.models import ExecutionPlan, PlanStep
            
            mock_plan = ExecutionPlan(
                plan_id="plan_parallel",
                steps=[
                    PlanStep(
                        step_id="step1",
                        tool="database",
                        args={"query": "A产线OEE"},
                        depends_on=[],
                        can_parallel=True,  # 无依赖，可并行
                    ),
                    PlanStep(
                        step_id="step2",
                        tool="database",
                        args={"query": "B产线OEE"},
                        depends_on=[],
                        can_parallel=True,  # 无依赖，可并行
                    ),
                    PlanStep(
                        step_id="step3",
                        tool="rag",
                        args={"query": "对比分析"},
                        depends_on=["step1", "step2"],
                        can_parallel=False,  # 有依赖，不可并行
                    ),
                ],
                estimated_tokens=1500,
                fallback_strategy="sequential",
            )
            mock_call.return_value = mock_plan
            
            decision = await self.planner.plan(query, llm_decision)
            
            # 验证计划中的并行标记
            plan = decision.metadata["plan"]
            assert plan["steps"][0]["can_parallel"] is True
            assert plan["steps"][1]["can_parallel"] is True
            assert plan["steps"][2]["can_parallel"] is False
            
            # 验证依赖关系
            assert plan["steps"][2]["depends_on"] == ["step1", "step2"]

    @pytest.mark.asyncio
    async def test_planner_fallback_on_exception(self):
        """测试 Planner 异常降级。"""
        query = "复杂任务"
        llm_decision = RouteDecision(
            target="hybrid",
            confidence=0.80,
            source="llm",
            reason="混合意图",
            complexity="complex",
            metadata={},
        )
        
        # Mock Planner 抛出异常
        with patch.object(Planner, '_call_planner', new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = Exception("Planner error")
            
            decision = await self.planner.plan(query, llm_decision)
            
            # 应该降级到 LLM 路由结果
            assert decision.target == llm_decision.target
            assert decision.source == "llm"
            assert decision.metadata.get("planner_failed") is True

    def test_determine_target_single_tool(self):
        """测试 _determine_target 方法：单工具场景。"""
        from agent.langgraph.routers.models import ExecutionPlan, PlanStep
        
        plan = ExecutionPlan(
            plan_id="plan_single",
            steps=[
                PlanStep(
                    step_id="step1",
                    tool="database",
                    args={"query": "查询数据"},
                    depends_on=[],
                    can_parallel=False,
                ),
            ],
            estimated_tokens=500,
            fallback_strategy="sequential",
        )
        
        target = self.planner._determine_target(plan)
        assert target == "database"

    def test_determine_target_multiple_tools(self):
        """测试 _determine_target 方法：多工具场景。"""
        from agent.langgraph.routers.models import ExecutionPlan, PlanStep
        
        plan = ExecutionPlan(
            plan_id="plan_multi",
            steps=[
                PlanStep(
                    step_id="step1",
                    tool="database",
                    args={"query": "查询数据"},
                    depends_on=[],
                    can_parallel=True,
                ),
                PlanStep(
                    step_id="step2",
                    tool="rag",
                    args={"query": "检索知识"},
                    depends_on=[],
                    can_parallel=True,
                ),
                PlanStep(
                    step_id="step3",
                    tool="web",
                    args={"query": "搜索外部信息"},
                    depends_on=["step1", "step2"],
                    can_parallel=False,
                ),
            ],
            estimated_tokens=2000,
            fallback_strategy="sequential",
        )
        
        target = self.planner._determine_target(plan)
        # 使用了多个不同工具，应该返回 hybrid
        assert target == "hybrid"

    def test_determine_target_no_steps(self):
        """测试 _determine_target 方法：无步骤场景。"""
        from agent.langgraph.routers.models import ExecutionPlan
        
        plan = ExecutionPlan(
            plan_id="plan_empty",
            steps=[],
            estimated_tokens=0,
            fallback_strategy="sequential",
        )
        
        target = self.planner._determine_target(plan)
        # 无步骤时默认返回 chitchat
        assert target == "chitchat"


class TestBadCases:
    """测试 Bad Case 修复。"""

    def setup_method(self):
        """初始化测试环境。"""
        self.config = load_config()
        self.pre_filter = PreFilter()
        self.rule_router = RuleRouter(self.config)

    def test_data_governance_definition(self):
        """测试 Bad Case: '数据治理是什么' 应该路由到 rag。"""
        query = "数据治理是什么"
        
        # 第0层：前置过滤
        pre_decision = self.pre_filter.filter(query)
        assert pre_decision is None, "Should not match pre-filter"
        
        # 第1层：规则路由
        rule_decision = self.rule_router.route(query)
        assert rule_decision.target == "rag", f"Should route to rag: {query}"
        assert rule_decision.confidence >= 0.7, f"Confidence should be >= 0.7: {query}"

    def test_today_weather(self):
        """测试 Bad Case: '今天天气怎么样' 应该路由到 web。"""
        query = "今天天气怎么样"
        
        # 第0层：前置过滤
        pre_decision = self.pre_filter.filter(query)
        assert pre_decision is None, "Should not match pre-filter"
        
        # 第1层：规则路由
        rule_decision = self.rule_router.route(query)
        assert rule_decision.target == "web", f"Should route to web: {query}"
        assert rule_decision.confidence >= 0.75, f"Confidence should be >= 0.75: {query}"
        assert rule_decision.metadata.get("freshness_required") is True

    def test_recent_quality_improvement(self):
        """测试 Bad Case: '最近质量异常有改善吗' 应该路由到 database。"""
        query = "最近质量异常有改善吗"
        
        # 第0层：前置过滤
        pre_decision = self.pre_filter.filter(query)
        assert pre_decision is None, "Should not match pre-filter"
        
        # 第1层：规则路由
        rule_decision = self.rule_router.route(query)
        # 应该路由到 database（查询数据）
        assert rule_decision.target == "database", f"Should route to database: {query}"

    def test_compare_production_lines(self):
        """测试 Bad Case: '对比 A 产线和 B 产线的 OEE' 应该路由到 hybrid。"""
        query = "对比 A 产线和 B 产线的 OEE"
        
        # 第0层：前置过滤
        pre_decision = self.pre_filter.filter(query)
        assert pre_decision is None, "Should not match pre-filter"
        
        # 第1层：规则路由
        rule_decision = self.rule_router.route(query)
        # 混合意图可能路由到 hybrid 或根据主要意图路由
        assert rule_decision.target in ["hybrid", "database"], f"Should route to hybrid or database: {query}"


class TestPerformance:
    """测试性能指标。"""

    def setup_method(self):
        """初始化测试环境。"""
        self.config = load_config()
        self.pre_filter = PreFilter()
        self.rule_router = RuleRouter(self.config)

    def test_pre_filter_latency(self):
        """测试第0层前置过滤延迟 < 10ms。"""
        queries = [
            "'; DROP TABLE users; --",
            "你好",
            "SKU-12345",
            "百度搜索最新新闻",
        ]
        
        start_time = time.time()
        for query in queries:
            self.pre_filter.filter(query)
        elapsed_ms = (time.time() - start_time) * 1000
        
        # 平均延迟应该 < 10ms
        avg_latency = elapsed_ms / len(queries)
        assert avg_latency < 10, f"Pre-filter latency should be < 10ms, got {avg_latency:.2f}ms"

    def test_rule_router_latency(self):
        """测试第1层规则路由延迟 < 10ms。"""
        queries = [
            "Q3 华东区总产量",
            "数据治理是什么",
            "今天天气怎么样",
            "对比 A 产线和 B 产线的 OEE",
        ]
        
        start_time = time.time()
        for query in queries:
            self.rule_router.route(query)
        elapsed_ms = (time.time() - start_time) * 1000
        
        # 平均延迟应该 < 10ms
        avg_latency = elapsed_ms / len(queries)
        assert avg_latency < 10, f"Rule router latency should be < 10ms, got {avg_latency:.2f}ms"


class TestConfigHotReload:
    """测试配置热更新。"""

    def test_config_hot_reload(self):
        """测试配置热更新无需重启。"""
        import os
        import tempfile
        
        # 创建临时配置文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("""
rule_router:
  keywords:
    db_bias:
      - 测试关键词
""")
            temp_path = f.name
        
        try:
            # 加载配置
            from agent.langgraph.routers.config_loader import ConfigLoader
            loader = ConfigLoader(temp_path)
            config1 = loader.load()
            
            # 修改配置文件
            with open(temp_path, 'w') as f:
                f.write("""
rule_router:
  keywords:
    db_bias:
      - 新关键词
""")
            
            # 重新加载配置
            config2 = loader.load()
            
            # 配置应该已更新（使用字典访问方式）
            db_bias1 = config1.rule_router.get("keywords", {}).get("db_bias", [])
            db_bias2 = config2.rule_router.get("keywords", {}).get("db_bias", [])
            assert db_bias1 != db_bias2
        finally:
            os.unlink(temp_path)


class TestObservability:
    """测试可观测性。"""

    @pytest.mark.asyncio
    async def test_quality_report_attaches_resolved_skill_set_and_plan(self):
        from agent.langgraph.nodes.intent_router import intent_router_node

        routed = RouteDecision(
            target="hybrid",
            confidence=0.9,
            source="rule",
            reason="质量报告",
            complexity="complex",
            metadata={"report_type": "quality_analysis"},
        )
        planned = RouteDecision(
            target="hybrid",
            confidence=0.9,
            source="planner",
            reason="Skill planner generated execution plan",
            complexity="complex",
            metadata={
                "plan": {
                    "plan_id": "quality_plan",
                    "steps": [],
                    "estimated_tokens": 0,
                    "fallback_strategy": "sequential",
                }
            },
        )
        rule_router = type(
            "RuleRouterStub", (), {"route": lambda self, question: routed}
        )()
        planner = type(
            "PlannerStub", (), {"plan_with_skills": AsyncMock(return_value=planned)}
        )()

        with (
            patch(
                "agent.langgraph.nodes.intent_router.get_rule_router",
                return_value=rule_router,
            ),
            patch(
                "agent.langgraph.nodes.intent_router.get_planner",
                return_value=planner,
            ),
        ):
            result = await intent_router_node(
                {
                    "user_question": "请生成本月质量分析报告",
                    "tenant_id": "tenant_001",
                    "query_lang": "zh_CN",
                }
            )

        assert result["skill_set"]["report_skill"]["skill_id"] == "quality_report"
        assert result["skill_resolution"]["reason"] == "RESOLVED"
        assert result["skill_evidence_requirements"]
        assert result["route_decision"].metadata["plan"]["plan_id"] == "quality_plan"
        assert result["execution_plan"].plan_id == "quality_plan"
        planner.plan_with_skills.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_route_decision_logging(self):
        """测试路由决策日志完整性。"""
        from agent.langgraph.nodes.intent_router import intent_router_node
        
        state: AgentState = {
            "user_question": "Q3 华东区总产量",
            "query_lang": "zh_CN",
            "tenant_id": "test_tenant",
            "llm_id": "test_llm",
            "kb_ids": ["test_kb"],
        }
        
        # 执行路由节点
        result = await intent_router_node(state)
        
        # 检查路由决策信息
        assert "route_target" in result
        assert "route_decision" in result
        assert "node_timings" in result
        
        decision = result["route_decision"]
        assert decision.target == "database"
        assert decision.confidence >= 0.7
        assert decision.source == "rule"
        assert decision.reason != ""
        assert decision.complexity == "simple"


class TestAgentStateDefinition:
    """测试 AgentState 状态定义。"""

    def test_state_has_route_decision_field(self):
        """测试 AgentState 包含 route_decision 字段。"""
        from agent.langgraph.state import AgentState
        from agent.langgraph.routers.models import RouteDecision
        
        # 创建完整的状态
        state: AgentState = {
            "user_question": "测试查询",
            "query_lang": "zh_CN",
            "tenant_id": "test_tenant",
            "llm_id": "test_llm",
            "kb_ids": ["test_kb"],
            "route_target": "database",
            "route_decision": RouteDecision(
                target="database",
                confidence=0.85,
                source="rule",
                reason="包含聚合词",
                complexity="simple",
                metadata={},
            ),
        }
        
        # 验证字段存在
        assert "route_decision" in state
        assert isinstance(state["route_decision"], RouteDecision)
        assert state["route_decision"].target == "database"
        assert state["route_decision"].confidence == 0.85
        assert state["route_decision"].source == "rule"

    def test_state_backward_compatibility(self):
        """测试状态向后兼容性（保留 route_target）。"""
        from agent.langgraph.state import AgentState
        
        # 创建状态（同时包含 route_target 和 route_decision）
        state: AgentState = {
            "user_question": "测试查询",
            "query_lang": "zh_CN",
            "tenant_id": "test_tenant",
            "llm_id": "test_llm",
            "kb_ids": ["test_kb"],
            "route_target": "rag",
        }
        
        # 验证 route_target 字段仍然存在
        assert "route_target" in state
        assert state["route_target"] == "rag"

    def test_state_optional_route_decision(self):
        """测试 route_decision 字段是可选的。"""
        from agent.langgraph.state import AgentState
        
        # 创建状态（不包含 route_decision）
        state: AgentState = {
            "user_question": "测试查询",
            "query_lang": "zh_CN",
            "tenant_id": "test_tenant",
            "llm_id": "test_llm",
            "kb_ids": ["test_kb"],
            "route_target": "chitchat",
        }
        
        # 验证状态有效（route_decision 是可选的）
        assert "route_target" in state
        # route_decision 可能不存在
        assert state.get("route_decision") is None or isinstance(
            state.get("route_decision"), RouteDecision
        )


class TestPromptManager:
    """测试 Prompt 版本管理器。"""

    def test_load_prompt_template(self):
        """测试加载 Prompt 模板。"""
        from agent.langgraph.routers.prompt_manager import PromptManager
        
        manager = PromptManager()
        
        # 加载 intent_router v1 模板
        template = manager.load_prompt("intent_router", "v1")
        assert template is not None
        assert len(template) > 0
        assert "{query}" in template  # 应该包含变量占位符

    def test_render_prompt_template(self):
        """测试渲染 Prompt 模板。"""
        from agent.langgraph.routers.prompt_manager import PromptManager
        
        manager = PromptManager()
        
        # 渲染模板
        rendered = manager.render_prompt(
            "intent_router",
            "v1",
            query="测试查询"
        )
        
        assert "测试查询" in rendered
        assert "{query}" not in rendered  # 变量应该被替换

    def test_prompt_cache(self):
        """测试 Prompt 缓存机制。"""
        from agent.langgraph.routers.prompt_manager import PromptManager
        
        manager = PromptManager()
        
        # 第一次加载
        template1 = manager.load_prompt("intent_router", "v1")
        
        # 第二次加载（应该从缓存读取）
        template2 = manager.load_prompt("intent_router", "v1")
        
        assert template1 == template2

    def test_clear_cache(self):
        """测试清空缓存。"""
        from agent.langgraph.routers.prompt_manager import PromptManager
        
        manager = PromptManager()
        
        # 加载模板
        manager.load_prompt("intent_router", "v1")
        
        # 清空缓存
        manager.clear_cache()
        
        # 验证缓存已清空
        assert len(manager._cache) == 0

    def test_default_template_fallback(self):
        """测试默认模板回退。"""
        from agent.langgraph.routers.prompt_manager import PromptManager
        
        manager = PromptManager()
        
        # 加载不存在的模板
        template = manager.load_prompt("non_existent", "v1")
        
        # 应该返回默认模板
        assert template is not None
        assert len(template) > 0
