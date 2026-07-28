"""
配置管理模块
负责：
1. 从环境变量加载配置
2. 提供全局配置访问接口
3. 支持不同环境（dev/test/prod）的配置
"""

from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """应用配置"""

    # 应用基础配置
    APP_NAME: str = Field(default="Agentic RAG API", description="应用名称")
    APP_VERSION: str = Field(default="1.0.0", description="应用版本")
    DEBUG: bool = Field(default=False, description="是否开启调试模式")

    # 服务配置
    HOST: str = Field(default="0.0.0.0", description="服务监听地址")
    PORT: int = Field(default=8000, description="服务端口")

    # 数据库配置
    DATABASE_URL: str = Field(default="postgresql+asyncpg://postgres:postgres@localhost:5432/agentic_rag", description="PostgreSQL 数据库连接 URL")

    # Redis 配置
    REDIS_URL: str = Field(default="redis://localhost:6379/0", description="Redis 连接 URL")

    # RAGFlow 配置
    RAGFLOW_BASE_URL: str = Field(default="http://localhost:9380", description="RAGFlow 服务基础 URL")
    RAGFLOW_API_KEY: str = Field(default="", description="RAGFlow API Key")

    # JWT 配置
    # 安全说明：生产环境必须显式配置 JWT_SECRET_KEY，禁止使用不安全默认值
    JWT_SECRET_KEY: str = Field(default="", description="JWT 密钥（生产环境必须显式配置）")
    JWT_ALGORITHM: str = Field(default="HS256", description="JWT 加密算法")
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=1440,  # 24 小时
        description="JWT 访问令牌过期时间（分钟）",
    )

    # CORS 配置
    CORS_ORIGINS: List[str] = Field(default=["http://localhost:3000", "http://localhost:8000"], description="允许的 CORS 源")

    # 日志配置
    LOG_LEVEL: str = Field(default="INFO", description="日志级别")

    # 模型配置（使用 pydantic-settings 的 ConfigDict）
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=True, extra="ignore")


# 全局配置实例
settings = Settings()


def _validate_settings() -> None:
    """对运行环境进行校验。

    生产环境（非 DEBUG）必须显式配置 ``JWT_SECRET_KEY``，否则启动失败。
    这避免了使用不安全默认值导致 JWT 被伪造的风险。
    """
    is_production = not settings.DEBUG
    if is_production and not settings.JWT_SECRET_KEY:
        raise RuntimeError("JWT_SECRET_KEY 必须在生产环境中显式配置（当前为空）。请在环境变量或 .env 文件中设置 JWT_SECRET_KEY。")


# 模块加载时执行校验
_validate_settings()


def get_settings() -> Settings:
    """获取配置实例"""
    return settings
