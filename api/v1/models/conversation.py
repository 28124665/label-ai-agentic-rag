"""
对话模型
存储用户对话信息
"""

from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional, List, TYPE_CHECKING
import uuid

from core.database import Base

if TYPE_CHECKING:
    from models.user import User
    from models.agent import Agent
    from models.message import Message


class Conversation(Base):
    """对话表"""
    __tablename__ = "conversations"
    
    # 主键
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4())
    )
    
    # 外键
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    agent_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    
    # 基本信息
    title: Mapped[str] = mapped_column(
        String(200),
        default="新对话",
        nullable=False
    )
    
    # 统计信息
    message_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False
    )
    
    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )
    last_message_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True
    )
    
    # 关系
    user: Mapped["User"] = relationship(
        "User",
        back_populates="conversations"
    )
    agent: Mapped[Optional["Agent"]] = relationship(
        "Agent",
        back_populates="conversations"
    )
    messages: Mapped[List["Message"]] = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at"
    )
    
    def __repr__(self) -> str:
        return f"<Conversation(id={self.id}, title={self.title})>"
