# Agentic RAG 系统后端接口设计文档

> **项目名称**：Agentic RAG 智能知识问答系统  
> **文档版本**：V1.0  
> **关联文档**：[前端设计文档](./frontend_design.md)、[LangGraph 重构方案](./langgraph_refactor_design.md)

---

## 一、架构设计原则

### 1.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    新前端（Next.js）                          │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│              新后端（FastAPI / LangGraph）                    │
│  ─────────────────────────────────────────────────────────  │
│  职责：LangGraph 编排、工具管理、用户认证、审计日志          │
│  技术栈：FastAPI + LangGraph + PostgreSQL + Redis            │
└─────────────────────────────────────────────────────────────┘
                              ↓
              ┌───────────────┼───────────────┐
              ↓               ↓               ↓
    ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
    │  RAGFlow    │  │  Database   │  │  Web Search │
    │  (知识库)   │  │  (数据库)   │  │  (互联网)   │
    └─────────────┘  └─────────────┘  └─────────────┘
```

### 1.2 与 RAGFlow 的交互策略

| 功能模块 | 交互策略 | 说明 |
|---------|---------|------|
| **知识库管理** | 调用 RAGFlow API | 复用 RAGFlow 的知识库、文档管理、检索能力 |
| **模型配置** | 调用 RAGFlow API | 复用 RAGFlow 的 LLM/Embedding/Rerank 模型管理 |
| **对话管理** | 新后端独立实现 | 新后端管理对话历史，调用 RAGFlow 进行检索 |
| **Agent 编排** | 新后端独立实现 | LangGraph 状态图编排，调用 RAGFlow 作为工具 |
| **数据源管理** | 新后端独立实现 | 新增 Database Tool 管理 |
| **监控审计** | 新后端独立实现 | 新增监控、日志、成本统计 |

### 1.3 核心约束

| 约束 | 说明 |
|------|------|
| **RAGFlow 作为知识库工具** | 新后端通过 API 调用 RAGFlow，不直接修改 RAGFlow 数据库 |
| **模型配置同步** | 新后端的模型配置最终写入 RAGFlow 数据库，确保检索时使用相同模型 |
| **用户认证独立** | 新后端独立管理用户认证，通过 API Token 调用 RAGFlow |
| **数据隔离** | 新后端数据库存储对话历史、审计日志等；RAGFlow 存储知识库、文档等 |

---

## 二、RAGFlow API 交互清单

### 2.1 需要调用 RAGFlow 的接口

| 功能 | RAGFlow API | 用途 | 调用时机 |
|------|-------------|------|----------|
| **知识库列表** | `GET /api/v1/datasets` | 获取知识库列表 | 前端展示知识库列表 |
| **知识库详情** | `GET /api/v1/datasets/{id}` | 获取知识库详情 | 前端展示知识库详情 |
| **创建知识库** | `POST /api/v1/datasets` | 创建新知识库 | 前端创建知识库 |
| **更新知识库** | `PUT /api/v1/datasets/{id}` | 更新知识库配置 | 前端更新知识库 |
| **删除知识库** | `DELETE /api/v1/datasets/{id}` | 删除知识库 | 前端删除知识库 |
| **文档列表** | `GET /api/v1/datasets/{id}/documents` | 获取文档列表 | 前端展示文档列表 |
| **上传文档** | `POST /api/v1/datasets/{id}/documents` | 上传文档 | 前端上传文档 |
| **删除文档** | `DELETE /api/v1/datasets/{id}/documents/{id}` | 删除文档 | 前端删除文档 |
| **检索测试** | `POST /api/v1/datasets/{id}/retrieval` | 检索测试 | 前端检索测试 |
| **模型列表** | `GET /api/v1/llm` | 获取模型列表 | 前端配置模型 |
| **设置模型** | `POST /api/v1/llm/set` | 设置模型配置 | 前端配置模型 |

### 2.2 RAGFlow API 调用封装

```python
# ragflow_client.py

import httpx
from typing import Optional, Dict, Any, List
from pydantic import BaseModel

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

