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
import asyncio
import logging
import math
import os
import re
from abc import ABC
from typing import Any

import json_repair
import numpy as np

from agent.component.base import ComponentBase, ComponentParamBase
from agent.component.state_fields import set_state
from common.connection_utils import timeout
from common.constants import LLMType

"""子查询拆解组件与 RRF 结果合并算法。

对应需求：P2-FR-02（查询重写 - 子查询拆解与合并）

功能说明：
  将复杂查询拆解为 2-5 个独立的子查询，分别检索后使用 RRF（Reciprocal Rank Fusion）
  算法合并结果，并通过语义去重消除冗余。

实现方式：
  1. 子查询拆解（SubQueryDecomposer 组件）：
     - 使用 LLM 将复杂查询拆解为 2-5 个语义独立的子查询
     - 每个子查询聚焦一个具体方面，便于分别检索
     - LLM 输出 JSON 数组格式，解析失败时回退为按编号行拆分
     - 结果去重并限制在 [min_count, max_count] 范围内
  2. RRF 合并（merge_sub_query_results 函数）：
     - 对每个子查询的检索结果按排名计算 RRF 分数：score = 1/(k + rank + 1)
     - 同一文档被多个子查询命中时，RRF 分数累加
     - 按 RRF 分数降序排列
  3. 语义去重：
     - 如果提供了 embedding_fn，使用余弦相似度判断重复（阈值默认 0.92）
     - 未提供 embedding_fn 时，使用内容精确匹配去重
     - 去重时保留 sub_query_sources 字段，记录每个结果来自哪些子查询
  4. 最终返回 Top-K（默认 10）个去重后的结果
"""


DEFAULT_SUB_QUERY_PROMPT = """你是一位查询拆解专家。请将以下复杂查询拆解为 2-5 个独立的子查询，每个子查询应聚焦一个具体方面，便于分别检索后合并结果。

原始查询：{query}

请输出 JSON 数组，格式如下：
[
  "子查询 1",
  "子查询 2",
  "子查询 3"
]

要求：
1. 子查询数量在 2-5 个之间
2. 每个子查询语义完整、可独立检索
3. 子查询之间尽量避免重复
4. 只输出 JSON 数组，不要其他解释"""

DEFAULT_DEDUP_THRESHOLD = 0.92
DEFAULT_SUB_QUERY_TOP_K = 10
DEFAULT_RRF_K = 60


class SubQueryDecomposerParam(ComponentParamBase):
    """
    Define the SubQueryDecomposer component parameters.
    """

    def __init__(self):
        super().__init__()
        self.query = "sys.query"
        self.llm_id = ""
        self.sub_query_prompt = ""
        self.min_count = 2
        self.max_count = 5
        self.top_k = DEFAULT_SUB_QUERY_TOP_K
        self.dedup_threshold = DEFAULT_DEDUP_THRESHOLD
        self.rrf_k = DEFAULT_RRF_K

    def check(self):
        self.check_defined_type(self.query, "[SubQueryDecomposer] query", ["str"])
        self.check_empty(self.llm_id, "[SubQueryDecomposer] llm_id")
        self.check_positive_integer(self.min_count, "[SubQueryDecomposer] min_count")
        self.check_positive_integer(self.max_count, "[SubQueryDecomposer] max_count")
        if self.min_count > self.max_count:
            raise ValueError("[SubQueryDecomposer] min_count can not exceed max_count")
        self.check_positive_integer(self.top_k, "[SubQueryDecomposer] top_k")
        self.check_decimal_float(float(self.dedup_threshold), "[SubQueryDecomposer] dedup_threshold")
        self.check_positive_integer(self.rrf_k, "[SubQueryDecomposer] rrf_k")


