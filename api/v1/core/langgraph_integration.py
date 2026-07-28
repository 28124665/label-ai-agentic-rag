"""
LangGraph 状态图集成
实现智能问答流程的编排和状态管理

核心特性：
1. 根据 Agent 配置动态构建状态图（tools_config / routing_config / degradation_config）
2. 节点调用实际服务（RAGFlow 检索、数据源 SQL 执行等）
3. 通过事件回调机制将状态变化转换为 SSE 事件流
4. 支持质量检查和降级重试
"""

from typing import Dict, Any, List, Optional, TypedDict, Annotated, Callable, Awaitable
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
import logging
import json
import asyncio

logger = logging.getLogger(__name__)


# SSE 事件回调类型：async def callback(event_type: str, data: dict) -> None
EventCallback = Callable[[str, Dict[str, Any]], Awaitable[None]]


class AgentState(TypedDict):
    """Agent 状态定义"""
    # 消息历史
    messages: Annotated[List[BaseMessage], add_messages]

    # 用户信息
    user_id: str
    conversation_id: str

    # 路由决策
    route_target: Optional[str]  # "rag", "database", "web", "chitchat"

    # RAG 工具状态
    rag_query: Optional[str]
    rag_documents: Optional[List[Dict[str, Any]]]
    rag_quality_score: Optional[float]

    # 数据库工具状态
    db_sql: Optional[str]
    db_result: Optional[List[Dict[str, Any]]]
    db_quality_score: Optional[float]

    # Web 搜索状态
    web_query: Optional[str]
    web_results: Optional[List[Dict[str, Any]]]

    # 最终响应
    final_response: Optional[str]

    # 元数据（含重试计数、质量分数等）
    metadata: Dict[str, Any]


