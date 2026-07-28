"""
模型配置路由
提供模型配置管理和同步接口
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from core.database import get_db
from core.auth import get_current_user
from models.user import User
from schemas.model_config import (
    ModelConfigCreate,
    ModelConfigUpdate,
    ModelConfigResponse,
    ModelConfigListItem,
    SyncToRAGFlowRequest,
    SyncToRAGFlowResponse,
    ModelTestRequest,
    ModelTestResponse
)
from schemas.response import ResponseBase, PageResponse, PageData
from services.model_config_service import ModelConfigService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/model-configs", tags=["模型配置"])


@router.post("", response_model=ResponseBase[ModelConfigResponse])
async def create_model_config(
    request: ModelConfigCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    创建模型配置
    
    - 存储模型配置信息
    - API Key 加密存储
    """
    service = ModelConfigService(db)
    config = await service.create(
        user_id=current_user.id,
        data=request
    )
    
    return ResponseBase(data=ModelConfigResponse.model_validate(config))


@router.get("", response_model=PageResponse[ModelConfigListItem])
async def list_model_configs(
    page: int = 1,
    size: int = 20,
    model_type: str = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取模型配置列表
    
    - 分页查询
    - 支持按模型类型过滤
    """
    service = ModelConfigService(db)
    configs, total = await service.list(
        user_id=current_user.id,
        page=page,
        size=size,
        model_type=model_type
    )
    
    pages = (total + size - 1) // size
    
    return PageResponse(
        data=PageData(
            items=[ModelConfigListItem.model_validate(c) for c in configs],
            total=total,
            page=page,
            size=size,
            pages=pages
        )
    )


@router.get("/{config_id}", response_model=ResponseBase[ModelConfigResponse])
async def get_model_config(
    config_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取模型配置详情
    
    - 验证用户权限
    """
    service = ModelConfigService(db)
    config = await service.get(config_id, current_user.id)
    
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="配置不存在"
        )
    
    return ResponseBase(data=ModelConfigResponse.model_validate(config))


@router.put("/{config_id}", response_model=ResponseBase[ModelConfigResponse])
async def update_model_config(
    config_id: str,
    request: ModelConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    更新模型配置
    
    - 修改配置信息
    """
    service = ModelConfigService(db)
    config = await service.update(
        config_id=config_id,
        user_id=current_user.id,
        update_data=request
    )
    
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="配置不存在"
        )
    
    return ResponseBase(data=ModelConfigResponse.model_validate(config))


@router.delete("/{config_id}", response_model=ResponseBase[dict])
async def delete_model_config(
    config_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    删除模型配置
    """
    service = ModelConfigService(db)
    success = await service.delete(config_id, current_user.id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="配置不存在"
        )
    
    return ResponseBase(data={"deleted": True})


@router.post("/sync", response_model=ResponseBase[SyncToRAGFlowResponse])
async def sync_to_ragflow(
    request: SyncToRAGFlowRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    同步模型配置到 RAGFlow
    
    - 批量同步
    - 返回同步结果
    """
    service = ModelConfigService(db)
    result = await service.sync_to_ragflow(
        user_id=current_user.id,
        config_ids=request.config_ids
    )
    
    return ResponseBase(data=SyncToRAGFlowResponse(**result))


@router.post("/{config_id}/test", response_model=ResponseBase[ModelTestResponse])
async def test_model(
    config_id: str,
    request: ModelTestRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    测试模型配置
    
    - 验证配置是否可用
    """
    service = ModelConfigService(db)
    result = await service.test_model(
        config_id=config_id,
        user_id=current_user.id,
        test_input=request.test_input
    )
    
    return ResponseBase(data=ModelTestResponse(**result))
