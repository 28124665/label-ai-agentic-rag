"""
知识库服务
代理 RAGFlow 的知识库管理 API
"""

from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
import logging
import time

from core.ragflow_client import get_ragflow_client
from schemas.knowledge import DatasetCreate, DatasetUpdate

logger = logging.getLogger(__name__)


class KnowledgeService:
    """知识库服务类（代理 RAGFlow）"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.client = get_ragflow_client()
    
    async def list_datasets(
        self,
        page: int = 1,
        size: int = 20
    ) -> tuple[List[Dict[str, Any]], int]:
        """
        获取知识库列表
        
        Args:
            page: 页码
            size: 每页数量
        
        Returns:
            (知识库列表, 总数)
        """
        try:
            response = await self.client.list_datasets(page=page, size=size)
            
            # RAGFlow API 返回格式
            datasets = response.get("data", {}).get("items", [])
            total = response.get("data", {}).get("total", 0)
            
            return datasets, total
        except Exception as e:
            logger.error(f"Failed to list datasets: {e}")
            raise
    
    async def get_dataset(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        """
        获取知识库详情
        
        Args:
            dataset_id: 知识库 ID
        
        Returns:
            知识库详情
        """
        try:
            response = await self.client.get_dataset(dataset_id)
            return response.get("data")
        except Exception as e:
            logger.error(f"Failed to get dataset {dataset_id}: {e}")
            return None
    
    async def create_dataset(
        self,
        data: DatasetCreate
    ) -> Optional[Dict[str, Any]]:
        """
        创建知识库
        
        Args:
            data: 创建数据
        
        Returns:
            新创建的知识库
        """
        try:
            payload = {
                "name": data.name,
                "description": data.description,
                "embedding_model": data.embedding_model,
                "chunk_method": data.chunk_method
            }
            if data.parser_config:
                payload["parser_config"] = data.parser_config
            
            response = await self.client.create_dataset(payload)
            return response.get("data")
        except Exception as e:
            logger.error(f"Failed to create dataset: {e}")
            raise
    
    async def update_dataset(
        self,
        dataset_id: str,
        data: DatasetUpdate
    ) -> Optional[Dict[str, Any]]:
        """
        更新知识库
        
        Args:
            dataset_id: 知识库 ID
            data: 更新数据
        
        Returns:
            更新后的知识库
        """
        try:
            payload = {}
            if data.name is not None:
                payload["name"] = data.name
            if data.description is not None:
                payload["description"] = data.description
            if data.embedding_model is not None:
                payload["embedding_model"] = data.embedding_model
            
            response = await self.client.update_dataset(dataset_id, payload)
            return response.get("data")
        except Exception as e:
            logger.error(f"Failed to update dataset {dataset_id}: {e}")
            raise
    
    async def delete_dataset(self, dataset_id: str) -> bool:
        """
        删除知识库
        
        Args:
            dataset_id: 知识库 ID
        
        Returns:
            是否删除成功
        """
        try:
            await self.client.delete_dataset(dataset_id)
            return True
        except Exception as e:
            logger.error(f"Failed to delete dataset {dataset_id}: {e}")
            return False
    
    async def list_documents(
        self,
        dataset_id: str,
        page: int = 1,
        size: int = 20
    ) -> tuple[List[Dict[str, Any]], int]:
        """
        获取文档列表
        
        Args:
            dataset_id: 知识库 ID
            page: 页码
            size: 每页数量
        
        Returns:
            (文档列表, 总数)
        """
        try:
            response = await self.client.list_documents(
                dataset_id=dataset_id,
                page=page,
                size=size
            )
            
            documents = response.get("data", {}).get("items", [])
            total = response.get("data", {}).get("total", 0)
            
            return documents, total
        except Exception as e:
            logger.error(f"Failed to list documents for dataset {dataset_id}: {e}")
            raise
    
    async def upload_document(
        self,
        dataset_id: str,
        file: bytes,
        filename: str
    ) -> Optional[Dict[str, Any]]:
        """
        上传文档
        
        Args:
            dataset_id: 知识库 ID
            file: 文件内容
            filename: 文件名
        
        Returns:
            上传的文档信息
        """
        try:
            response = await self.client.upload_document(
                dataset_id=dataset_id,
                file=file,
                filename=filename
            )
            return response.get("data")
        except Exception as e:
            logger.error(f"Failed to upload document to dataset {dataset_id}: {e}")
            raise
    
    async def delete_document(
        self,
        dataset_id: str,
        document_id: str
    ) -> bool:
        """
        删除文档
        
        Args:
            dataset_id: 知识库 ID
            document_id: 文档 ID
        
        Returns:
            是否删除成功
        """
        try:
            await self.client.delete_document(
                dataset_id=dataset_id,
                document_id=document_id
            )
            return True
        except Exception as e:
            logger.error(f"Failed to delete document {document_id}: {e}")
            return False
    
    async def retrieval(
        self,
        dataset_id: str,
        query: str,
        top_k: int = 5
    ) -> Dict[str, Any]:
        """
        检索测试
        
        Args:
            dataset_id: 知识库 ID
            query: 查询内容
            top_k: 返回文档数量
        
        Returns:
            检索结果
        """
        start_time = time.time()
        
        try:
            response = await self.client.retrieval(
                dataset_id=dataset_id,
                query=query,
                top_k=top_k
            )
            
            execution_time_ms = int((time.time() - start_time) * 1000)
            
            # 构建响应
            results = response.get("data", {}).get("chunks", [])
            
            return {
                "query": query,
                "results": results,
                "total": len(results),
                "execution_time_ms": execution_time_ms
            }
        except Exception as e:
            logger.error(f"Failed to retrieval in dataset {dataset_id}: {e}")
            raise
