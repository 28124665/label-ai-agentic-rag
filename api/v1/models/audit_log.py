"""
审计日志模型
记录系统操作日志
"""

from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, Text, JSON, Integer
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional, Dict, Any
import uuid

from core.database import Base


class AuditLog(Base):
    """审计日志表"""
    __tablename__ = "audit_logs"
    
    # 主键
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4())
    )
    
    # 外键（可选，某些系统操作可能没有用户）
    user_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    
    # 操作类型：create, update, delete, login, logout, query 等
    action: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True
    )
    
    # 资源类型：conversation, message, datasource, agent, knowledge_base 等
    resource_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True
    )
    
    # 资源 ID
    resource_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        nullable=True
    )
    
    # 操作详情（JSON 格式）
    details: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True
    )
    
    # IP 地址
    ip_address: Mapped[Optional[str]] = mapped_column(
        String(45),
        nullable=True
    )
    
    # User-Agent
    user_agent: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True
    )
    
    # 执行耗时（毫秒）
    execution_time_ms: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True
    )
    
    # 状态码
    status_code: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True
    )
    
    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True
    )
    
    def __repr__(self) -> str:
        return f"<AuditLog(id={self.id}, action={self.action})>"
