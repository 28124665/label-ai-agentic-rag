"""
对话服务
负责对话和消息的管理，并集成 LangGraph 执行流程
"""

from datetime import datetime
from typing import Optional, List, AsyncGenerator, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update
import logging
import time

from models.conversation import Conversation
from models.message import Message
from models.agent import Agent
from schemas.conversation import ConversationUpdate
from core.sse_translator import translate_node_output, format_sse
from agent.langgraph.runner import get_runner

logger = logging.getLogger(__name__)


class ConversationService:
    """对话服务类"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, user_id: str, agent_id: Optional[str] = None, title: str = "新对话") -> Conversation:
        """
        创建新对话

        Args:
            user_id: 用户 ID
            agent_id: Agent ID（可选）
            title: 对话标题

        Returns:
            新创建的 Conversation 对象
        """
        conversation = Conversation(user_id=user_id, agent_id=agent_id, title=title)

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
        result = await self.db.execute(select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id))
        return result.scalar_one_or_none()

    async def list(self, user_id: str, page: int = 1, size: int = 20) -> tuple[List[Conversation], int]:
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
        count_result = await self.db.execute(select(func.count(Conversation.id)).where(Conversation.user_id == user_id))
        total = count_result.scalar()

        # 查询列表
        offset = (page - 1) * size
        result = await self.db.execute(select(Conversation).where(Conversation.user_id == user_id).order_by(Conversation.last_message_at.desc().nullslast()).offset(offset).limit(size))
        conversations = result.scalars().all()

        return conversations, total

    async def update(self, conversation_id: str, user_id: str, update_data: ConversationUpdate) -> Optional[Conversation]:
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
        execution_time_ms: Optional[int] = None,
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
        message = Message(conversation_id=conversation_id, role=role, content=content, tool_calls=tool_calls, references=references, token_usage=token_usage, execution_time_ms=execution_time_ms)

        self.db.add(message)

        # 更新对话统计
        await self.db.execute(
            update(Conversation).where(Conversation.id == conversation_id).values(message_count=Conversation.message_count + 1, last_message_at=datetime.utcnow(), updated_at=datetime.utcnow())
        )

        await self.db.commit()
        await self.db.refresh(message)

        return message

    async def get_messages(self, conversation_id: str, user_id: str, limit: int = 50) -> Optional[List[Message]]:
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

        result = await self.db.execute(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at).limit(limit))
        messages = result.scalars().all()

        return messages

    async def stream_response(self, conversation_id: str, user_message: str, user_id: str) -> AsyncGenerator[str, None]:
        """流式生成响应（SSE）。

        接入 ``agent/langgraph/runner.py`` 的完整 LangGraph 实现，逐节点
        产出 ``(node_name, node_output)``，再通过 ``sse_translator`` 翻译
        为前端可消费的 SSE 事件。

        流程：
        1. 验证对话权限
        2. 加载当前消息之前的最近 10 条对话历史
        3. 保存用户消息
        4. 加载 Agent 配置，提取 kb_ids / db_id / llm_id / tenant_id
        5. 调用 ``LangGraphRunner.arun_stream`` 执行图
        6. 对每个 ``(node_name, node_output)`` 调用 ``translate_node_output``
           → ``format_sse`` 后 yield
        7. 收集 final_answer / tool_calls / references
        8. 保存 assistant 消息，yield ``done`` 事件

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
            yield format_sse({"type": "error", "error": "对话不存在"})
            return

        # 2. 加载对话历史（当前消息之前的最近 10 条）
        conversation_history = await self._load_conversation_history(conversation_id, limit=10)

        # 3. 保存用户消息
        await self.add_message(conversation_id=conversation_id, role="user", content=user_message)

        # 4. 加载 Agent 配置，提取工具参数
        agent_config = await self._load_agent_config(conversation.agent_id)
        kb_ids, db_id, llm_id = self._extract_agent_resources(agent_config)

        # tenant_id 暂用 user_id（agent/langgraph/tools/rag_tool.py 已有
        # tenant_id 兜底逻辑，后续多租户支持时再解耦）
        tenant_id = user_id

        # 5. 执行 LangGraph 工作流并流式翻译为 SSE
        start_time = time.time()
        collected_data: Dict[str, Any] = {
            "tool_calls": [],
            "references": [],
            "final_answer": "",
        }

        try:
            runner = get_runner()
            async for node_name, node_output in runner.arun_stream(
                user_question=user_message,
                tenant_id=tenant_id,
                llm_id=llm_id,
                kb_ids=kb_ids,
                db_id=db_id,
                conversation_history=conversation_history,
                agent_config=agent_config,
            ):
                # 收集关键产物用于持久化 assistant 消息
                self._collect_node_output(node_name, node_output, collected_data)

                # 翻译节点输出为 SSE 事件并 yield
                for event in translate_node_output(node_name, node_output):
                    yield format_sse(event)

        except Exception as e:
            logger.error(f"LangGraph execution failed: {e}", exc_info=True)
            yield format_sse({"type": "error", "error": str(e)})
            return

        # 6. 保存 assistant 消息
        execution_time_ms = int((time.time() - start_time) * 1000)
        response_content = collected_data["final_answer"] or "抱歉，我暂时无法回答您的问题。"

        try:
            tool_calls_data = collected_data["tool_calls"] if collected_data["tool_calls"] else None
            references_data = {"references": collected_data["references"]} if collected_data["references"] else None

            assistant_msg = await self.add_message(
                conversation_id=conversation_id,
                role="assistant",
                content=response_content,
                tool_calls=tool_calls_data,
                references=references_data,
                execution_time_ms=execution_time_ms,
            )

            yield format_sse(
                {
                    "type": "done",
                    "message_id": assistant_msg.id,
                }
            )

        except Exception as e:
            logger.error(f"Failed to save assistant message: {e}", exc_info=True)
            yield format_sse({"type": "error", "error": f"保存消息失败: {e}"})

    async def _load_conversation_history(
        self,
        conversation_id: str,
        limit: int = 10,
    ) -> List[Dict[str, str]]:
        """加载当前消息之前的最近 N 条对话历史。

        仅返回 ``user`` / ``assistant`` 角色的消息，按时间正序排列，
        供 ``prompt_assembly`` 注入 LLM 上下文。

        Args:
            conversation_id: 对话 ID
            limit: 最大返回条数

        Returns:
            list[dict]: ``[{role, content}]``
        """
        result = await self.db.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role.in_(["user", "assistant"]),
            )
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        messages = result.scalars().all()
        # 反转为正序，便于在 Prompt 中按时间线呈现
        messages = list(reversed(messages))
        return [{"role": msg.role, "content": msg.content or ""} for msg in messages]

    @staticmethod
    def _extract_agent_resources(
        agent_config: Dict[str, Any],
    ) -> tuple[List[str], str, str]:
        """从 Agent 配置中提取知识库、数据库、LLM 资源 ID。

        Args:
            agent_config: Agent 配置字典

        Returns:
            tuple: ``(kb_ids, db_id, llm_id)``
        """
        tools_config = agent_config.get("tools_config", {}) or {}
        model_config = agent_config.get("model_config", {}) or {}

        kb_ids: List[str] = list(tools_config.get("kb_ids", []) or [])
        db_id = tools_config.get("db_id", "") or ""
        llm_id = model_config.get("llm_id", "") or model_config.get("llm", "") or ""

        return kb_ids, db_id, llm_id

    @staticmethod
    def _collect_node_output(
        node_name: str,
        node_output: Dict[str, Any],
        collected_data: Dict[str, Any],
    ) -> None:
        """从节点输出中收集用于持久化 assistant 消息的关键产物。

        - ``answer_output`` 节点：收集 ``final_answer``
        - ``rag_tool`` / ``db_tool`` / ``web_tool`` 节点：收集
          ``tool_calls`` 与 ``references``

        Args:
            node_name: LangGraph 节点标识
            node_output: 节点返回的部分状态更新字典
            collected_data: 收集器字典（原地修改）
        """
        if node_name == "answer_output":
            final_answer = node_output.get("final_answer", "")
            if final_answer:
                collected_data["final_answer"] = final_answer
            return

        if node_name == "rag_tool":
            docs = node_output.get("rag_docs", []) or []
            collected_data["tool_calls"].append(
                {
                    "tool": "rag",
                    "status": "completed",
                    "documents": docs,
                }
            )
            for doc in docs:
                collected_data["references"].append(
                    {
                        "type": "document",
                        "title": doc.get("source", ""),
                        "content": (doc.get("content") or "")[:200],
                        "score": doc.get("score", 0.0),
                    }
                )
            return

        if node_name == "db_tool":
            db_result = node_output.get("db_result", {}) or {}
            collected_data["tool_calls"].append(
                {
                    "tool": "database",
                    "status": "completed",
                    "sql": db_result.get("sql", ""),
                    "rows": db_result.get("rows", []),
                    "row_count": db_result.get("row_count", 0),
                }
            )
            sql = db_result.get("sql", "")
            if sql:
                collected_data["references"].append(
                    {
                        "type": "database",
                        "title": f"SQL: {sql[:50]}",
                        "content": f"查询返回 {db_result.get('row_count', 0)} 条结果",
                        "source": sql,
                    }
                )
            return

        if node_name == "web_tool":
            docs = node_output.get("web_docs", []) or []
            collected_data["tool_calls"].append(
                {
                    "tool": "web",
                    "status": "completed",
                    "results": docs,
                }
            )
            for doc in docs:
                collected_data["references"].append(
                    {
                        "type": "web",
                        "title": doc.get("title", ""),
                        "content": (doc.get("content") or "")[:200],
                        "source": doc.get("url", ""),
                    }
                )
            return

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
            return {"tools_config": {"tools": ["rag"]}, "routing_config": {"strategy": "keyword", "rules": []}, "degradation_config": {"max_retries": 0, "fallback": "default"}}

        try:
            result = await self.db.execute(select(Agent).where(Agent.id == agent_id))
            agent = result.scalar_one_or_none()

            if not agent:
                logger.warning(f"Agent not found: {agent_id}, using default config")
                return {"tools_config": {"tools": ["rag"]}, "routing_config": {"strategy": "keyword", "rules": []}, "degradation_config": {"max_retries": 0, "fallback": "default"}}

            return {
                "tools_config": agent.tools_config or {"tools": ["rag"]},
                "routing_config": agent.routing_config or {"strategy": "keyword", "rules": []},
                "degradation_config": agent.degradation_config or {"max_retries": 0, "fallback": "default"},
                "model_config": agent.model_config or {},
            }
        except Exception as e:
            logger.error(f"Failed to load agent config: {e}")
            return {"tools_config": {"tools": ["rag"]}, "routing_config": {"strategy": "keyword", "rules": []}, "degradation_config": {"max_retries": 0, "fallback": "default"}}
