"""
Agent 相关 Pydantic 模型
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class AgentCreate(BaseModel):
    """创建 Agent 请求"""
    name: str = Field(..., min_length=1, max_length=100, description="Agent 名称")
    description: Optional[str] = Field(None, max_length=500, description="Agent 描述")
    tools_config: Optional[Dict[str, Any]] = Field(None, description="工具配置")
    routing_config: Optional[Dict[str, Any]] = Field(None, description="路由策略配置")
    degradation_config: Optional[Dict[str, Any]] = Field(None, description="降级策略配置")
    model_config: Optional[Dict[str, Any]] = Field(None, description="模型配置")


class AgentUpdate(BaseModel):
    """更新 Agent 请求"""
    name: Optional[str] = Field(None, min_length=1, max_length=100, description="Agent 名称")
    description: Optional[str] = Field(None, max_length=500, description="Agent 描述")
    tools_config: Optional[Dict[str, Any]] = Field(None, description="工具配置")
    routing_config: Optional[Dict[str, Any]] = Field(None, description="路由策略配置")
    degradation_config: Optional[Dict[str, Any]] = Field(None, description="降级策略配置")
    model_config: Optional[Dict[str, Any]] = Field(None, description="模型配置")
    is_active: Optional[bool] = Field(None, description="是否启用")


class AgentResponse(BaseModel):
    """Agent 响应"""
    id: str
    user_id: str
    name: str
    description: Optional[str] = None
    tools_config: Optional[Dict[str, Any]] = None
    routing_config: Optional[Dict[str, Any]] = None
    degradation_config: Optional[Dict[str, Any]] = None
    model_config: Optional[Dict[str, Any]] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class AgentListItem(BaseModel):
    """Agent 列表项"""
    id: str
    name: str
    description: Optional[str] = None
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


class ToolsConfig(BaseModel):
    """工具配置"""
    tools: List[str] = Field(default=[], description="启用的工具列表")
    rag_config: Optional[Dict[str, Any]] = Field(None, description="RAG 工具配置")
    database_config: Optional[Dict[str, Any]] = Field(None, description="数据库工具配置")
    web_config: Optional[Dict[str, Any]] = Field(None, description="Web 搜索工具配置")


class RoutingConfig(BaseModel):
    """路由策略配置"""
    strategy: str = Field(default="keyword", description="路由策略：keyword, llm, hybrid")
    rules: Optional[List[Dict[str, Any]]] = Field(None, description="路由规则")


class DegradationConfig(BaseModel):
    """降级策略配置"""
    max_retries: int = Field(default=3, ge=1, le=10, description="最大重试次数")
    fallback: str = Field(default="web_search", description="降级策略：web_search, reject")
    timeout_seconds: int = Field(default=30, ge=5, le=300, description="超时时间")


class ModelConfig(BaseModel):
    """模型配置"""
    llm: str = Field(default="gpt-4", description="LLM 模型")
    embedding: str = Field(default="BAAI/bge-large-zh", description="Embedding 模型")
    rerank: Optional[str] = Field(None, description="Rerank 模型")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="温度参数")