def get_ragflow_client() -> RAGFlowClient:
    """获取 RAGFlow 客户端"""
    global ragflow_client
    if ragflow_client is None:
        from config import settings
        ragflow_client = RAGFlowClient(
            base_url=settings.RAGFLOW_BASE_URL,
            api_key=settings.RAGFLOW_API_KEY
        )
    return ragflow_client
```

---

## 三、新后端 API 接口设计

### 3.1 API 总览

| 模块 | 路由前缀 | 说明 |
|------|---------|------|
| **认证** | `/api/v1/auth` | 用户认证、Token 管理 |
| **对话** | `/api/v1/conversations` | 对话管理、消息发送 |
| **知识库** | `/api/v1/knowledge` | 知识库管理（代理 RAGFlow） |
| **数据源** | `/api/v1/datasources` | 数据库连接管理 |
| **Agent** | `/api/v1/agents` | Agent 配置管理 |
| **监控** | `/api/v1/monitor` | 监控指标、日志查询 |
| **管理** | `/api/v1/admin` | 用户管理、权限配置 |
| **系统** | `/api/v1/system` | 系统配置、健康检查 |

### 3.2 统一响应格式

```python
# schemas/response.py

from typing import Optional, Any, Generic, TypeVar
from pydantic import BaseModel

T = TypeVar("T")

class ResponseBase(BaseModel, Generic[T]):
    """统一响应格式"""
    code: int = 0  # 0 表示成功，非 0 表示失败
    message: str = "success"
    data: Optional[T] = None
    error: Optional[str] = None

class PageData(BaseModel, Generic[T]):
    """分页数据"""
    items: List[T]
    total: int
    page: int
    size: int
    pages: int

class PageResponse(BaseModel, Generic[T]):
    """分页响应"""
    code: int = 0
    message: str = "success"
    data: Optional[PageData[T]] = None
```

---

## 四、认证模块 API

### 4.1 用户登录

```
POST /api/v1/auth/login
```

**请求体**：
```json
{
  "username": "string",
  "password": "string"
}
```

**响应体**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIs...",
    "token_type": "bearer",
    "expires_in": 86400,
    "user": {
      "id": "user_123",
      "username": "admin",
      "email": "admin@example.com",
      "role": "admin",
      "avatar": "https://..."
    }
  }
}
```

**实现逻辑**：
```python
# api/v1/auth.py

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from schemas.auth import LoginRequest, LoginResponse
from services.auth_service import AuthService
from core.database import get_db

router = APIRouter(prefix="/auth", tags=["认证"])

@router.post("/login", response_model=ResponseBase[LoginResponse])
async def login(
    request: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """用户登录"""
    auth_service = AuthService(db)
    user = await auth_service.authenticate(request.username, request.password)
    
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    
    token = auth_service.create_access_token(user.id)
    
    return ResponseBase(
        data=LoginResponse(
            access_token=token,
            token_type="bearer",
            expires_in=86400,
            user=user
        )
    )
```

### 4.2 用户注册

```
POST /api/v1/auth/register
```

**请求体**：
```json
{
  "username": "string",
  "password": "string",
  "email": "string",
  "company": "string"
}
```

### 4.3 获取当前用户

```
GET /api/v1/auth/me
```

**响应体**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "user_123",
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin",
    "avatar": "https://...",
    "created_at": "2024-01-01T00:00:00Z"
  }
}
```

---

## 五、对话模块 API

### 5.1 创建对话

```
POST /api/v1/conversations
```

**请求体**：
```json
{
  "agent_id": "agent_123",
  "title": "Q3 产量分析"
}
```

**响应体**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "conv_456",
    "agent_id": "agent_123",
    "title": "Q3 产量分析",
    "user_id": "user_123",
    "created_at": "2024-10-15T14:30:00Z",
    "updated_at": "2024-10-15T14:30:00Z"
  }
}
```

