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
import json
import logging
import os
import re
from abc import ABC
from typing import Any

import json_repair

from agent.component.base import ComponentBase, ComponentParamBase
from agent.component.state_fields import set_state
from common.connection_utils import timeout
from common.constants import LLMType

"""查询重写与复杂度分析组件。

对应需求：P2-FR-02（查询重写与分级重试）

功能说明：
  分析用户查询的复杂度类型，根据复杂度选择对应的查询重写策略，
  提升检索召回率和精准度。

实现方式：
  1. 查询复杂度分析（analyze_query_complexity）：
     基于规则的分类器，按优先级依次判断：
     - multi_aspect：包含"比较/区别/差异/优劣/vs"等对比词 -> 推荐子查询拆解
     - factual：包含年份、数字、百分比等具体数据 -> 推荐 HyDE
     - boolean：是/否问题（"是否/有没有/能否"开头或"吗？"结尾） -> 推荐同义词扩展
     - simple：长度 ≤ 15 字且实体 ≤ 1 个 -> 推荐同义词扩展
     - complex：多实体、长度 > 35、多问号、复杂连词 -> 推荐全部策略组合
     - unknown：规则无法覆盖 -> 使用轻量级 LLM 兜底分类
  2. 同义词扩展策略：
     - 从本地同义词词典（rag.nlp.synonym.Dealer）查找查询词的同义词
     - 可选使用 LLM 将同义词自然融入查询
     - LLM 失败时回退为 OR 拼接格式
  3. 策略选择：根据当前重试次数轮转使用推荐策略列表中的策略
  4. 实体识别：使用正则表达式提取中英文实体，不引入重型 NLP 库
"""


DEFAULT_SYNONYM_PROMPT = """你是一位查询改写专家。请根据用户原始查询和提供的同义词，生成一个改写后的查询。
改写要求：
1. 保留原始查询的核心语义
2. 自然融入同义词扩展，提高检索召回率
3. 不要改变原意，不要添加原始查询中没有的信息
4. 输出 ONLY 改写后的查询文本，不要解释

原始查询：{query}
可扩展同义词：{synonyms}

请输出改写后的查询："""

# 查询复杂度类型常量
# 每种复杂度对应推荐的查询重写策略列表
COMPLEXITY_SIMPLE = "simple"
COMPLEXITY_MULTI_ASPECT = "multi_aspect"
COMPLEXITY_FACTUAL = "factual"
COMPLEXITY_COMPLEX = "complex"
COMPLEXITY_BOOLEAN = "boolean"
COMPLEXITY_UNKNOWN = "unknown"

STRATEGY_SYNONYM_REWRITE = "synonym_rewrite"
STRATEGY_SUB_QUERY_DECOMPOSE = "sub_query_decompose"
STRATEGY_HYDE = "hyde"

COMPLEXITY_STRATEGIES = {
    COMPLEXITY_SIMPLE: [STRATEGY_SYNONYM_REWRITE],
    COMPLEXITY_BOOLEAN: [STRATEGY_SYNONYM_REWRITE],
    COMPLEXITY_MULTI_ASPECT: [STRATEGY_SUB_QUERY_DECOMPOSE],
    COMPLEXITY_FACTUAL: [STRATEGY_HYDE],
    COMPLEXITY_COMPLEX: [
        STRATEGY_SYNONYM_REWRITE,
        STRATEGY_SUB_QUERY_DECOMPOSE,
        STRATEGY_HYDE,
    ],
}

DEFAULT_STRATEGIES = [STRATEGY_SYNONYM_REWRITE]

DEFAULT_COMPLEXITY_PROMPT = """你是一位查询复杂度分析专家。请判断以下用户查询的复杂度类型，只能从 simple、multi_aspect、factual、complex、boolean 中选择一种。

判断标准：
- simple：查询简短（≤15字）且只涉及单一实体或概念
- multi_aspect：查询包含比较、区别、差异、优劣等对比含义
- factual：查询包含具体数字、年份、指标、数据等
- complex：查询包含多个实体、多个问题或复杂逻辑
- boolean：是/否问题

用户查询：{query}

请仅输出一个 JSON 对象，格式如下：
{{"complexity": "simple", "reason": "简短理由"}}
"""

