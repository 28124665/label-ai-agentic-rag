#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

import psycopg2
from psycopg2.extras import execute_values
import numpy as np
import logging
import json
from abc import abstractmethod
from common.doc_store.doc_store_base import DocStoreConnection, MatchExpr, MatchTextExpr, MatchDenseExpr, OrderByExpr, \
    FusionExpr
from common.constants import PAGERANK_FLD, TAG_FLD


class PGConnectionBase(DocStoreConnection):
    """
    PostgreSQL + pgvector connection for vector storage and retrieval
    """

    def __init__(self, logger_name: str = "ragflow.pg_conn", table_name_prefix: str = "ragflow_"):
        from common import settings
        from common.doc_store.pg_conn_pool import PG_CONN

        self.config = settings.POSTGRESQL
        self.oss_config = settings.OSS
        self.logger = logging.getLogger(logger_name)
        self.table_name_prefix = table_name_prefix
        self.connPool = PG_CONN.get_conn_pool()
        self.logger.info(f"Use PostgreSQL {self.config.get('host', 'localhost')}:{self.config.get('port', 5432)} as the doc engine.")

    def _get_fixed_table_name(self, index_name: str, knowledgebase_id: str = None, vector_size: int = None) -> str:
        """
        Get fixed table name
        - Document metadata table: {table_name_prefix}doc_meta_{tenant_id}
        - Vector storage table: {table_name_prefix}doc_embeddings_{message_type}
        """
        if index_name.startswith(f"{self.table_name_prefix}doc_meta_"):
            return index_name
        else:
            # 解析message_type
            parts = index_name.split(":")
            if len(parts) >= 2:
                message_type = parts[0]
                return f"{self.table_name_prefix}doc_embeddings_{message_type}"
            # Include vector size in table name to support different embedding models
            return f"{self.table_name_prefix}doc_embeddings_{vector_size}" if vector_size else f"{self.table_name_prefix}doc_embeddings"

    def db_type(self) -> str:
        return "postgresql"

    def health(self) -> dict:
        conn = None
        try:
            conn = self.connPool.getconn()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            return {"status": "ok"}
        except Exception as e:
            self.logger.error(f"Health check failed: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            if conn:
                self.connPool.putconn(conn)

    def create_idx(self, index_name: str, dataset_id: str, vector_size: int, parser_id: str = None):
        """
        Create table for vector storage with full schema support
        """
        conn = None
        try:
            conn = self.connPool.getconn()
            cursor = conn.cursor()
            # Create table if not exists
            table_name = self._get_fixed_table_name(index_name, dataset_id, vector_size)
            self.logger.info(f"Creating table: {table_name}")
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id TEXT PRIMARY KEY, -- 分段ID（主键）
                    content TEXT, -- 分段内容
                    content_with_weight TEXT, -- 带权重的分段内容
                    content_ltks TEXT, -- 长文本标记
                    content_sm_ltks TEXT, -- 短文本标记
                    doc_id TEXT, -- 文档ID
                    docnm_kwd TEXT, -- 文档名称关键词
                    title_tks TEXT, -- 标题标记
                    title_sm_tks TEXT, -- 短标题标记
                    kb_id TEXT, -- 知识库ID
                    source_path TEXT, -- 原始文档地址
                    available_int INTEGER, -- 可用性标识
                    important_kwd TEXT, -- 重要关键词
                    important_tks TEXT, -- 重要标记
                    question_kwd TEXT, -- 问题关键词
                    question_tks TEXT, -- 问题标记
                    authors_tks TEXT, -- 作者标记
                    authors_sm_tks TEXT, -- 短作者标记
                    position_int TEXT, -- 位置信息
                    page_num_int TEXT, -- 页码信息
                    top_int TEXT, -- 顶部信息
                    create_timestamp_flt FLOAT, -- 创建时间戳
                    pagerank_fea INTEGER DEFAULT 0, -- 页面排名特征
                    knowledge_graph_kwd TEXT, -- 添加知识图谱关键词列
                    q_{vector_size}_vec VECTOR({vector_size}), -- 向量表示
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP -- 创建时间
                )
            """)

            # Create indexes
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {table_name}_doc_id_idx ON {table_name} (doc_id)")
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {table_name}_kb_id_idx ON {table_name} (kb_id)")
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS {table_name}_available_int_idx ON {table_name} (available_int)")
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS {table_name}_vector_idx ON {table_name} USING ivfflat (q_{vector_size}_vec) WITH (lists = 100)")
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {table_name}_source_path_idx ON {table_name} (source_path)")
            conn.commit()
            self.logger.debug(f"Table {table_name} created successfully")
        except Exception as e:
            if conn:
                conn.rollback()
            self.logger.error(f"Failed to create index: {e}")
            raise
        finally:
            if conn:
                self.connPool.putconn(conn)

    def delete_idx(self, index_name: str, dataset_id: str):
        """
        Drop table
        """
        conn = None
        try:
            conn = self.connPool.getconn()
            cursor = conn.cursor()
            # 对于包含message_type的索引，不需要vector_size
            table_name = self._get_fixed_table_name(index_name, dataset_id, 1024)
            self.logger.info(f"Dropping table: {table_name}")
            cursor.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.commit()
            self.logger.info(f"Table {table_name} dropped successfully")
        except Exception as e:
            if conn:
                conn.rollback()
            self.logger.error(f"Failed to delete index: {e}")
            raise
        finally:
            if conn:
                self.connPool.putconn(conn)

    def index_exist(self, index_name: str, dataset_id: str = None, vector_size: int = None) -> bool:
        """
        Check if table exists
        """
        conn = None
        try:
            conn = self.connPool.getconn()
            cursor = conn.cursor()
            # 确定表名
            table_name = self._get_fixed_table_name(index_name, dataset_id, vector_size)

            self.logger.info(f"Checking if table exists: {table_name}")
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND lower(table_name) = lower(%s)
                )
            """, (table_name,))
            result = cursor.fetchone()[0]
            self.logger.debug(f"Table {table_name} exists: {result}")
            return result
        except Exception as e:
            self.logger.error(f"Failed to check index existence: {e}")
            return False
        finally:
            if conn:
                self.connPool.putconn(conn)

    def create_doc_meta_idx(self, index_name: str):
        """
        Create document metadata index
        Document metadata table stores document-level metadata with fields:
        - id: document ID
        - kb_id: knowledge base ID
        - source_path: original document path
        - meta_fields: JSON object containing metadata
        """
        conn = None
        try:
            conn = self.connPool.getconn()
            cursor = conn.cursor()
            # 使用固定表名存储文档元数据
            table_name = self._get_fixed_table_name(index_name, None, 1024)
            self.logger.info(f"Creating document metadata index: {table_name}")

            # 检查表是否存在
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = %s
                )
            """, (table_name,))
            exists = cursor.fetchone()[0]

            if not exists:
                # 创建文档元数据表 - 使用 JSONB 存储动态元数据
                cursor.execute(f"""
                    CREATE TABLE {table_name} (
                        id TEXT PRIMARY KEY, -- 文档ID（主键）
                        kb_id TEXT, -- 知识库ID
                        source_path TEXT, -- 原始文档地址
                        meta_fields JSONB, -- 元数据（JSON格式）
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- 创建时间
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP -- 更新时间
                    )
                """)

                # 创建索引
                cursor.execute(f"""
                    CREATE INDEX idx_{table_name}_kb_id ON {table_name}(kb_id)
                """)
                cursor.execute(f"""
                    CREATE INDEX idx_{table_name}_source_path ON {table_name}(source_path)
                """)

                conn.commit()
                self.logger.info(f"Document metadata table {table_name} created successfully")
            else:
                self.logger.debug(f"Document metadata table {table_name} already exists")

            return True
        except Exception as e:
            if conn:
                conn.rollback()
            self.logger.error(f"Failed to create document metadata index: {e}")
            return False
        finally:
            if conn:
                self.connPool.putconn(conn)

    @staticmethod
    @abstractmethod
    def field_keyword(field_name: str):
        # judge keyword or not, such as "*_kwd" tag-like columns.
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def convert_select_fields(self, output_fields: list[str]) -> list[str]:
        # rm _kwd, _tks, _sm_tks, _with_weight suffix in field name.
        raise NotImplementedError("Not implemented")

    @staticmethod
    @abstractmethod
    def convert_matching_field(field_weight_str: str) -> str:
        # convert matching field to
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def search(self, select_fields: list[str], highlight_fields: list[str], condition: dict,
               match_expressions: list[MatchExpr], order_by: OrderByExpr, offset: int, limit: int,
               index_names: str | list[str], knowledgebase_ids: list[str], agg_fields: list[str] | None = None,
               rank_feature: dict | None = None):
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def get(self, data_id: str, index_name: str, knowledgebase_ids: list[str]) -> dict | None:
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def insert(self, rows: list[dict], index_name: str, knowledgebase_id: str = None) -> list[str]:
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def update(self, condition: dict, new_value: dict, index_name: str, knowledgebase_id: str) -> bool:
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def get_fields(self, res, fields: list[str]) -> dict[str, dict]:
        raise NotImplementedError("Not implemented")

    def delete(self, condition: dict, index_name: str, knowledgebase_id: str) -> int:
        """
        Delete rows with given conjunctive equivalent filtering condition
        """
        conn = None
        try:
            conn = self.connPool.getconn()
            cursor = conn.cursor()
            # 使用固定表名
            table_name = self._get_fixed_table_name(index_name, knowledgebase_id, 1024)

            self.logger.debug(f"Deleting from table: {table_name}")

            # 确保表存在
            # 直接检查表是否存在，而不是使用index_exist
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = %s
                )
            """, (table_name,))
            exists = cursor.fetchone()[0]
            
            if not exists:
                self.logger.warning(f"Table {table_name} does not exist, skipping")
                return 0

            # Build WHERE clause
            where_clauses = []
            params = []

            for k, v in condition.items():
                if isinstance(v, list):
                    placeholders = ",".join(["%s"] * len(v))
                    where_clauses.append(f"{k} IN ({placeholders})")
                    params.extend(v)
                else:
                    where_clauses.append(f"{k} = %s")
                    params.append(v)

            where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"

            # Execute delete
            query = f"DELETE FROM {table_name} WHERE {where_clause}"
            self.logger.debug(f"Executing delete query: {query}")
            cursor.execute(query, params)
            affected_rows = cursor.rowcount
            conn.commit()
            self.logger.debug(f"Deleted {affected_rows} rows from {table_name}")
            return affected_rows
        except Exception as e:
            if conn:
                conn.rollback()
            self.logger.error(f"Failed to delete documents: {e}")
            return 0
        finally:
            if conn:
                self.connPool.putconn(conn)

    def get_total(self, res):
        """
        Get total number of results
        """
        try:
            return res.get("hits", {}).get("total", {}).get("value", 0)
        except Exception:
            return 0

    def get_doc_ids(self, res):
        """
        Get document IDs from results
        """
        try:
            hits = res.get("hits", {}).get("hits", [])
            return [hit.get("_id") for hit in hits]
        except Exception:
            return []

    def get_highlight(self, res, keywords: list[str], field_name: str):
        """
        Get highlight results
        """
        try:
            result = {}
            hits = res.get("hits", {}).get("hits", [])
            for hit in hits:
                doc_id = hit.get("_id")
                if doc_id:
                    source = hit.get("_source", {})
                    content = source.get(field_name)
                    if content:
                        # Simple highlight implementation
                        highlighted = content
                        for keyword in keywords:
                            highlighted = highlighted.replace(keyword, f"<em>{keyword}</em>")
                        result[doc_id] = highlighted
            return result
        except Exception:
            return {}

    def get_aggregation(self, res, field_name: str):
        """
        Get aggregation results
        """
        try:
            from collections import Counter
            counter = Counter()
            hits = res.get("hits", {}).get("hits", [])
            for hit in hits:
                source = hit.get("_source", {})
                value = source.get(field_name)
                if value:
                    if isinstance(value, list):
                        counter.update(value)
                    else:
                        counter[value] += 1
            return [[k, v] for k, v in counter.most_common()]
        except Exception:
            return []

    def sql(self, sql: str, fetch_size: int, format: str):
        """
        Run the sql generated by text-to-sql
        """
        conn = None
        try:
            conn = self.connPool.getconn()
            cursor = conn.cursor()
            self.logger.debug(f"Executing SQL: {sql}")
            cursor.execute(sql)
            
            # Get column names
            column_names = [desc[0] for desc in cursor.description]
            
            # Fetch results
            results = cursor.fetchmany(fetch_size) if fetch_size > 0 else cursor.fetchall()
            
            # Format results based on format parameter
            if format == "json":
                # Convert results to list of dictionaries
                formatted_results = []
                for row in results:
                    formatted_results.append(dict(zip(column_names, row)))
                return formatted_results
            else:
                # Return raw results
                return results
        except Exception as e:
            self.logger.error(f"Failed to execute SQL: {e}")
            return []
        finally:
            if conn:
                self.connPool.putconn(conn)