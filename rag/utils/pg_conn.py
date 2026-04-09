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


@singleton
class PGConnection(PGConnectionBase):
    """
    PostgreSQL + pgvector connection for vector storage and retrieval
    
    This class maintains backward compatibility while using the new base class structure
    """

    def __init__(self):
        super().__init__(logger_name="ragflow.pg_conn", table_name_prefix="ragflow_")

    @staticmethod
    def field_keyword(field_name: str):
        # Treat "*_kwd" tag-like columns as keyword lists except knowledge_graph_kwd; source_id is also keyword-like.
        if field_name == "source_id" or (
                field_name.endswith("_kwd") and field_name not in ["knowledge_graph_kwd", "docnm_kwd", "important_kwd",
                                                                   "question_kwd"]):
            return True
        return False

    def convert_select_fields(self, output_fields: list[str]) -> list[str]:
        # 字段转换逻辑
        need_empty_count = "important_kwd" in output_fields
        for i, field in enumerate(output_fields):
            if field in ["docnm_kwd", "title_tks", "title_sm_tks"]:
                output_fields[i] = "docnm"
            elif field in ["important_kwd", "important_tks"]:
                output_fields[i] = "important_keywords"
            elif field in ["question_kwd", "question_tks"]:
                output_fields[i] = "questions"
            elif field in ["content_with_weight", "content_ltks", "content_sm_ltks"]:
                output_fields[i] = "content"
            elif field in ["authors_tks", "authors_sm_tks"]:
                output_fields[i] = "authors"
        if need_empty_count and "important_kwd_empty_count" not in output_fields:
            output_fields.append("important_kwd_empty_count")
        return list(set(output_fields))

    @staticmethod
    def convert_matching_field(field_weight_str: str) -> str:
        tokens = field_weight_str.split("^")
        field = tokens[0]
        if field == "docnm_kwd" or field == "title_tks":
            field = "docnm@ft_docnm_rag_coarse"
        elif field == "title_sm_tks":
            field = "docnm@ft_docnm_rag_fine"
        elif field == "important_kwd":
            field = "important_keywords@ft_important_keywords_rag_coarse"
        elif field == "important_tks":
            field = "important_keywords@ft_important_keywords_rag_fine"
        elif field == "question_kwd":
            field = "questions@ft_questions_rag_coarse"
        elif field == "question_tks":
            field = "questions@ft_questions_rag_fine"
        elif field == "content_with_weight" or field == "content_ltks":
            field = "content@ft_content_rag_coarse"
        elif field == "content_sm_ltks":
            field = "content@ft_content_rag_fine"
        elif field == "authors_tks":
            field = "authors@ft_authors_rag_coarse"
        elif field == "authors_sm_tks":
            field = "authors@ft_authors_rag_fine"
        tokens[0] = field
        return "^".join(tokens)

    def search(self, select_fields: list[str], highlight_fields: list[str], condition: dict,
               match_expressions: list[MatchExpr], order_by: OrderByExpr, offset: int, limit: int,
               index_names: str | list[str], knowledgebase_ids: list[str], agg_fields: list[str] | None = None,
               rank_feature: dict | None = None):
        """
        Search documents in PostgreSQL
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

            # Search across all index names and knowledgebase IDs
            for index_name in index_names:
                for kb_id in knowledgebase_ids:
                    # 处理文档元数据表的特殊逻辑
                    if index_name.startswith("ragflow_doc_meta_"):
                        table_name = self._get_fixed_table_name(index_name, kb_id)
                        self.logger.debug(f"Searching document metadata table: {table_name}")
                        
                        # 确保表存在
                        if not self.index_exist("", table_name):
                            self.logger.info(f"Creating document metadata table: {table_name}")
                            self.create_doc_meta_idx(index_name)
                        
                        # Build WHERE clause
                        where_clauses = []
                        params = []

                        # Add kb_id condition
                        where_clauses.append("kb_id = %s")
                        params.append(kb_id)

                        # Add other conditions
                        for k, v in condition.items():
                            if k == "available_int":
                                if v == 0:
                                    where_clauses.append("available_int < 1")
                                else:
                                    where_clauses.append("available_int >= 1")
                            elif v:
                                if isinstance(v, list):
                                    placeholders = ",".join(["%s"] * len(v))
                                    where_clauses.append(f"{k} IN ({placeholders})")
                                    params.extend(v)
                                else:
                                    where_clauses.append(f"{k} = %s")
                                    params.append(v)

                        where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"

                        # Build query
                        query = f"SELECT * FROM {table_name} WHERE {where_clause}"

                        # Execute query
                        self.logger.debug(f"Executing search query: {query}")
                        cursor.execute(query, params)
                        results = cursor.fetchall()

                        # Format results
                        for row in results:
                            hit = {
                                "_id": row[0],
                                "_source": {
                                    "id": row[0],
                                    "kb_id": row[1],
                                    "source_path": row[2],
                                    "meta_fields": row[3]
                                }
                            }
                            all_hits.append(hit)

                        total_count += len(results)
                    else:
                        # 对于向量存储表，需要考虑不同的向量大小
                        # 这里使用默认向量大小 1024，实际应用中可能需要根据实际情况调整
                        default_vector_sizes = [1024]  # 常见的向量大小

                        for vector_size in default_vector_sizes:
                            table_name = self._get_fixed_table_name(index_name, kb_id, vector_size)
                            self.logger.debug(f"Searching table: {table_name}, vector_size: {vector_size}")
                            
                            # 确保表存在
                            if not self.index_exist("", table_name):
                                self.logger.info(f"Creating table: {table_name}")
                                self.create_idx(index_name, kb_id, vector_size)
                            else:
                                # Build WHERE clause
                                where_clauses = []
                                params = []

                                # Add kb_id condition
                                where_clauses.append("kb_id = %s")
                                params.append(kb_id)

                                # Add other conditions
                                for k, v in condition.items():
                                    if k == "available_int":
                                        if v == 0:
                                            where_clauses.append("available_int < 1")
                                        else:
                                            where_clauses.append("available_int >= 1")
                                    elif v:
                                        if isinstance(v, list):
                                            placeholders = ",".join(["%s"] * len(v))
                                            where_clauses.append(f"{k} IN ({placeholders})")
                                            params.extend(v)
                                        else:
                                            where_clauses.append(f"{k} = %s")
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
                                    self.logger.debug(f"Executing search query: {query}")
                                    cursor.execute(query, params)
                                    results = cursor.fetchall()

                                    # Format results
                                    for row in results:
                                        hit = {
                                            "_id": row[0],
                                            "_source": {
                                                "id": row[0],
                                                "content": row[1],
                                                "content_with_weight": row[2],
                                                "doc_id": row[5],
                                                "kb_id": row[9],
                                                "source_path": row[10],
                                                "available_int": row[11]
                                            }
                                        }
                                        all_hits.append(hit)

                                    total_count += len(results)
                                except Exception as e:
                                    self.logger.error(f"Error searching table {table_name}: {e}")
                                    continue

            self.logger.debug(f"Search completed, total hits: {total_count}")
            return {
                "hits": {
                    "total": {"value": total_count},
                    "hits": all_hits
                }
            }
        except Exception as e:
            self.logger.error(f"Failed to search documents: {e}")
            raise
        finally:
            if conn:
                self.connPool.putconn(conn)

    def get(self, data_id: str, index_name: str, knowledgebase_ids: list[str]) -> dict | None:
        """
        Get single chunk with given id
        """
        conn = None
        try:
            conn = self.connPool.getconn()
            cursor = conn.cursor()
            # Search across all knowledgebase IDs
            for kb_id in knowledgebase_ids:
                # 处理文档元数据表的特殊逻辑
                if index_name.startswith("ragflow_doc_meta_"):
                    table_name = self._get_fixed_table_name(index_name, kb_id)
                    self.logger.debug(f"Getting document from metadata table: {table_name}, id: {data_id}")

                    # 确保表存在
                    if not self.index_exist("", table_name):
                        self.logger.warning(f"Table {table_name} does not exist, skipping")
                        continue

                    # Execute query
                    cursor.execute(f"SELECT * FROM {table_name} WHERE id = %s", (data_id,))
                    result = cursor.fetchone()

                    if result:
                        # Format result
                        return {
                            "id": result[0],
                            "kb_id": result[1],
                            "source_path": result[2],
                            "meta_fields": result[3]
                        }
                else:
                    # 对于向量存储表，需要考虑不同的向量大小
                    default_vector_sizes = [1024]  # 常见的向量大小
                    for vector_size in default_vector_sizes:
                        table_name = self._get_fixed_table_name(index_name, kb_id, vector_size)
                        self.logger.debug(f"Getting document from table: {table_name}, id: {data_id}")

                        # 确保表存在
                        if not self.index_exist("", table_name):
                            self.logger.warning(f"Table {table_name} does not exist, skipping")
                            continue

                        # Execute query
                        try:
                            cursor.execute(f"SELECT * FROM {table_name} WHERE id = %s", (data_id,))
                            result = cursor.fetchone()

                            if result:
                                # Format result
                                return {
                                    "id": result[0],
                                    "content": result[1],
                                    "content_with_weight": result[2],
                                    "doc_id": result[5],
                                    "kb_id": result[9],
                                    "source_path": result[10],
                                    "available_int": result[11]
                                }
                        except Exception as e:
                            self.logger.error(f"Error getting document from table {table_name}: {e}")
                            continue

            self.logger.debug(f"Document not found: {data_id}")
            return None
        except Exception as e:
            self.logger.error(f"Failed to get document: {e}")
            return None
        finally:
            if conn:
                self.connPool.putconn(conn)

    def insert(self, rows: list[dict], index_name: str, knowledgebase_id: str = None) -> list[str]:
        """
        Update or insert a bulk of rows
        """
        conn = None
        try:
            if not knowledgebase_id:
                raise Exception("knowledgebase_id is required")

            conn = self.connPool.getconn()
            cursor = conn.cursor()

            # 处理文档元数据表的特殊插入逻辑
            if index_name.startswith("ragflow_doc_meta_"):
                table_name = self._get_fixed_table_name(index_name, knowledgebase_id)
                self.logger.debug(f"Inserting into document metadata table: {table_name}, rows: {len(rows)}")
                return self._insert_doc_meta(rows, table_name, cursor, conn)

            # Determine vector size from rows
            vector_size = None
            for row in rows:
                for key in row.keys():
                    if key.startswith("q_") and key.endswith("_vec"):
                        vector_size = int(key.split("_")[1])
                        break
                if vector_size:
                    break

            if not vector_size:
                # 默认向量大小
                vector_size = 1024  # 常见的嵌入模型向量大小

            # 使用固定表名，包含向量大小
            table_name = self._get_fixed_table_name(index_name, knowledgebase_id, vector_size)
            self.logger.debug(f"Inserting into table: {table_name}, rows: {len(rows)}, vector_size: {vector_size}")

            # Create table if not exists
            if not self.index_exist("", table_name, vector_size):
                self.create_idx(index_name, knowledgebase_id, vector_size)

            # Prepare insert data with all fields
            insert_data = []
            ids = []
            for row in rows:
                vec_key = f"q_{vector_size}_vec"
                vector = row.get(vec_key, [])
                row_id = row.get("id")

                # Handle kb_id - it might be a list
                kb_id = row.get("kb_id")
                if isinstance(kb_id, list):
                    kb_id = kb_id[0] if kb_id else None
                # 处理 source_path 字段: 改成oss://bucket/location
                if "location" in row:
                    # 拼接完整的 OSS URL
                    bucket = self.oss_config.get('bucket', '')
                    location = row["location"]

                    # 构建完整 URL
                    row["source_path"] = f"oss://{bucket}/{location}"
                
                # Handle list fields - convert to string
                def list_to_str(val):
                    if isinstance(val, list):
                        return json.dumps(val) if val else None
                    return val

                # 使用 content_with_weight 作为 content 的值，确保 content 字段不为空
                content = row.get("content") or row.get("content_with_weight")
                content_with_weight = row.get("content_with_weight") or content
                
                insert_data.append((
                    row_id,
                    content,
                    content_with_weight,
                    row.get("content_ltks"),
                    row.get("content_sm_ltks"),
                    row.get("doc_id"),
                    row.get("docnm_kwd"),
                    row.get("title_tks"),
                    row.get("title_sm_tks"),
                    kb_id,
                    row.get("source_path"),
                    row.get("available_int", 1),
                    list_to_str(row.get("important_kwd")),
                    row.get("important_tks"),
                    list_to_str(row.get("question_kwd")),
                    row.get("question_tks"),
                    row.get("authors_tks"),
                    row.get("authors_sm_tks"),
                    list_to_str(row.get("position_int")),
                    list_to_str(row.get("page_num_int")),
                    list_to_str(row.get("top_int")),
                    row.get("create_timestamp_flt"),
                    row.get("pagerank_fea", 0),
                    list_to_str(row.get("knowledge_graph_kwd")),
                    vector
                ))
                ids.append(row_id)

            # Execute batch insert with all fields
            execute_values(
                cursor,
                f"""
                INSERT INTO {table_name} (
                    id, content, content_with_weight, content_ltks, content_sm_ltks,
                    doc_id, docnm_kwd, title_tks, title_sm_tks, kb_id, source_path, available_int,
                    important_kwd, important_tks, question_kwd, question_tks,
                    authors_tks, authors_sm_tks, position_int, page_num_int, top_int,
                    create_timestamp_flt, pagerank_fea, knowledge_graph_kwd, q_{vector_size}_vec
                )
                VALUES %s
                ON CONFLICT (id) DO UPDATE SET
                    content = EXCLUDED.content,
                    content_with_weight = EXCLUDED.content_with_weight,
                    content_ltks = EXCLUDED.content_ltks,
                    content_sm_ltks = EXCLUDED.content_sm_ltks,
                    doc_id = EXCLUDED.doc_id,
                    docnm_kwd = EXCLUDED.docnm_kwd,
                    title_tks = EXCLUDED.title_tks,
                    title_sm_tks = EXCLUDED.title_sm_tks,
                    kb_id = EXCLUDED.kb_id,
                    source_path = EXCLUDED.source_path,
                    available_int = EXCLUDED.available_int,
                    important_kwd = EXCLUDED.important_kwd,
                    important_tks = EXCLUDED.important_tks,
                    question_kwd = EXCLUDED.question_kwd,
                    question_tks = EXCLUDED.question_tks,
                    authors_tks = EXCLUDED.authors_tks,
                    authors_sm_tks = EXCLUDED.authors_sm_tks,
                    position_int = EXCLUDED.position_int,
                    page_num_int = EXCLUDED.page_num_int,
                    top_int = EXCLUDED.top_int,
                    create_timestamp_flt = EXCLUDED.create_timestamp_flt,
                    pagerank_fea = EXCLUDED.pagerank_fea,
                    knowledge_graph_kwd = EXCLUDED.knowledge_graph_kwd,
                    q_{vector_size}_vec = EXCLUDED.q_{vector_size}_vec
                """,
                insert_data
            )
            conn.commit()
            self.logger.debug(f"Inserted {len(ids)} rows into {table_name}")
            # 返回空列表表示插入成功，与其他存储实现保持一致
            return []
        except Exception as e:
            if conn:
                conn.rollback()
            self.logger.error(f"Failed to insert documents: {e}")
            raise
        finally:
            if conn:
                self.connPool.putconn(conn)

    def _insert_doc_meta(self, rows: list[dict], table_name: str, cursor, conn) -> list[str]:
        """
        Insert document metadata into doc_meta table
        """
        try:
            # Create table if not exists
            if not self.index_exist("", table_name):
                self.create_doc_meta_idx(table_name)

            insert_data = []
            ids = []
            for row in rows:
                row_id = row.get("id")
                kb_id = row.get("kb_id")
                if isinstance(kb_id, list):
                    kb_id = kb_id[0] if kb_id else None

                # 获取 source_path
                source_path = row.get("source_path")

                # meta_fields is a dict, convert to JSON string
                meta_fields = row.get("meta_fields", {})
                if isinstance(meta_fields, dict):
                    meta_fields = json.dumps(meta_fields)

                insert_data.append((row_id, kb_id, source_path, meta_fields))
                ids.append(row_id)

            # Execute insert
            execute_values(
                cursor,
                f"""
                INSERT INTO {table_name} (id, kb_id, source_path, meta_fields)
                VALUES %s
                ON CONFLICT (id) DO UPDATE SET
                    kb_id = EXCLUDED.kb_id,
                    source_path = EXCLUDED.source_path,
                    meta_fields = EXCLUDED.meta_fields,
                    updated_at = CURRENT_TIMESTAMP
                """,
                insert_data
            )
            conn.commit()
            self.logger.debug(f"Inserted {len(ids)} document metadata rows into {table_name}")
            # 返回空列表表示插入成功，与其他存储实现保持一致
            return []
        except Exception as e:
            conn.rollback()
            self.logger.error(f"Failed to insert document metadata: {e}")
            raise

    def update(self, condition: dict, new_value: dict, index_name: str, knowledgebase_id: str) -> bool:
        """
        Update rows with given conjunctive equivalent filtering condition
        """
        conn = None
        try:
            conn = self.connPool.getconn()
            cursor = conn.cursor()
            # 使用固定表名
            table_name = self._get_fixed_table_name(index_name, knowledgebase_id, 1024)

            self.logger.debug(f"Updating table: {table_name}")

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
                self.logger.warning(f"Table {table_name} does not exist, creating...")
                if index_name.startswith("ragflow_doc_meta_"):
                    self.create_doc_meta_idx(index_name)
                else:
                    # 尝试从index_name中提取message_type
                    parts = index_name.split(":")
                    if len(parts) > 2:
                        # 对于包含message_type的索引，不需要向量大小
                        self.create_idx(index_name, knowledgebase_id, 1024)  # 使用默认向量大小创建表结构
                    else:
                        self.create_idx(index_name, knowledgebase_id, 1024)  # 默认向量大小

            # Build SET clause
            set_clauses = []
            params = []

            for k, v in new_value.items():
                set_clauses.append(f"{k} = %s")
                params.append(v)

            set_clause = ", ".join(set_clauses) if set_clauses else ""

            if not set_clause:
                self.logger.debug("No values to update")
                return True

            # Build WHERE clause
            where_clauses = []

            for k, v in condition.items():
                if isinstance(v, list):
                    placeholders = ",".join(["%s"] * len(v))
                    where_clauses.append(f"{k} IN ({placeholders})")
                    params.extend(v)
                else:
                    where_clauses.append(f"{k} = %s")
                    params.append(v)

            where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"

            # Execute update
            query = f"UPDATE {table_name} SET {set_clause} WHERE {where_clause}"
            self.logger.debug(f"Executing update query: {query}")
            cursor.execute(query, params)
            conn.commit()
            self.logger.debug(f"Updated {cursor.rowcount} rows in {table_name}")
            return True
        except Exception as e:
            if conn:
                conn.rollback()
            self.logger.error(f"Failed to update documents: {e}")
            return False
        finally:
            if conn:
                self.connPool.putconn(conn)

    def get_fields(self, res, fields: list[str]) -> dict[str, dict]:
        """
        Get specific fields from results
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


# Maintain backward compatibility
# The singleton instance is now created through the base class pattern