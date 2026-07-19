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
"""查询预处理与语言路由模块。

对应设计文档「查询预处理与语言路由」章节。

为何要做查询预处理
------------------

文档入库阶段已经对每条 chunk 同时存储 ``original_text``（保留原文语言，例如台湾繁体原文）
与 ``search_text``（繁转简后的简体文本，用于检索/Embedding/Rerank）。因此检索阶段，
用户查询也必须经过同样的「繁转简」预处理，才能与文档 ``search_text`` 处于同一语义空间，
保证向量检索与 Rerank 的相似度计算是「同语言对同语言」，避免繁体查询与简体文档向量
错位导致的召回率下降。

路由策略
--------

根据查询语言将请求路由到不同分区，并选用匹配的 Embedding 模型：

- 中文查询（无论简体/繁体）-> ``chinese`` 分区 -> 使用 ``BAAI/bge-large-zh`` 中文 Embedding
  模型，该模型在中文语义向量空间表现更优。
- 英文查询 -> ``english`` 分区 -> 使用 ``text-embedding-3-small`` 英文 Embedding 模型，
  英文场景下该模型在性能与成本之间平衡更好。

中文查询（无论原繁体/简体）均统一路由到 ``chinese`` 分区，因为繁体查询在预处理阶段已被
转换为简体，与 ``chinese`` 分区中文文档的 ``search_text``（简体）一致，可以共用同一套
中文 Embedding 模型与向量索引。
"""

from rag.nlp.lang_detect import LANG_EN, LANG_ZH_SIMPLIFIED, detect_language
from rag.nlp.t2s import convert

# 中文分区名
PARTITION_CHINESE = "chinese"
# 英文分区名
PARTITION_ENGLISH = "english"

# 中文场景推荐的 Embedding 模型
EMBEDDING_MODEL_ZH = "BAAI/bge-large-zh"
# 英文场景推荐的 Embedding 模型
EMBEDDING_MODEL_EN = "text-embedding-3-small"


def route_partition(query_lang: str) -> str:
    """根据查询语言返回对应的路由分区名。

    Args:
        query_lang: 语言标识，取值为 :data:`LANG_ZH_SIMPLIFIED` / :data:`LANG_ZH_TRADITIONAL`
            / :data:`LANG_EN`。其他未知值按中文兜底处理（默认值即中文）。

    Returns:
        分区名字符串：中文（含简体/繁体/未知）返回 :data:`PARTITION_CHINESE`，
        英文返回 :data:`PARTITION_ENGLISH`。
    """
    if query_lang == LANG_EN:
        return PARTITION_ENGLISH
    return PARTITION_CHINESE


def preprocess_query(query: str) -> dict:
    """对原始用户查询做预处理与语言路由。

    处理流程：

    1. 调用 :func:`rag.nlp.lang_detect.detect_language` 检测查询语言；
    2. 英文查询：原样保留，路由到 ``english`` 分区，建议使用英文 Embedding 模型；
    3. 中文查询（简体/繁体）：调用 :func:`rag.nlp.t2s.convert` 做繁转简
       （简体文本经过转换保持不变），路由到 ``chinese`` 分区，建议使用中文 Embedding 模型；
    4. 空查询兜底：返回简体中文默认值，避免下游因 ``None`` / 空串报错。

    Args:
        query: 原始用户查询字符串，允许为 ``None`` 或空字符串。

    Returns:
        字典，包含以下字段：

        - ``query_lang``：查询语言标识（``zh-simplified`` / ``zh-traditional`` / ``en``）
        - ``query_simplified``：转换后的查询文本（中文繁体已转简体，英文保持不变）
        - ``partition``：路由分区名（``"chinese"`` 或 ``"english"``）
        - ``embedding_model``：建议使用的 Embedding 模型名
    """
    # 空查询兜底：默认按简体中文处理
    # 作用：避免下游代码因 None 或空串而报错，保证检索流程的健壮性
    if not query or not query.strip():
        return {
            "query_lang": LANG_ZH_SIMPLIFIED,
            "query_simplified": "",
            "partition": PARTITION_CHINESE,
            "embedding_model": EMBEDDING_MODEL_ZH,
        }

    # 步骤 1：检测查询语言
    # 作用：判断用户查询是繁体中文、简体中文还是英文，决定后续处理策略
    query_lang = detect_language(query)

    if query_lang == LANG_EN:
        # 英文查询处理策略：
        # 1. query_simplified：保持原样（英文不需要繁转简）
        # 2. partition：路由到 english 分区（英文文档存储在该分区）
        # 3. embedding_model：使用英文 Embedding 模型（text-embedding-3-small）
        # 为什么英文不需要转换？
        #   1. 英文 Embedding 模型（如 text-embedding-3-small）在英文语义空间表现最优
        #   2. 英文文档存储时也是原文，查询与文档在同一语义空间
        return {
            "query_lang": query_lang,
            "query_simplified": query,
            "partition": PARTITION_ENGLISH,
            "embedding_model": EMBEDDING_MODEL_EN,
        }

    # 中文查询处理策略（简体/繁体）：
    # 1. query_simplified：调用 convert() 做繁转简（简体经 convert 后保持不变）
    # 2. partition：路由到 chinese 分区（中文文档存储在该分区，search_text 为简体）
    # 3. embedding_model：使用中文 Embedding 模型（BAAI/bge-large-zh）
    # 为什么中文需要繁转简？
    #   1. 文档入库时，繁体文档的 search_text 已被转换为简体
    #   2. 查询也必须转换为简体，才能与文档的 search_text 在同一语义空间
    #   3. 如果不转换，繁体查询与简体文档之间的字形差异会降低向量召回率
    return {
        "query_lang": query_lang,
        "query_simplified": convert(query),
        "partition": PARTITION_CHINESE,
        "embedding_model": EMBEDDING_MODEL_ZH,
    }
