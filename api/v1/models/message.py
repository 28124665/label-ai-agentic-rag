"""
消息模型
存储对话中的消息
"""

from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, Text, JSON, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional, Dict, Any, TYPE_CHECKING
import uuid

from core.database import Base

if TYPE_CHECKING:
    from models.conversation import Conversation


class Message(Base):
    """消息表"""
    __tablename__ = "messages"
    
    # 主键
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4())
    )
    
    # 外键
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    # 消息角色：user, assistant, system, tool
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False
    )
    
    # 消息内容
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )
    
    # 工具调用信息（JSON 格式）
    tool_calls: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True
    )
    
    # 引用来源（JSON 格式）
    references: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True
    )
    
    # Token 使用统计
    token_usage: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True
    )
    
    # 执行耗时（毫秒）
    execution_time_ms: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True
    )
    
    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )
    
    # 关系
    conversation: Mapped["Conversation"] = relationship(
        "Conversation",
        back_populates="messages"
    )
    
    def __repr__(self) -> str:
        return f"<Message(id={self.id}, role={self.role})>"
