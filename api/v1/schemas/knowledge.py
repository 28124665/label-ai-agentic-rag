"""
知识库相关 Pydantic 模型
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class DatasetCreate(BaseModel):
    """创建知识库请求"""
    name: str = Field(..., min_length=1, max_length=100, description="知识库名称")
    description: Optional[str] = Field(None, max_length=500, description="知识库描述")
    embedding_model: str = Field(default="BAAI/bge-large-zh", description="Embedding 模型")
    chunk_method: str = Field(default="naive", description="分块方法")
    parser_config: Optional[Dict[str, Any]] = Field(None, description="解析配置")


class DatasetUpdate(BaseModel):
    """更新知识库请求"""
    name: Optional[str] = Field(None, min_length=1, max_length=100, description="知识库名称")
    description: Optional[str] = Field(None, max_length=500, description="知识库描述")
    embedding_model: Optional[str] = Field(None, description="Embedding 模型")


class DatasetResponse(BaseModel):
    """知识库响应"""
    id: str
    name: str
    description: Optional[str] = None
    embedding_model: str
    chunk_count: int = 0
    document_count: int = 0
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class DatasetListItem(BaseModel):
    """知识库列表项"""
    id: str
    name: str
    description: Optional[str] = None
    document_count: int = 0
    chunk_count: int = 0
    created_at: datetime
    
    class Config:
        from_attributes = True


class DocumentResponse(BaseModel):
    """文档响应"""
    id: str
    name: str
    size: int
    chunk_count: int = 0
    status: str  # uploading, parsing, completed, failed
    created_at: datetime
    
    class Config:
        from_attributes = True


class RetrievalRequest(BaseModel):
    """检索请求"""
    query: str = Field(..., min_length=1, description="查询内容")
    top_k: int = Field(default=5, ge=1, le=50, description="返回文档数量")
    similarity_threshold: float = Field(default=0.7, ge=0.0, le=1.0, description="相似度阈值")


class RetrievalResult(BaseModel):
    """检索结果"""
    content: str
    similarity: float
    document_name: str
    chunk_id: str
    metadata: Optional[Dict[str, Any]] = None


class RetrievalResponse(BaseModel):
    """检索响应"""
    query: str
    results: List[RetrievalResult]
    total: int
    execution_time_ms: int
