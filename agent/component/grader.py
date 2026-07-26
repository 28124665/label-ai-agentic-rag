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
import os
import re
import time
from abc import ABC
from typing import Any, Dict, Optional

import json_repair

from agent.component.base import ComponentBase, ComponentParamBase
from agent.component.state_fields import set_state
from api.utils import metrics
from api.utils.structured_logger import log_grader
from common.connection_utils import timeout
from common.constants import LLMType
from common.token_utils import num_tokens_from_string

"""Grader 检索结果评估组件。

对应需求：P2-FR-03（Grader 检索结果评估）

功能说明：
  在 Retrieval 组件的 Rerank（Cross-Encoder）之后，对检索返回的文档进行
  更深层次的语义评估，过滤不相关文档，提高生成答案的质量。

  Rerank 解决的是"排序"问题（谁更相关），Grader 解决的是"判断"问题（谁能不能用）。
  Rerank 基于 Cross-Encoder 的语义相似度分数无法区分"语义相似但不能回答"
  和"语义相似且能回答"的文档，Grader 通过 LLM/NLI 进行深层语义判断来弥补这一不足。

实现方式：
  1. 两种评估模式（Rerank 之后）：
     - llm（LLM-as-Judge）：使用 LLM 对文档进行语义相关性判断
       适合复杂查询、多文档 QA，精度最高
     - local_nli（NLI）：使用 NLI（自然语言推理）风格的 prompt 进行蕴含判断
       适合事实性查询（查数据、查指标），判断文档是否蕴含答案
  2. Rerank 分数降级：
     当 LLM/NLI 调用失败时，自动复用检索阶段已有的 Rerank 分数（rerank_score）
     进行阈值过滤，零额外延迟和成本。
  3. 批量评估：将文档按 batch_size 分批发送给 LLM，减少调用次数。
     每批文档的总 token 数不超过 max_eval_tokens。
  4. 长文档处理：超过 max_eval_tokens 的文档按语义段落（双换行）拆分，
     再按句子边界进一步切分，确保每个 chunk 不超过 token 限制。
  5. 多级降级策略：
     - LLM 调用超时 -> 使用 Rerank 分数降级
     - JSON 解析失败 -> 重试 max_retry_on_parse_error 次
     - 配额超限 -> 切换到 backup_llm_model
     - 服务不可用 -> 降级为 Rerank 分数或标记全部相关
  6. 结果聚合：同一文档的多个 chunk 评估结果取最高分。
  7. 可观测性：记录评估耗时、命中率、降级原因等指标和结构化日志。

设计说明：
  已移除 cross_encoder 作为主动评估模式。原因是 Grader 在 Retrieval 之后执行，
  Retrieval 内部已调用 Rerank（Cross-Encoder）完成重排序，如果 Grader 再次调用
  Cross-Encoder 属于冗余计算。Rerank 分数已作为降级方案保留。
"""


DEFAULT_GRADER_PROMPT = """你是一个文档相关性评估专家。
请判断以下每个文档是否与用户问题直接相关。

用户问题：{query}

待评估文档：
{documents}

请输出 JSON 数组，格式如下：
[
  {"index": 0, "relevance": "relevant", "score": 0.92, "reason": "..."},
  {"index": 1, "relevance": "not_relevant", "score": 0.15, "reason": "..."}
]
"""

DEFAULT_NLI_PROMPT = """你是自然语言推理（NLI）专家。请判断每个文档（premise）是否支持或蕴含用户问题（hypothesis）。

用户问题：{query}

待评估文档：
{documents}

请输出 JSON 数组，格式如下：
[
  {"index": 0, "relevance": "relevant", "score": 0.92, "reason": "文档蕴含问题答案"},
  {"index": 1, "relevance": "not_relevant", "score": 0.15, "reason": "文档与问题无关"}
]

relevance 取值说明：
- relevant：文档能回答或蕴含用户问题（entailment）
- not_relevant：文档与用户问题无关、矛盾或无法支撑（contradiction / neutral）
"""

