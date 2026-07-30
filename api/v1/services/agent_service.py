"""
Agent 服务
负责 Agent 配置管理
"""

from datetime import datetime
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
import logging

from models.agent import Agent
from schemas.agent import AgentCreate, AgentUpdate

logger = logging.getLogger(__name__)


class AgentService:
    """Agent 服务类"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create(
        self,
        user_id: str,
        data: AgentCreate
    ) -> Agent:
        """
        创建 Agent
        
        Args:
            user_id: 用户 ID
            data: 创建数据
        
        Returns:
            新创建的 Agent 对象
        """
        agent = Agent(
            user_id=user_id,
            name=data.name,
            description=data.description,
            tools_config=data.tools_config,
            routing_config=data.routing_config,
            degradation_config=data.degradation_config,
            model_config=data.model_cfg,
            is_active=True
        )
        
        self.db.add(agent)
        await self.db.commit()
        await self.db.refresh(agent)
        
        logger.info(f"Agent created: {agent.id} by user {user_id}")
        return agent
    
    async def get(self, agent_id: str, user_id: str) -> Optional[Agent]:
        """
        获取 Agent 详情
        
        Args:
            agent_id: Agent ID
            user_id: 用户 ID
        
        Returns:
            Agent 对象（如果存在且属于该用户）
        """
        result = await self.db.execute(
            select(Agent)
            .where(
                Agent.id == agent_id,
                Agent.user_id == user_id
            )
        )
        return result.scalar_one_or_none()
    
    async def list(
        self,
        user_id: str,
        page: int = 1,
        size: int = 20
    ) -> tuple[List[Agent], int]:
        """
        获取 Agent 列表
        
        Args:
            user_id: 用户 ID
            page: 页码
            size: 每页数量
        
        Returns:
            (Agent 列表, 总数)
        """
        # 查询总数
        count_result = await self.db.execute(
            select(func.count(Agent.id))
            .where(Agent.user_id == user_id)
        )
        total = count_result.scalar()
        
        # 查询列表
        offset = (page - 1) * size
        result = await self.db.execute(
            select(Agent)
            .where(Agent.user_id == user_id)
            .order_by(Agent.created_at.desc())
            .offset(offset)
            .limit(size)
        )
        agents = result.scalars().all()
        
        return agents, total
    
    async def update(
        self,
        agent_id: str,
        user_id: str,
        update_data: AgentUpdate
    ) -> Optional[Agent]:
        """
        更新 Agent
        
        Args:
            agent_id: Agent ID
            user_id: 用户 ID
            update_data: 更新数据
        
        Returns:
            更新后的 Agent 对象
        """
        agent = await self.get(agent_id, user_id)
        if not agent:
            return None
        
        # 更新字段
        if update_data.name is not None:
            agent.name = update_data.name
        if update_data.description is not None:
            agent.description = update_data.description
        if update_data.tools_config is not None:
            agent.tools_config = update_data.tools_config
        if update_data.routing_config is not None:
            agent.routing_config = update_data.routing_config
        if update_data.degradation_config is not None:
            agent.degradation_config = update_data.degradation_config
        if update_data.model_cfg is not None:
            agent.model_config = update_data.model_cfg
        if update_data.is_active is not None:
            agent.is_active = update_data.is_active
        
        agent.updated_at = datetime.utcnow()
        await self.db.commit()
        await self.db.refresh(agent)
        
        return agent
    
    async def delete(self, agent_id: str, user_id: str) -> bool:
        """
        删除 Agent
        
        Args:
            agent_id: Agent ID
            user_id: 用户 ID
        
        Returns:
            是否删除成功
        """
        agent = await self.get(agent_id, user_id)
        if not agent:
            return False
        
        await self.db.delete(agent)
        await self.db.commit()
        
        logger.info(f"Agent deleted: {agent_id}")
        return True
    
    async def get_default_agent(self, user_id: str) -> Optional[Agent]:
        """
        获取用户的默认 Agent
        
        Args:
            user_id: 用户 ID
        
        Returns:
            默认 Agent（如果存在）
        """
        result = await self.db.execute(
            select(Agent)
            .where(
                Agent.user_id == user_id,
                Agent.is_active
            )
            .order_by(Agent.created_at)
            .limit(1)
        )
        return result.scalar_one_or_none()