# 查询特征匹配正则模式
# 用于基于规则的复杂度分类，覆盖中文和英文常见模式

_MULTI_ASPECT_PATTERN = re.compile(
    r"(?:比较|对比|区别|差异|优劣|不同|差距|vs|versus|相较|相比|哪个好|哪一种)",
    re.IGNORECASE,
)

_FACTUAL_PATTERN = re.compile(
    r"(?:\d{4}(?:年|[-/]\d{1,2})?|\d+(?:[.,]\d+)?%?|"
    r"[一二三四五六七八九十百千万亿]+|"
    r"Q[1-4]|第[一二三四1234]季度|"
    r"\d+(?:个|条|件|次|倍|元|美元|万|亿|吨|kg|千米|km|GB|MB|KB)?)",
    re.IGNORECASE,
)

_BOOLEAN_START_PATTERN = re.compile(
    r"^(?:是否|是不是|有没有|能否|可不可以|能不能|会否|会不会|对吗|对吧|"
    r"是|有|能|会|在|为|可)",
    re.UNICODE,
)
_BOOLEAN_END_PATTERN = re.compile(r"[吗？?]$", re.UNICODE)
_BOOLEAN_VERB_PATTERN = re.compile(
    r"(?:是|有|能|会|在|为|可|可以|对吗|对吧)",
    re.UNICODE,
)
_WH_WORD_PATTERN = re.compile(r"(?:什么|怎么|如何|哪些|为什么|多少|几|谁|哪里|何时)", re.UNICODE)

_COMPLEX_CONJUNCTION_PATTERN = re.compile(
    r"(?:不仅.*而且|虽然.*但是|因为.*所以|如果.*那么|除了.*还|"
    r"(?:什么|怎么|如何|哪些|介绍|说明|要求|方式|方法|配置|流程|步骤).{0,8}"
    r"[和与及以及].{0,8}"
    r"(?:什么|怎么|如何|哪些|介绍|说明|要求|方式|方法|配置|流程|步骤))",
    re.UNICODE,
)

_ENGLISH_ENTITY_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{1,}")
_CHINESE_ENTITY_PATTERN = re.compile(
    r"[\u4e00-\u9fa5]{2,}(?:公司|集团|系统|平台|产品|模型|框架|网络|引擎|语言|工具|"
    r"库|项目|团队|协会|组织|大学|学院|研究所|品牌|设备|软件|硬件|数据库|算法|方法)",
    re.UNICODE,
)


class QueryRewriterParam(ComponentParamBase):
    """
    Define the QueryRewriter component parameters.
    """

    def __init__(self):
        super().__init__()
        self.query = "sys.query"
        self.retry_count = "sys.retry_count"
        self.llm_id = ""
        self.enable_llm_fallback = True
        self.enable_synonym = True
        self.synonym_topn = 3
        self.synonym_prompt = ""
        self.max_retry_tokens = 2000
        self.max_retries = 3
        self.complexity_prompt = ""

    def check(self):
        self.check_defined_type(self.query, "[QueryRewriter] query", ["str"])
        self.check_defined_type(self.retry_count, "[QueryRewriter] retry_count", ["str"])
        self.check_boolean(self.enable_llm_fallback, "[QueryRewriter] enable_llm_fallback")
        self.check_boolean(self.enable_synonym, "[QueryRewriter] enable_synonym")
        self.check_nonnegative_number(self.synonym_topn, "[QueryRewriter] synonym_topn")
        self.check_nonnegative_number(self.max_retry_tokens, "[QueryRewriter] max_retry_tokens")
        self.check_nonnegative_number(self.max_retries, "[QueryRewriter] max_retries")