**实现逻辑**：
```python
# api/v1/conversations.py

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from schemas.conversation import ConversationCreate, ConversationResponse
from services.conversation_service import ConversationService
from core.database import get_db
from core.auth import get_current_user
from models.user import User

router = APIRouter(prefix="/conversations", tags=["对话"])

@router.post("", response_model=ResponseBase[ConversationResponse])
async def create_conversation(
    request: ConversationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """创建对话"""
    service = ConversationService(db)
    conversation = await service.create(
        user_id=current_user.id,
        agent_id=request.agent_id,
        title=request.title
    )
    return ResponseBase(data=conversation)
```

### 5.2 获取对话列表

```
GET /api/v1/conversations?page=1&size=20
```

**响应体**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "id": "conv_456",
        "title": "Q3 产量分析",
        "agent_id": "agent_123",
        "last_message_at": "2024-10-15T14:35:00Z",
        "message_count": 5
      }
    ],
    "total": 100,
    "page": 1,
    "size": 20,
    "pages": 5
  }
}
```

### 5.3 发送消息（流式）

```
POST /api/v1/conversations/{conversation_id}/messages
```

**请求体**：
```json
{
  "content": "Q3 各厂区的产量是多少？",
  "files": ["file_123"]
}
```

**响应**：Server-Sent Events (SSE)

```
event: message
data: {"type": "thinking", "content": "正在分析意图..."}

event: message
data: {"type": "tool_call", "tool": "database", "sql": "SELECT ...", "status": "running"}

event: message
data: {"type": "tool_result", "tool": "database", "rows": [...], "status": "completed"}

event: message
data: {"type": "text", "content": "根据数据库查询结果..."}

event: message
data: {"type": "reference", "sources": [...]}

event: message
data: {"type": "done", "message_id": "msg_789"}
```

**实现逻辑**：
```python
# api/v1/conversations.py

from fastapi.responses import StreamingResponse
from langgraph.graph import StateGraph
from schemas.message import MessageCreate

@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    request: MessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """发送消息（流式响应）"""
    
    async def event_stream():
        # 1. 保存用户消息
        message_service = MessageService(db)
        user_message = await message_service.create(
            conversation_id=conversation_id,
            role="user",
            content=request.content,
            files=request.files
        )
        
        # 2. 初始化 LangGraph
        graph = build_agent_graph()
        
        # 3. 执行状态图
        initial_state = {
            "user_question": request.content,
            "conversation_id": conversation_id,
            "user_id": current_user.id,
            "messages": [],
            "tool_calls": [],
            "references": []
        }
        
        async for event in graph.astream_events(initial_state):
            # 发送事件到前端
            yield f"event: {event['event']}\n"
            yield f"data: {json.dumps(event['data'])}\n\n"
        
        # 4. 保存 AI 回复
        ai_message = await message_service.create(
            conversation_id=conversation_id,
            role="assistant",
            content=final_answer,
            references=references
        )
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream"
    )
```

### 5.4 获取对话历史

```
GET /api/v1/conversations/{conversation_id}/messages?page=1&size=50
```

**响应体**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "id": "msg_001",
        "role": "user",
        "content": "Q3 各厂区的产量是多少？",
        "files": [],
        "created_at": "2024-10-15T14:30:00Z"
      },
      {
        "id": "msg_002",
        "role": "assistant",
        "content": "根据数据库查询结果...",
        "tool_calls": [
          {
            "id": "tc_001",
            "tool": "database",
            "sql": "SELECT ...",
            "rows": [...],
            "execution_time_ms": 1200
          }
        ],
        "references": [
          {
            "type": "database",
            "source": "SAP 生产数据库",
            "table": "output_record",
            "query_time": "2024-10-15T14:30:22Z"
          }
        ],
        "created_at": "2024-10-15T14:30:25Z"
      }
    ],
    "total": 10,
    "page": 1,
    "size": 50,
    "pages": 1
  }
}
```

---

## 六、知识库模块 API（代理 RAGFlow）

### 6.1 获取知识库列表

```
GET /api/v1/knowledge?page=1&size=20
```

**响应体**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "id": "kb_123",
        "name": "产品手册",
        "description": "产品相关文档",
        "document_count": 125,
        "chunk_count": 2345,
        "size_bytes": 1288490188,
        "embedding_model": "BAAI/bge-large-zh",
        "created_at": "2024-01-15T10:30:00Z",
        "updated_at": "2024-10-15T14:22:00Z"
      }
    ],
    "total": 5,
    "page": 1,
    "size": 20,
    "pages": 1
  }
}
```

**实现逻辑**：
```python
# api/v1/knowledge.py

