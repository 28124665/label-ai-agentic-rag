"""
数据源相关 Pydantic 模型
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class DataSourceCreate(BaseModel):
    """创建数据源请求"""
    name: str = Field(..., min_length=1, max_length=100, description="数据源名称")
    description: Optional[str] = Field(None, max_length=500, description="数据源描述")
    db_type: str = Field(..., description="数据库类型：mysql, postgresql, oracle, sqlserver")
    host: str = Field(..., description="主机地址")
    port: int = Field(..., ge=1, le=65535, description="端口")
    database: str = Field(..., description="数据库名")
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")
    extra_config: Optional[Dict[str, Any]] = Field(None, description="额外配置")


class DataSourceUpdate(BaseModel):
    """更新数据源请求"""
    name: Optional[str] = Field(None, min_length=1, max_length=100, description="数据源名称")
    description: Optional[str] = Field(None, max_length=500, description="数据源描述")
    host: Optional[str] = Field(None, description="主机地址")
    port: Optional[int] = Field(None, ge=1, le=65535, description="端口")
    database: Optional[str] = Field(None, description="数据库名")
    username: Optional[str] = Field(None, description="用户名")
    password: Optional[str] = Field(None, description="密码")
    extra_config: Optional[Dict[str, Any]] = Field(None, description="额外配置")


class DataSourceResponse(BaseModel):
    """数据源响应"""
    id: str
    user_id: str
    name: str
    description: Optional[str] = None
    db_type: str
    host: str
    port: int
    database: str
    username: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_tested_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class DataSourceListItem(BaseModel):
    """数据源列表项"""
    id: str
    name: str
    db_type: str
    host: str
    port: int
    database: str
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


class ConnectionTestRequest(BaseModel):
    """连接测试请求"""
    host: str
    port: int
    database: str
    username: str
    password: str
    db_type: str


class ConnectionTestResponse(BaseModel):
    """连接测试响应"""
    success: bool
    message: str
    execution_time_ms: int


class TableInfo(BaseModel):
    """表信息"""
    name: str
    comment: Optional[str] = None
    row_count: Optional[int] = None


class ColumnInfo(BaseModel):
    """列信息"""
    name: str
    data_type: str
    nullable: bool
    comment: Optional[str] = None
    is_primary_key: bool = False


class TableDetail(BaseModel):
    """表详情"""
    name: str
    comment: Optional[str] = None
    columns: List[ColumnInfo]
    row_count: Optional[int] = None


class SQLExecuteRequest(BaseModel):
    """SQL 执行请求"""
    sql: str = Field(..., min_length=1, description="SQL 语句")
    max_rows: int = Field(default=100, ge=1, le=10000, description="最大返回行数")


class SQLExecuteResponse(BaseModel):
    """SQL 执行响应"""
    sql: str
    columns: List[str]
    rows: List[Dict[str, Any]]
    row_count: int
    execution_time_ms: int
    truncated: bool