class AgenticRAGGraph:
    """
    Agentic RAG 状态图

    根据 Agent 配置动态构建：
    - tools_config.tools 决定启用哪些工具节点
    - routing_config.strategy 决定路由实现（keyword / llm / priority）
    - degradation_config 决定是否添加质量检查和降级边
    """

    def __init__(
        self,
        agent_config: Dict[str, Any],
        ragflow_client,
        db_service,
        knowledge_service,
        event_callback: Optional[EventCallback] = None
    ):
        """
        初始化状态图

        Args:
            agent_config: Agent 配置，包含 tools_config / routing_config / degradation_config
            ragflow_client: RAGFlow 客户端
            db_service: 数据源服务（DataSourceService 实例）
            knowledge_service: 知识库服务
            event_callback: SSE 事件回调函数
        """
        self.agent_config = agent_config
        self.tools_config = agent_config.get("tools_config", {})
        self.routing_config = agent_config.get("routing_config", {})
        self.degradation_config = agent_config.get("degradation_config", {})
        self.model_config = agent_config.get("model_config", {})

        self.ragflow_client = ragflow_client
        self.db_service = db_service
        self.knowledge_service = knowledge_service
        self.event_callback = event_callback

        # 构建状态图
        self.graph = self._build_graph()

    # ========== 动态图构建 ==========

    def _build_graph(self) -> Any:
        """根据 Agent 配置动态构建 LangGraph 状态图"""
        workflow = StateGraph(AgentState)

        # 1. 始终添加的节点
        workflow.add_node("intent_router", self._intent_router)
        workflow.add_node("response_generator", self._response_generator)

        # 2. 根据 tools_config 动态添加工具节点
        enabled_tools = self.tools_config.get("tools", [])

        if "rag" in enabled_tools:
            workflow.add_node("rag_retrieval", self._rag_retrieval)
        if "database" in enabled_tools:
            workflow.add_node("database_query", self._database_query)
        if "web" in enabled_tools:
            workflow.add_node("web_search", self._web_search)

        # 3. 添加质量检查节点（如果配置了降级策略）
        max_retries = self.degradation_config.get("max_retries", 0)
        if max_retries > 0:
            workflow.add_node("quality_check", self._quality_check)

        # 4. 设置入口
        workflow.set_entry_point("intent_router")

        # 5. 无工具时直接闲聊
        if not enabled_tools:
            workflow.add_edge("intent_router", "response_generator")
        else:
            # 根据路由策略添加条件边
            workflow.add_conditional_edges(
                "intent_router",
                self._route_decision,
                self._build_route_map(enabled_tools)
            )

            # 6. 工具节点 → 质量检查 / 响应生成器
            for tool in enabled_tools:
                node_name = self._tool_node_name(tool)
                if max_retries > 0:
                    workflow.add_edge(node_name, "quality_check")
                else:
                    workflow.add_edge(node_name, "response_generator")

            # 7. 质量检查的条件边
            if max_retries > 0:
                fallback_target = self._fallback_tool(enabled_tools)
                route_map = {
                    "pass": "response_generator",
                    "fail": "response_generator"  # 已达最大重试，用已有结果生成
                }
                if fallback_target:
                    route_map["retry"] = fallback_target
                workflow.add_conditional_edges(
                    "quality_check",
                    self._quality_decision,
                    route_map
                )

        # 8. 响应生成器 → 结束
        workflow.add_edge("response_generator", END)

        # 编译图
        return workflow.compile()

    def _build_route_map(self, enabled_tools: List[str]) -> Dict[str, str]:
        """构建路由映射表"""
        route_map = {}
        if "rag" in enabled_tools:
            route_map["rag"] = "rag_retrieval"
        if "database" in enabled_tools:
            route_map["database"] = "database_query"
        if "web" in enabled_tools:
            route_map["web"] = "web_search"
        route_map["chitchat"] = "response_generator"
        return route_map

    def _tool_node_name(self, tool: str) -> str:
        """工具标识转节点名"""
        return {
            "rag": "rag_retrieval",
            "database": "database_query",
            "web": "web_search"
        }.get(tool, "")

    def _fallback_tool(self, enabled_tools: List[str]) -> Optional[str]:
        """获取降级目标工具节点名"""
        if not enabled_tools:
            return None
        # 简单策略：降级到第一个工具（后续可扩展为按优先级链式降级）
        return self._tool_node_name(enabled_tools[0])

    # ========== 事件回调 ==========

    async def _emit_event(self, event_type: str, data: Dict[str, Any]):
        """发送 SSE 事件"""
        if self.event_callback:
            try:
                await self.event_callback(event_type, data)
            except Exception as e:
                logger.error(f"Event callback failed: {e}")

    # ========== 意图路由节点 ==========

    async def _intent_router(self, state: AgentState) -> AgentState:
        """意图路由节点：根据路由策略判断使用哪个工具"""
        await self._emit_event("thinking", {"content": "正在分析意图..."})

        messages = state.get("messages", [])
        if not messages:
            state["route_target"] = "chitchat"
            return state

        last_message = messages[-1]
        user_query = last_message.content if hasattr(last_message, 'content') else str(last_message)

        strategy = self.routing_config.get("strategy", "keyword")

        if strategy == "keyword":
            state = await self._route_by_keyword(state, user_query)
        elif strategy == "llm":
            state = await self._route_by_llm(state, user_query)
        elif strategy == "priority":
            state = await self._route_by_priority(state, user_query)
        else:
            state = await self._route_by_keyword(state, user_query)

        # 发送路由决策事件
        target = state.get("route_target", "chitchat")
        await self._emit_event("thinking", {"content": f"选择工具: {target}"})

        return state

    async def _route_by_keyword(self, state: AgentState, user_query: str) -> AgentState:
        """关键词路由：根据 routing_config.rules 匹配"""
        rules = self.routing_config.get("rules", [])
        query_lower = user_query.lower()

        for rule in rules:
            keywords = rule.get("keywords", [])
            target = rule.get("target", "")
            if any(kw.lower() in query_lower for kw in keywords):
                state["route_target"] = target
                self._set_tool_query(state, target, user_query)
                return state

        # 默认走优先级最高的工具
        enabled_tools = self.tools_config.get("tools", [])
        if enabled_tools:
            default_target = enabled_tools[0]
            state["route_target"] = default_target
            self._set_tool_query(state, default_target, user_query)
        else:
            state["route_target"] = "chitchat"

        return state

    async def _route_by_llm(self, state: AgentState, user_query: str) -> AgentState:
        """LLM 智能路由：使用 LLM 判断意图（TODO: 接入实际 LLM）"""
        # 暂时降级为关键词路由
        return await self._route_by_keyword(state, user_query)

    async def _route_by_priority(self, state: AgentState, user_query: str) -> AgentState:
        """优先级路由：按 tools 列表顺序尝试"""
        enabled_tools = self.tools_config.get("tools", [])
        if enabled_tools:
            state["route_target"] = enabled_tools[0]
            self._set_tool_query(state, enabled_tools[0], user_query)
        else:
            state["route_target"] = "chitchat"
        return state

    def _set_tool_query(self, state: AgentState, target: str, user_query: str):
        """根据路由目标设置对应的查询字段"""
        if target == "rag":
            state["rag_query"] = user_query
        elif target == "database":
            state["db_sql"] = user_query
        elif target == "web":
            state["web_query"] = user_query

    def _route_decision(self, state: AgentState) -> str:
        """路由决策函数（用于条件边）"""
        return state.get("route_target", "chitchat")

    # ========== RAG 检索节点 ==========

    async def _rag_retrieval(self, state: AgentState) -> AgentState:
        """RAG 检索节点：调用 RAGFlow 进行知识库检索"""
        query = state.get("rag_query", "") or state["messages"][-1].content

        await self._emit_event("tool_call", {
            "tool": "rag",
            "query": query,
            "status": "running"
        })

        # 从 Agent 配置中获取 RAG 参数
        rag_config = self.tools_config.get("rag_config", {})
        dataset_ids = rag_config.get("dataset_ids", [])
        top_k = rag_config.get("top_k", 5)
        similarity_threshold = rag_config.get("similarity_threshold", 0.5)

        # 如果没有配置知识库，返回空结果
        if not dataset_ids:
            logger.warning("RAG retrieval: no dataset_ids configured")
            state["rag_documents"] = []
            state["rag_quality_score"] = 0.0
            await self._emit_event("tool_result", {
                "tool": "rag",
                "status": "completed",
                "message": "未配置知识库"
            })
            return state

        all_documents = []
        for dataset_id in dataset_ids:
            try:
                result = await self.ragflow_client.retrieval(
                    dataset_id=dataset_id,
                    query=query,
                    top_k=top_k
                )
                # 解析 RAGFlow 返回结果
                chunks = result.get("data", {}).get("chunks", [])
                if not chunks:
                    # 兼容另一种返回格式
                    chunks = result.get("chunks", [])

                for chunk in chunks:
                    score = chunk.get("similarity", chunk.get("score", 0))
                    if score >= similarity_threshold:
                        all_documents.append({
                            "content": chunk.get("content", ""),
                            "source": chunk.get("document_keyword", chunk.get("document_name", "未知文档")),
                            "score": score
                        })
            except Exception as e:
                logger.error(f"RAG retrieval failed for dataset {dataset_id}: {e}")

        # 按分数排序取 top_k
        all_documents.sort(key=lambda x: x["score"], reverse=True)
        all_documents = all_documents[:top_k]

        state["rag_documents"] = all_documents
        state["rag_quality_score"] = max([d["score"] for d in all_documents], default=0.0)

        # 发送工具完成事件
        await self._emit_event("tool_result", {
            "tool": "rag",
            "documents": all_documents,
            "status": "completed"
        })

        # 发送引用来源事件
        if all_documents:
            await self._emit_event("reference", {
                "references": [
                    {
                        "type": "document",
                        "title": d["source"],
                        "content": d["content"][:200],
                        "score": d["score"]
                    }
                    for d in all_documents
                ]
            })

        return state

    # ========== 数据库查询节点 ==========

    async def _database_query(self, state: AgentState) -> AgentState:
        """数据库查询节点：调用数据源服务执行 SQL"""
        user_query = state.get("db_sql", "") or state["messages"][-1].content

        await self._emit_event("tool_call", {
            "tool": "database",
            "query": user_query,
            "status": "running"
        })

        # 从 Agent 配置中获取数据库参数
        db_config = self.tools_config.get("database_config", {})
        datasource_id = db_config.get("datasource_id", "")
        max_rows = db_config.get("max_rows", 100)

        if not datasource_id:
            logger.warning("Database query: no datasource_id configured")
            state["db_result"] = []
            state["db_quality_score"] = 0.0
            await self._emit_event("tool_result", {
                "tool": "database",
                "status": "completed",
                "message": "未配置数据源"
            })
            return state

        try:
            # 调用数据源服务执行 SQL
            # 注意：这里直接传递用户输入，后续可接入 Text-to-SQL 模块
            sql = user_query
            result = await self.db_service.execute_sql(
                datasource_id=datasource_id,
                user_id=state["user_id"],
                sql=sql,
                max_rows=max_rows
            )

            rows = result.get("rows", [])
            state["db_result"] = rows
            state["db_sql"] = sql
            state["db_quality_score"] = 0.8 if rows else 0.0

            await self._emit_event("tool_result", {
                "tool": "database",
                "sql": sql,
                "rows": rows,
                "row_count": result.get("row_count", 0),
                "status": "completed"
            })

            await self._emit_event("reference", {
                "references": [{
                    "type": "database",
                    "title": f"SQL: {sql[:50]}",
                    "content": f"查询返回 {result.get('row_count', 0)} 条结果",
                    "source": sql
                }]
            })

        except Exception as e:
            logger.error(f"Database query failed: {e}")
            state["db_result"] = []
            state["db_quality_score"] = 0.0
            await self._emit_event("tool_result", {
                "tool": "database",
                "error": str(e),
                "status": "error"
            })

        return state

    # ========== Web 搜索节点 ==========

    async def _web_search(self, state: AgentState) -> AgentState:
        """Web 搜索节点：从互联网搜索信息（暂保留 mock）"""
        query = state.get("web_query", "") or state["messages"][-1].content

        await self._emit_event("tool_call", {
            "tool": "web",
            "query": query,
            "status": "running"
        })

        try:
            # TODO: 接入实际 Web 搜索 API
            # 目前返回模拟数据
            await asyncio.sleep(0.5)

            results = [
                {
                    "title": f"搜索结果：{query}",
                    "url": "https://example.com",
                    "snippet": f"关于「{query}」的搜索结果摘要..."
                }
            ]

            state["web_results"] = results

            await self._emit_event("tool_result", {
                "tool": "web",
                "results": results,
                "status": "completed"
            })

            await self._emit_event("reference", {
                "references": [
                    {
                        "type": "web",
                        "title": r["title"],
                        "content": r["snippet"],
                        "source": r["url"]
                    }
                    for r in results
                ]
            })

        except Exception as e:
            logger.error(f"Web search failed: {e}")
            state["web_results"] = []
            await self._emit_event("tool_result", {
                "tool": "web",
                "error": str(e),
                "status": "error"
            })

        return state

    # ========== 质量检查节点 ==========

    async def _quality_check(self, state: AgentState) -> AgentState:
        """质量检查节点：评估检索结果质量"""
        route_target = state.get("route_target")
        retry_count = state.get("metadata", {}).get("retry_count", 0)

        # 获取当前工具的质量分数
        score_map = {
            "rag": state.get("rag_quality_score", 0),
            "database": state.get("db_quality_score", 0),
            "web": 0.7  # Web 搜索暂不评估质量
        }
        current_score = score_map.get(route_target, 0)

        state.setdefault("metadata", {})
        state["metadata"]["retry_count"] = retry_count + 1
        state["metadata"]["last_quality_score"] = current_score

        await self._emit_event("thinking", {
            "content": f"质量检查：分数 {current_score:.2f}，重试次数 {retry_count + 1}"
        })

        return state

    def _quality_decision(self, state: AgentState) -> str:
        """质量检查后的路由决策"""
        score = state.get("metadata", {}).get("last_quality_score", 0)
        retry_count = state.get("metadata", {}).get("retry_count", 0)
        max_retries = self.degradation_config.get("max_retries", 3)
        threshold = self.tools_config.get("rag_config", {}).get("similarity_threshold", 0.5)

        if score >= threshold:
            return "pass"

        if retry_count >= max_retries:
            fallback = self.degradation_config.get("fallback", "default")
            if fallback == "error":
                return "fail"
            return "pass"  # 用已有结果生成

        return "retry"

    # ========== 响应生成节点 ==========

    async def _response_generator(self, state: AgentState) -> AgentState:
        """响应生成节点：根据检索结果生成最终回答"""
        route_target = state.get("route_target", "chitchat")

        # 构建上下文
        context_parts = []
        if route_target == "rag" and state.get("rag_documents"):
            context_parts.append("知识库检索结果：")
            for doc in state["rag_documents"]:
                context_parts.append(f"- [{doc['source']}] {doc['content']}")

        elif route_target == "database" and state.get("db_result"):
            context_parts.append(f"数据库查询结果（SQL: {state.get('db_sql', '')}）：")
            context_parts.append(json.dumps(state["db_result"], ensure_ascii=False, indent=2))

        elif route_target == "web" and state.get("web_results"):
            context_parts.append("网上搜索结果：")
            for result in state["web_results"]:
                context_parts.append(f"- [{result['title']}] {result['snippet']}")

        context = "\n".join(context_parts) if context_parts else ""
        user_query = state["messages"][-1].content

        # TODO: 调用 LLM 生成回答（目前使用模板）
        if context:
            response = f"根据您的问题「{user_query}」，以下是相关信息：\n\n{context}\n\n希望以上信息对您有帮助。"
        else:
            response = "您好！我是智能助手，有什么可以帮助您的吗？"

        state["final_response"] = response

        # 流式输出文本（按字符）
        for char in response:
            await self._emit_event("text", {"content": char})

        # 添加 AI 消息到历史
        ai_message = AIMessage(content=response)
        state["messages"].append(ai_message)

        return state

    # ========== 执行入口 ==========

    async def invoke(self, state: AgentState) -> AgentState:
        """
        执行状态图

        Args:
            state: 初始状态

        Returns:
            最终状态
        """
        try:
            result = await self.graph.ainvoke(state)
            return result
        except Exception as e:
            logger.error(f"Graph invocation failed: {e}", exc_info=True)
            raise