class QueryRewriter(ComponentBase, ABC):
    component_name = "QueryRewriter"

    def get_input_elements(self) -> dict[str, Any]:
        res = {}
        res.update(self.get_input_elements_from_text(self._param.query))
        res.update(self.get_input_elements_from_text(self._param.retry_count))
        return res

    def get_input_form(self) -> dict[str, dict]:
        return {
            "query": {"name": "Query", "type": "line"},
            "retry_count": {"name": "Retry Count", "type": "line"},
        }

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 10 * 60)))
    def _invoke(self, **kwargs):
        return asyncio.run(self._invoke_async(**kwargs))

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 10 * 60)))
    async def _invoke_async(self, **kwargs):
        if self.check_if_canceled("QueryRewriter processing"):
            return

        query = self._resolve_query(kwargs)
        retry_count = self._resolve_retry_count(kwargs)

        complexity, strategies = await self._analyze_query_complexity(query)
        selected_strategy = self._select_strategy(strategies, retry_count)

        rewritten_query = query
        if selected_strategy == STRATEGY_SYNONYM_REWRITE:
            rewritten_query = await self._rewrite_with_synonyms(query)

        self.set_output("query_complexity", complexity)
        self.set_output("recommended_strategies", strategies)
        self.set_output("selected_strategy", selected_strategy)
        self.set_output("rewritten_query", rewritten_query)

        try:
            set_state(self._canvas, "query_complexity", complexity)
            set_state(self._canvas, "retry_count", retry_count)
            set_state(self._canvas, "rewritten_query", rewritten_query)
        except Exception as e:
            logging.warning(f"[QueryRewriter] Failed to write shared state: {e}")

    async def _analyze_query_complexity(self, query: str) -> tuple[str, list[str]]:
        """Rule-based complexity analysis with optional lightweight LLM fallback."""
        complexity, _ = analyze_query_complexity(query)
        if complexity == COMPLEXITY_UNKNOWN:
            if self._param.enable_llm_fallback and self._param.llm_id:
                try:
                    chat_mdl = self._create_llm_bundle(self._param.llm_id)
                    complexity = await self._llm_classify_complexity(query, chat_mdl)
                except Exception as e:
                    logging.warning(f"[QueryRewriter] LLM fallback failed: {e}")
                    complexity = COMPLEXITY_SIMPLE
            else:
                complexity = COMPLEXITY_SIMPLE
        return complexity, list(COMPLEXITY_STRATEGIES.get(complexity, DEFAULT_STRATEGIES))

    async def _llm_classify_complexity(self, query: str, chat_mdl) -> str:
        prompt = self._param.complexity_prompt or DEFAULT_COMPLEXITY_PROMPT
        prompt = self.string_format(prompt, {"query": query})
        history = [{"role": "user", "content": prompt}]
        ans = await chat_mdl.async_chat("", history, {"temperature": 0.0, "max_tokens": 256})
        if not ans or "**ERROR**" in ans:
            logging.warning(f"[QueryRewriter] LLM returned error: {ans}")
            return COMPLEXITY_SIMPLE
        return self._parse_complexity_from_llm(ans)

    @staticmethod
    def _parse_complexity_from_llm(ans: str) -> str:
        ans = _clean_llm_output(ans)
        try:
            data = json_repair.loads(ans)
            if isinstance(data, dict):
                complexity = data.get("complexity") or data.get("type") or data.get("category")
                if complexity and complexity in COMPLEXITY_STRATEGIES:
                    return complexity
        except Exception as exc:
            logging.warning(f"[QueryRewriter] Failed to parse LLM response as JSON: {exc}")
        # Fallback keyword extraction from raw text.
        low = str(ans).lower()
        for key in COMPLEXITY_STRATEGIES:
            if key in low:
                return key
        return COMPLEXITY_SIMPLE

    def _resolve_query(self, kwargs: dict) -> str:
        key = self._param.query or "sys.query"
        if key in kwargs:
            return kwargs[key] or ""
        return self._canvas.get_variable_value(key) or ""

    def _resolve_retry_count(self, kwargs: dict) -> int:
        key = self._param.retry_count or "sys.retry_count"
        value = kwargs.get(key)
        if value is None:
            value = self._canvas.get_variable_value(key)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _create_llm_bundle(self, model_id: str):
        from api.db.joint_services.tenant_model_service import get_model_config_by_type_and_name
        from api.db.services.llm_service import LLMBundle

        config = get_model_config_by_type_and_name(self._canvas.get_tenant_id(), LLMType.CHAT, model_id)
        return LLMBundle(self._canvas.get_tenant_id(), config)

    @staticmethod
    def _select_strategy(strategies: list[str], retry_count: int) -> str:
        if not strategies:
            return STRATEGY_SYNONYM_REWRITE
        return strategies[retry_count % len(strategies)]

    async def _rewrite_with_synonyms(self, query: str) -> str:
        """Expand query with synonyms from local thesaurus and optional LLM rewrite."""
        if not query or not self._param.enable_synonym:
            return query

        expansion = _build_synonym_expansion(query, topn=self._param.synonym_topn)
        if not expansion or not expansion.get("synonyms"):
            return query

        synonyms_text = json.dumps(expansion["synonyms"], ensure_ascii=False)
        if self._param.llm_id:
            try:
                chat_mdl = self._create_llm_bundle(self._param.llm_id)
                prompt = self._param.synonym_prompt or DEFAULT_SYNONYM_PROMPT
                prompt = self.string_format(prompt, {"query": query, "synonyms": synonyms_text})
                history = [{"role": "user", "content": prompt}]
                ans = await chat_mdl.async_chat("", history, {"temperature": 0.0, "max_tokens": 256})
                if ans and "**ERROR**" not in ans:
                    ans = ans.strip().strip('"').strip("'").split("\n")[0]
                    if ans:
                        return ans
            except Exception as e:
                logging.warning(f"[QueryRewriter] LLM synonym rewrite failed: {e}")

        # Fallback: append OR-expanded terms.
        expanded_terms = [query]
        for term, syns in expansion["synonyms"].items():
            for syn in syns:
                expanded_terms.append(f'"{syn}"')
        return " OR ".join(expanded_terms)

    def thoughts(self) -> str:
        return f"Analyzing query complexity and selecting rewrite strategy for: {self._param.query}"


