"""
Agent 模型
存储 Agent 配置信息
"""

from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, Text, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional, Dict, Any, List, TYPE_CHECKING
import uuid

from core.database import Base

if TYPE_CHECKING:
    from models.conversation import Conversation


class Agent(Base):
    """Agent 表"""
    __tablename__ = "agents"
    
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
    
    # 基本信息
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True
    )
    
    # 工具配置（JSON 格式）
    # 例如：{"tools": ["rag", "database", "web"], "config": {...}}
    tools_config: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True
    )
    
    # 路由策略配置（JSON 格式）
    # 例如：{"strategy": "keyword", "rules": [...]}
    routing_config: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True
    )
    
    # 降级策略配置（JSON 格式）
    # 例如：{"max_retries": 3, "fallback": "web_search"}
    degradation_config: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True
    )
    
    # 模型配置（JSON 格式）
    # 例如：{"llm": "gpt-4", "embedding": "bge-large-zh"}
    model_config: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True
    )
    
    # 状态
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
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
    
    # 关系
    conversations: Mapped[List["Conversation"]] = relationship(
        "Conversation",
        back_populates="agent"
    )
    
    def __repr__(self) -> str:
        return f"<Agent(id={self.id}, name={self.name})>"