class SubQueryDecomposer(ComponentBase, ABC):
    """子查询拆解组件。

    将复杂查询通过 LLM 拆解为多个独立子查询，写入 sys.sub_queries 状态字段。
    配合 merge_sub_query_results 函数使用，实现多路检索结果的 RRF 合并。
    """
    component_name = "SubQueryDecomposer"

    def get_input_elements(self) -> dict[str, Any]:
        res = {}
        res.update(self.get_input_elements_from_text(self._param.query))
        return res

    def get_input_form(self) -> dict[str, dict]:
        return {
            "query": {"name": "Query", "type": "line"},
        }

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 10 * 60)))
    def _invoke(self, **kwargs):
        return asyncio.run(self._invoke_async(**kwargs))

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 10 * 60)))
    async def _invoke_async(self, **kwargs):
        if self.check_if_canceled("SubQueryDecomposer processing"):
            return

        query = self._resolve_query(kwargs)
        sub_queries = await self._decompose(query)

        self.set_output("sub_queries", sub_queries)
        self.set_output("sub_query_count", len(sub_queries))

        try:
            set_state(self._canvas, "sub_queries", sub_queries)
        except Exception as e:
            logging.warning(f"[SubQueryDecomposer] Failed to write shared state: {e}")

    async def _decompose(self, query: str) -> list[str]:
        """Decompose a complex query into 2-5 sub-queries using LLM."""
        if not query:
            return []

        chat_mdl = self._create_llm_bundle(self._param.llm_id)
        prompt = self._param.sub_query_prompt or DEFAULT_SUB_QUERY_PROMPT
        prompt = self.string_format(prompt, {"query": query})
        history = [{"role": "user", "content": prompt}]

        try:
            ans = await chat_mdl.async_chat("", history, {"temperature": 0.0, "max_tokens": 512})
        except Exception as e:
            logging.warning(f"[SubQueryDecomposer] LLM decomposition failed: {e}")
            return []

        if not ans or "**ERROR**" in ans:
            logging.warning(f"[SubQueryDecomposer] LLM returned error: {ans}")
            return []

        return self._parse_sub_queries(ans)

    def _parse_sub_queries(self, ans: str) -> list[str]:
        """解析 LLM 返回的子查询列表。

        解析策略（按优先级）：
          1. 尝试 JSON 解析（使用 json_repair 容错）
          2. 如果 JSON 是 dict，提取其中的 list 值
          3. 解析失败时回退为按编号行拆分（支持 "1. "、"- "、"一、" 等格式）
        最后去重并限制数量范围。
        """
        ans = _clean_llm_output(ans)
        sub_queries: list[str] = []
        try:
            data = json_repair.loads(ans)
            if isinstance(data, list):
                sub_queries = [str(item).strip() for item in data if item]
            elif isinstance(data, dict):
                # Some models may wrap the list under a key.
                for value in data.values():
                    if isinstance(value, list):
                        sub_queries = [str(item).strip() for item in value if item]
                        break
        except Exception as exc:
            logging.warning(f"[SubQueryDecomposer] Failed to parse LLM response as JSON: {exc}")
            # Fallback: split by numbered lines.
            sub_queries = _fallback_split_sub_queries(ans)

        # Deduplicate and clamp to configured range.
        seen = set()
        unique = []
        for q in sub_queries:
            if not q:
                continue
            key = q.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(q)

        if len(unique) < self._param.min_count:
            # If LLM returned too few, keep the original query as the only sub-query.
            query = self._resolve_query({})
            if query and query.lower() not in seen:
                unique.insert(0, query)

        return unique[: self._param.max_count]

    def _resolve_query(self, kwargs: dict) -> str:
        key = self._param.query or "sys.query"
        if key in kwargs:
            return kwargs[key] or ""
        return self._canvas.get_variable_value(key) or ""

    def _create_llm_bundle(self, model_id: str):
        from api.db.joint_services.tenant_model_service import get_model_config_by_type_and_name
        from api.db.services.llm_service import LLMBundle

        config = get_model_config_by_type_and_name(self._canvas.get_tenant_id(), LLMType.CHAT, model_id)
        return LLMBundle(self._canvas.get_tenant_id(), config)

    def thoughts(self) -> str:
        return f"Decomposing complex query into sub-queries for: {self._param.query}"


def _clean_llm_output(ans: str) -> str:
    if not isinstance(ans, str):
        ans = str(ans)
    ans = re.sub(r"<think>.*?</think>", "", ans, flags=re.DOTALL)
    ans = re.sub(r"^.*?```json", "", ans, flags=re.DOTALL)
    ans = re.sub(r"```\s*$", "", ans, flags=re.DOTALL)
    return ans.strip()


