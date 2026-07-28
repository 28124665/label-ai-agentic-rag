"""
Agent 路由
提供 Agent 配置管理接口
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from core.database import get_db
from core.auth import get_current_user
from models.user import User
from schemas.agent import (
    AgentCreate,
    AgentUpdate,
    AgentResponse,
    AgentListItem
)
from schemas.response import ResponseBase, PageResponse, PageData
from services.agent_service import AgentService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["Agent"])


@router.post("", response_model=ResponseBase[AgentResponse])
async def create_agent(
    request: AgentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    创建 Agent
    
    - 配置工具、路由策略、降级策略
    """
    service = AgentService(db)
    agent = await service.create(
        user_id=current_user.id,
        data=request
    )
    
    return ResponseBase(data=AgentResponse.model_validate(agent))


@router.get("", response_model=PageResponse[AgentListItem])
async def list_agents(
    page: int = 1,
    size: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取 Agent 列表
    
    - 分页查询
    """
    service = AgentService(db)
    agents, total = await service.list(
        user_id=current_user.id,
        page=page,
        size=size
    )
    
    pages = (total + size - 1) // size
    
    return PageResponse(
        data=PageData(
            items=[AgentListItem.model_validate(a) for a in agents],
            total=total,
            page=page,
            size=size,
            pages=pages
        )
    )


@router.get("/{agent_id}", response_model=ResponseBase[AgentResponse])
async def get_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取 Agent 详情
    
    - 验证用户权限
    """
    service = AgentService(db)
    agent = await service.get(agent_id, current_user.id)
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent 不存在"
        )
    
    return ResponseBase(data=AgentResponse.model_validate(agent))


@router.put("/{agent_id}", response_model=ResponseBase[AgentResponse])
async def update_agent(
    agent_id: str,
    request: AgentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    更新 Agent
    
    - 修改配置
    """
    service = AgentService(db)
    agent = await service.update(
        agent_id=agent_id,
        user_id=current_user.id,
        update_data=request
    )
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent 不存在"
        )
    
    return ResponseBase(data=AgentResponse.model_validate(agent))


@router.delete("/{agent_id}", response_model=ResponseBase[dict])
async def delete_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    删除 Agent
    """
    service = AgentService(db)
    success = await service.delete(agent_id, current_user.id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent 不存在"
        )
    
    return ResponseBase(data={"deleted": True})


@router.get("/default", response_model=ResponseBase[AgentResponse])
async def get_default_agent(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取默认 Agent
    
    - 返回第一个创建的 Agent
    """
    service = AgentService(db)
    agent = await service.get_default_agent(current_user.id)
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="没有可用的 Agent"
        )
    
    return ResponseBase(data=AgentResponse.model_validate(agent))