from fastapi import APIRouter, Depends
from core.ragflow import get_ragflow_client

router = APIRouter(prefix="/knowledge", tags=["知识库"])

@router.get("", response_model=PageResponse[KnowledgeBaseResponse])
async def list_knowledge_bases(
    page: int = 1,
    size: int = 20,
    current_user: User = Depends(get_current_user)
):
    """获取知识库列表（代理 RAGFlow）"""
    ragflow = get_ragflow_client()
    
    # 调用 RAGFlow API
    result = await ragflow.list_datasets(page=page, size=size)
    
    # 转换响应格式
    items = [
        KnowledgeBaseResponse(
            id=kb["id"],
            name=kb["name"],
            description=kb.get("description", ""),
            document_count=kb.get("document_count", 0),
            chunk_count=kb.get("chunk_count", 0),
            size_bytes=kb.get("size", 0),
            embedding_model=kb.get("embedding_model", ""),
            created_at=kb.get("create_time", ""),
            updated_at=kb.get("update_time", "")
        )
        for kb in result.get("data", {}).get("items", [])
    ]
    
    return PageResponse(
        data=PageData(
            items=items,
            total=result.get("data", {}).get("total", 0),
            page=page,
            size=size,
            pages=(result.get("data", {}).get("total", 0) + size - 1) // size
        )
    )
```

### 6.2 创建知识库

```
POST /api/v1/knowledge
```

**请求体**：
```json
{
  "name": "产品手册",
  "description": "产品相关文档",
  "embedding_model": "BAAI/bge-large-zh",
  "chunk_method": "smart",
  "chunk_size": 512
}
```

**响应体**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "kb_123",
    "name": "产品手册",
    "description": "产品相关文档",
    "embedding_model": "BAAI/bge-large-zh",
    "created_at": "2024-10-15T14:30:00Z"
  }
}
```

**实现逻辑**：
```python
@router.post("", response_model=ResponseBase[KnowledgeBaseResponse])
async def create_knowledge_base(
    request: KnowledgeBaseCreate,
    current_user: User = Depends(get_current_user)
):
    """创建知识库（代理 RAGFlow）"""
    ragflow = get_ragflow_client()
    
    # 调用 RAGFlow API
    result = await ragflow.create_dataset({
        "name": request.name,
        "description": request.description,
        "embedding_model": request.embedding_model,
        "parser_id": request.chunk_method,
        "parser_config": {
            "chunk_token_num": request.chunk_size
        }
    })
    
    # 转换响应
    kb_data = result.get("data", {})
    return ResponseBase(
        data=KnowledgeBaseResponse(
            id=kb_data["id"],
            name=kb_data["name"],
            description=kb_data.get("description", ""),
            embedding_model=kb_data.get("embedding_model", ""),
            created_at=kb_data.get("create_time", "")
        )
    )
```

### 6.3 上传文档

```
POST /api/v1/knowledge/{kb_id}/documents
Content-Type: multipart/form-data
```

**请求体**：
```
file: <binary>
```

**响应体**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "doc_456",
    "name": "产品规格书.pdf",
    "size_bytes": 2411724,
    "status": "parsing",
    "created_at": "2024-10-15T14:30:00Z"
  }
}
```

### 6.4 检索测试

```
POST /api/v1/knowledge/{kb_id}/search
```

**请求体**：
```json
{
  "query": "Q3 产量",
  "top_k": 5,
  "threshold": 0.7,
  "enable_rerank": true
}
```

**响应体**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "query": "Q3 产量",
    "rewritten_query": "第三季度 产量 统计",
    "complexity": "simple",
    "strategy": "synonym_rewrite",
    "results": [
      {
        "chunk_id": "chunk_001",
        "content": "Q3 产量达到 150 万件...",
        "score": 0.92,
        "document_id": "doc_123",
        "document_name": "产品规格书.pdf",
        "page": 3
      }
    ],
    "total": 3,
    "execution_time_ms": 235
  }
}
```

