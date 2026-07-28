"""
对话相关 Pydantic 模型
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    """创建对话请求"""
    agent_id: Optional[str] = Field(None, description="Agent ID")
    title: str = Field(default="新对话", max_length=200, description="对话标题")


class ConversationUpdate(BaseModel):
    """更新对话请求"""
    title: Optional[str] = Field(None, max_length=200, description="对话标题")


class ConversationResponse(BaseModel):
    """对话响应"""
    id: str
    user_id: str
    agent_id: Optional[str] = None
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime
    last_message_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class ConversationListItem(BaseModel):
    """对话列表项"""
    id: str
    title: str
    agent_id: Optional[str] = None
    message_count: int
    last_message_at: Optional[datetime] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class MessageCreate(BaseModel):
    """发送消息请求"""
    content: str = Field(..., min_length=1, description="消息内容")
    files: Optional[List[str]] = Field(None, description="附件 ID 列表")


class MessageResponse(BaseModel):
    """消息响应"""
    id: str
    conversation_id: str
    role: str
    content: str
    tool_calls: Optional[Dict[str, Any]] = None
    references: Optional[Dict[str, Any]] = None
    token_usage: Optional[Dict[str, Any]] = None
    execution_time_ms: Optional[int] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class SSEEvent(BaseModel):
    """SSE 事件"""
    type: str  # thinking, tool_call, tool_result, text, reference, done, error
    content: Optional[str] = None
    tool: Optional[str] = None
    sql: Optional[str] = None
    status: Optional[str] = None
    rows: Optional[List[Dict[str, Any]]] = None
    sources: Optional[List[Dict[str, Any]]] = None
    message_id: Optional[str] = None
    error: Optional[str] = None