def _build_synonym_expansion(query: str, topn: int = 3) -> dict[str, Any]:
    """Lookup synonyms for query terms using the local synonym dictionary."""
    expansion: dict[str, Any] = {"query": query, "synonyms": {}}
    try:
        from rag.nlp.synonym import Dealer
        dealer = Dealer()
    except Exception as e:
        logging.warning(f"[QueryRewriter] Failed to load synonym dealer: {e}")
        return expansion

    # Tokenize: keep CJK characters and alphabetic words as separate terms.
    terms = _extract_query_terms(query)
    for term in terms:
        if not term or len(term) < 2:
            continue
        syns = dealer.lookup(term, topn=topn)
        if syns:
            expansion["synonyms"][term] = [str(s) for s in syns]
    return expansion


def _extract_query_terms(query: str) -> list[str]:
    """Extract candidate terms from a query for synonym lookup."""
    # CJK terms (2+ characters)
    cjk_terms = re.findall(r"[\u4e00-\u9fa5]{2,}", query)
    # Alphabetic words (2+ chars)
    en_terms = re.findall(r"[A-Za-z][A-Za-z0-9_\-]{1,}", query)
    # Deduplicate while preserving order.
    seen = set()
    terms = []
    for t in cjk_terms + en_terms:
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(t)
    return terms