---

## 七、数据源模块 API

### 7.1 获取数据源列表

```
GET /api/v1/datasources?page=1&size=20
```

**响应体**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "id": "ds_123",
        "name": "SAP ERP",
        "type": "postgresql",
        "host": "10.11.24.181",
        "port": 5432,
        "database": "boss_ai_data",
        "status": "healthy",
        "table_count": 45,
        "created_at": "2024-01-15T10:30:00Z"
      }
    ],
    "total": 3,
    "page": 1,
    "size": 20,
    "pages": 1
  }
}
```

### 7.2 创建数据源

```
POST /api/v1/datasources
```

**请求体**：
```json
{
  "name": "SAP ERP",
  "type": "postgresql",
  "host": "10.11.24.181",
  "port": 5432,
  "database": "boss_ai_data",
  "username": "readonly_user",
  "password": "encrypted_password",
  "whitelist_tables": ["production_order", "output_record"]
}
```

**响应体**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "ds_123",
    "name": "SAP ERP",
    "type": "postgresql",
    "status": "testing",
    "created_at": "2024-10-15T14:30:00Z"
  }
}
```

**实现逻辑**：
```python
# api/v1/datasources.py

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from schemas.datasource import DataSourceCreate, DataSourceResponse
from services.datasource_service import DataSourceService
from core.database import get_db

router = APIRouter(prefix="/datasources", tags=["数据源"])

@router.post("", response_model=ResponseBase[DataSourceResponse])
async def create_datasource(
    request: DataSourceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """创建数据源"""
    service = DataSourceService(db)
    
    # 1. 加密密码
    encrypted_password = encrypt_password(request.password)
    
    # 2. 测试连接
    connection = create_db_connection(
        type=request.type,
        host=request.host,
        port=request.port,
        database=request.database,
        username=request.username,
        password=request.password
    )
    
    try:
        await connection.test()
        status = "healthy"
    except Exception as e:
        status = "unhealthy"
        return ResponseBase(
            code=1,
            message=f"连接测试失败: {str(e)}",
            data=None
        )
    
    # 3. 保存数据源
    datasource = await service.create(
        user_id=current_user.id,
        name=request.name,
        type=request.type,
        host=request.host,
        port=request.port,
        database=request.database,
        username=request.username,
        password=encrypted_password,
        whitelist_tables=request.whitelist_tables,
        status=status
    )
    
    return ResponseBase(data=datasource)
```

### 7.3 获取表结构

```
GET /api/v1/datasources/{datasource_id}/tables
```

**响应体**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "name": "output_record",
        "comment": "产量记录表",
        "row_count": 2345678,
        "size_bytes": 125829120,
        "columns": [
          {
            "name": "record_id",
            "type": "VARCHAR(50)",
            "comment": "记录ID",
            "nullable": false,
            "sensitive": false
          },
          {
            "name": "factory_code",
            "type": "VARCHAR(20)",
            "comment": "厂区代码",
            "nullable": false,
            "sensitive": false
          }
        ]
      }
    ],
    "total": 45
  }
}
```

### 7.4 执行 SQL

```
POST /api/v1/datasources/{datasource_id}/query
```

**请求体**：
```json
{
  "sql": "SELECT factory_code, SUM(quantity) as total FROM output_record WHERE record_date >= '2024-07-01' GROUP BY factory_code",
  "limit": 100
}
```

**响应体**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "sql": "SELECT ...",
    "columns": ["factory_code", "total"],
    "rows": [
      {"factory_code": "SZ-01", "total": 1500000},
      {"factory_code": "KS-02", "total": 1200000}
    ],
    "row_count": 2,
    "execution_time_ms": 1200
  }
}
```

---

## 八、Agent 模块 API

### 8.1 获取 Agent 列表

```
GET /api/v1/agents?page=1&size=20
```

