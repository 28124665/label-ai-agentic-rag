"""
对话服务
负责对话和消息的管理，并集成 LangGraph 执行流程
"""

from datetime import datetime
from typing import Optional, List, AsyncGenerator, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update
import logging
import json
import asyncio
import time

from models.conversation import Conversation
from models.message import Message
from models.agent import Agent
from schemas.conversation import ConversationUpdate
from core.langgraph_integration import AgenticRAGGraph, AgentState
from core.ragflow_client import get_ragflow_client
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)


class ConversationService:
    """对话服务类"""

    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create(
        self,
        user_id: str,
        agent_id: Optional[str] = None,
        title: str = "新对话"
    ) -> Conversation:
        """
        创建新对话
        
        Args:
            user_id: 用户 ID
            agent_id: Agent ID（可选）
            title: 对话标题
        
        Returns:
            新创建的 Conversation 对象
        """
        conversation = Conversation(
            user_id=user_id,
            agent_id=agent_id,
            title=title
        )
        
        self.db.add(conversation)
        await self.db.commit()
        await self.db.refresh(conversation)
        
        logger.info(f"Conversation created: {conversation.id}")
        return conversation
    
    async def get(self, conversation_id: str, user_id: str) -> Optional[Conversation]:
        """
        获取对话详情
        
        Args:
            conversation_id: 对话 ID
            user_id: 用户 ID（用于权限验证）
        
        Returns:
            Conversation 对象（如果存在且属于该用户），否则 None
        """
        result = await self.db.execute(
            select(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id
            )
        )
        return result.scalar_one_or_none()
    
    async def list(
        self,
        user_id: str,
        page: int = 1,
        size: int = 20
    ) -> tuple[List[Conversation], int]:
        """
        获取对话列表（分页）
        
        Args:
            user_id: 用户 ID
            page: 页码
            size: 每页数量
        
        Returns:
            (对话列表, 总数)
        """
        # 查询总数
        count_result = await self.db.execute(
            select(func.count(Conversation.id))
            .where(Conversation.user_id == user_id)
        )
        total = count_result.scalar()
        
        # 查询列表
        offset = (page - 1) * size
        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.last_message_at.desc().nullslast())
            .offset(offset)
            .limit(size)
        )
        conversations = result.scalars().all()
        
        return conversations, total
    
    async def update(
        self,
        conversation_id: str,
        user_id: str,
        update_data: ConversationUpdate
    ) -> Optional[Conversation]:
        """
        更新对话
        
        Args:
            conversation_id: 对话 ID
            user_id: 用户 ID
            update_data: 更新数据
        
        Returns:
            更新后的 Conversation 对象
        """
        conversation = await self.get(conversation_id, user_id)
        if not conversation:
            return None
        
        if update_data.title is not None:
            conversation.title = update_data.title
        
        conversation.updated_at = datetime.utcnow()
        await self.db.commit()
        await self.db.refresh(conversation)
        
        return conversation
    
    async def delete(self, conversation_id: str, user_id: str) -> bool:
        """
        删除对话
        
        Args:
            conversation_id: 对话 ID
            user_id: 用户 ID
        
        Returns:
            是否删除成功
        """
        conversation = await self.get(conversation_id, user_id)
        if not conversation:
            return False
        
        await self.db.delete(conversation)
        await self.db.commit()
        
        logger.info(f"Conversation deleted: {conversation_id}")
        return True
    
    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        tool_calls: Optional[dict] = None,
        references: Optional[dict] = None,
        token_usage: Optional[dict] = None,
        execution_time_ms: Optional[int] = None
    ) -> Message:
        """
        添加消息到对话
        
        Args:
            conversation_id: 对话 ID
            role: 消息角色（user, assistant, system, tool）
            content: 消息内容
            tool_calls: 工具调用信息
            references: 引用来源
            token_usage: Token 使用统计
            execution_time_ms: 执行耗时
        
        Returns:
            新创建的 Message 对象
        """
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            tool_calls=tool_calls,
            references=references,
            token_usage=token_usage,
            execution_time_ms=execution_time_ms
        )
        
        self.db.add(message)
        
        # 更新对话统计
        await self.db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(
                message_count=Conversation.message_count + 1,
                last_message_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
        )
        
        await self.db.commit()
        await self.db.refresh(message)
        
        return message
    
    async def get_messages(
        self,
        conversation_id: str,
        user_id: str,
        limit: int = 50
    ) -> Optional[List[Message]]:
        """
        获取对话消息历史
        
        Args:
            conversation_id: 对话 ID
            user_id: 用户 ID
            limit: 最大返回数量
        
        Returns:
            消息列表（如果对话存在且属于该用户）
        """
        # 验证对话权限
        conversation = await self.get(conversation_id, user_id)
        if not conversation:
            return None
        
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
            .limit(limit)
        )
        messages = result.scalars().all()
        
        return messages
    
    async def stream_response(
        self,
        conversation_id: str,
        user_message: str,
        user_id: str
    ) -> AsyncGenerator[str, None]:
        """
        流式生成响应（SSE）

        集成 LangGraph 执行流程：
        1. 验证对话权限
        2. 保存用户消息
        3. 加载 Agent 配置
        4. 构建 LangGraph 状态图
        5. 执行状态图，通过事件队列将状态变化转换为 SSE 事件
        6. 保存助手消息

        Args:
            conversation_id: 对话 ID
            user_message: 用户消息
            user_id: 用户 ID

        Yields:
            SSE 事件字符串
        """
        # 1. 验证对话权限
        conversation = await self.get(conversation_id, user_id)
        if not conversation:
            yield self._format_sse({"type": "error", "error": "对话不存在"})
            return

        # 2. 保存用户消息
        await self.add_message(
            conversation_id=conversation_id,
            role="user",
            content=user_message
        )

        # 3. 加载 Agent 配置
        agent_config = await self._load_agent_config(conversation.agent_id)

        # 4. 创建事件队列（用于 LangGraph 状态变化 → SSE 事件流）
        event_queue: asyncio.Queue = asyncio.Queue()

        async def event_callback(event_type: str, data: Dict[str, Any]):
            """LangGraph 事件回调：将事件放入队列"""
            await event_queue.put({"type": event_type, **data})

        # 5. 构建 LangGraph 状态图
        try:
            ragflow_client = get_ragflow_client()
        except Exception as e:
            logger.error(f"Failed to get RAGFlow client: {e}")
            yield self._format_sse({"type": "error", "error": f"RAGFlow 客户端初始化失败: {e}"})
            return

        # 延迟导入 DataSourceService，避免循环依赖
        from services.datasource_service import DataSourceService
        db_service = DataSourceService(self.db)

        # 6. 启动 LangGraph 执行（后台任务）
        start_time = time.time()
        collected_data = {
            "tool_calls": [],
            "references": [],
            "final_response": ""
        }

        async def run_graph():
            """执行 LangGraph 状态图"""
            try:
                graph = AgenticRAGGraph(
                    agent_config=agent_config,
                    ragflow_client=ragflow_client,
                    db_service=db_service,
                    knowledge_service=None,
                    event_callback=event_callback
                )

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

                final_state = await graph.invoke(initial_state)

                # 收集最终响应
                collected_data["final_response"] = final_state.get("final_response", "")

            except Exception as e:
                logger.error(f"LangGraph execution failed: {e}", exc_info=True)
                await event_queue.put({"type": "error", "error": str(e)})
            finally:
                # 发送结束信号
                await event_queue.put(None)

        task = asyncio.create_task(run_graph())

        # 7. 从队列中读取事件并 yield SSE
        try:
            while True:
                event = await event_queue.get()

                # 结束信号
                if event is None:
                    break

                # 收集工具调用和引用来源
                if event.get("type") == "tool_call":
                    collected_data["tool_calls"].append({
                        "tool": event.get("tool"),
                        "query": event.get("query"),
                        "status": event.get("status")
                    })
                elif event.get("type") == "tool_result":
                    collected_data["tool_calls"].append({
                        "tool": event.get("tool"),
                        "status": event.get("status"),
                        "rows": event.get("rows"),
                        "documents": event.get("documents"),
                        "error": event.get("error")
                    })
                elif event.get("type") == "reference":
                    collected_data["references"].extend(event.get("references", []))

                # yield SSE 事件
                yield self._format_sse(event)

        except Exception as e:
            logger.error(f"Error in stream_response: {e}", exc_info=True)
            yield self._format_sse({"type": "error", "error": str(e)})

        finally:
            # 确保后台任务完成
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.TimeoutError:
                task.cancel()
                logger.warning("LangGraph execution timed out, task cancelled")
            except Exception as e:
                logger.error(f"Error waiting for graph task: {e}")

        # 8. 保存助手消息
        execution_time_ms = int((time.time() - start_time) * 1000)
        response_content = collected_data["final_response"] or "抱歉，我暂时无法回答您的问题。"

        try:
            # 构建工具调用和引用的 JSON 数据
            tool_calls_data = collected_data["tool_calls"] if collected_data["tool_calls"] else None
            references_data = {"references": collected_data["references"]} if collected_data["references"] else None

            assistant_msg = await self.add_message(
                conversation_id=conversation_id,
                role="assistant",
                content=response_content,
                tool_calls=tool_calls_data,
                references=references_data,
                execution_time_ms=execution_time_ms
            )

            # 发送完成事件
            yield self._format_sse({
                "type": "done",
                "message_id": assistant_msg.id
            })

        except Exception as e:
            logger.error(f"Failed to save assistant message: {e}", exc_info=True)
            yield self._format_sse({"type": "error", "error": f"保存消息失败: {e}"})

    async def _load_agent_config(self, agent_id: Optional[str]) -> Dict[str, Any]:
        """
        加载 Agent 配置，如果没有则使用默认配置

        Args:
            agent_id: Agent ID（可选）

        Returns:
            Agent 配置字典，包含 tools_config / routing_config / degradation_config
        """
        if not agent_id:
            # 默认配置：只启用 RAG
            return {
                "tools_config": {"tools": ["rag"]},
                "routing_config": {"strategy": "keyword", "rules": []},
                "degradation_config": {"max_retries": 0, "fallback": "default"}
            }

        try:
            result = await self.db.execute(
                select(Agent).where(Agent.id == agent_id)
            )
            agent = result.scalar_one_or_none()

            if not agent:
                logger.warning(f"Agent not found: {agent_id}, using default config")
                return {
                    "tools_config": {"tools": ["rag"]},
                    "routing_config": {"strategy": "keyword", "rules": []},
                    "degradation_config": {"max_retries": 0, "fallback": "default"}
                }

            return {
                "tools_config": agent.tools_config or {"tools": ["rag"]},
                "routing_config": agent.routing_config or {"strategy": "keyword", "rules": []},
                "degradation_config": agent.degradation_config or {"max_retries": 0, "fallback": "default"},
                "model_config": agent.model_config or {}
            }
        except Exception as e:
            logger.error(f"Failed to load agent config: {e}")
            return {
                "tools_config": {"tools": ["rag"]},
                "routing_config": {"strategy": "keyword", "rules": []},
                "degradation_config": {"max_retries": 0, "fallback": "default"}
            }

    def _format_sse(self, data: dict) -> str:
        """格式化 SSE 事件"""
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