def analyze_query_complexity(query: str) -> tuple[str, list[str]]:
    """基于规则的用户查询复杂度分析。

    分类优先级（从高到低）：
      1. multi_aspect：对比类查询（包含"比较/区别"等关键词）
      2. factual：事实类查询（包含数字/年份/百分比）
      3. boolean：是非类查询（"是否/有没有"开头或"吗？"结尾）
      4. simple：简单查询（≤15字且≤1个实体）
      5. complex：复杂查询（多实体/长文本/多问号/复杂连词）
      6. unknown：规则无法覆盖，交由调用方使用 LLM 兜底

    Returns:
        (complexity_type, recommended_strategies) 元组
    """
    if not query or not isinstance(query, str):
        return COMPLEXITY_SIMPLE, list(COMPLEXITY_STRATEGIES[COMPLEXITY_SIMPLE])

    query = query.strip()
    if not query:
        return COMPLEXITY_SIMPLE, list(COMPLEXITY_STRATEGIES[COMPLEXITY_SIMPLE])

    # Order matters: more specific signals are evaluated first.
    if _is_multi_aspect(query):
        return COMPLEXITY_MULTI_ASPECT, list(COMPLEXITY_STRATEGIES[COMPLEXITY_MULTI_ASPECT])
    if _is_factual(query):
        return COMPLEXITY_FACTUAL, list(COMPLEXITY_STRATEGIES[COMPLEXITY_FACTUAL])
    if _is_boolean(query):
        return COMPLEXITY_BOOLEAN, list(COMPLEXITY_STRATEGIES[COMPLEXITY_BOOLEAN])
    if _is_simple(query):
        return COMPLEXITY_SIMPLE, list(COMPLEXITY_STRATEGIES[COMPLEXITY_SIMPLE])
    if _is_complex(query):
        return COMPLEXITY_COMPLEX, list(COMPLEXITY_STRATEGIES[COMPLEXITY_COMPLEX])

    return COMPLEXITY_UNKNOWN, list(DEFAULT_STRATEGIES)


def _is_multi_aspect(query: str) -> bool:
    return bool(_MULTI_ASPECT_PATTERN.search(query))


def _is_factual(query: str) -> bool:
    return bool(_FACTUAL_PATTERN.search(query))


def _is_boolean(query: str) -> bool:
    """判断是否为是非类查询。

    判断逻辑：
      1. 以"是否/有没有/能否"等布尔前缀开头 -> True
      2. 否则需要同时满足：以"吗/？"结尾 + 包含布尔动词 + 不含疑问词（什么/怎么等）
    排除含疑问词的查询，避免将"什么是X吗"误判为布尔查询。
    """
    # Queries that explicitly start with a boolean prefix are yes/no questions.
    if _BOOLEAN_START_PATTERN.search(query):
        return True
    # Otherwise require a question ending, a boolean verb, and no wh-words.
    if not _BOOLEAN_END_PATTERN.search(query):
        return False
    if _WH_WORD_PATTERN.search(query):
        return False
    return bool(_BOOLEAN_VERB_PATTERN.search(query))


def _is_simple(query: str) -> bool:
    if len(query) > 15:
        return False
    return len(_extract_entities(query)) <= 1


def _is_complex(query: str) -> bool:
    entities = _extract_entities(query)
    if len(entities) >= 2:
        return True
    if len(query) > 35:
        return True
    if query.count("？") + query.count("?") >= 2:
        return True
    if _COMPLEX_CONJUNCTION_PATTERN.search(query):
        return True
    return False


def _extract_entities(query: str) -> list[str]:
    """使用正则表达式提取查询中的实体候选。

    支持两类实体：
      - 英文实体：以大写字母开头的字母数字组合（如 GPT-4、PyTorch）
      - 中文实体：2字以上中文词 + 组织/产品后缀（如"百度公司"、"深度学习框架"）
    不引入 NLP 库，保持轻量级。
    """
    seen = set()
    entities = []
    for pattern in (_ENGLISH_ENTITY_PATTERN, _CHINESE_ENTITY_PATTERN):
        for match in pattern.finditer(query):
            entity = match.group(0)
            if entity in seen:
                continue
            seen.add(entity)
            entities.append(entity)
    return entities


def _clean_llm_output(ans: str) -> str:
    if not isinstance(ans, str):
        ans = str(ans)
    ans = re.sub(r"<think>.*?</think>", "", ans, flags=re.DOTALL)
    ans = re.sub(r"^.*?```json", "", ans, flags=re.DOTALL)
    ans = re.sub(r"```\s*$", "", ans, flags=re.DOTALL)
    return ans.strip()