**响应体**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "id": "agent_123",
        "name": "智能助手",
        "description": "通用智能问答助手",
        "tools": ["rag", "database"],
        "status": "active",
        "conversation_count": 1234,
        "created_at": "2024-01-15T10:30:00Z"
      }
    ],
    "total": 3,
    "page": 1,
    "size": 20,
    "pages": 1
  }
}
```

### 8.2 创建 Agent

```
POST /api/v1/agents
```

**请求体**：
```json
{
  "name": "智能助手",
  "description": "通用智能问答助手",
  "tools": [
    {
      "type": "rag",
      "config": {
        "knowledge_bases": ["kb_123", "kb_456"],
        "top_k": 5,
        "threshold": 0.7
      }
    },
    {
      "type": "database",
      "config": {
        "datasources": ["ds_123"],
        "enable_template": true
      }
    }
  ],
  "routing_strategy": {
    "rules": [
      {"condition": "contains_aggregation", "target": "database"},
      {"condition": "contains_concept", "target": "rag"}
    ]
  },
  "fallback_strategy": {
    "max_retries": 3,
    "quality_threshold": 0.7,
    "fallback_order": ["rag", "database", "web"]
  }
}
```

---

## 九、数据库模型设计

### 9.1 核心表结构

```python
# models/conversation.py

from sqlalchemy import Column, String, DateTime, Integer, JSON, ForeignKey
from sqlalchemy.sql import func
from core.database import Base

class Conversation(Base):
    """对话表"""
    __tablename__ = "conversations"
    
    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    agent_id = Column(String(36), ForeignKey("agents.id"), nullable=False)
    title = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # 关系
    messages = relationship("Message", back_populates="conversation")


class Message(Base):
    """消息表"""
    __tablename__ = "messages"
    
    id = Column(String(36), primary_key=True)
    conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=False)
    role = Column(String(20), nullable=False)  # user / assistant / system
    content = Column(JSON, nullable=False)
    files = Column(JSON, default=[])
    tool_calls = Column(JSON, default=[])
    references = Column(JSON, default=[])
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 关系
    conversation = relationship("Conversation", back_populates="messages")


# models/datasource.py

class DataSource(Base):
    """数据源表"""
    __tablename__ = "datasources"
    
    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False)
    type = Column(String(50), nullable=False)  # postgresql / mysql / sap_hana
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False)
    database = Column(String(255), nullable=False)
    username = Column(String(255), nullable=False)
    password = Column(String(500), nullable=False)  # 加密存储
    whitelist_tables = Column(JSON, default=[])
    status = Column(String(20), default="unknown")  # healthy / unhealthy / unknown
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


# models/agent.py

class Agent(Base):
    """Agent 表"""
    __tablename__ = "agents"
    
    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(String(1000))
    tools = Column(JSON, default=[])
    routing_strategy = Column(JSON, default={})
    fallback_strategy = Column(JSON, default={})
    status = Column(String(20), default="active")  # active / inactive
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


# models/audit_log.py

class AuditLog(Base):
    """审计日志表"""
    __tablename__ = "audit_logs"
    
    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    action = Column(String(100), nullable=False)  # login / query / upload / etc.
    resource_type = Column(String(50))  # conversation / datasource / etc.
    resource_id = Column(String(36))
    details = Column(JSON, default={})
    ip_address = Column(String(50))
    user_agent = Column(String(500))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

---

## 十、模型配置同步策略

### 10.1 问题描述

新后端需要配置 LLM/Embedding/Rerank 模型，但这些模型配置最终需要应用到 RAGFlow 中，因为 RAGFlow 负责实际的检索和生成。

### 10.2 同步策略

```
┌─────────────────────────────────────────────────────────────┐
│  新后端配置模型                                               │
│  ─────────────────────────────────────────────────────────  │
│  1. 前端提交模型配置到新后端                                  │
│  2. 新后端验证配置（API Key、模型可用性）                     │
│  3. 新后端保存配置到自己的数据库（用于 Agent 编排）           │
│  4. 新后端调用 RAGFlow API 同步配置                          │
│  5. RAGFlow 保存配置到自己的数据库                           │
└─────────────────────────────────────────────────────────────┘
```

### 10.3 实现代码

