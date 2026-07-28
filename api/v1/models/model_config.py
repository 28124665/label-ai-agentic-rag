"""
模型配置模型
存储模型配置信息
"""

from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, Text, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional, Dict, Any
import uuid

from core.database import Base


class ModelConfig(Base):
    """模型配置表"""
    __tablename__ = "model_configs"
    
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
    
    # 模型类型：llm, embedding, rerank
    model_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False
    )
    
    # 模型提供商：openai, azure, local
    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False
    )
    
    # 模型名称
    model_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )
    
    # API 配置（加密存储）
    api_key: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True
    )
    api_base: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True
    )
    
    # 额外配置（JSON 格式）
    config: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True
    )
    
    # 状态
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False
    )
    
    # 同步状态
    synced_to_ragflow: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
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
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True
    )
    
    def __repr__(self) -> str:
        return f"<ModelConfig(id={self.id}, name={self.name})>"
