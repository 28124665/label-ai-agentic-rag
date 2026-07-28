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
"""语言工具模块。

统一 agent/langgraph 内部使用的语言标识（zh_CN / zh_TW / en）与历史 RAGFlow
模块使用的语言标识（zh-simplified / zh-traditional / en）之间的映射，
并提供繁简转换等通用能力。
"""

from rag.nlp.lang_detect import (
    LANG_EN,
    LANG_ZH_SIMPLIFIED,
    LANG_ZH_TRADITIONAL,
    detect_language,
)
from rag.nlp.t2s import convert

# LangGraph 语言标识 -> 历史 RAGFlow 语言标识
LANGGRAPH_TO_HISTORICAL = {
    "zh_CN": LANG_ZH_SIMPLIFIED,
    "zh_TW": LANG_ZH_TRADITIONAL,
    "en": LANG_EN,
}

# 历史 RAGFlow 语言标识 -> LangGraph 语言标识
HISTORICAL_TO_LANGGRAPH = {
    LANG_ZH_SIMPLIFIED: "zh_CN",
    LANG_ZH_TRADITIONAL: "zh_TW",
    LANG_EN: "en",
}


def to_historical_lang(langgraph_lang: str) -> str:
    """将 LangGraph 语言标识转换为历史 RAGFlow 语言标识。"""
    return LANGGRAPH_TO_HISTORICAL.get(langgraph_lang, LANG_ZH_SIMPLIFIED)


def to_langgraph_lang(historical_lang: str) -> str:
    """将历史 RAGFlow 语言标识转换为 LangGraph 语言标识。"""
    return HISTORICAL_TO_LANGGRAPH.get(historical_lang, "zh_CN")


def detect_query_language(query: str) -> tuple[str, str]:
    """检测查询语言并返回两种标识。

    Returns:
        tuple: (langgraph_lang, historical_lang)
    """
    historical_lang = detect_language(query)
    return to_langgraph_lang(historical_lang), historical_lang


def get_query_simplified(query: str, historical_lang: str) -> str:
    """根据语言将查询转换为简体（英文保持不变）。"""
    if historical_lang == LANG_EN:
        return query
    return convert(query)