def _fallback_split_sub_queries(ans: str) -> list[str]:
    """Fallback parser that extracts numbered or bullet items."""
    lines = ans.splitlines()
    result = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Remove leading numbering/bullets like "1. " or "- "
        line = re.sub(r"^(?:\d+[\.、]\s*|[-*•]\s+)", "", line)
        if line:
            result.append(line)
    return result


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b:
        return 0.0
    a_vec = np.array(a, dtype=float)
    b_vec = np.array(b, dtype=float)
    norm_a = np.linalg.norm(a_vec)
    norm_b = np.linalg.norm(b_vec)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a_vec, b_vec) / (norm_a * norm_b))


def merge_sub_query_results(
    results_per_query: list[list[dict]],
    k: int = DEFAULT_RRF_K,
    top_k: int = DEFAULT_SUB_QUERY_TOP_K,
    dedup_threshold: float = DEFAULT_DEDUP_THRESHOLD,
    embedding_fn=None,
) -> list[dict]:
    """使用 RRF 算法合并多个子查询的检索结果，并进行语义去重。

    RRF（Reciprocal Rank Fusion）公式：score(d) = Σ 1/(k + rank_i(d) + 1)
    其中 k 为平滑常数（默认 60），rank_i(d) 为文档 d 在第 i 个子查询结果中的排名。

    去重策略：
      - 有 embedding_fn 时：计算余弦相似度，≥ dedup_threshold（默认 0.92）视为重复
      - 无 embedding_fn 时：使用文档内容精确匹配

    每个结果保留 sub_query_sources 字段，记录该结果被哪些子查询命中（用于溯源）。

    Args:
        results_per_query: 每个子查询的检索结果列表
        k: RRF 平滑常数，越大则排名差异的影响越小
        top_k: 最终保留的结果数量
        dedup_threshold: 语义去重的余弦相似度阈值
        embedding_fn: 可选的文档向量化函数

    Returns:
        去重后的 Top-K 结果列表，按 RRF 分数降序排列
    """
    if not results_per_query:
        return []

    # RRF scoring and source tracking.
    rrf_scores: dict[str, float] = {}
    doc_by_id: dict[str, dict] = {}
    sources_by_id: dict[str, set[int]] = {}

    for query_idx, results in enumerate(results_per_query):
        for rank, doc in enumerate(results):
            if not isinstance(doc, dict):
                continue
            doc_id = str(doc.get("id") or doc.get("doc_id") or doc.get("chunk_id") or _doc_hash(doc))
            score = 1.0 / (k + rank + 1)
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + score
            if doc_id not in doc_by_id:
                doc_by_id[doc_id] = dict(doc)
            sources_by_id.setdefault(doc_id, set()).add(query_idx)

    if not doc_by_id:
        return []

    sorted_docs = sorted(doc_by_id.items(), key=lambda x: rrf_scores[x[0]], reverse=True)

    # Semantic deduplication.
    merged: list[dict] = []
    embeddings: list[list[float]] = []

    for doc_id, doc in sorted_docs:
        if embedding_fn is None:
            # Without embeddings, skip exact content duplicates.
            content = _doc_text(doc)
            if any(_doc_text(existing) == content for existing in merged):
                # Merge source indices.
                existing_doc = next(existing for existing in merged if _doc_text(existing) == content)
                existing_sources = set(existing_doc.get("sub_query_sources", []))
                existing_sources.update(sources_by_id.get(doc_id, set()))
                existing_doc["sub_query_sources"] = sorted(existing_sources)
                continue
            doc_copy = dict(doc)
            doc_copy["sub_query_sources"] = sorted(sources_by_id.get(doc_id, set()))
            doc_copy["rrf_score"] = rrf_scores.get(doc_id, 0.0)
            merged.append(doc_copy)
            continue

        emb = embedding_fn(doc)
        if emb is None:
            continue

        is_duplicate = False
        for existing_emb in embeddings:
            sim = _cosine_similarity(emb, existing_emb)
            if sim >= dedup_threshold:
                is_duplicate = True
                break

        if is_duplicate:
            continue

        doc_copy = dict(doc)
        doc_copy["sub_query_sources"] = sorted(sources_by_id.get(doc_id, set()))
        doc_copy["rrf_score"] = rrf_scores.get(doc_id, 0.0)
        merged.append(doc_copy)
        embeddings.append(emb)

    return merged[:top_k]


def _doc_text(doc: dict) -> str:
    return str(doc.get("content") or doc.get("content_with_weight") or doc.get("text") or "")


def _doc_hash(doc: dict) -> str:
    """Stable hash for documents without an explicit id."""
    import hashlib

    text = _doc_text(doc)
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]
