"""
RAGFlow API 客户端模块
负责：
1. 封装 RAGFlow API 调用
2. 提供知识库、文档、检索、模型管理等接口
3. 管理客户端生命周期
"""

import httpx
from typing import Optional, Dict, Any
import logging

from core.config import settings

logger = logging.getLogger(__name__)


class RAGFlowClient:
    """RAGFlow API 客户端"""
    
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0
        )
    
    # ============ 知识库管理 ============
    
    async def list_datasets(self, page: int = 1, size: int = 20) -> Dict[str, Any]:
        """获取知识库列表"""
        response = await self.client.get(
            "/api/v1/datasets",
            params={"page": page, "size": size}
        )
        response.raise_for_status()
        return response.json()
    
    async def get_dataset(self, dataset_id: str) -> Dict[str, Any]:
        """获取知识库详情"""
        response = await self.client.get(f"/api/v1/datasets/{dataset_id}")
        response.raise_for_status()
        return response.json()
    
    async def create_dataset(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """创建知识库"""
        response = await self.client.post("/api/v1/datasets", json=data)
        response.raise_for_status()
        return response.json()
    
    async def update_dataset(self, dataset_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """更新知识库"""
        response = await self.client.put(f"/api/v1/datasets/{dataset_id}", json=data)
        response.raise_for_status()
        return response.json()
    
    async def delete_dataset(self, dataset_id: str) -> Dict[str, Any]:
        """删除知识库"""
        response = await self.client.delete(f"/api/v1/datasets/{dataset_id}")
        response.raise_for_status()
        return response.json()
    
    # ============ 文档管理 ============
    
    async def list_documents(self, dataset_id: str, page: int = 1, size: int = 20) -> Dict[str, Any]:
        """获取文档列表"""
        response = await self.client.get(
            f"/api/v1/datasets/{dataset_id}/documents",
            params={"page": page, "size": size}
        )
        response.raise_for_status()
        return response.json()
    
    async def upload_document(self, dataset_id: str, file: bytes, filename: str) -> Dict[str, Any]:
        """上传文档"""
        files = {"file": (filename, file)}
        response = await self.client.post(
            f"/api/v1/datasets/{dataset_id}/documents",
            files=files
        )
        response.raise_for_status()
        return response.json()
    
    async def delete_document(self, dataset_id: str, document_id: str) -> Dict[str, Any]:
        """删除文档"""
        response = await self.client.delete(
            f"/api/v1/datasets/{dataset_id}/documents/{document_id}"
        )
        response.raise_for_status()
        return response.json()
    
    # ============ 检索 ============
    
    async def retrieval(self, dataset_id: str, query: str, top_k: int = 5) -> Dict[str, Any]:
        """检索测试"""
        response = await self.client.post(
            f"/api/v1/datasets/{dataset_id}/retrieval",
            json={"query": query, "top_k": top_k}
        )
        response.raise_for_status()
        return response.json()
    
    # ============ 模型管理 ============
    
    async def list_llm(self) -> Dict[str, Any]:
        """获取模型列表"""
        response = await self.client.get("/api/v1/llm")
        response.raise_for_status()
        return response.json()
    
    async def set_llm(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """设置模型配置"""
        response = await self.client.post("/api/v1/llm/set", json=data)
        response.raise_for_status()
        return response.json()
    
    async def close(self):
        """关闭客户端"""
        await self.client.aclose()


# 全局客户端实例
ragflow_client: Optional[RAGFlowClient] = None


def init_ragflow_client() -> RAGFlowClient:
    """初始化 RAGFlow 客户端"""
    global ragflow_client
    if ragflow_client is None:
        ragflow_client = RAGFlowClient(
            base_url=settings.RAGFLOW_BASE_URL,
            api_key=settings.RAGFLOW_API_KEY
        )
        logger.info("RAGFlow client initialized")
    return ragflow_client


def get_ragflow_client() -> RAGFlowClient:
    """获取 RAGFlow 客户端"""
    global ragflow_client
    if ragflow_client is None:
        ragflow_client = init_ragflow_client()
    return ragflow_client


async def close_ragflow_client():
    """关闭 RAGFlow 客户端"""
    global ragflow_client
    if ragflow_client is not None:
        await ragflow_client.close()
        ragflow_client = None
        logger.info("RAGFlow client closed")
