"""
FastAPI 应用入口
负责：
1. 创建 FastAPI 应用实例
2. 注册中间件（CORS、异常处理）
3. 注册路由模块
4. 生命周期管理（启动/关闭事件）
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging

from core.config import settings
from core.database import init_db, close_db
from core.ragflow_client import init_ragflow_client, close_ragflow_client

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# 创建 FastAPI 应用
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Agentic RAG 智能知识问答系统 API",
    docs_url="/docs",
    redoc_url="/redoc"
)

# 配置 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局异常处理
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理，返回统一格式"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "code": 500,
            "message": "Internal server error",
            "data": None,
            "error": str(exc) if settings.DEBUG else None
        }
    )

# 启动事件
@app.on_event("startup")
async def startup_event():
    """应用启动时初始化资源"""
    logger.info("Starting up Agentic RAG API...")
    
    # 初始化数据库连接
    await init_db()
    logger.info("Database initialized")
    
    # 初始化 RAGFlow 客户端
    init_ragflow_client()
    logger.info("RAGFlow client initialized")
    
    logger.info("Agentic RAG API started successfully")

# 关闭事件
@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时清理资源"""
    logger.info("Shutting down Agentic RAG API...")
    
    # 关闭数据库连接
    await close_db()
    logger.info("Database closed")
    
    # 关闭 RAGFlow 客户端
    await close_ragflow_client()
    logger.info("RAGFlow client closed")
    
    logger.info("Agentic RAG API shutdown complete")

# 健康检查
@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {
        "code": 0,
        "message": "healthy",
        "data": {
            "status": "ok",
            "version": settings.APP_VERSION
        }
    }

# 注册路由模块
from routes import auth, conversations, knowledge, datasources, agents

app.include_router(auth.router, prefix="/api/v1")
app.include_router(conversations.router, prefix="/api/v1")
app.include_router(knowledge.router, prefix="/api/v1")
app.include_router(datasources.router, prefix="/api/v1")
app.include_router(agents.router, prefix="/api/v1")

logger.info("FastAPI application created and configured")
