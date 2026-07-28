"""
数据源模型
存储数据库连接信息
"""

from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, Text, JSON, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional, Dict, Any
import uuid

from core.database import Base


class DataSource(Base):
    """数据源表"""
    __tablename__ = "datasources"
    
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
    
    # 数据库类型：mysql, postgresql, oracle, sqlserver 等
    db_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False
    )
    
    # 连接配置（加密存储）
    host: Mapped[str] = mapped_column(
        String(200),
        nullable=False
    )
    port: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )
    database: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )
    username: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )
    password_encrypted: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )
    
    # 额外配置（JSON 格式）
    extra_config: Mapped[Optional[Dict[str, Any]]] = mapped_column(
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
    last_tested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True
    )
    
    def __repr__(self) -> str:
        return f"<DataSource(id={self.id}, name={self.name})>"
