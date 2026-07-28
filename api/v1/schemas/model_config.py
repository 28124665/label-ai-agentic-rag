"""
模型配置相关 Pydantic 模型
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class ModelConfigCreate(BaseModel):
    """创建模型配置请求"""
    name: str = Field(..., min_length=1, max_length=100, description="配置名称")
    model_type: str = Field(..., description="模型类型：llm, embedding, rerank")
    provider: str = Field(..., description="模型提供商：openai, azure, local")
    model_name: str = Field(..., description="模型名称")
    api_key: Optional[str] = Field(None, description="API Key")
    api_base: Optional[str] = Field(None, description="API Base URL")
    config: Optional[Dict[str, Any]] = Field(None, description="额外配置")
    is_active: bool = Field(default=True, description="是否启用")


class ModelConfigUpdate(BaseModel):
    """更新模型配置请求"""
    name: Optional[str] = Field(None, min_length=1, max_length=100, description="配置名称")
    provider: Optional[str] = Field(None, description="模型提供商")
    model_name: Optional[str] = Field(None, description="模型名称")
    api_key: Optional[str] = Field(None, description="API Key")
    api_base: Optional[str] = Field(None, description="API Base URL")
    config: Optional[Dict[str, Any]] = Field(None, description="额外配置")
    is_active: Optional[bool] = Field(None, description="是否启用")


class ModelConfigResponse(BaseModel):
    """模型配置响应"""
    id: str
    user_id: str
    name: str
    model_type: str
    provider: str
    model_name: str
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    is_active: bool
    synced_to_ragflow: bool
    created_at: datetime
    updated_at: datetime
    last_synced_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class ModelConfigListItem(BaseModel):
    """模型配置列表项"""
    id: str
    name: str
    model_type: str
    provider: str
    model_name: str
    is_active: bool
    synced_to_ragflow: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


class SyncToRAGFlowRequest(BaseModel):
    """同步到 RAGFlow 请求"""
    config_ids: List[str] = Field(..., description="要同步的配置 ID 列表")


class SyncToRAGFlowResponse(BaseModel):
    """同步到 RAGFlow 响应"""
    success_count: int
    failed_count: int
    results: List[Dict[str, Any]]


class ModelTestRequest(BaseModel):
    """模型测试请求"""
    config_id: str = Field(..., description="配置 ID")
    test_input: str = Field(..., description="测试输入")


class ModelTestResponse(BaseModel):
    """模型测试响应"""
    success: bool
    output: Optional[str] = None
    execution_time_ms: int
    error: Optional[str] = None
