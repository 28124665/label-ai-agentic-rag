"""
统一响应格式定义
"""

from typing import Optional, Generic, TypeVar, List
from pydantic import BaseModel

T = TypeVar("T")


class ResponseBase(BaseModel, Generic[T]):
    """统一响应格式"""
    code: int = 0  # 0 表示成功，非 0 表示失败
    message: str = "success"
    data: Optional[T] = None
    error: Optional[str] = None


class PageData(BaseModel, Generic[T]):
    """分页数据"""
    items: List[T]
    total: int
    page: int
    size: int
    pages: int


class PageResponse(BaseModel, Generic[T]):
    """分页响应"""
    code: int = 0
    message: str = "success"
    data: Optional[PageData[T]] = None
