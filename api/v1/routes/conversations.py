"""
对话路由
提供对话管理、消息发送等接口
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from core.database import get_db
from core.auth import get_current_user
from models.user import User
from schemas.conversation import (
    ConversationCreate,
    ConversationUpdate,
    ConversationResponse,
    ConversationListItem,
    MessageCreate,
    MessageResponse
)
from schemas.response import ResponseBase, PageResponse, PageData
from services.conversation_service import ConversationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["对话"])


@router.post("", response_model=ResponseBase[ConversationResponse])
async def create_conversation(
    request: ConversationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    创建新对话
    
    - 创建对话记录
    - 可选关联 Agent
    """
    service = ConversationService(db)
    conversation = await service.create(
        user_id=current_user.id,
        agent_id=request.agent_id,
        title=request.title
    )
    
    return ResponseBase(data=ConversationResponse.model_validate(conversation))


@router.get("", response_model=PageResponse[ConversationListItem])
async def list_conversations(
    page: int = 1,
    size: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取对话列表
    
    - 分页查询
    - 按最后消息时间排序
    """
    service = ConversationService(db)
    conversations, total = await service.list(
        user_id=current_user.id,
        page=page,
        size=size
    )
    
    pages = (total + size - 1) // size
    
    return PageResponse(
        data=PageData(
            items=[ConversationListItem.model_validate(c) for c in conversations],
            total=total,
            page=page,
            size=size,
            pages=pages
        )
    )


@router.get("/{conversation_id}", response_model=ResponseBase[ConversationResponse])
async def get_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取对话详情
    
    - 验证用户权限
    """
    service = ConversationService(db)
    conversation = await service.get(conversation_id, current_user.id)
    
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="对话不存在"
        )
    
    return ResponseBase(data=ConversationResponse.model_validate(conversation))


@router.put("/{conversation_id}", response_model=ResponseBase[ConversationResponse])
async def update_conversation(
    conversation_id: str,
    request: ConversationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    更新对话
    
    - 修改对话标题
    """
    service = ConversationService(db)
    conversation = await service.update(
        conversation_id=conversation_id,
        user_id=current_user.id,
        update_data=request
    )
    
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="对话不存在"
        )
    
    return ResponseBase(data=ConversationResponse.model_validate(conversation))


@router.delete("/{conversation_id}", response_model=ResponseBase[dict])
async def delete_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    删除对话
    
    - 级联删除所有消息
    """
    service = ConversationService(db)
    success = await service.delete(conversation_id, current_user.id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="对话不存在"
        )
    
    return ResponseBase(data={"deleted": True})


@router.get("/{conversation_id}/messages", response_model=ResponseBase[list[MessageResponse]])
async def get_messages(
    conversation_id: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取对话消息历史
    
    - 按时间顺序返回
    """
    service = ConversationService(db)
    messages = await service.get_messages(
        conversation_id=conversation_id,
        user_id=current_user.id,
        limit=limit
    )
    
    if messages is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="对话不存在"
        )
    
    return ResponseBase(
        data=[MessageResponse.model_validate(m) for m in messages]
    )


@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    request: MessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    发送消息（流式响应）
    
    - 使用 Server-Sent Events (SSE)
    - 实时返回 AI 回复
    """
    service = ConversationService(db)
    
    return StreamingResponse(
        service.stream_response(
            conversation_id=conversation_id,
            user_message=request.content,
            user_id=current_user.id
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # 禁用 Nginx 缓冲
        }
    )
