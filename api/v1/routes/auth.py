"""
认证路由
提供用户登录、注册、获取当前用户等接口
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from core.database import get_db
from core.auth import get_current_user
from models.user import User
from schemas.auth import LoginRequest, RegisterRequest, LoginResponse, UserResponse
from schemas.response import ResponseBase
from services.auth_service import AuthService
from core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login", response_model=ResponseBase[LoginResponse])
async def login(
    request: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    用户登录
    
    - 验证用户名和密码
    - 返回 JWT Token
    """
    auth_service = AuthService(db)
    user = await auth_service.authenticate(request.username, request.password)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # 创建 Token
    access_token = auth_service.create_access_token(user.id)
    
    # 构建响应
    response_data = LoginResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserResponse.model_validate(user)
    )
    
    logger.info(f"User logged in: {user.username}")
    
    return ResponseBase(data=response_data)


@router.post("/register", response_model=ResponseBase[UserResponse])
async def register(
    request: RegisterRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    用户注册
    
    - 创建新用户
    - 密码加密存储
    """
    auth_service = AuthService(db)
    
    try:
        user = await auth_service.register(
            username=request.username,
            password=request.password,
            email=request.email,
            company=request.company
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    
    logger.info(f"User registered: {user.username}")
    
    return ResponseBase(data=UserResponse.model_validate(user))


@router.get("/me", response_model=ResponseBase[UserResponse])
async def get_current_user_info(
    current_user: User = Depends(get_current_user)
):
    """
    获取当前登录用户信息
    
    - 需要 Bearer Token 认证
    """
    return ResponseBase(data=UserResponse.model_validate(current_user))


@router.post("/refresh", response_model=ResponseBase[dict])
async def refresh_token(
    current_user: User = Depends(get_current_user)
):
    """
    刷新 Token
    
    - 需要 Bearer Token 认证
    - 返回新的访问令牌
    """
    auth_service = AuthService(None)  # 不需要数据库会话
    new_token = auth_service.create_access_token(current_user.id)
    
    return ResponseBase(
        data={
            "access_token": new_token,
            "token_type": "bearer",
            "expires_in": settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
        }
    )
