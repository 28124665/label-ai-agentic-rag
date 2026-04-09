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
from common.doc_store.pg_conn_base import PGConnectionBase
from common.doc_store.doc_store_base import MatchExpr, MatchTextExpr, MatchDenseExpr, OrderByExpr, FusionExpr
from common.decorator import singleton
from common.constants import PAGERANK_FLD, TAG_FLD
from common.time_utils import date_string_to_timestamp


@singleton
class MPGConnection(PGConnectionBase):
    """
    PostgreSQL + pgvector connection for message storage and retrieval
    
    This class is specifically designed for message storage with ragflow_memory_ prefix
    """

    def __init__(self):
        super().__init__(logger_name="ragflow.memory_pg_conn", table_name_prefix="ragflow_memory_")

    @staticmethod
    def field_keyword(field_name: str):
        # No keywords right now for message storage
        return False

    @staticmethod
    def convert_message_field_to_pg(field_name: str):
        """
        Convert message field name to PostgreSQL field name
        """
        match field_name:
            case "message_type":
                return "message_type_kwd"
            case "status":
                return "status_int"
            case "content_embed":
                # Will be handled dynamically based on vector size
                return field_name
            case "valid_at":
                return "valid_at_flt"
            case "invalid_at":
                return "invalid_at_flt"
            case "forget_at":
                return "forget_at_flt"
            case _:
                return field_name

    @staticmethod
    def convert_pg_field_to_message(field_name: str):
        """
        Convert PostgreSQL field name to message field name
        """
        if field_name.startswith("message_type"):
            return "message_type"
        if field_name.startswith("status"):
            return "status"
        if field_name.startswith("valid_at"):
            return "valid_at"
        if field_name.startswith("invalid_at"):
            return "invalid_at"
        if field_name.startswith("forget_at"):
            return "forget_at"
        if field_name.startswith("q_") and field_name.endswith("_vec"):
            return "content_embed"
        return field_name

    def convert_select_fields(self, output_fields: list[str]) -> list[str]:
        """
        Convert message field names to PostgreSQL field names for select
        """
        return list({self.convert_message_field_to_pg(f) for f in output_fields})

    @staticmethod
    def convert_matching_field(field_weight_str: str) -> str:
        """
        Convert matching field for full-text search
        """
        tokens = field_weight_str.split("^")
        field = tokens[0]
        if field == "content":
            field = "content@ft_content_rag_fine"
        tokens[0] = field
        return "^".join(tokens)

    def _get_fixed_table_name(self, index_name: str, memory_id: str = None, vector_size: int = None) -> str:
        """
        Get fixed table name for message storage
        Format: ragflow_memory_{index_name}_{memory_id}
        """
        if memory_id:
            return f"{self.table_name_prefix}{index_name}_{memory_id}"
        return f"{self.table_name_prefix}{index_name}"

    def create_idx(self, index_name: str, memory_id: str, vector_size: int, parser_id: str = None):
        """
        Create table for message storage with full schema support
        """
        conn = None
        try:
            conn = self.connPool.getconn()
            cursor = conn.cursor()
            # Create table if not exists
            table_name = self._get_fixed_table_name(index_name, memory_id, vector_size)
            self.logger.info(f"Creating message table: {table_name}")
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id TEXT PRIMARY KEY, -- 消息ID（主键）
                    content TEXT, -- 消息内容
                    message_type_kwd TEXT, -- 消息类型
                    status_int INTEGER, -- 消息状态
                    memory_id TEXT, -- 记忆ID
                    valid_at_flt FLOAT, -- 有效时间
                    invalid_at_flt FLOAT, -- 无效时间
                    forget_at_flt FLOAT, -- 遗忘时间
                    q_{vector_size}_vec VECTOR({vector_size}), -- 向量表示
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP -- 创建时间
                )
            """)

            # Create indexes
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {table_name}_id_idx ON {table_name} (id)")
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {table_name}_memory_id_idx ON {table_name} (memory_id)")
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {table_name}_message_type_idx ON {table_name} (message_type_kwd)")
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {table_name}_status_idx ON {table_name} (status_int)")
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {table_name}_vector_idx ON {table_name} USING ivfflat (q_{vector_size}_vec) WITH (lists = 100)")
            conn.commit()
            self.logger.debug(f"Message table {table_name} created successfully")
        except Exception as e:
            if conn:
                conn.rollback()
            self.logger.error(f"Failed to create message index: {e}")
            raise
        finally:
            if conn:
                self.connPool.putconn(conn)

    def search(self, select_fields: list[str], highlight_fields: list[str], condition: dict,
               match_expressions: list[MatchExpr], order_by: OrderByExpr, offset: int, limit: int,
               index_names: str | list[str], memory_ids: list[str], agg_fields: list[str] | None = None,
               rank_feature: dict | None = None, hide_forgotten: bool = True):
        """
        Search messages in PostgreSQL
        """
        conn = None
        try:
            conn = self.connPool.getconn()
            cursor = conn.cursor()
            if isinstance(index_names, str):
                index_names = index_names.split(",")

            # Convert select fields
            output_fields = self.convert_select_fields(select_fields.copy())
            if agg_fields:
                for field in agg_fields:
                    if field not in output_fields:
                        output_fields.append(field)

            all_hits = []
            total_count = 0

            # Search across all index names and memory IDs
            for index_name in index_names:
                for memory_id in memory_ids:
                    table_name = self._get_fixed_table_name(index_name, memory_id)
                    self.logger.debug(f"Searching message table: {table_name}")
                    
                    # Build WHERE clause
                    where_clauses = []
                    params = []

                    # Add memory_id condition
                    where_clauses.append("memory_id = %s")
                    params.append(memory_id)

                    # Add hide_forgotten condition
                    if hide_forgotten:
                        where_clauses.append("(forget_at_flt IS NULL OR forget_at_flt = 0)")

                    # Add other conditions
                    for k, v in condition.items():
                        pg_field = self.convert_message_field_to_pg(k)
                        if v:
                            if isinstance(v, list):
                                placeholders = ",".join(["%s"] * len(v))
                                where_clauses.append(f"{pg_field} IN ({placeholders})")
                                params.extend(v)
                            else:
                                where_clauses.append(f"{pg_field} = %s")
                                params.append(v)

                    where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"

                    # Build query
                    query = f"SELECT * FROM {table_name} WHERE {where_clause}"

                    # Add vector search if MatchDenseExpr is present
                    vector_expr = None
                    for expr in match_expressions:
                        if isinstance(expr, MatchDenseExpr):
                            vector_column = expr.vector_column_name
                            embedding = expr.embedding_data
                            topn = expr.topn

                            # Add vector similarity search
                            query += f" ORDER BY {vector_column} <-> %s LIMIT %s OFFSET %s"
                            params.extend([embedding, limit, offset])
                            vector_expr = expr
                            break
                    else:
                        # No vector search, add limit and offset
                        query += f" LIMIT %s OFFSET %s"
                        params.extend([limit, offset])

                    # Execute query
                    try:
                        self.logger.debug(f"Executing message search query: {query}")
                        cursor.execute(query, params)
                        results = cursor.fetchall()

                        # Format results
                        for row in results:
                            hit = {
                                "_id": row[0],
                                "_source": {
                                    "id": row[0],
                                    "content": row[1],
                                    "message_type": row[2],
                                    "status": row[3],
                                    "memory_id": row[4],
                                    "valid_at": row[5],
                                    "invalid_at": row[6],
                                    "forget_at": row[7]
                                }
                            }
                            all_hits.append(hit)

                        total_count += len(results)
                    except Exception as e:
                        self.logger.error(f"Error searching message table {table_name}: {e}")
                        continue

            self.logger.debug(f"Message search completed, total hits: {total_count}")
            return {
                "hits": {
                    "total": {"value": total_count},
                    "hits": all_hits
                }
            }
        except Exception as e:
            self.logger.error(f"Failed to search messages: {e}")
            raise
        finally:
            if conn:
                self.connPool.putconn(conn)

    def get(self, message_id: str, index_name: str, memory_ids: list[str]) -> dict | None:
        """
        Get single message with given id
        """
        conn = None
        try:
            conn = self.connPool.getconn()
            cursor = conn.cursor()
            # Search across all memory IDs
            for memory_id in memory_ids:
                table_name = self._get_fixed_table_name(index_name, memory_id)
                self.logger.debug(f"Getting message from table: {table_name}, id: {message_id}")

                # Execute query
                try:
                    cursor.execute(f"SELECT * FROM {table_name} WHERE id = %s", (message_id,))
                    result = cursor.fetchone()

                    if result:
                        # Format result
                        return {
                            "id": result[0],
                            "content": result[1],
                            "message_type": result[2],
                            "status": result[3],
                            "memory_id": result[4],
                            "valid_at": result[5],
                            "invalid_at": result[6],
                            "forget_at": result[7]
                        }
                except Exception as e:
                    self.logger.error(f"Error getting message from table {table_name}: {e}")
                    continue

            self.logger.debug(f"Message not found: {message_id}")
            return None
        except Exception as e:
            self.logger.error(f"Failed to get message: {e}")
            return None
        finally:
            if conn:
                self.connPool.putconn(conn)

    def insert(self, documents: list[dict], index_name: str, memory_id: str = None) -> list[str]:
        """
        Update or insert a bulk of messages
        """
        conn = None
        try:
            if not memory_id:
                raise Exception("memory_id is required")

            conn = self.connPool.getconn()
            cursor = conn.cursor()

            # Determine vector size from documents
            vector_size = None
            for doc in documents:
                if "content_embed" in doc:
                    vector_size = len(doc["content_embed"])
                    break

            if not vector_size:
                # Default vector size
                vector_size = 768  # Common embedding size

            # Create table if not exists
            table_name = self._get_fixed_table_name(index_name, memory_id, vector_size)
            if not self.index_exist(index_name, memory_id, vector_size):
                self.create_idx(index_name, memory_id, vector_size)

            # Prepare insert data
            insert_data = []
            ids = []
            for doc in documents:
                doc_id = doc.get("id")
                content = doc.get("content")
                message_type = doc.get("message_type")
                status = doc.get("status", 0)
                valid_at = date_string_to_timestamp(doc.get("valid_at")) if doc.get("valid_at") else 0
                invalid_at = date_string_to_timestamp(doc.get("invalid_at")) if doc.get("invalid_at") else 0
                forget_at = date_string_to_timestamp(doc.get("forget_at")) if doc.get("forget_at") else 0
                content_embed = doc.get("content_embed", [0] * vector_size)

                insert_data.append((
                    doc_id,
                    content,
                    message_type,
                    status,
                    memory_id,
                    valid_at,
                    invalid_at,
                    forget_at,
                    content_embed
                ))
                ids.append(doc_id)

            # Execute batch insert
            execute_values(
                cursor,
                f"""
                INSERT INTO {table_name} (
                    id, content, message_type_kwd, status_int, memory_id,
                    valid_at_flt, invalid_at_flt, forget_at_flt, q_{vector_size}_vec
                )
                VALUES %s
                ON CONFLICT (id) DO UPDATE SET
                    content = EXCLUDED.content,
                    message_type_kwd = EXCLUDED.message_type_kwd,
                    status_int = EXCLUDED.status_int,
                    memory_id = EXCLUDED.memory_id,
                    valid_at_flt = EXCLUDED.valid_at_flt,
                    invalid_at_flt = EXCLUDED.invalid_at_flt,
                    forget_at_flt = EXCLUDED.forget_at_flt,
                    q_{vector_size}_vec = EXCLUDED.q_{vector_size}_vec
                """,
                insert_data
            )
            conn.commit()
            self.logger.debug(f"Inserted {len(ids)} messages into {table_name}")
            return []
        except Exception as e:
            if conn:
                conn.rollback()
            self.logger.error(f"Failed to insert messages: {e}")
            raise
        finally:
            if conn:
                self.connPool.putconn(conn)

    def update(self, condition: dict, new_value: dict, index_name: str, memory_id: str) -> bool:
        """
        Update messages with given condition
        """
        conn = None
        try:
            conn = self.connPool.getconn()
            cursor = conn.cursor()
            # Use fixed table name
            table_name = self._get_fixed_table_name(index_name, memory_id)

            self.logger.debug(f"Updating message table: {table_name}")

            # Build SET clause
            set_clauses = []
            params = []

            for k, v in new_value.items():
                pg_field = self.convert_message_field_to_pg(k)
                if pg_field in ["valid_at_flt", "invalid_at_flt", "forget_at_flt"]:
                    set_clauses.append(f"{pg_field} = %s")
                    params.append(date_string_to_timestamp(v) if v else 0)
                else:
                    set_clauses.append(f"{pg_field} = %s")
                    params.append(v)

            set_clause = ", ".join(set_clauses) if set_clauses else ""

            if not set_clause:
                self.logger.debug("No values to update")
                return True

            # Build WHERE clause
            where_clauses = []

            # Add memory_id condition
            where_clauses.append("memory_id = %s")
            params.append(memory_id)

            for k, v in condition.items():
                pg_field = self.convert_message_field_to_pg(k)
                if isinstance(v, list):
                    placeholders = ",".join(["%s"] * len(v))
                    where_clauses.append(f"{pg_field} IN ({placeholders})")
                    params.extend(v)
                else:
                    where_clauses.append(f"{pg_field} = %s")
                    params.append(v)

            where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"

            # Execute update
            query = f"UPDATE {table_name} SET {set_clause} WHERE {where_clause}"
            self.logger.debug(f"Executing message update query: {query}")
            cursor.execute(query, params)
            conn.commit()
            self.logger.debug(f"Updated {cursor.rowcount} messages in {table_name}")
            return True
        except Exception as e:
            if conn:
                conn.rollback()
            self.logger.error(f"Failed to update messages: {e}")
            return False
        finally:
            if conn:
                self.connPool.putconn(conn)

    def get_fields(self, res, fields: list[str]) -> dict[str, dict]:
        """
        Get specific fields from message results
        """
        try:
            result = {}
            hits = res.get("hits", {}).get("hits", [])
            for hit in hits:
                doc_id = hit.get("_id")
                if doc_id:
                    result[doc_id] = {}
                    source = hit.get("_source", {})
                    for field in fields:
                        if field in source:
                            result[doc_id][field] = source[field]
            return result
        except Exception:
            return {}

    def get_forgotten_messages(self, select_fields: list[str], index_name: str, memory_id: str, limit: int=512):
        """
        Get forgotten messages
        """
        condition = {"memory_id": memory_id}
        where_clauses = ["memory_id = %s", "(forget_at_flt IS NOT NULL AND forget_at_flt > 0)"]
        params = [memory_id]

        conn = None
        try:
            conn = self.connPool.getconn()
            cursor = conn.cursor()
            table_name = self._get_fixed_table_name(index_name, memory_id)

            query = f"SELECT * FROM {table_name} WHERE {' AND '.join(where_clauses)} ORDER BY forget_at_flt ASC LIMIT %s"
            params.append(limit)

            cursor.execute(query, params)
            results = cursor.fetchall()

            # Format results
            messages = []
            for row in results:
                message = {
                    "id": row[0],
                    "content": row[1],
                    "message_type": row[2],
                    "status": row[3],
                    "memory_id": row[4],
                    "valid_at": row[5],
                    "invalid_at": row[6],
                    "forget_at": row[7]
                }
                messages.append(message)

            return messages
        except Exception as e:
            self.logger.error(f"Failed to get forgotten messages: {e}")
            return []
        finally:
            if conn:
                self.connPool.putconn(conn)

    def get_missing_field_message(self, select_fields: list[str], index_name: str, memory_id: str, field_name: str, limit: int=512):
        """
        Get messages with missing field
        """
        condition = {"memory_id": memory_id}
        where_clauses = ["memory_id = %s", f"{self.convert_message_field_to_pg(field_name)} IS NULL OR {self.convert_message_field_to_pg(field_name)} = ''"]
        params = [memory_id]

        conn = None
        try:
            conn = self.connPool.getconn()
            cursor = conn.cursor()
            table_name = self._get_fixed_table_name(index_name, memory_id)

            query = f"SELECT * FROM {table_name} WHERE {' AND '.join(where_clauses)} ORDER BY valid_at_flt ASC LIMIT %s"
            params.append(limit)

            cursor.execute(query, params)
            results = cursor.fetchall()

            # Format results
            messages = []
            for row in results:
                message = {
                    "id": row[0],
                    "content": row[1],
                    "message_type": row[2],
                    "status": row[3],
                    "memory_id": row[4],
                    "valid_at": row[5],
                    "invalid_at": row[6],
                    "forget_at": row[7]
                }
                messages.append(message)

            return messages
        except Exception as e:
            self.logger.error(f"Failed to get messages with missing field: {e}")
            return []
        finally:
            if conn:
                self.connPool.putconn(conn)
