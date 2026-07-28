"""
数据源路由
提供数据库连接管理、Schema 探查、SQL 执行等接口
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from core.database import get_db
from core.auth import get_current_user
from models.user import User
from schemas.datasource import (
    DataSourceCreate,
    DataSourceUpdate,
    DataSourceResponse,
    DataSourceListItem,
    ConnectionTestResponse,
    TableInfo,
    TableDetail,
    SQLExecuteRequest,
    SQLExecuteResponse
)
from schemas.response import ResponseBase, PageResponse, PageData
from services.datasource_service import DataSourceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/datasources", tags=["数据源"])


@router.post("", response_model=ResponseBase[DataSourceResponse])
async def create_datasource(
    request: DataSourceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    创建数据源
    
    - 存储数据库连接信息
    - 密码加密存储
    """
    service = DataSourceService(db)
    datasource = await service.create(
        user_id=current_user.id,
        data=request
    )
    
    return ResponseBase(data=DataSourceResponse.model_validate(datasource))


@router.get("", response_model=PageResponse[DataSourceListItem])
async def list_datasources(
    page: int = 1,
    size: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取数据源列表
    
    - 分页查询
    - 按创建时间倒序
    """
    service = DataSourceService(db)
    datasources, total = await service.list(
        user_id=current_user.id,
        page=page,
        size=size
    )
    
    pages = (total + size - 1) // size
    
    return PageResponse(
        data=PageData(
            items=[DataSourceListItem.model_validate(d) for d in datasources],
            total=total,
            page=page,
            size=size,
            pages=pages
        )
    )


@router.get("/{datasource_id}", response_model=ResponseBase[DataSourceResponse])
async def get_datasource(
    datasource_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取数据源详情
    
    - 验证用户权限
    """
    service = DataSourceService(db)
    datasource = await service.get(datasource_id, current_user.id)
    
    if not datasource:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="数据源不存在"
        )
    
    return ResponseBase(data=DataSourceResponse.model_validate(datasource))


@router.put("/{datasource_id}", response_model=ResponseBase[DataSourceResponse])
async def update_datasource(
    datasource_id: str,
    request: DataSourceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    更新数据源
    
    - 修改连接配置
    """
    service = DataSourceService(db)
    datasource = await service.update(
        datasource_id=datasource_id,
        user_id=current_user.id,
        update_data=request
    )
    
    if not datasource:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="数据源不存在"
        )
    
    return ResponseBase(data=DataSourceResponse.model_validate(datasource))


@router.delete("/{datasource_id}", response_model=ResponseBase[dict])
async def delete_datasource(
    datasource_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    删除数据源
    """
    service = DataSourceService(db)
    success = await service.delete(datasource_id, current_user.id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="数据源不存在"
        )
    
    return ResponseBase(data={"deleted": True})


@router.post("/{datasource_id}/test", response_model=ResponseBase[ConnectionTestResponse])
async def test_connection(
    datasource_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    测试数据源连接
    
    - 验证连接配置
    - 返回连接状态
    """
    service = DataSourceService(db)
    result = await service.test_connection(datasource_id, current_user.id)
    
    return ResponseBase(data=ConnectionTestResponse(**result))


@router.get("/{datasource_id}/tables", response_model=ResponseBase[list[TableInfo]])
async def list_tables(
    datasource_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取表列表
    
    - Schema 探查
    """
    service = DataSourceService(db)
    tables = await service.list_tables(datasource_id, current_user.id)
    
    return ResponseBase(data=[TableInfo(**t) for t in tables])


@router.get("/{datasource_id}/tables/{table_name}", response_model=ResponseBase[TableDetail])
async def describe_table(
    datasource_id: str,
    table_name: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取表详情
    
    - 列信息、主键、注释
    """
    service = DataSourceService(db)
    table = await service.describe_table(datasource_id, current_user.id, table_name)
    
    if not table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="表不存在"
        )
    
    return ResponseBase(data=TableDetail(**table))


@router.post("/{datasource_id}/execute", response_model=ResponseBase[SQLExecuteResponse])
async def execute_sql(
    datasource_id: str,
    request: SQLExecuteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    执行 SQL 查询
    
    - 安全检查（只允许 SELECT）
    - 限制返回行数
    """
    service = DataSourceService(db)
    
    try:
        result = await service.execute_sql(
            datasource_id=datasource_id,
            user_id=current_user.id,
            sql=request.sql,
            max_rows=request.max_rows
        )
        return ResponseBase(data=SQLExecuteResponse(**result))
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