async def create_langgraph_runner(
    user_id: str,
    conversation_id: str,
    user_message: str,
    agent_config: Dict[str, Any],
    ragflow_client,
    db_service,
    knowledge_service=None,
    event_callback: Optional[EventCallback] = None
) -> str:
    """
    创建 LangGraph 运行器并执行

    Args:
        user_id: 用户 ID
        conversation_id: 对话 ID
        user_message: 用户消息
        agent_config: Agent 配置
        ragflow_client: RAGFlow 客户端
        db_service: 数据源服务
        knowledge_service: 知识库服务（可选）
        event_callback: SSE 事件回调（可选）

    Returns:
        AI 响应内容
    """
    # 创建状态图
    graph = AgenticRAGGraph(
        agent_config=agent_config,
        ragflow_client=ragflow_client,
        db_service=db_service,
        knowledge_service=knowledge_service,
        event_callback=event_callback
    )

    # 初始化状态
    initial_state = AgentState(
        messages=[HumanMessage(content=user_message)],
        user_id=user_id,
        conversation_id=conversation_id,
        route_target=None,
        rag_query=None,
        rag_documents=None,
        rag_quality_score=None,
        db_sql=None,
        db_result=None,
        db_quality_score=None,
        web_query=None,
        web_results=None,
        final_response=None,
        metadata={}
    )

    # 执行状态图
    final_state = await graph.invoke(initial_state)

    # 返回最终响应
    return final_state.get("final_response", "抱歉，我暂时无法回答您的问题。")
