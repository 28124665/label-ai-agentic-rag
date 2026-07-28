"""
模型配置服务
负责模型配置管理和同步到 RAGFlow
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
import logging
import base64

from models.model_config import ModelConfig
from schemas.model_config import ModelConfigCreate, ModelConfigUpdate
from core.ragflow_client import get_ragflow_client

logger = logging.getLogger(__name__)


class ModelConfigService:
    """模型配置服务类"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.ragflow_client = get_ragflow_client()
    
    async def create(
        self,
        user_id: str,
        data: ModelConfigCreate
    ) -> ModelConfig:
        """
        创建模型配置
        
        Args:
            user_id: 用户 ID
            data: 创建数据
        
        Returns:
            新创建的 ModelConfig 对象
        """
        # 加密 API Key
        api_key_encrypted = None
        if data.api_key:
            api_key_encrypted = self._encrypt_api_key(data.api_key)
        
        config = ModelConfig(
            user_id=user_id,
            name=data.name,
            model_type=data.model_type,
            provider=data.provider,
            model_name=data.model_name,
            api_key=api_key_encrypted,
            api_base=data.api_base,
            config=data.config,
            is_active=data.is_active,
            synced_to_ragflow=False
        )
        
        self.db.add(config)
        await self.db.commit()
        await self.db.refresh(config)
        
        logger.info(f"ModelConfig created: {config.id} by user {user_id}")
        return config
    
    async def get(self, config_id: str, user_id: str) -> Optional[ModelConfig]:
        """
        获取模型配置详情
        
        Args:
            config_id: 配置 ID
            user_id: 用户 ID
        
        Returns:
            ModelConfig 对象（如果存在且属于该用户）
        """
        result = await self.db.execute(
            select(ModelConfig)
            .where(
                ModelConfig.id == config_id,
                ModelConfig.user_id == user_id
            )
        )
        return result.scalar_one_or_none()
    
    async def list(
        self,
        user_id: str,
        page: int = 1,
        size: int = 20,
        model_type: Optional[str] = None
    ) -> tuple[List[ModelConfig], int]:
        """
        获取模型配置列表
        
        Args:
            user_id: 用户 ID
            page: 页码
            size: 每页数量
            model_type: 模型类型过滤（可选）
        
        Returns:
            (配置列表, 总数)
        """
        # 构建查询条件
        conditions = [ModelConfig.user_id == user_id]
        if model_type:
            conditions.append(ModelConfig.model_type == model_type)
        
        # 查询总数
        count_result = await self.db.execute(
            select(func.count(ModelConfig.id))
            .where(*conditions)
        )
        total = count_result.scalar()
        
        # 查询列表
        offset = (page - 1) * size
        result = await self.db.execute(
            select(ModelConfig)
            .where(*conditions)
            .order_by(ModelConfig.created_at.desc())
            .offset(offset)
            .limit(size)
        )
        configs = result.scalars().all()
        
        return configs, total
    
    async def update(
        self,
        config_id: str,
        user_id: str,
        update_data: ModelConfigUpdate
    ) -> Optional[ModelConfig]:
        """
        更新模型配置
        
        Args:
            config_id: 配置 ID
            user_id: 用户 ID
            update_data: 更新数据
        
        Returns:
            更新后的 ModelConfig 对象
        """
        config = await self.get(config_id, user_id)
        if not config:
            return None
        
        # 更新字段
        if update_data.name is not None:
            config.name = update_data.name
        if update_data.provider is not None:
            config.provider = update_data.provider
        if update_data.model_name is not None:
            config.model_name = update_data.model_name
        if update_data.api_key is not None:
            config.api_key = self._encrypt_api_key(update_data.api_key)
        if update_data.api_base is not None:
            config.api_base = update_data.api_base
        if update_data.config is not None:
            config.config = update_data.config
        if update_data.is_active is not None:
            config.is_active = update_data.is_active
        
        # 标记为未同步
        config.synced_to_ragflow = False
        
        config.updated_at = datetime.utcnow()
        await self.db.commit()
        await self.db.refresh(config)
        
        return config
    
    async def delete(self, config_id: str, user_id: str) -> bool:
        """
        删除模型配置
        
        Args:
            config_id: 配置 ID
            user_id: 用户 ID
        
        Returns:
            是否删除成功
        """
        config = await self.get(config_id, user_id)
        if not config:
            return False
        
        await self.db.delete(config)
        await self.db.commit()
        
        logger.info(f"ModelConfig deleted: {config_id}")
        return True
    
    async def sync_to_ragflow(
        self,
        user_id: str,
        config_ids: List[str]
    ) -> Dict[str, Any]:
        """
        同步模型配置到 RAGFlow
        
        Args:
            user_id: 用户 ID
            config_ids: 配置 ID 列表
        
        Returns:
            同步结果
        """
        success_count = 0
        failed_count = 0
        results = []
        
        for config_id in config_ids:
            config = await self.get(config_id, user_id)
            if not config:
                failed_count += 1
                results.append({
                    "config_id": config_id,
                    "success": False,
                    "error": "配置不存在"
                })
                continue
            
            try:
                # 构建 RAGFlow API 请求
                ragflow_data = {
                    "model_type": config.model_type,
                    "provider": config.provider,
                    "model_name": config.model_name,
                    "config": config.config or {}
                }
                
                # 解密 API Key
                if config.api_key:
                    ragflow_data["api_key"] = self._decrypt_api_key(config.api_key)
                
                if config.api_base:
                    ragflow_data["api_base"] = config.api_base
                
                # 调用 RAGFlow API
                await self.ragflow_client.set_llm(ragflow_data)
                
                # 更新同步状态
                config.synced_to_ragflow = True
                config.last_synced_at = datetime.utcnow()
                await self.db.commit()
                
                success_count += 1
                results.append({
                    "config_id": config_id,
                    "success": True
                })
                
                logger.info(f"ModelConfig synced to RAGFlow: {config_id}")
                
            except Exception as e:
                failed_count += 1
                results.append({
                    "config_id": config_id,
                    "success": False,
                    "error": str(e)
                })
                logger.error(f"Failed to sync ModelConfig {config_id}: {e}")
        
        return {
            "success_count": success_count,
            "failed_count": failed_count,
            "results": results
        }
    
    async def test_model(
        self,
        config_id: str,
        user_id: str,
        test_input: str
    ) -> Dict[str, Any]:
        """
        测试模型配置
        
        Args:
            config_id: 配置 ID
            user_id: 用户 ID
            test_input: 测试输入
        
        Returns:
            测试结果
        """
        config = await self.get(config_id, user_id)
        if not config:
            return {
                "success": False,
                "output": None,
                "execution_time_ms": 0,
                "error": "配置不存在"
            }
        
        import time
        start_time = time.time()
        
        try:
            # TODO: 实现实际的模型调用
            # 这里暂时返回模拟结果
            await self._call_model(config, test_input)
            
            execution_time_ms = int((time.time() - start_time) * 1000)
            
            return {
                "success": True,
                "output": f"模型 {config.model_name} 测试成功，输入：{test_input}",
                "execution_time_ms": execution_time_ms,
                "error": None
            }
        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Model test failed: {e}")
            return {
                "success": False,
                "output": None,
                "execution_time_ms": execution_time_ms,
                "error": str(e)
            }
    
    def _encrypt_api_key(self, api_key: str) -> str:
        """加密 API Key（简单 base64 编码，生产环境应使用更强的加密）"""
        return base64.b64encode(api_key.encode()).decode()
    
    def _decrypt_api_key(self, api_key_encrypted: str) -> str:
        """解密 API Key"""
        return base64.b64decode(api_key_encrypted.encode()).decode()
    
    async def _call_model(self, config: ModelConfig, input_text: str):
        """调用模型（占位符）"""
        # TODO: 实现实际的模型调用
        pass
