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
from models.conversation_summary import ConversationSummary
from models.agent import Agent
from schemas.conversation import ConversationUpdate
from core.sse_translator import translate_node_output, format_sse
from agent.langgraph.runner import get_runner
from common.token_utils import num_tokens_from_string

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

        # 2. 加载 Agent 配置，提取工具参数（提前加载以便获取 llm_id 用于摘要生成）
        agent_config = await self._load_agent_config(conversation.agent_id)
        kb_ids, db_id, llm_id = self._extract_agent_resources(agent_config, user_id)

        # tenant_id 暂用 user_id（agent/langgraph/tools/rag_tool.py 已有
        # tenant_id 兜底逻辑，后续多租户支持时再解耦）
        tenant_id = user_id

        # 3. 加载对话历史（Token 预算感知 + 摘要压缩）
        max_tokens = agent_config.get("memory_max_tokens", 2000)
        conversation_history, conversation_summary = await self._load_conversation_history(
            conversation_id,
            max_tokens=max_tokens,
            max_rounds=20,
            tenant_id=tenant_id,
            llm_id=llm_id,
        )

        # 4. 保存用户消息
        await self.add_message(conversation_id=conversation_id, role="user", content=user_message)

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
                conversation_summary=conversation_summary,
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
        max_tokens: int = 2000,
        max_rounds: int = 20,
        tenant_id: str = "",
        llm_id: str = "",
    ) -> tuple[List[Dict[str, str]], str]:
        """加载对话历史（Token 预算感知 + 摘要压缩）。

        从 DB 加载最近 max_rounds 条消息，按 Token 预算填充窗口，
        超出窗口的消息通过 LLM 摘要压缩后注入上下文。

        Args:
            conversation_id: 对话 ID
            max_tokens: Token 预算上限
            max_rounds: 最大回溯轮数（安全上限）
            tenant_id: 租户 ID（用于摘要 LLM 调用）
            llm_id: LLM 模型 ID（用于摘要 LLM 调用）

        Returns:
            tuple: ``(recent_messages, conversation_summary)``
                - recent_messages: ``[{role, content}]``，Token 预算内的消息
                - conversation_summary: 超出窗口的历史摘要（可能为空字符串）
        """
        result = await self.db.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role.in_(["user", "assistant"]),
            )
            .order_by(Message.created_at.desc())
            .limit(max_rounds)
        )
        messages = result.scalars().all()
        # 反转为正序
        messages = list(reversed(messages))

        if not messages:
            return [], ""

        # 转换为 dict 列表（保留 id 用于摘要版本判断）
        msg_dicts = [
            {"role": msg.role, "content": msg.content or "", "id": msg.id}
            for msg in messages
        ]

        # Token 预算填充窗口
        recent, overflow = self._fill_token_window(msg_dicts, max_tokens)

        # 提取窗口内消息的 role/content（去除内部 id 字段）
        recent_messages = [
            {"role": m["role"], "content": m["content"]} for m in recent
        ]

        # 摘要生成
        if overflow:
            conversation_summary = await self._ensure_summary(
                conversation_id=conversation_id,
                overflow_messages=overflow,
                tenant_id=tenant_id,
                llm_id=llm_id,
            )
            return recent_messages, conversation_summary

        return recent_messages, ""

    @staticmethod
    def _fill_token_window(
        messages: list[dict],
        max_tokens: int,
    ) -> tuple[list[dict], list[dict]]:
        """从消息列表（正序）中按 Token 预算填充窗口。

        从最新消息（列表末尾）向前累加 Token，直到超出预算。
        返回 (窗口内消息, 溢出消息)，均保持正序。

        边界处理：单条消息超过 max_tokens 时，至少保留最近一条。

        Args:
            messages: 消息列表（正序），每条含 role/content/id
            max_tokens: Token 预算上限

        Returns:
            tuple: ``(recent, overflow)``，均保持正序
        """
        recent = []
        token_count = 0

        for msg in reversed(messages):
            msg_tokens = num_tokens_from_string(msg.get("content", "") or "")
            if token_count + msg_tokens <= max_tokens:
                recent.append(msg)
                token_count += msg_tokens
            else:
                break

        recent.reverse()  # 恢复正序

        # 边界：单条消息超过 max_tokens 时，至少保留最近一条
        if not recent and messages:
            recent = [messages[-1]]
            overflow = messages[:-1]
        else:
            overflow = messages[: len(messages) - len(recent)]

        return recent, overflow

    async def _ensure_summary(
        self,
        conversation_id: str,
        overflow_messages: list[dict],
        tenant_id: str = "",
        llm_id: str = "",
    ) -> str:
        """获取或生成对话摘要。

        优先从 DB 读取缓存摘要，若摘要过期（last_message_id 不匹配）
        则调用 LLM 增量更新。

        Args:
            conversation_id: 对话 ID
            overflow_messages: 溢出消息列表（正序，含 id 字段）
            tenant_id: 租户 ID
            llm_id: LLM 模型 ID

        Returns:
            str: 摘要文本（可能为空字符串）
        """
        if not overflow_messages:
            return ""

        # 查询已有摘要
        result = await self.db.execute(
            select(ConversationSummary).where(
                ConversationSummary.conversation_id == conversation_id,
            )
        )
        existing = result.scalar_one_or_none()

        # 摘要过期判断：last_message_id 是否匹配溢出消息的最后一条
        overflow_last_id = overflow_messages[-1].get("id", "")
        if existing and existing.last_message_id == overflow_last_id:
            # 摘要有效，直接复用
            return existing.summary_text or ""

        # 需要生成/更新摘要
        if existing:
            # 增量更新：已有摘要 + 新增溢出消息
            summary = await self._generate_summary(
                existing_summary=existing.summary_text,
                new_messages=overflow_messages,
                tenant_id=tenant_id,
                llm_id=llm_id,
            )
        else:
            # 首次生成
            summary = await self._generate_summary(
                existing_summary="",
                new_messages=overflow_messages,
                tenant_id=tenant_id,
                llm_id=llm_id,
            )

        if not summary:
            return existing.summary_text if existing else ""

        # 写入/更新 DB
        summary_token_count = num_tokens_from_string(summary)
        if existing:
            existing.summary_text = summary
            existing.last_message_id = overflow_last_id
            existing.message_count = len(overflow_messages)
            existing.token_count = summary_token_count
        else:
            new_summary = ConversationSummary(
                conversation_id=conversation_id,
                summary_text=summary,
                last_message_id=overflow_last_id,
                message_count=len(overflow_messages),
                token_count=summary_token_count,
            )
            self.db.add(new_summary)

        await self.db.commit()
        return summary

    async def _generate_summary(
        self,
        existing_summary: str,
        new_messages: list[dict],
        tenant_id: str = "",
        llm_id: str = "",
    ) -> str:
        """调用 LLM 生成/更新对话摘要。

        Args:
            existing_summary: 已有摘要（首次生成时为空字符串）
            new_messages: 需要摘要的消息列表（正序，含 role/content/id）
            tenant_id: 租户 ID
            llm_id: LLM 模型 ID

        Returns:
            str: 生成的摘要文本
        """
        if not tenant_id or not llm_id:
            logger.warning(
                "[ConversationService] 摘要生成需要 tenant_id 和 llm_id，跳过"
            )
            return ""

        try:
            from api.db.services.llm_service import LLMBundle
            from api.db.services.tenant_llm_service import TenantLLMService
            from common.constants import LLMType

            model_config = TenantLLMService.get_model_config(
                tenant_id, LLMType.CHAT, llm_id
            )
            if not model_config:
                logger.warning(
                    f"[ConversationService] 未找到 LLM 配置: tenant={tenant_id}, llm={llm_id}"
                )
                return ""

            chat_mdl = LLMBundle(tenant_id, model_config)

            # 构建新增对话文本
            new_text = "\n".join(
                f"{'用户' if m['role'] == 'user' else '助手'}: {m.get('content', '')}"
                for m in new_messages
            )

            if existing_summary:
                system_prompt = (
                    "你是一个对话摘要助手。已有摘要如下：\n\n"
                    f"{existing_summary}\n\n"
                    "新增对话：\n"
                    f"{new_text}\n\n"
                    "请将以上内容合并为一段简洁的摘要，保留所有关键信息，控制在 300 字以内。使用中文输出。"
                )
            else:
                system_prompt = (
                    "你是一个对话摘要助手。请将以下对话历史压缩为简洁的摘要，保留关键信息。\n\n"
                    "要求：\n"
                    "1. 保留关键实体（人名、项目名、技术术语、数字等）\n"
                    "2. 保留重要结论和决策\n"
                    "3. 保留用户明确表达的偏好和需求\n"
                    "4. 忽略寒暄和无关细节\n"
                    "5. 摘要长度控制在 300 字以内\n"
                    "6. 使用中文输出\n\n"
                    f"对话历史：\n{new_text}\n\n"
                    "摘要："
                )

            summary = await chat_mdl.async_chat(
                system=system_prompt,
                history=[],
                gen_conf={},
            )
            logger.info(
                f"[ConversationService] 摘要生成成功: "
                f"conversation={conversation_id}, "
                f"messages={len(new_messages)}, "
                f"summary_tokens={num_tokens_from_string(summary)}"
            )
            return summary or ""

        except Exception as e:
            logger.error(f"[ConversationService] 摘要生成失败: {e}", exc_info=True)
            return ""

    @staticmethod
    def _extract_agent_resources(
        agent_config: Dict[str, Any],
        user_id: str = "",
    ) -> tuple[List[str], str, str]:
        """从 Agent 配置中提取知识库、数据库、LLM 资源 ID，
        并根据用户权限过滤知识库。

        Args:
            agent_config: Agent 配置字典
            user_id: 当前用户 ID，用于权限过滤

        Returns:
            tuple: ``(kb_ids, db_id, llm_id)``
        """
        from api.db.services.kb_permission_service import KBPermissionService

        tools_config = agent_config.get("tools_config", {}) or {}
        model_config = agent_config.get("model_config", {}) or {}

        kb_ids: List[str] = list(tools_config.get("kb_ids", []) or [])
        db_id = tools_config.get("db_id", "") or ""
        llm_id = model_config.get("llm_id", "") or model_config.get("llm", "") or ""

        # 根据用户权限过滤知识库
        if user_id:
            kb_ids = KBPermissionService.filter_permitted_kb_ids(kb_ids, user_id)

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

            tools_config = agent.tools_config or {"tools": ["rag"]}
            agent_config = {
                "tools_config": tools_config,
                "routing_config": agent.routing_config or {"strategy": "keyword", "rules": []},
                "degradation_config": agent.degradation_config or {"max_retries": 0, "fallback": "default"},
                "model_config": agent.model_config or {},
            }

            # 挂载 graph 配置：从 tools_config 提升到顶层 agent_config，
            # 供 GraphTool / graph_tool_node / resolver / planner_adapter 读取（设计文档 §6 第1步 / §3.3.2）
            for key in ("graph_config", "graph_tool"):
                value = tools_config.get(key)
                if isinstance(value, dict):
                    agent_config[key] = value

            return agent_config
        except Exception as e:
            logger.error(f"Failed to load agent config: {e}")
            return {"tools_config": {"tools": ["rag"]}, "routing_config": {"strategy": "keyword", "rules": []}, "degradation_config": {"max_retries": 0, "fallback": "default"}}
