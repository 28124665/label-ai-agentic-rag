#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
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
"""多语言 Rerank 封装模块。

对应设计文档「Rerank 基于 search_text」章节。

为何 Rerank 必须使用 ``search_text`` 而非 ``original_text``
----------------------------------------------------------

查询在预处理阶段（见 :mod:`api.utils.query_preprocessor`）已经统一被转换为简体中文
（``query_simplified``）。文档入库阶段也对每条 chunk 存储了 ``search_text``（繁转简后）
与 ``original_text``（保留原文，例如台湾繁体原文）两个字段。

Rerank 阶段的核心是把「查询」与「候选文档」组成 pair，交由 rerank 模型计算语义相关性
分数。为了保证 rerank 模型在同一语言、同一字形空间内计算相似度，必须使用 ``search_text``
（简体）与 ``query_simplified``（简体）配对，而非 ``original_text``（繁体）。

如果错用 ``original_text``（繁体）与 ``query_simplified``（简体）配对，rerank 模型会同时
看到繁体字形与简体字形，引入字形差异噪声，导致：

- 短文本场景下字形差异占比过高，rerank 分数被字形差异主导，无法反映真实语义相关性；
- 多语言 rerank 模型对繁简体可能给出不一致的分数，排序结果不稳定。

因此本模块在构造 pair 时严格使用 :func:`api.utils.chunk_preprocessor.extract_search_text`
提取每个 chunk 的 ``search_text`` 字段，保证 query 与 candidate 在同一简体语义空间内。

rerank_model 接口契约
----------------------

注入到 :class:`MultilingualReranker` 的 ``rerank_model`` 实例需提供 ``score_pairs`` 方法：

.. code-block:: python

    def score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        ...

- 输入：``pairs`` 为 ``(query, document)`` 二元组列表；
- 输出：与 ``pairs`` 等长的 ``float`` 列表，第 i 个元素表示 ``pairs[i]`` 的相关性分数。

若 ``rerank_model`` 为 ``None`` 或调用 ``score_pairs`` 抛出异常，本模块会记录 warning 并
按原顺序返回前 ``top_k`` 条结果（即跳过 rerank 步骤），保证整体检索流程的可用性。
"""

import logging

from api.utils.chunk_preprocessor import extract_search_text

logger = logging.getLogger(__name__)