RELEVANT = "relevant"
NOT_RELEVANT = "not_relevant"


class GraderError(Exception):
    pass


class LLMQuotaExceededError(GraderError):
    pass


class LLMResponseParseError(GraderError):
    pass


class LLMServiceUnavailableError(GraderError):
    pass


class GraderParam(ComponentParamBase):
    """Grader 组件参数定义。

    关键参数说明：
      - evaluator_model: 评估模式（llm/local_nli）
        已移除 cross_encoder 模式，因为 Grader 在 Rerank 之后执行，
        再次调用 Cross-Encoder 属于冗余计算。Rerank 分数作为降级方案保留。
      - batch_size: 每批评估的文档数量（最大不超过 max_batch_size）
      - max_eval_tokens: 单次 LLM 调用的最大 token 数（含 prompt 和文档）
      - timeout_seconds: LLM 调用超时时间
      - fallback_on_failure: 评估失败时是否降级（True=使用 Rerank 分数，False=标记全部相关）
      - max_retry_on_parse_error: JSON 解析失败时的重试次数
      - backup_llm_model: 主模型配额超限时使用的备用模型
      - relevance_threshold: 降级时判定相关的分数阈值（用于 Rerank 分数降级）
    """

    def __init__(self):
        super().__init__()
        self.query = "sys.query"
        self.retrieved_docs = "sys.retrieved_docs"
        self.evaluator_model = "llm"  # llm | local_nli
        self.batch_size = 5
        self.max_batch_size = 10
        self.max_eval_tokens = 2000
        self.timeout_seconds = 10
        self.fallback_on_failure = True
        self.max_retry_on_parse_error = 1
        self.backup_llm_model = None
        self.relevance_threshold = 0.5
        self.llm_id = ""
        self.prompt = ""
        self.nli_prompt = ""

    def check(self):
        self.check_defined_type(self.query, "[Grader] query", ["str"])
        self.check_defined_type(self.retrieved_docs, "[Grader] retrieved_docs", ["str"])
        self.check_valid_value(
            self.evaluator_model,
            "[Grader] evaluator_model",
            ["llm", "local_nli"],
        )
        self.check_positive_integer(self.batch_size, "[Grader] batch_size")
        self.check_positive_integer(self.max_batch_size, "[Grader] max_batch_size")
        self.check_positive_integer(self.max_eval_tokens, "[Grader] max_eval_tokens")
        self.check_positive_number(self.timeout_seconds, "[Grader] timeout_seconds")
        self.check_boolean(self.fallback_on_failure, "[Grader] fallback_on_failure")
        self.check_nonnegative_number(self.max_retry_on_parse_error, "[Grader] max_retry_on_parse_error")
        self.check_decimal_float(float(self.relevance_threshold), "[Grader] relevance_threshold")
        if self.batch_size > self.max_batch_size:
            raise ValueError("[Grader] batch_size can not exceed max_batch_size")
        # Both llm and local_nli modes require llm_id
        self.check_empty(self.llm_id, "[Grader] llm_id")


