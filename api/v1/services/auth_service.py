"""
认证服务
负责用户认证、注册、Token 管理
"""

from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import logging

from models.user import User
from core.auth import verify_password, get_password_hash, create_access_token
from core.config import settings

logger = logging.getLogger(__name__)


class AuthService:
    """认证服务类"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def authenticate(self, username: str, password: str) -> Optional[User]:
        """
        验证用户凭证
        
        Args:
            username: 用户名
            password: 明文密码
        
        Returns:
            User 对象（如果验证成功），否则 None
        """
        # 查询用户
        result = await self.db.execute(
            select(User).where(User.username == username)
        )
        user = result.scalar_one_or_none()
        
        if user is None:
            return None
        
        # 验证密码
        if not verify_password(password, user.password_hash):
            return None
        
        # 检查用户状态
        if not user.is_active:
            return None
        
        # 更新最后登录时间
        user.last_login_at = datetime.utcnow()
        await self.db.commit()
        
        return user
    
    async def register(
        self,
        username: str,
        password: str,
        email: str,
        company: Optional[str] = None
    ) -> User:
        """
        注册新用户
        
        Args:
            username: 用户名
            password: 明文密码
            email: 邮箱
            company: 公司（可选）
        
        Returns:
            新创建的 User 对象
        
        Raises:
            ValueError: 如果用户名或邮箱已存在
        """
        # 检查用户名是否已存在
        result = await self.db.execute(
            select(User).where(User.username == username)
        )
        if result.scalar_one_or_none() is not None:
            raise ValueError("用户名已存在")
        
        # 检查邮箱是否已存在
        result = await self.db.execute(
            select(User).where(User.email == email)
        )
        if result.scalar_one_or_none() is not None:
            raise ValueError("邮箱已被注册")
        
        # 创建新用户
        user = User(
            username=username,
            email=email,
            password_hash=get_password_hash(password),
            company=company,
            role="user",
            is_active=True
        )
        
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        
        logger.info(f"New user registered: {username}")
        return user
    
    def create_access_token(self, user_id: str) -> str:
        """
        创建访问令牌
        
        Args:
            user_id: 用户 ID
        
        Returns:
            JWT token 字符串
        """
        expires_delta = timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
        return create_access_token(user_id, expires_delta)
    
    async def get_user_by_id(self, user_id: str) -> Optional[User]:
        """
        根据 ID 获取用户
        
        Args:
            user_id: 用户 ID
        
        Returns:
            User 对象（如果存在），否则 None
        """
        result = await self.db.execute(
            select(User).where(User.id == user_id)
        )
        return result.scalar_one_or_none()
    
    async def get_user_by_username(self, username: str) -> Optional[User]:
        """
        根据用户名获取用户
        
        Args:
            username: 用户名
        
        Returns:
            User 对象（如果存在），否则 None
        """
        result = await self.db.execute(
            select(User).where(User.username == username)
        )
        return result.scalar_one_or_none()
