"""
数据源服务
负责数据库连接管理、Schema 探查、SQL 执行
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
import logging
import time
import base64

from models.datasource import DataSource
from schemas.datasource import DataSourceCreate, DataSourceUpdate

logger = logging.getLogger(__name__)


class DataSourceService:
    """数据源服务类"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create(
        self,
        user_id: str,
        data: DataSourceCreate
    ) -> DataSource:
        """
        创建数据源
        
        Args:
            user_id: 用户 ID
            data: 创建数据
        
        Returns:
            新创建的 DataSource 对象
        """
        # 加密密码
        password_encrypted = self._encrypt_password(data.password)
        
        datasource = DataSource(
            user_id=user_id,
            name=data.name,
            description=data.description,
            db_type=data.db_type,
            host=data.host,
            port=data.port,
            database=data.database,
            username=data.username,
            password_encrypted=password_encrypted,
            extra_config=data.extra_config,
            is_active=True
        )
        
        self.db.add(datasource)
        await self.db.commit()
        await self.db.refresh(datasource)
        
        logger.info(f"DataSource created: {datasource.id} by user {user_id}")
        return datasource
    
    async def get(self, datasource_id: str, user_id: str) -> Optional[DataSource]:
        """
        获取数据源详情
        
        Args:
            datasource_id: 数据源 ID
            user_id: 用户 ID
        
        Returns:
            DataSource 对象（如果存在且属于该用户）
        """
        result = await self.db.execute(
            select(DataSource)
            .where(
                DataSource.id == datasource_id,
                DataSource.user_id == user_id
            )
        )
        return result.scalar_one_or_none()
    
    async def list(
        self,
        user_id: str,
        page: int = 1,
        size: int = 20
    ) -> tuple[List[DataSource], int]:
        """
        获取数据源列表
        
        Args:
            user_id: 用户 ID
            page: 页码
            size: 每页数量
        
        Returns:
            (数据源列表, 总数)
        """
        # 查询总数
        count_result = await self.db.execute(
            select(func.count(DataSource.id))
            .where(DataSource.user_id == user_id)
        )
        total = count_result.scalar()
        
        # 查询列表
        offset = (page - 1) * size
        result = await self.db.execute(
            select(DataSource)
            .where(DataSource.user_id == user_id)
            .order_by(DataSource.created_at.desc())
            .offset(offset)
            .limit(size)
        )
        datasources = result.scalars().all()
        
        return datasources, total
    
    async def update(
        self,
        datasource_id: str,
        user_id: str,
        update_data: DataSourceUpdate
    ) -> Optional[DataSource]:
        """
        更新数据源
        
        Args:
            datasource_id: 数据源 ID
            user_id: 用户 ID
            update_data: 更新数据
        
        Returns:
            更新后的 DataSource 对象
        """
        datasource = await self.get(datasource_id, user_id)
        if not datasource:
            return None
        
        # 更新字段
        if update_data.name is not None:
            datasource.name = update_data.name
        if update_data.description is not None:
            datasource.description = update_data.description
        if update_data.host is not None:
            datasource.host = update_data.host
        if update_data.port is not None:
            datasource.port = update_data.port
        if update_data.database is not None:
            datasource.database = update_data.database
        if update_data.username is not None:
            datasource.username = update_data.username
        if update_data.password is not None:
            datasource.password_encrypted = self._encrypt_password(update_data.password)
        if update_data.extra_config is not None:
            datasource.extra_config = update_data.extra_config
        
        datasource.updated_at = datetime.utcnow()
        await self.db.commit()
        await self.db.refresh(datasource)
        
        return datasource
    
    async def delete(self, datasource_id: str, user_id: str) -> bool:
        """
        删除数据源
        
        Args:
            datasource_id: 数据源 ID
            user_id: 用户 ID
        
        Returns:
            是否删除成功
        """
        datasource = await self.get(datasource_id, user_id)
        if not datasource:
            return False
        
        await self.db.delete(datasource)
        await self.db.commit()
        
        logger.info(f"DataSource deleted: {datasource_id}")
        return True
    
    async def test_connection(
        self,
        datasource_id: str,
        user_id: str
    ) -> Dict[str, Any]:
        """
        测试数据源连接
        
        Args:
            datasource_id: 数据源 ID
            user_id: 用户 ID
        
        Returns:
            测试结果
        """
        datasource = await self.get(datasource_id, user_id)
        if not datasource:
            return {
                "success": False,
                "message": "数据源不存在",
                "execution_time_ms": 0
            }
        
        start_time = time.time()
        
        try:
            # TODO: 实现实际的数据库连接测试
            # 这里暂时返回模拟结果
            await self._test_db_connection(datasource)
            
            execution_time_ms = int((time.time() - start_time) * 1000)
            
            # 更新最后测试时间
            datasource.last_tested_at = datetime.utcnow()
            await self.db.commit()
            
            return {
                "success": True,
                "message": "连接成功",
                "execution_time_ms": execution_time_ms
            }
        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Connection test failed: {e}")
            return {
                "success": False,
                "message": f"连接失败: {str(e)}",
                "execution_time_ms": execution_time_ms
            }
    
    async def list_tables(
        self,
        datasource_id: str,
        user_id: str
    ) -> List[Dict[str, Any]]:
        """
        获取表列表
        
        Args:
            datasource_id: 数据源 ID
            user_id: 用户 ID
        
        Returns:
            表信息列表
        """
        datasource = await self.get(datasource_id, user_id)
        if not datasource:
            return []
        
        # TODO: 实现实际的表列表查询
        # 这里暂时返回模拟数据
        return [
            {"name": "users", "comment": "用户表", "row_count": 1000},
            {"name": "orders", "comment": "订单表", "row_count": 5000},
            {"name": "products", "comment": "产品表", "row_count": 500}
        ]
    
    async def describe_table(
        self,
        datasource_id: str,
        user_id: str,
        table_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        获取表详情（列信息）
        
        Args:
            datasource_id: 数据源 ID
            user_id: 用户 ID
            table_name: 表名
        
        Returns:
            表详情
        """
        datasource = await self.get(datasource_id, user_id)
        if not datasource:
            return None
        
        # TODO: 实现实际的表结构查询
        # 这里暂时返回模拟数据
        return {
            "name": table_name,
            "comment": f"{table_name} 表",
            "columns": [
                {"name": "id", "data_type": "BIGINT", "nullable": False, "is_primary_key": True},
                {"name": "name", "data_type": "VARCHAR(100)", "nullable": True},
                {"name": "created_at", "data_type": "TIMESTAMP", "nullable": False}
            ],
            "row_count": 1000
        }
    
    async def execute_sql(
        self,
        datasource_id: str,
        user_id: str,
        sql: str,
        max_rows: int = 100
    ) -> Dict[str, Any]:
        """
        执行 SQL 查询
        
        Args:
            datasource_id: 数据源 ID
            user_id: 用户 ID
            sql: SQL 语句
            max_rows: 最大返回行数
        
        Returns:
            查询结果
        """
        datasource = await self.get(datasource_id, user_id)
        if not datasource:
            raise ValueError("数据源不存在")
        
        # 安全检查
        self._validate_sql(sql)
        
        start_time = time.time()
        
        try:
            # TODO: 实现实际的 SQL 执行
            # 这里暂时返回模拟结果
            result = await self._execute_db_sql(datasource, sql, max_rows)
            
            execution_time_ms = int((time.time() - start_time) * 1000)
            
            return {
                "sql": sql,
                "columns": result.get("columns", []),
                "rows": result.get("rows", []),
                "row_count": len(result.get("rows", [])),
                "execution_time_ms": execution_time_ms,
                "truncated": False
            }
        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            logger.error(f"SQL execution failed: {e}")
            raise
    
    def _encrypt_password(self, password: str) -> str:
        """加密密码（简单 base64 编码，生产环境应使用更强的加密）"""
        return base64.b64encode(password.encode()).decode()
    
    def _decrypt_password(self, password_encrypted: str) -> str:
        """解密密码"""
        return base64.b64decode(password_encrypted.encode()).decode()
    
    def _validate_sql(self, sql: str):
        """SQL 安全检查"""
        sql_upper = sql.upper().strip()
        
        # 禁止危险操作
        dangerous_keywords = ["DROP", "TRUNCATE", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE"]
        for keyword in dangerous_keywords:
            if sql_upper.startswith(keyword):
                raise ValueError(f"不允许执行 {keyword} 操作")
        
        # 必须是 SELECT 查询
        if not sql_upper.startswith("SELECT"):
            raise ValueError("只允许执行 SELECT 查询")
    
    async def _test_db_connection(self, datasource: DataSource):
        """测试数据库连接（占位符）"""
        # TODO: 实现实际的连接测试
        pass
    
    async def _execute_db_sql(
        self,
        datasource: DataSource,
        sql: str,
        max_rows: int
    ) -> Dict[str, Any]:
        """执行数据库 SQL（占位符）"""
        # TODO: 实现实际的 SQL 执行
        return {
            "columns": ["id", "name"],
            "rows": [{"id": 1, "name": "示例"}]
        }
