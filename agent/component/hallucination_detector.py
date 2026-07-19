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
"""幻觉检测组件。

对应需求：P2-FR-04（幻觉检测与忠实度验证）

功能说明：
  验证生成的答案是否被检索文档支持，通过多层架构检测幻觉内容，
  并根据忠实度分数执行分级处置。

实现方式：
  1. 论断拆解（_decompose_claims）：
     - 按句子边界拆分答案
     - 拆分复合句（中文连词：虽然/但是/因为/所以等）
     - 拆分列表项（编号/项目符号列表）
     - 合并数值类论断与上下文（如 "2024年Q3" + "营收增长15%"）
     - 去重并限制单条论断长度（默认 200 字符）
  2. 多层验证（_verify_claims）：
     - 规则层（rule）：调用 fact_checker 精确校验数值/日期/专有名词
       权重 0.4，速度快、零成本
     - NLI 层：使用 LLM 进行自然语言推理判断（entailment/neutral/contradiction）
       权重 0.4
     - LLM 层：使用 LLM 进行语义支持度判断（SUPPORTED/NOT_SUPPORTED/CONTRADICTED）
       权重 0.2
     - 规则层矛盾（CONTRADICTED）为强信号，直接跳过 NLI/LLM 层
     - 规则层支持且有精确实体时，跳过 LLM 层以节省成本
  3. 加权投票（_claim_score）：
     score = rule_weight × rule_score + nli_weight × nli_score + llm_weight × llm_score
  4. 分级处置（_dispose）：
     - ≥ 0.85（pass_threshold）：直接通过
     - 0.6 ~ 0.85（filter_threshold）：过滤不支持的论断，返回保守答案
     - 0.3 ~ 0.6（regenerate_threshold）：准备高置信度文档用于重新生成
     - < 0.3：严重幻觉，直接拒答
  5. 可观测性：记录忠实度分数、幻觉数量、处置动作等指标和结构化日志。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from abc import ABC
from dataclasses import dataclass, field
from typing import Any, Literal

import json_repair

from agent.component.base import ComponentBase, ComponentParamBase
from agent.component.state_fields import set_state
from api.utils import metrics
from api.utils.structured_logger import log_hallucination_detection
from api.utils.fact_checker import rule_check_fact
from common.connection_utils import timeout

RuleResult = Literal["SUPPORTED", "NOT_SUPPORTED", "CONTRADICTED"]
NLIResult = Literal["entailment", "neutral", "contradiction"]

_RULE_SCORES = {
    "SUPPORTED": 1.0,
    "NOT_SUPPORTED": 0.0,
    "CONTRADICTED": 0.0,
}

_NLI_SCORES = {
    "entailment": 1.0,
    "neutral": 0.5,
    "contradiction": 0.0,
}

_LLM_SCORES = {
    "SUPPORTED": 1.0,
    "NOT_SUPPORTED": 0.3,
    "CONTRADICTED": 0.0,
}


@dataclass
class Claim:
    """单条论断及其验证结果。

    每条论断经过规则层、NLI 层、LLM 层三重验证，
    最终通过加权投票计算综合分数 score。
    """

    text: str
    rule_result: RuleResult = "SUPPORTED"
    nli_result: NLIResult = "entailment"
    llm_result: RuleResult = "SUPPORTED"
    evidence: str = ""
    rule_entities: list[dict[str, Any]] = field(default_factory=list)
    score: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "supported": self.score >= 0.85,
            "score": round(self.score, 4),
            "rule_result": self.rule_result,
            "nli_result": self.nli_result,
            "llm_result": self.llm_result,
            "evidence": self.evidence,
            "rule_entities": self.rule_entities,
        }


class HallucinationDetectorParam(ComponentParamBase):
    """幻觉检测组件参数定义。

    关键参数说明：
      - use_llm/use_nli: 是否启用 LLM/NLI 验证层
      - skip_llm_when_rule_supported: 规则层已支持时跳过 LLM（节省成本）
      - rule_weight/nli_weight/llm_weight: 三层验证的权重（总和必须为 1.0）
      - pass_threshold: 通过阈值（≥ 此值直接返回原答案）
      - filter_threshold: 过滤阈值（≥ 此值返回保守答案）
      - regenerate_threshold: 重生成阈值（≥ 此值尝试重新生成）
      - enable_regeneration: 是否允许重新生成（否则直接拒答）
    """

    def __init__(self):
        super().__init__()
        # Input field references (Canvas variable syntax)
        self.answer = "{sys.answer_with_citations}"
        self.retrieved_docs = "{sys.retrieved_docs}"
        self.query = "{sys.query}"

        # Behaviour switches
        self.use_llm = True
        self.use_nli = True
        self.skip_llm_when_rule_supported = True
        self.enable_regeneration = True
        self.max_claim_length = 200

        # Scoring weights
        self.rule_weight = 0.4
        self.nli_weight = 0.4
        self.llm_weight = 0.2

        # Thresholds
        self.pass_threshold = 0.85
        self.filter_threshold = 0.6
        self.regenerate_threshold = 0.3

        # LLM / NLI config
        self.llm_id = ""
        self.temperature = 0.1
        self.max_tokens = 1024
        self.timeout_seconds = 30

        self.outputs = {
            "is_hallucination": {"value": False, "type": "boolean"},
            "faithfulness_score": {"value": 0.0, "type": "float"},
            "claims": {"value": [], "type": "list"},
            "hallucination_count": {"value": 0, "type": "int"},
            "final_answer": {"value": "", "type": "string"},
            "action": {"value": "pass", "type": "string"},
            "regenerate_context": {"value": [], "type": "list"},
            "regenerate_prompt": {"value": "", "type": "string"},
        }

    def check(self):
        self.check_decimal_float(float(self.rule_weight), "[HallucinationDetector] Rule weight")
        self.check_decimal_float(float(self.nli_weight), "[HallucinationDetector] NLI weight")
        self.check_decimal_float(float(self.llm_weight), "[HallucinationDetector] LLM weight")
        total = float(self.rule_weight) + float(self.nli_weight) + float(self.llm_weight)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"[HallucinationDetector] Weights must sum to 1.0, got {total}")
        self.check_decimal_float(float(self.pass_threshold), "[HallucinationDetector] Pass threshold")
        self.check_decimal_float(float(self.filter_threshold), "[HallucinationDetector] Filter threshold")
        self.check_decimal_float(float(self.regenerate_threshold), "[HallucinationDetector] Regenerate threshold")
        self.check_positive_number(int(self.max_claim_length), "[HallucinationDetector] Max claim length")
        self.check_nonnegative_number(int(self.timeout_seconds), "[HallucinationDetector] Timeout seconds")

    def get_input_form(self) -> dict[str, dict]:
        return {
            "answer": {"name": "Answer", "type": "text"},
            "retrieved_docs": {"name": "Retrieved Documents", "type": "text"},
            "query": {"name": "Query", "type": "line"},
        }


class HallucinationDetector(ComponentBase, ABC):
    """Detect hallucinations in a generated answer against retrieved docs."""

    component_name = "HallucinationDetector"

    def get_input_elements(self) -> dict[str, Any]:
        res = {}
        for ref in [self._param.answer, self._param.retrieved_docs, self._param.query]:
            if ref:
                res.update(self.get_input_elements_from_text(ref))
        return res

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 10 * 60)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("HallucinationDetector processing"):
            return

        start_ts = time.perf_counter()
        # Resolve inputs from Canvas globals using the configured variable refs.
        answer = self._resolve_input(self._param.answer)
        retrieved_docs = self._coerce_docs(self._resolve_input(self._param.retrieved_docs))
        query = self._resolve_input(self._param.query)

        self.set_input_value("answer", answer)
        self.set_input_value("retrieved_docs", retrieved_docs)
        self.set_input_value("query", query)

        claims = self._decompose_claims(answer)
        if not claims:
            # No verifiable claims; treat as pass.
            self._set_pass_outputs(answer, query, [])
            self._record_metrics(1.0, 0, "pass", start_ts, "success")
            return

        self._verify_claims(claims, retrieved_docs)
        faithfulness_score, hallucination_count = self._aggregate_scores(claims)
        action, final_answer, regenerate_context, regenerate_prompt = self._dispose(answer, query, claims, faithfulness_score, retrieved_docs)

        is_hallucination = faithfulness_score < self._param.pass_threshold

        self.set_output("is_hallucination", is_hallucination)
        self.set_output("faithfulness_score", round(faithfulness_score, 4))
        self.set_output("claims", [c.to_dict() for c in claims])
        self.set_output("hallucination_count", hallucination_count)
        self.set_output("final_answer", final_answer)
        self.set_output("action", action)
        self.set_output("regenerate_context", regenerate_context)
        self.set_output("regenerate_prompt", regenerate_prompt)

        # Persist canonical state fields.
        set_state(self._canvas, "is_hallucination", is_hallucination)
        set_state(self._canvas, "faithfulness_score", faithfulness_score)
        self._record_metrics(faithfulness_score, hallucination_count, action, start_ts, "success")

    # ------------------------------------------------------------------
    # Input coercion
    # ------------------------------------------------------------------

    def _resolve_input(self, ref: str) -> Any:
        """Return the Canvas value for a parameter reference.

        If the reference is a literal (not a Canvas variable), return it as-is.
        """
        if not ref:
            return ""
        if isinstance(ref, str) and self._canvas.is_reff(ref):
            return self._canvas.get_variable_value(ref)
        return ref

    @staticmethod
    def _coerce_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)

    @staticmethod
    def _coerce_docs(value: Any) -> list[dict[str, Any]]:
        if not value:
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return HallucinationDetector._normalize_docs(parsed)
            except Exception:
                return [{"content": value.strip()}]
        if isinstance(value, list):
            return HallucinationDetector._normalize_docs(value)
        if isinstance(value, dict):
            return [value]
        return []

    @staticmethod
    def _normalize_docs(docs: list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for doc in docs:
            if isinstance(doc, str):
                normalized.append({"content": doc})
            elif isinstance(doc, dict):
                normalized.append(doc)
            else:
                normalized.append({"content": str(doc)})
        return normalized

    # ------------------------------------------------------------------
    # Claim decomposition
    # ------------------------------------------------------------------

    def _decompose_claims(self, answer: str) -> list[Claim]:
        """将答案拆解为独立的待验证论断。

        拆解流程：
          1. 按句子边界拆分（句号/问号/感叹号）
          2. 拆分复合句（中文连词分隔）
          3. 拆分列表项（编号/项目符号）
          4. 合并数值类论断与上下文（避免 "2024年Q3" 被拆为孤立片段）
          5. 去重并限制长度
        """
        if not answer:
            return []

        # Step 1: sentence-level split.
        sentence_chunks = self._split_sentences(answer)

        # Step 2 & 3: split compound sentences and list items.
        raw_claims: list[str] = []
        for chunk in sentence_chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            for compound in self._split_compound_sentences(chunk):
                compound = compound.strip()
                if not compound:
                    continue
                items = self._split_list_items(compound)
                raw_claims.extend([i.strip() for i in items if i.strip()])

        # Step 4: merge numerical claims with adjacent context.
        raw_claims = self._merge_numerical_claims(raw_claims)

        # Deduplicate and bound length.
        seen: set[str] = set()
        claims: list[Claim] = []
        for text in raw_claims:
            text = self._clean_claim_text(text)
            if not text or len(text) < 3:
                continue
            if len(text) > self._param.max_claim_length:
                text = text[: self._param.max_claim_length]
            if text in seen:
                continue
            seen.add(text)
            claims.append(Claim(text=text))
        return claims

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        # Preserve sentence-ending punctuation, even when no whitespace follows.
        return [s.strip() for s in re.split(r"(?<=[。！？.?!])\s*", text) if s.strip()]

    # Common Chinese conjunction words used to join two clauses.
    _CONJUNCTION_DELIMITERS = [
        "虽然",
        "尽管",
        "即使",
        "因为",
        "由于",
        "如果",
        "与其",
        "不但",
        "不仅",
        "要么",
        "但是",
        "但",
        "因此",
        "因而",
        "所以",
        "那么",
        "就",
        "则",
        "也",
        "还",
        "而且",
        "不如",
    ]

    @classmethod
    def _split_compound_sentences(cls, text: str) -> list[str]:
        """Split common Chinese/English compound conjunctions into clauses."""
        pattern = re.compile(r"\s*(?:" + "|".join(re.escape(d) for d in cls._CONJUNCTION_DELIMITERS) + r")\s*[，,]?\s*")
        parts = [p.strip() for p in pattern.split(text) if p.strip()]
        return parts if parts else [text]

    @staticmethod
    def _split_list_items(text: str) -> list[str]:
        """Split bullet/numbered lists into separate claims."""
        if not text:
            return []
        # Chinese numbered list: 一、 二、 ... or (1) (2) ... or 1. 2. ... or - *
        list_pattern = re.compile(
            r"(?:\n|\r|^)\s*"
            r"(?:"
            r"[\(（]?\d+[\.、\)）]\s+|"
            r"[一二三四五六七八九十]+[\.、\)）]\s+|"
            r"[-*•]\s+"
            r")"
        )
        items = [item.strip() for item in list_pattern.split(text) if item.strip()]
        return items if items else [text]

    _DATE_FRAGMENT_RE = re.compile(r"\d{4}年|第[一二三四]季度|Q[1-4]|第\d季度|\d{1,2}月")

    @classmethod
    def _merge_numerical_claims(cls, claims: list[str]) -> list[str]:
        """Merge a bare numerical/date fragment with the previous contextual claim."""
        if not claims:
            return claims

        def _is_date_fragment(text: str) -> bool:
            return bool(cls._DATE_FRAGMENT_RE.search(text))

        merged: list[str] = [claims[0]]
        number_like = re.compile(r"\d")
        for current in claims[1:]:
            prev = merged[-1]
            current_has_number = bool(number_like.search(current))
            prev_has_number = bool(number_like.search(prev))
            current_is_date = _is_date_fragment(current)
            prev_is_date = _is_date_fragment(prev)
            # Merge if current is short and numeric while previous provides context,
            # or vice versa, and neither already fully contains the other.
            # Date fragments (e.g. "2024年Q3") are merged with adjacent numeric claims
            # even when both contain digits.
            if len(current) <= 30 and current_has_number and current not in prev and (not prev_has_number or prev_is_date or current_is_date):
                merged[-1] = f"{prev}{current}"
            elif len(prev) <= 30 and prev_has_number and prev not in current and (not current_has_number or prev_is_date or current_is_date):
                merged[-1] = f"{prev}{current}"
            else:
                merged.append(current)
        return merged

    @staticmethod
    def _clean_claim_text(text: str) -> str:
        # Remove list markers only, not dates/years at the start of a claim.
        text = re.sub(
            r"^\s*(?:[\(（]?\d+[\.、\)）]\s+|[-*•]\s+|[一二三四五六七八九十]+[\.、\)）]\s+)",
            "",
            text,
        )
        text = re.sub(r"\s+", " ", text)
        return text.strip(" \t\n\r。；，")

    # ------------------------------------------------------------------
    # Verification layers
    # ------------------------------------------------------------------

    def _verify_claims(self, claims: list[Claim], retrieved_docs: list[dict[str, Any]]) -> None:
        """对每条论断执行多层验证。

        验证流程（按优先级）：
          1. 规则层：始终执行，精确校验数值/日期/专有名词
          2. 规则矛盾 -> 直接标记为 CONTRADICTED，跳过后续层
          3. 规则支持且有精确实体 -> 跳过 LLM（可配置）
          4. 其他情况 -> 调用 LLM 进行 NLI + 语义验证
          5. LLM 调用失败 -> 根据规则结果保守降级
        """
        context = self._build_context(retrieved_docs)

        for claim in claims:
            rule_result = rule_check_fact(claim.text, retrieved_docs)
            claim.rule_result = rule_result.get("rule_result", "SUPPORTED")
            claim.evidence = rule_result.get("matched_evidence") or ""
            claim.rule_entities = rule_result.get("extracted_entities", [])

            # Rule contradiction is a strong signal; no need to call LLM.
            if claim.rule_result == "CONTRADICTED":
                claim.nli_result = "contradiction"
                claim.llm_result = "CONTRADICTED"
                claim.score = 0.0
                continue

            if claim.rule_result == "NOT_SUPPORTED":
                # Fall through to NLI/LLM for semantic rescue.
                pass

            # Rule-supported claims with precise entities can skip expensive LLM calls.
            if claim.rule_result == "SUPPORTED" and self._param.skip_llm_when_rule_supported and claim.rule_entities:
                claim.nli_result = "entailment"
                claim.llm_result = "SUPPORTED"
                claim.score = 1.0
                continue

            # Use NLI/LLM only when configured and useful.
            if self._param.use_llm or self._param.use_nli:
                try:
                    self._llm_verify_claim(claim, context, retrieved_docs)
                except Exception as e:
                    logging.warning("[HallucinationDetector] LLM verification failed: %s", e)
                    # Conservative fallback.
                    if claim.rule_result == "NOT_SUPPORTED":
                        claim.nli_result = "neutral"
                        claim.llm_result = "NOT_SUPPORTED"
                    else:
                        claim.nli_result = "entailment"
                        claim.llm_result = "SUPPORTED"

            claim.score = self._claim_score(claim)

    def _build_context(self, retrieved_docs: list[dict[str, Any]]) -> str:
        """将检索文档拼接为 LLM 验证所需的上下文文本。

        格式：每个文档以 "[Document N]" 为标题，最多取前 10 个文档。
        """
        parts: list[str] = []
        for i, doc in enumerate(retrieved_docs[:10], start=1):
            content = doc.get("content") or ""
            if content:
                parts.append(f"[Document {i}]\n{content.strip()}")
        return "\n\n".join(parts)

    def _claim_score(self, claim: Claim) -> float:
        """对单条论断执行加权投票，计算综合忠实度分数。

        计算公式：
          score = rule_weight × rule_score + nli_weight × nli_score + llm_weight × llm_score
        各层分数映射：
          - 规则层：SUPPORTED=1.0, NOT_SUPPORTED=0.0, CONTRADICTED=0.0
          - NLI 层：entailment=1.0, neutral=0.5, contradiction=0.0
          - LLM 层：SUPPORTED=1.0, NOT_SUPPORTED=0.3, CONTRADICTED=0.0
        """
        rule_score = _RULE_SCORES.get(claim.rule_result, 0.5)
        nli_score = _NLI_SCORES.get(claim.nli_result, 0.5)
        llm_score = _LLM_SCORES.get(claim.llm_result, 0.5)
        return float(self._param.rule_weight) * rule_score + float(self._param.nli_weight) * nli_score + float(self._param.llm_weight) * llm_score

    def _aggregate_scores(self, claims: list[Claim]) -> tuple[float, int]:
        """Return overall faithfulness score and hallucination count."""
        if not claims:
            return 1.0, 0

        has_contradiction = any(c.rule_result == "CONTRADICTED" for c in claims)
        total_weight = sum(max(1, len(c.text)) for c in claims)
        score = sum(c.score * max(1, len(c.text)) for c in claims) / total_weight

        if has_contradiction:
            score = min(score, 0.5)

        hallucination_count = sum(1 for c in claims if c.score < self._param.pass_threshold)
        return score, hallucination_count

    # ------------------------------------------------------------------
    # LLM-based verification
    # ------------------------------------------------------------------

    def _llm_verify_claim(self, claim: Claim, context: str, retrieved_docs: list[dict[str, Any]]) -> None:
        """Use LLM to produce NLI + semantic support judgment for a claim."""
        if not self._param.llm_id:
            # No LLM configured; keep rule-based result as fallback.
            claim.nli_result = self._rule_to_nli(claim.rule_result)
            return

        chat_model_config = self._get_model_config()
        if not chat_model_config:
            claim.nli_result = self._rule_to_nli(claim.rule_result)
            return

        from api.db.services.llm_service import LLMBundle

        chat_mdl = LLMBundle(self._canvas.get_tenant_id(), chat_model_config)

        prompt = self._build_verification_prompt(context, claim.text)
        messages = [{"role": "user", "content": prompt}]
        gen_conf = {"temperature": float(self._param.temperature), "max_tokens": int(self._param.max_tokens)}

        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                ans = asyncio.run_coroutine_threadsafe(chat_mdl.async_chat("", messages, gen_conf), loop).result(timeout=self._param.timeout_seconds)
            else:
                ans = loop.run_until_complete(chat_mdl.async_chat("", messages, gen_conf))
        except Exception as e:
            logging.warning("[HallucinationDetector] LLM call failed: %s", e)
            claim.nli_result = self._rule_to_nli(claim.rule_result)
            return

        parsed = self._parse_verification_answer(ans)
        claim.nli_result = parsed.get("nli", self._rule_to_nli(claim.rule_result))
        claim.llm_result = parsed.get("llm", claim.rule_result)
        if parsed.get("evidence"):
            claim.evidence = parsed["evidence"]

    def _get_model_config(self):
        from common.constants import LLMType
        from api.db.joint_services.tenant_model_service import get_model_config_by_type_and_name

        try:
            return get_model_config_by_type_and_name(self._canvas.get_tenant_id(), LLMType.CHAT, self._param.llm_id)
        except Exception as e:
            logging.warning("[HallucinationDetector] Failed to load LLM config: %s", e)
            return None

    @staticmethod
    def _rule_to_nli(rule_result: RuleResult) -> NLIResult:
        return {"SUPPORTED": "entailment", "NOT_SUPPORTED": "neutral", "CONTRADICTED": "contradiction"}.get(rule_result, "neutral")

    def _build_verification_prompt(self, context: str, claim_text: str) -> str:
        return (
            "You are a fact-consistency evaluator. Given the retrieved context and a claim, "
            "classify the claim in two ways:\n"
            "1. NLI: entailment / neutral / contradiction\n"
            "2. Semantic support: SUPPORTED / NOT_SUPPORTED / CONTRADICTED\n\n"
            "Use the context only. If the context does not contain enough information, "
            "use NOT_SUPPORTED / neutral. If the claim conflicts with the context, "
            "use CONTRADICTED / contradiction.\n\n"
            f"## Context\n{context}\n\n"
            f"## Claim\n{claim_text}\n\n"
            "Output strictly as JSON:\n"
            '{"nli": "entailment|neutral|contradiction", '
            '"llm": "SUPPORTED|NOT_SUPPORTED|CONTRADICTED", '
            '"evidence": "relevant document snippet or empty"}'
        )

    def _parse_verification_answer(self, answer: str) -> dict[str, Any]:
        if not answer:
            return {}
        # Strip markdown fences.
        text = re.sub(r"^.*```json", "", answer, flags=re.DOTALL)
        text = re.sub(r"```\s*$", "", text, flags=re.DOTALL)
        text = text.strip()
        try:
            parsed = json_repair.loads(text)
        except Exception:
            parsed = {}

        if not isinstance(parsed, dict):
            parsed = {}

        result: dict[str, Any] = {}
        nli_raw = str(parsed.get("nli", parsed.get("NLI", ""))).lower().strip()
        if nli_raw in {"entailment", "neutral", "contradiction"}:
            result["nli"] = nli_raw
        elif "contradict" in nli_raw:
            result["nli"] = "contradiction"
        elif "support" in nli_raw or "entail" in nli_raw:
            result["nli"] = "entailment"
        elif "neutral" in nli_raw:
            result["nli"] = "neutral"

        llm_raw = str(parsed.get("llm", parsed.get("semantic", parsed.get("result", "")))).upper().strip()
        if llm_raw in {"SUPPORTED", "NOT_SUPPORTED", "CONTRADICTED"}:
            result["llm"] = llm_raw
        elif "CONTRADICT" in llm_raw:
            result["llm"] = "CONTRADICTED"
        elif "NOT" in llm_raw or "UNSUPPORTED" in llm_raw:
            result["llm"] = "NOT_SUPPORTED"
        elif "SUPPORTED" in llm_raw or "SUPPORT" in llm_raw:
            result["llm"] = "SUPPORTED"

        result["evidence"] = str(parsed.get("evidence", "")).strip()
        return result

    # ------------------------------------------------------------------
    # Disposal logic
    # ------------------------------------------------------------------

    def _dispose(
        self,
        answer: str,
        query: str,
        claims: list[Claim],
        faithfulness_score: float,
        retrieved_docs: list[dict[str, Any]],
    ) -> tuple[str, str, list[dict[str, Any]], str]:
        """根据忠实度分数执行分级处置。

        四级处置策略：
          pass（≥ 0.85）：答案可信，直接返回
          filter（0.6 ~ 0.85）：仅保留已验证的论断，生成保守答案
          regenerate（0.3 ~ 0.6）：准备高置信度文档，尝试重新生成
          refuse（< 0.3）：严重幻觉，返回标准拒答话术
        """
        if faithfulness_score >= self._param.pass_threshold:
            return "pass", answer, [], ""

        if faithfulness_score >= self._param.filter_threshold:
            # 0.6 ~ 0.85: filter unsupported claims and return a conservative answer.
            supported_claims = [c for c in claims if c.score >= self._param.pass_threshold]
            final_answer = self._build_conservative_answer(supported_claims, intro_prefix=True)
            return "filter", final_answer, [], ""

        if faithfulness_score >= self._param.regenerate_threshold:
            # 0.3 ~ 0.6: prepare high-confidence docs for regeneration.
            high_confidence_docs = self._select_high_confidence_docs(retrieved_docs)
            regenerate_prompt = self._build_regenerate_prompt(query, high_confidence_docs)
            if self._param.enable_regeneration:
                return "regenerate", self._build_conservative_answer(claims, intro_prefix=True), high_confidence_docs, regenerate_prompt
            return "refuse", self._standard_refusal(), [], ""

        # < 0.3: serious hallucination, refuse.
        return "refuse", self._standard_refusal(), [], ""

    @staticmethod
    def _build_conservative_answer(supported_claims: list[Claim], intro_prefix: bool = False) -> str:
        if not supported_claims:
            return HallucinationDetector._standard_refusal()
        lines: list[str] = []
        if intro_prefix:
            lines.append("根据现有资料，已为您整理可确认的信息：")
        else:
            lines.append("根据现有资料，我可以确认以下信息：")
        for i, claim in enumerate(supported_claims, start=1):
            evidence = f"（引用：{claim.evidence}）" if claim.evidence else ""
            lines.append(f"- {claim.text}{evidence}")
        lines.append("\n对于其他未列出的部分，当前资料不足以给出可靠结论。")
        return "\n".join(lines)

    @staticmethod
    def _standard_refusal() -> str:
        return "抱歉，当前资料不足以对您的问题给出可靠回答。建议您补充更具体的关键词或联系相关人员。"

    def _select_high_confidence_docs(self, retrieved_docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return docs that are relevant and have high score."""
        docs: list[dict[str, Any]] = []
        for doc in retrieved_docs:
            score = doc.get("score") or doc.get("rerank_score") or 0.0
            relevance = doc.get("relevance", "")
            if isinstance(score, (int, float)) and score >= 0.8 and relevance in {"relevant", "", None}:
                docs.append(doc)
        # Fallback: if none qualify, keep top docs by score.
        if not docs:
            scored = sorted(
                [d for d in retrieved_docs if d.get("score") or d.get("rerank_score")],
                key=lambda d: d.get("score") or d.get("rerank_score") or 0.0,
                reverse=True,
            )
            docs = scored[:3]
        return docs

    def _build_regenerate_prompt(self, query: str, docs: list[dict[str, Any]]) -> str:
        context = self._build_context(docs)
        return (
            "You must answer the user question using ONLY the provided documents.\n"
            "Do not introduce any information not present in the documents.\n"
            "Cite sources when possible. Keep the answer concise and factual.\n\n"
            f"## User Question\n{query}\n\n"
            f"## Documents\n{context}\n\n"
            "## Answer"
        )

    def _set_pass_outputs(self, answer: str, query: str, claims: list[Claim]) -> None:
        self.set_output("is_hallucination", False)
        self.set_output("faithfulness_score", 1.0)
        self.set_output("claims", [c.to_dict() for c in claims])
        self.set_output("hallucination_count", 0)
        self.set_output("final_answer", answer)
        self.set_output("action", "pass")
        self.set_output("regenerate_context", [])
        self.set_output("regenerate_prompt", "")
        set_state(self._canvas, "is_hallucination", False)
        set_state(self._canvas, "faithfulness_score", 1.0)

    def _record_metrics(
        self,
        faithfulness_score: float,
        hallucination_count: int,
        action: str,
        start_ts: float,
        status: str,
    ) -> None:
        """Record hallucination detection metrics and structured log."""
        try:
            duration_ms = (time.perf_counter() - start_ts) * 1000.0
            kb_id = "unknown"
            if self._canvas and hasattr(self._canvas, "_extract_kb_id"):
                kb_id = self._canvas._extract_kb_id()
            metrics.record_faithfulness_score(kb_id, faithfulness_score)
            log_hallucination_detection(
                trace_id=getattr(self._canvas, "task_id", ""),
                span_id=self._id,
                duration_ms=duration_ms,
                status=status,
                faithfulness_score=faithfulness_score,
                hallucination_count=hallucination_count,
                action=action,
                metadata={"kb_id": kb_id},
            )
        except Exception as e:
            logging.warning("[HallucinationDetector] Failed to record metrics: %s", e)

    def thoughts(self) -> str:
        return "Checking whether the generated answer is faithful to the retrieved documents..."