class Grader(ComponentBase, ABC):
    """Grader 检索结果评估组件。

    继承 ComponentBase，作为 Canvas 工作流中的独立节点运行。
    输入：用户查询（query）+ 检索返回的文档列表（retrieved_docs）
    输出：评估后的文档列表（graded_docs）+ 是否有相关文档（has_relevant）+ 相关文档数（relevant_count）
    """
    component_name = "Grader"

    def get_input_elements(self) -> dict[str, Any]:
        res = {}
        res.update(self.get_input_elements_from_text(self._param.query))
        res.update(self.get_input_elements_from_text(self._param.retrieved_docs))
        return res

    def get_input_form(self) -> dict[str, dict]:
        return {
            "query": {"name": "Query", "type": "line"},
            "retrieved_docs": {"name": "Retrieved Docs", "type": "line"},
        }

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 10 * 60)))
    def _invoke(self, **kwargs):
        return asyncio.run(self._invoke_async(**kwargs))

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 10 * 60)))
    async def _invoke_async(self, **kwargs):
        if self.check_if_canceled("Grader processing"):
            return

        start_ts = time.perf_counter()
        query = self._resolve_query(kwargs)
        docs = self._resolve_docs(kwargs)
        if not isinstance(docs, list):
            docs = []
        docs = self._normalize_docs(docs)

        if not docs:
            self._write_outputs([])
            self._record_metrics([], start_ts, "success")
            return

        evaluator = self._param.evaluator_model
        if evaluator == "llm":
            graded = await self._evaluate_with_llm(query, docs)
        else:  # local_nli
            graded = await self._evaluate_with_local_nli(query, docs)

        self._write_outputs(graded)
        self._record_metrics(graded, start_ts, "success")

    def _resolve_query(self, kwargs: dict) -> str:
        key = self._param.query or "sys.query"
        if key in kwargs:
            return kwargs[key]
        return self._canvas.get_variable_value(key) or ""

    def _resolve_docs(self, kwargs: dict) -> list:
        key = self._param.retrieved_docs or "sys.retrieved_docs"
        if key in kwargs:
            return kwargs[key]
        return self._canvas.get_variable_value(key) or []

    def _normalize_docs(self, docs: list) -> list[dict]:
        normalized = []
        for i, doc in enumerate(docs):
            if isinstance(doc, dict):
                nd = dict(doc)
            else:
                nd = {"content": str(doc)}
            nd["index"] = i
            if "content" not in nd or not nd["content"]:
                nd["content"] = nd.get("content_with_weight") or str(doc)
            normalized.append(nd)
        return normalized

    def _write_outputs(self, graded: list[dict]):
        has_relevant = any(d.get("relevance") == RELEVANT for d in graded)
        relevant_count = sum(1 for d in graded if d.get("relevance") == RELEVANT)

        self.set_output("graded_docs", graded)
        self.set_output("has_relevant", has_relevant)
        self.set_output("relevant_count", relevant_count)

        try:
            set_state(self._canvas, "graded_docs", graded)
            set_state(self._canvas, "has_relevant", has_relevant)
            set_state(self._canvas, "relevant_count", relevant_count)
        except Exception as e:
            logging.warning(f"[Grader] Failed to write shared state: {e}")

    def _record_metrics(self, graded: list[dict], start_ts: float, status: str) -> None:
        """Record Grader evaluation metrics and structured log."""
        try:
            duration_ms = (time.perf_counter() - start_ts) * 1000.0
            has_relevant = any(d.get("relevance") == RELEVANT for d in graded)
            relevant_count = sum(1 for d in graded if d.get("relevance") == RELEVANT)
            fallback_reason = ""
            if graded and any(d.get("fallback_reason") for d in graded):
                fallback_reason = next(
                    (d.get("fallback_reason") for d in graded if d.get("fallback_reason")), ""
                )
            kb_id = "unknown"
            if graded and isinstance(graded[0], dict):
                kb_id = str(graded[0].get("kb_id") or "unknown")

            metrics.record_grader_hit(kb_id, has_relevant)
            log_grader(
                trace_id=getattr(self._canvas, "task_id", ""),
                span_id=self._id,
                duration_ms=duration_ms,
                status=status,
                fallback_reason=fallback_reason,
                relevant_count=relevant_count,
                total_count=len(graded),
                metadata={"evaluator_model": self._param.evaluator_model, "kb_id": kb_id},
            )
        except Exception as e:
            logging.warning(f"[Grader] Failed to record metrics: {e}")

    # ------------------------------------------------------------------
    # LLM evaluator
    # ------------------------------------------------------------------
    async def _evaluate_with_llm(self, query: str, docs: list[dict]) -> list[dict]:
        """使用 LLM 评估文档相关性。

        评估流程：
          1. 创建 LLM Bundle（根据配置的 llm_id）
          2. 调用 _llm_evaluate 进行批量评估
          3. 捕获各类异常并触发对应的降级策略：
             - TimeoutError -> Rerank 降级
             - QuotaExceeded -> 切换备用模型
             - ParseError -> Rerank 降级
             - ServiceUnavailable -> Rerank 降级
        """
        chat_mdl = self._create_llm_bundle(self._param.llm_id)
        try:
            return await self._llm_evaluate(query, docs, chat_mdl, mode="llm", graded_by="llm")
        except asyncio.TimeoutError:
            return self._handle_failure(docs, "llm_timeout")
        except LLMQuotaExceededError:
            return await self._try_backup_llm(query, docs)
        except LLMResponseParseError:
            return self._handle_failure(docs, "parse_error")
        except LLMServiceUnavailableError:
            return self._handle_failure(docs, "service_unavailable")
        except Exception as e:
            logging.warning(f"[Grader] LLM evaluation failed: {e}")
            return self._handle_failure(docs, "llm_failure")

    async def _try_backup_llm(self, query: str, docs: list[dict]) -> list[dict]:
        if not self._param.backup_llm_model:
            return self._handle_failure(docs, "llm_quota_exceeded")
        try:
            backup_mdl = self._create_llm_bundle(self._param.backup_llm_model)
            return await self._llm_evaluate(query, docs, backup_mdl, mode="llm", graded_by="backup_llm")
        except asyncio.TimeoutError:
            return self._handle_failure(docs, "llm_timeout")
        except LLMQuotaExceededError:
            return self._handle_failure(docs, "llm_quota_exceeded")
        except LLMResponseParseError:
            return self._handle_failure(docs, "parse_error")
        except LLMServiceUnavailableError:
            return self._handle_failure(docs, "service_unavailable")
        except Exception as e:
            logging.warning(f"[Grader] Backup LLM evaluation failed: {e}")
            return self._handle_failure(docs, "llm_failure")

    async def _evaluate_with_local_nli(self, query: str, docs: list[dict]) -> list[dict]:
        chat_mdl = self._create_llm_bundle(self._param.llm_id)
        try:
            return await self._llm_evaluate(query, docs, chat_mdl, mode="local_nli", graded_by="local_nli")
        except asyncio.TimeoutError:
            return self._handle_failure(docs, "llm_timeout")
        except LLMQuotaExceededError:
            return await self._try_backup_llm(query, docs)
        except LLMResponseParseError:
            return self._handle_failure(docs, "parse_error")
        except LLMServiceUnavailableError:
            return self._handle_failure(docs, "service_unavailable")
        except Exception as e:
            logging.warning(f"[Grader] Local NLI evaluation failed: {e}")
            return self._handle_failure(docs, "llm_failure")

    def _create_llm_bundle(self, model_id: str):
        from api.db.joint_services.tenant_model_service import get_model_config_by_type_and_name
        from api.db.services.llm_service import LLMBundle

        config = get_model_config_by_type_and_name(self._canvas.get_tenant_id(), LLMType.CHAT, model_id)
        return LLMBundle(self._canvas.get_tenant_id(), config)

    async def _llm_evaluate(
        self,
        query: str,
        docs: list[dict],
        chat_mdl,
        mode: str = "llm",
        graded_by: str = "llm",
    ) -> list[dict]:
        """LLM 批量评估核心逻辑。

        流程：
          1. 构建评估项（文档按 token 限制拆分为 chunks）
          2. 将评估项按 batch_size 分批
          3. 对每批执行：调用 LLM -> 解析 JSON 响应 -> 失败则重试
          4. 聚合所有批次结果，同一文档取最高分
          5. 合并为最终评估结果
        """
        items = self._build_evaluation_items(docs)
        batches = self._split_items_into_batches(items)
        parsed_results: list[dict] = []

        for batch in batches:
            for attempt in range(self._param.max_retry_on_parse_error + 1):
                try:
                    ans = await asyncio.wait_for(
                        self._call_llm_batch(query, batch, chat_mdl, mode=mode),
                        timeout=self._param.timeout_seconds,
                    )
                    parsed = self._parse_llm_response(ans, batch)
                    parsed_results.extend(parsed)
                    break
                except asyncio.TimeoutError:
                    raise
                except LLMResponseParseError:
                    if attempt >= self._param.max_retry_on_parse_error:
                        raise
                    logging.info(f"[Grader] Parse error, retrying batch ({attempt + 1})")
                    continue
                except GraderError:
                    raise
                except Exception as e:
                    raise self._classify_llm_error(str(e))
            else:
                raise LLMResponseParseError("Failed to parse LLM response after retries")

        return self._aggregate_chunk_results(docs, items, parsed_results, graded_by=graded_by)

    def _build_evaluation_items(self, docs: list[dict]) -> list[dict]:
        items = []
        max_doc_tokens = max(1, self._param.max_eval_tokens // max(1, self._param.batch_size))
        for doc in docs:
            text = self._doc_text_for_eval(doc)
            chunks = self._split_semantic_paragraphs(text, max_doc_tokens)
            for chunk in chunks:
                items.append({"doc_index": doc["index"], "text": chunk})
        return items

    def _split_items_into_batches(self, items: list[dict]) -> list[list[dict]]:
        batch_size = min(self._param.batch_size, self._param.max_batch_size)
        query_tokens = num_tokens_from_string(self._resolve_query({}) or "")
        prompt_tokens = num_tokens_from_string(self._param.prompt or DEFAULT_GRADER_PROMPT)
        available = self._param.max_eval_tokens - query_tokens - prompt_tokens
        if available <= 0:
            available = self._param.max_eval_tokens // 2

        batches = []
        current = []
        current_tokens = 0
        for item in items:
            item_tokens = num_tokens_from_string(item["text"])
            if current and (len(current) >= batch_size or current_tokens + item_tokens > available):
                batches.append(current)
                current = []
                current_tokens = 0
            current.append(item)
            current_tokens += item_tokens
        if current:
            batches.append(current)
        return batches if batches else [[]]

    def _split_semantic_paragraphs(self, text: str, max_tokens: int) -> list[str]:
        """将长文本按语义段落拆分为不超过 max_tokens 的 chunks。

        拆分策略（按优先级）：
          1. 如果整段文本不超过限制，直接返回
          2. 按双换行（段落边界）拆分
          3. 超长段落按句子边界（句号、问号、感叹号）进一步拆分
          4. 单个句子仍超长时，按 token 比例截断

        这确保了评估时不会丢失文档的关键语义信息。
        """
        if num_tokens_from_string(text) <= max_tokens:
            return [text]

        paragraphs = re.split(r"\n\s*\n", text)
        chunks = []
        current = ""
        for p in paragraphs:
            p = p.strip()
            if not p:
                continue
            if num_tokens_from_string(p) > max_tokens:
                sentences = re.split(r"(?<=[。！？.!?])\s+", p)
                for s in sentences:
                    s = s.strip()
                    if not s:
                        continue
                    if num_tokens_from_string(current + s) > max_tokens:
                        if current:
                            chunks.append(current.strip())
                            current = ""
                        if num_tokens_from_string(s) > max_tokens:
                            ratio = max_tokens / max(1, num_tokens_from_string(s))
                            s = s[: int(len(s) * ratio)]
                        current = s
                    else:
                        current = current + " " + s if current else s
            else:
                combined = current + "\n\n" + p if current else p
                if num_tokens_from_string(combined) > max_tokens:
                    if current:
                        chunks.append(current.strip())
                        current = ""
                    current = p
                else:
                    current = combined
        if current:
            chunks.append(current.strip())
        return chunks if chunks else [text[: max_tokens * 4]]

    async def _call_llm_batch(
        self,
        query: str,
        batch: list[dict],
        chat_mdl,
        mode: str = "llm",
    ) -> str:
        try:
            prompt = self._build_prompt(query, batch, mode=mode)
            ans = await chat_mdl.async_chat(prompt, [], {})
        except asyncio.TimeoutError:
            raise
        except Exception as e:
            raise self._classify_llm_error(str(e))
        if ans and "**ERROR**" in ans:
            raise self._classify_llm_error(ans)
        return ans

    def _build_prompt(self, query: str, batch: list[dict], mode: str = "llm") -> str:
        docs_text = "\n\n".join(f"[{i}] {item['text']}" for i, item in enumerate(batch))
        if mode == "local_nli":
            template = self._param.nli_prompt or DEFAULT_NLI_PROMPT
        else:
            template = self._param.prompt or DEFAULT_GRADER_PROMPT
        return self.string_format(template, {"query": query, "documents": docs_text})

    def _classify_llm_error(self, error_text: str) -> GraderError:
        if not error_text:
            return GraderError("Unknown LLM error")
        low = error_text.lower()
        if any(k in low for k in ["timeout", "timed out", "time out"]):
            return asyncio.TimeoutError()
        if any(k in low for k in ["quota", "rate limit", "insufficient quota", "billing", "limit exceeded", "too many requests"]):
            return LLMQuotaExceededError(error_text)
        if any(k in low for k in ["unavailable", "service unavailable", "503", "connection refused", "connection error"]):
            return LLMServiceUnavailableError(error_text)
        return GraderError(error_text)

    def _parse_llm_response(self, ans: str, batch: list[dict]) -> list[dict]:
        ans = self._clean_llm_output(ans)
        try:
            data = json_repair.loads(ans)
        except Exception as exc:
            raise LLMResponseParseError(f"Invalid JSON: {exc}")
        if not isinstance(data, list):
            raise LLMResponseParseError("Response is not a JSON list")

        parsed = []
        for item in data:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            rel = item.get("relevance")
            score = item.get("score")
            if idx is None or rel is None or score is None:
                continue
            try:
                batch_idx = int(idx)
                score_val = float(score)
            except (ValueError, TypeError):
                continue
            if batch_idx < 0 or batch_idx >= len(batch):
                continue
            parsed.append(
                {
                    "doc_index": batch[batch_idx]["doc_index"],
                    "relevance": self._normalize_relevance(rel),
                    "score": score_val,
                    "reason": str(item.get("reason", "")),
                }
            )
        if len(parsed) != len(batch):
            raise LLMResponseParseError(f"Incomplete results: expected {len(batch)}, got {len(parsed)}")
        return parsed

    def _clean_llm_output(self, ans: str) -> str:
        if not isinstance(ans, str):
            ans = str(ans)
        ans = re.sub(r"<think>.*?</think>", "", ans, flags=re.DOTALL)
        ans = re.sub(r"^.*?```json", "", ans, flags=re.DOTALL)
        ans = re.sub(r"```\s*$", "", ans, flags=re.DOTALL)
        return ans.strip()

    def _normalize_relevance(self, value: Any) -> str:
        if not isinstance(value, str):
            value = str(value)
        low = value.lower().replace(" ", "_").replace("-", "_")
        if low in ("relevant", "entailment", "entailed", "yes", "true"):
            return RELEVANT
        return NOT_RELEVANT

    def _aggregate_chunk_results(
        self,
        docs: list[dict],
        items: list[dict],
        parsed_results: list[dict],
        graded_by: str,
    ) -> list[dict]:
        # Map parsed result position to the item it corresponds to.
        doc_scores: Dict[int, dict] = {}
        for i, res in enumerate(parsed_results):
            if i >= len(items):
                break
            doc_idx = items[i]["doc_index"]
            current = doc_scores.get(doc_idx, {"score": 0.0, "relevance": NOT_RELEVANT, "reason": ""})
            if res["score"] > current["score"]:
                current = {
                    "score": res["score"],
                    "relevance": res["relevance"],
                    "reason": res.get("reason", ""),
                }
            doc_scores[doc_idx] = current

        results = []
        for doc in docs:
            info = doc_scores.get(
                doc["index"],
                {"score": 0.0, "relevance": NOT_RELEVANT, "reason": "No evaluation result"},
            )
            results.append(
                {
                    "index": doc["index"],
                    "relevance": info["relevance"],
                    "score": info["score"],
                    "reason": info["reason"],
                }
            )
        return self._merge_graded_docs(docs, results, graded_by=graded_by)

    # ------------------------------------------------------------------
    # Fallback helpers
    # ------------------------------------------------------------------
    def _handle_failure(self, docs: list[dict], reason: str) -> list[dict]:
        """评估失败时的降级处理。

        降级策略：
          - fallback_on_failure=True: 使用文档的 Rerank 分数作为评估结果
            （relevance 由 rerank_score 与 relevance_threshold 比较决定）
          - fallback_on_failure=False: 将所有文档标记为相关（保守策略，
            确保工作流不因评估失败而中断）

        每个降级结果都包含 graded_by 和 fallback_reason 字段，
        用于可观测性追踪。
        """
        logging.warning(f"[Grader] Evaluation failed, reason={reason}, fallback_on_failure={self._param.fallback_on_failure}")
        if self._param.fallback_on_failure:
            return self._fallback_by_rerank(docs, reason)
        return self._mark_all_relevant(docs, reason)

    def _fallback_by_rerank(self, docs: list[dict], reason: str) -> list[dict]:
        graded = []
        for doc in docs:
            score = float(doc.get("rerank_score") or doc.get("similarity") or 0.5)
            graded.append(
                {
                    "content": doc.get("content", ""),
                    "relevance": RELEVANT if score >= self._param.relevance_threshold else NOT_RELEVANT,
                    "score": score,
                    "reason": f"Rerank fallback due to {reason}",
                    "graded_by": "rerank_fallback",
                    "fallback_reason": reason,
                }
            )
        return graded

    def _mark_all_relevant(self, docs: list[dict], reason: str) -> list[dict]:
        graded = []
        for doc in docs:
            score = float(doc.get("rerank_score") or doc.get("similarity") or 0.5)
            graded.append(
                {
                    "content": doc.get("content", ""),
                    "relevance": RELEVANT,
                    "score": score,
                    "reason": "All LLM evaluators failed; marking relevant to continue flow",
                    "graded_by": "llm_all_failed",
                    "fallback_reason": reason,
                }
            )
        return graded

    # ------------------------------------------------------------------
    # Common helpers
    # ------------------------------------------------------------------
    def _doc_text_for_eval(self, doc: dict) -> str:
        text = doc.get("content") or doc.get("content_with_weight") or ""
        if not text and doc.get("caption"):
            text = doc["caption"]
        if not text:
            text = str(doc)
        return text.strip()

    def _merge_graded_docs(
        self,
        docs: list[dict],
        results: list[dict],
        graded_by: str,
        fallback_reason: Optional[str] = None,
    ) -> list[dict]:
        graded = []
        for doc in docs:
            res = next((r for r in results if r["index"] == doc["index"]), None)
            if res:
                graded.append(
                    {
                        "content": doc.get("content", ""),
                        "relevance": res["relevance"],
                        "score": float(res["score"]),
                        "reason": res.get("reason", ""),
                        "graded_by": graded_by,
                        "fallback_reason": fallback_reason,
                    }
                )
            else:
                score = float(doc.get("rerank_score") or doc.get("similarity") or 0.5)
                graded.append(
                    {
                        "content": doc.get("content", ""),
                        "relevance": RELEVANT if score >= self._param.relevance_threshold else NOT_RELEVANT,
                        "score": score,
                        "reason": "Missing evaluation result",
                        "graded_by": graded_by,
                        "fallback_reason": fallback_reason or "missing_result",
                    }
                )
        return graded

    def thoughts(self) -> str:
        return "Evaluating the relevance of retrieved documents to the user query."
