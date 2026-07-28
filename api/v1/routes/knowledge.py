"""
知识库路由
代理 RAGFlow 的知识库管理 API
"""

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from core.database import get_db
from core.auth import get_current_user
from models.user import User
from schemas.knowledge import (
    DatasetCreate,
    DatasetUpdate,
    DatasetResponse,
    DatasetListItem,
    DocumentResponse,
    RetrievalRequest,
    RetrievalResponse
)
from schemas.response import ResponseBase, PageResponse, PageData
from services.knowledge_service import KnowledgeService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["知识库"])


@router.get("/datasets", response_model=PageResponse[DatasetListItem])
async def list_datasets(
    page: int = 1,
    size: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取知识库列表
    
    - 代理 RAGFlow API
    """
    service = KnowledgeService(db)
    datasets, total = await service.list_datasets(page=page, size=size)
    
    pages = (total + size - 1) // size
    
    return PageResponse(
        data=PageData(
            items=[DatasetListItem(**d) for d in datasets],
            total=total,
            page=page,
            size=size,
            pages=pages
        )
    )


@router.get("/datasets/{dataset_id}", response_model=ResponseBase[DatasetResponse])
async def get_dataset(
    dataset_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取知识库详情
    
    - 代理 RAGFlow API
    """
    service = KnowledgeService(db)
    dataset = await service.get_dataset(dataset_id)
    
    if not dataset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识库不存在"
        )
    
    return ResponseBase(data=DatasetResponse(**dataset))


@router.post("/datasets", response_model=ResponseBase[DatasetResponse])
async def create_dataset(
    request: DatasetCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    创建知识库
    
    - 代理 RAGFlow API
    """
    service = KnowledgeService(db)
    
    try:
        dataset = await service.create_dataset(request)
        return ResponseBase(data=DatasetResponse(**dataset))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.put("/datasets/{dataset_id}", response_model=ResponseBase[DatasetResponse])
async def update_dataset(
    dataset_id: str,
    request: DatasetUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    更新知识库
    
    - 代理 RAGFlow API
    """
    service = KnowledgeService(db)
    
    try:
        dataset = await service.update_dataset(dataset_id, request)
        if not dataset:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="知识库不存在"
            )
        return ResponseBase(data=DatasetResponse(**dataset))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.delete("/datasets/{dataset_id}", response_model=ResponseBase[dict])
async def delete_dataset(
    dataset_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    删除知识库
    
    - 代理 RAGFlow API
    """
    service = KnowledgeService(db)
    success = await service.delete_dataset(dataset_id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="删除失败"
        )
    
    return ResponseBase(data={"deleted": True})


@router.get("/datasets/{dataset_id}/documents", response_model=PageResponse[DocumentResponse])
async def list_documents(
    dataset_id: str,
    page: int = 1,
    size: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取文档列表
    
    - 代理 RAGFlow API
    """
    service = KnowledgeService(db)
    documents, total = await service.list_documents(
        dataset_id=dataset_id,
        page=page,
        size=size
    )
    
    pages = (total + size - 1) // size
    
    return PageResponse(
        data=PageData(
            items=[DocumentResponse(**d) for d in documents],
            total=total,
            page=page,
            size=size,
            pages=pages
        )
    )


@router.post("/datasets/{dataset_id}/documents", response_model=ResponseBase[DocumentResponse])
async def upload_document(
    dataset_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    上传文档
    
    - 代理 RAGFlow API
    """
    service = KnowledgeService(db)
    
    try:
        file_content = await file.read()
        document = await service.upload_document(
            dataset_id=dataset_id,
            file=file_content,
            filename=file.filename
        )
        return ResponseBase(data=DocumentResponse(**document))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.delete("/datasets/{dataset_id}/documents/{document_id}", response_model=ResponseBase[dict])
async def delete_document(
    dataset_id: str,
    document_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    删除文档
    
    - 代理 RAGFlow API
    """
    service = KnowledgeService(db)
    success = await service.delete_document(
        dataset_id=dataset_id,
        document_id=document_id
    )
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="删除失败"
        )
    
    return ResponseBase(data={"deleted": True})


@router.post("/datasets/{dataset_id}/retrieval", response_model=ResponseBase[RetrievalResponse])
async def retrieval(
    dataset_id: str,
    request: RetrievalRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    检索测试
    
    - 代理 RAGFlow API
    """
    service = KnowledgeService(db)
    
    try:
        result = await service.retrieval(
            dataset_id=dataset_id,
            query=request.query,
            top_k=request.top_k
        )
        return ResponseBase(data=RetrievalResponse(**result))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