class MultilingualReranker:
    """多语言 Rerank 封装器。

    将 rerank 模型的调用细节（pair 构造、分数回填、异常兜底）封装在一处，
    上层只需传入 ``query_simplified`` 与候选 ``results`` 列表即可得到 rerank 后的结果。

    Attributes:
        rerank_model: 注入的 rerank 模型实例，需实现 ``score_pairs(pairs) -> list[float]``。
            允许为 ``None``，此时 :meth:`rerank` 会跳过模型调用并按原顺序返回前 ``top_k`` 条。
    """

    def __init__(self, rerank_model=None):
        """初始化 Rerank 封装器。

        Args:
            rerank_model: 可选的 rerank 模型实例。若为 ``None``，调用 :meth:`rerank` 时
                会跳过模型评分并按原顺序截取前 ``top_k`` 条返回。
        """
        self.rerank_model = rerank_model

    def _build_pairs(self, query_simplified: str, results: list[dict]) -> list[tuple[str, str]]:
        """构造 ``(query_simplified, search_text)`` 二元组列表。

        Args:
            query_simplified: 经预处理后的简体查询字符串。
            results: 候选 chunk 字典列表。

        Returns:
            与 ``results`` 等长的 ``(query_simplified, search_text)`` 二元组列表，
            其中 ``search_text`` 通过
            :func:`api.utils.chunk_preprocessor.extract_search_text` 提取，
            无 ``search_text`` 字段时 fallback 到 ``content``。
        """
        return [(query_simplified, extract_search_text(r)) for r in results]

    def rerank(self, query_simplified: str, results: list[dict], top_k: int = 10) -> list[dict]:
        """对候选 ``results`` 做 rerank 并按分数降序截取前 ``top_k`` 条。

        处理流程：

        1. 若 ``results`` 为空，直接返回空列表；
        2. 若 ``rerank_model`` 为 ``None``，记录 warning 并按原顺序返回前 ``top_k`` 条；
        3. 构造 ``(query_simplified, search_text)`` pairs，调用 ``rerank_model.score_pairs``
           获取分数列表；
        4. 将分数回填到每个 result 的 ``rerank_score`` 字段；
        5. 按 ``rerank_score`` 降序排序，截取前 ``top_k`` 条返回；
        6. 若 ``score_pairs`` 调用抛出异常，记录 warning，按原顺序返回前 ``top_k`` 条，
           并把 ``rerank_score`` 置为 ``0.0``。

        Args:
            query_simplified: 经预处理后的简体查询字符串。
            results: 候选 chunk 字典列表。
            top_k: 最终返回的条数上限，默认 10。

        Returns:
            rerank 后的 chunk 字典列表，长度 ``min(top_k, len(results))``。
            每个字典新增 ``rerank_score`` 字段。
        """
        # 边界检查 1：空结果列表
        # 作用：避免后续代码因空列表而报错，快速返回
        if not results:
            return []

        # 边界检查 2：top_k <= 0
        # 作用：避免返回负数或零条结果，保证语义正确
        if top_k <= 0:
            return []

        # 兜底策略 1：无 rerank 模型时跳过评分
        # 作用：保证即使没有配置 rerank 模型，检索流程仍能正常进行
        # 实现：按原顺序返回前 top_k 条（保留向量检索的原始排序）
        if self.rerank_model is None:
            logger.warning("rerank_model is None, skip rerank and return top_k=%d results in original order.", top_k)
            return results[:top_k]

        # 步骤 1：构造 (query, document) pairs
        # 关键设计：使用 search_text 而非 original_text
        # 为什么必须用 search_text？
        #   1. query_simplified 已经是简体（查询预处理阶段已繁转简）
        #   2. search_text 也是简体（文档入库时已繁转简）
        #   3. original_text 可能是繁体（保留原文）
        #   4. 如果用 original_text（繁体）与 query_simplified（简体）配对
        #      rerank 模型会同时看到繁简体，引入字形差异噪声，降低排序精度
        #   5. 用 search_text 保证 query 和 document 在同一简体语义空间
        pairs = self._build_pairs(query_simplified, results)

        # 步骤 2：调用 rerank 模型计算分数
        # 兜底策略 2：模型调用失败时按原顺序返回
        # 作用：保证即使 rerank 模型出错，检索流程仍能返回结果
        try:
            scores = self.rerank_model.score_pairs(pairs)
        except Exception as e:
            logger.warning("rerank_model.score_pairs failed: %s. Fallback to original order.", e)
            return results[:top_k]

        # 防御性检查：scores 长度必须与 pairs 等长
        # 作用：避免模型返回异常数据导致后续逻辑错误
        if not isinstance(scores, list) or len(scores) != len(pairs):
            logger.warning(
                "rerank_model.score_pairs returned invalid scores (len=%s, expected=%d). Fallback to original order.",
                "non-list" if not isinstance(scores, list) else len(scores),
                len(pairs),
            )
            return results[:top_k]

        # 步骤 3：回填 rerank_score 到每个 result
        # 作用：保留 rerank 分数，便于后续分析、调试、日志记录
        # 防御性编程：分数转换失败时置为 0.0，避免类型错误
        for r, score in zip(results, scores, strict=False):
            try:
                r["rerank_score"] = float(score)
            except (TypeError, ValueError):
                r["rerank_score"] = 0.0

        # 步骤 4：按分数降序排序并截取 top_k
        # 作用：返回最相关的 top_k 条结果
        # 排序策略：按 rerank_score 降序（分数越高越相关）
        sorted_results = sorted(results, key=lambda x: x.get("rerank_score", 0.0), reverse=True)
        return sorted_results[:top_k]


# 模块级单例：rerank_model 默认为 None，调用方可在运行时替换为真实模型
default_reranker = MultilingualReranker()


def rerank(query_simplified: str, results: list[dict], top_k: int = 10, rerank_model=None) -> list[dict]:
    """模块级便捷函数：使用指定或默认 reranker 对 results 做 rerank。

    Args:
        query_simplified: 经预处理后的简体查询字符串。
        results: 候选 chunk 字典列表。
        top_k: 最终返回的条数上限，默认 10。
        rerank_model: 可选的 rerank 模型实例。若为 ``None``，使用 :data:`default_reranker`
            内部的 ``rerank_model``（默认同样为 ``None``，会触发跳过逻辑）。

    Returns:
        rerank 后的 chunk 字典列表，长度 ``min(top_k, len(results))``。
    """
    if rerank_model is not None:
        # 临时构造一个 reranker 以使用指定的模型，避免修改单例状态
        reranker = MultilingualReranker(rerank_model)
        return reranker.rerank(query_simplified, results, top_k=top_k)
    return default_reranker.rerank(query_simplified, results, top_k=top_k)