```python
# services/model_service.py

class ModelService:
    """模型配置服务"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.ragflow = get_ragflow_client()
    
    async def create_model_config(self, user_id: str, config: ModelConfigCreate) -> ModelConfig:
        """创建模型配置"""
        
        # 1. 验证 API Key
        if not await self._verify_api_key(config):
            raise ValueError("API Key 验证失败")
        
        # 2. 保存到新后端数据库
        model_config = ModelConfig(
            id=str(uuid.uuid4()),
            user_id=user_id,
            name=config.name,
            type=config.type,  # llm / embedding / rerank
            provider=config.provider,
            model_name=config.model_name,
            api_key=encrypt(config.api_key),
            base_url=config.base_url,
            config=config.config
        )
        self.db.add(model_config)
        await self.db.commit()
        
        # 3. 同步到 RAGFlow
        try:
            await self.ragflow.set_llm({
                "model_type": config.type,
                "provider": config.provider,
                "model_name": config.model_name,
                "api_key": config.api_key,
                "base_url": config.base_url
            })
        except Exception as e:
            # 回滚新后端数据库
            await self.db.delete(model_config)
            await self.db.commit()
            raise ValueError(f"同步到 RAGFlow 失败: {str(e)}")
        
        return model_config
    
    async def _verify_api_key(self, config: ModelConfigCreate) -> bool:
        """验证 API Key"""
        try:
            if config.type == "llm":
                # 测试 LLM
                client = create_llm_client(config)
                await client.chat("Hello", max_tokens=10)
            elif config.type == "embedding":
                # 测试 Embedding
                client = create_embedding_client(config)
                await client.embed(["Hello"])
            elif config.type == "rerank":
                # 测试 Rerank
                client = create_rerank_client(config)
                await client.rerank("query", ["doc1", "doc2"])
            return True
        except Exception:
            return False
```

---

## 十一、实施计划

### Phase 1（2 周）- 基础框架

| 任务 | 说明 |
|------|------|
| 搭建 FastAPI 项目 | 项目结构、依赖管理、配置管理 |
| 数据库设计 | 创建核心表（用户、对话、消息、数据源、Agent） |
| 认证模块 | 用户登录、注册、Token 管理 |
| RAGFlow 客户端 | 封装 RAGFlow API 调用 |

### Phase 2（2 周）- 核心功能

| 任务 | 说明 |
|------|------|
| 对话模块 | 对话管理、消息发送（流式） |
| 知识库模块 | 代理 RAGFlow 知识库 API |
| LangGraph 集成 | 基础状态图编排 |

### Phase 3（2 周）- 工具集成

| 任务 | 说明 |
|------|------|
| Database Tool | 数据源管理、SQL 生成与执行 |
| Web Tool | Web 搜索集成 |
| 模型配置同步 | 模型配置同步到 RAGFlow |

### Phase 4（2 周）- 企业功能

| 任务 | 说明 |
|------|------|
| 监控模块 | 指标采集、日志查询 |
| 审计日志 | 用户操作记录 |
| 权限管理 | 用户角色、资源权限 |

---

## 十二、附录

### 12.1 错误码定义

| 错误码 | 说明 |
|--------|------|
| 0 | 成功 |
| 1001 | 参数错误 |
| 1002 | 认证失败 |
| 1003 | 权限不足 |
| 2001 | 知识库不存在 |
| 2002 | 文档上传失败 |
| 3001 | 数据源连接失败 |
| 3002 | SQL 执行失败 |
| 4001 | LLM 调用失败 |
| 4002 | 模型配置错误 |
| 5001 | RAGFlow 调用失败 |

### 12.2 环境变量配置

```bash
# .env

# 数据库
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/agentic_rag

# Redis
REDIS_URL=redis://localhost:6379/0

# RAGFlow
RAGFLOW_BASE_URL=http://localhost:9380
RAGFLOW_API_KEY=ragflow_xxx

# JWT
JWT_SECRET_KEY=your_secret_key
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=1440

# 加密
ENCRYPTION_KEY=your_encryption_key
```

---

*本文档为 Agentic RAG 系统后端接口设计文档，具体实现细节请参考各模块的代码实现。*