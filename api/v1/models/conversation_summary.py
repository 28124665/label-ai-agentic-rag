"""
对话摘要模型
存储超出 Token 窗口的历史消息的 LLM 压缩摘要
"""

from datetime import datetime
from sqlalchemy import String, DateTime, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column
import uuid

from core.database import Base


class ConversationSummary(Base):
    """对话摘要表

    每个 conversation 最多一条摘要记录，通过 last_message_id 判断摘要是否过期。
    当溢出消息超出 last_message_id 时，需要增量更新摘要。
    """
    __tablename__ = "conversation_summaries"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    conversation_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
        unique=True,
    )

    summary_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # 摘要覆盖到的最后一条消息 ID，用于判断是否过期
    last_message_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
    )

    # 被摘要的消息数量
    message_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    # 摘要的 Token 数
    token_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<ConversationSummary(id={self.id}, "
            f"conversation_id={self.conversation_id}, "
            f"message_count={self.message_count})>"
        )