#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""PairVerifier：NLI + LLM 分层验证融合（v3.0，§5 设计实现）。

在现有 CitationBinder（纯 Rule 层）基础上，引入 NLI 层和 LLM 层语义验证，
采用 Batch 分组方式调用大模型，平衡成本与精度。

设计文档：
    docs/NLI-LLM分层验证融合设计.md

核心流程：
    verify_pairs(pairs)
        ├── 1. Rule 层：对所有 pair 执行本地正则验证（同步，0 成本）
        ├── 2. 短路过滤：Rule 矛盾 / Rule 支持+实体 的 pair 跳过 LLM
        ├── 3. 剩余 pair 按 BATCH_SIZE 分组（默认 5 个/组）
        ├── 4. 对每组调用一次 LLM
        └── 5. 解析响应，返回含三层结果的列表

与现有组件的关系：
    - 使用 rule_check_fact（api.utils.fact_checker）做规则层校验
    - 使用 LLMBundle（api.db.services.llm_service）做 LLM 调用
    - 输出格式兼容 PairVerdict 构造（agent.langgraph.evidence.claim）
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

try:
    import json_repair
except ImportError:
    json_repair = None

from agent.langgraph.evidence.claim import (
    PairStatus,
    PairVerdict,
    VerifierStatus,
)

logger = logging.getLogger(__name__)

# 默认配置常量
DEFAULT_BATCH_SIZE = 5
DEFAULT_TEMPERATURE = 0.1
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_RETRIES_PER_BATCH = 1

# 规则层结果类型
RuleResult = Literal["SUPPORTED", "NOT_SUPPORTED", "CONTRADICTED"]
NLIResult = Literal["entailment", "neutral", "contradiction"]
LLMResult = Literal["SUPPORTED", "NOT_SUPPORTED", "CONTRADICTED"]

# 层结果到 PairStatus 的映射
_RULE_TO_PAIR_STATUS: dict[str, str] = {
    "SUPPORTED": PairStatus.SUPPORTED.value,
    "NOT_SUPPORTED": PairStatus.INSUFFICIENT.value,
    "CONTRADICTED": PairStatus.CONTRADICTED.value,
}

_NLI_TO_PAIR_STATUS: dict[str, str] = {
    "entailment": PairStatus.SUPPORTED.value,
    "neutral": PairStatus.INSUFFICIENT.value,
    "contradiction": PairStatus.CONTRADICTED.value,
}

_LLM_TO_PAIR_STATUS: dict[str, str] = {
    "SUPPORTED": PairStatus.SUPPORTED.value,
    "NOT_SUPPORTED": PairStatus.INSUFFICIENT.value,
    "CONTRADICTED": PairStatus.CONTRADICTED.value,
}


@dataclass
class PairVerifierConfig:
    """PairVerifier 配置参数（§5 配置项）。

    Attributes:
        batch_size: 每批 pair 数，默认 5
        skip_llm_when_rule_contradicted: Rule 矛盾时跳过 LLM
        skip_llm_when_rule_supported_with_entities: Rule 支持+精确实体时跳过 LLM
        temperature: LLM 温度
        max_tokens: LLM 最大 token 数
        timeout_seconds: 单次 LLM 调用超时
        max_retries_per_batch: batch 失败重试次数
    """

    batch_size: int = DEFAULT_BATCH_SIZE
    skip_llm_when_rule_contradicted: bool = True
    skip_llm_when_rule_supported_with_entities: bool = True
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_retries_per_batch: int = DEFAULT_MAX_RETRIES_PER_BATCH


@dataclass
class PairInput:
    """单个 pair 输入（Claim × Evidence）。

    Attributes:
        claim_text: 声明文本
        evidence_content: 证据内容
        evidence_id: 证据 ID
    """

    claim_text: str
    evidence_content: str
    evidence_id: str


@dataclass
class PairResult:
    """单个 pair 的完整验证结果（三层结果）。

    Attributes:
        evidence_id: 证据 ID
        rule_result: Rule 层结果
        nli_result: NLI 层结果（未调用时保持 "unknown"）
        llm_result: LLM 层结果（未调用时保持 "unknown"）
        pair_status: 聚合后的 pair 状态
        verifier_status: 验证器状态
        matched_evidence: Rule 层匹配的证据文本
        extracted_entities: 提取的实体列表
        partial_failure: 是否为部分失败（LLM 解析失败退化为 Rule 层）
    """

    evidence_id: str
    rule_result: str = "unknown"
    nli_result: str = "unknown"
    llm_result: str = "unknown"
    pair_status: str = PairStatus.INSUFFICIENT.value
    verifier_status: str = VerifierStatus.OK.value
    matched_evidence: str = ""
    extracted_entities: list[dict[str, Any]] = field(default_factory=list)
    partial_failure: bool = False

    def to_pair_verdict(self) -> PairVerdict:
        """转换为 PairVerdict 用于 Claim 级聚合。

        Returns:
            PairVerdict 实例
        """
        return PairVerdict(
            evidence_id=self.evidence_id,
            pair_status=self.pair_status,
            verifier_status=self.verifier_status,
            rule_result=self.rule_result,
            nli_result=self.nli_result,
            llm_result=self.llm_result,
        )


class PairVerifier:
    """NLI + LLM 分层验证融合核心类（§3 PairVerifier 组件设计）。

    使用方式::

        verifier = PairVerifier(
            llm_id="gpt-4o",
            tenant_id="tenant_001",
            config=PairVerifierConfig(batch_size=5),
        )
        results = await verifier.verify_pairs(pairs)

    设计决策：
        - Rule 层始终同步执行（0 成本），短路逻辑在 Rule 层之后
        - Batch 调用一次 LLM 处理多个 pair，batch 内按 pair_index 回填
        - 部分失败（batch 内部分 pair 解析失败）不会影响其他 pair
        - 整批失败（超时/非 JSON）退化为 Rule 层，不重试
    """

    def __init__(
        self,
        llm_id: str,
        tenant_id: str,
        config: PairVerifierConfig | None = None,
    ) -> None:
        """初始化 PairVerifier。

        Args:
            llm_id: LLM 模型 ID（租户已配置的聊天模型）
            tenant_id: 租户 ID
            config: 配置参数，为 None 时使用默认值
        """
        self.llm_id = llm_id
        self.tenant_id = tenant_id
        self.config = config or PairVerifierConfig()

        # LLM 调用预算跟踪
        self._llm_calls_used = 0
        self._max_llm_calls = 50  # 可由外部设置覆盖

        # 缓存 LLM 实例（延迟加载）
        self._chat_mdl = None

        # 调用方注入的 LLM 调用函数（用于测试替换）
        self._llm_call_fn = None

    # ============================== 主入口 ==============================

    async def verify_pairs(self, pairs: list[PairInput]) -> list[PairResult]:
        """验证一组 Claim × Evidence pair（三步：Rule → 短路 → Batch LLM）。

        Args:
            pairs: 待验证的 pair 列表

        Returns:
            与输入 pairs 对应的 PairResult 列表（顺序一致）
        """
        if not pairs:
            return []

        # Step 1: Rule 层（同步，0 成本）
        # 构造 retrieved_docs 列表供 rule_check_fact 使用
        # 每个 pair 的 evidence 独立作为一条文档
        rule_results = [self._rule_check(pair) for pair in pairs]

        # Step 2: 初始化结果列表 + 短路过滤
        results: list[PairResult] = []
        llm_pairs: list[tuple[int, PairInput]] = []  # (original_index, pair)

        for idx, pair in enumerate(pairs):
            rule = rule_results[idx]
            rule_result = rule.get("rule_result", "SUPPORTED")
            entities = rule.get("extracted_entities", [])
            matched_evidence = rule.get("matched_evidence", "") or ""

            if self._should_skip_llm(rule_result, entities):
                # 短路：跳过 LLM，根据 Rule 层结果填充 NLI/LLM 层
                nli_result, llm_result = self._short_circuit_values(rule_result)
                pair_status = self._aggregate_pair_status(rule_result, nli_result, llm_result)
                results.append(PairResult(
                    evidence_id=pair.evidence_id,
                    rule_result=rule_result,
                    nli_result=nli_result,
                    llm_result=llm_result,
                    pair_status=pair_status,
                    verifier_status=VerifierStatus.OK.value,
                    matched_evidence=matched_evidence,
                    extracted_entities=entities,
                ))
            else:
                # 需要 LLM 验证：先占位，后续 batch 填充
                results.append(PairResult(
                    evidence_id=pair.evidence_id,
                    rule_result=rule_result,
                    nli_result="unknown",
                    llm_result="unknown",
                    pair_status=PairStatus.INSUFFICIENT.value,
                    verifier_status=VerifierStatus.OK.value,
                    matched_evidence=matched_evidence,
                    extracted_entities=entities,
                ))
                llm_pairs.append((idx, pair))

        # Step 3: 预算检查
        if self._llm_calls_used >= self._max_llm_calls:
            logger.warning(
                "[PairVerifier] LLM 调用预算已耗尽 (%d/%d)，剩余 %d 个 pair 退化为 Rule 层",
                self._llm_calls_used,
                self._max_llm_calls,
                len(llm_pairs),
            )
            for orig_idx, pair in llm_pairs:
                self._degrade_to_rule(results, orig_idx, pair)
            return results

        # Step 4: Batch 分组 + LLM 调用
        await self._process_batches(results, llm_pairs, pairs)

        return results

    # ============================== Rule 层（Layer 1）==============================

    def _rule_check(self, pair: PairInput) -> dict[str, Any]:
        """对单个 pair 执行规则层校验（Layer 1）。

        Args:
            pair: 待验证的 pair

        Returns:
            rule_check_fact 返回字典
        """
        try:
            from api.utils.fact_checker import rule_check_fact

            # 构造检索文档列表（与 HallucinationDetector 一致）
            retrieved_docs = [{"content": pair.evidence_content}] if pair.evidence_content else []
            return rule_check_fact(pair.claim_text, retrieved_docs)
        except Exception as e:
            logger.warning("[PairVerifier] Rule 层校验异常: %s", e)
            return {
                "rule_result": "SUPPORTED",
                "matched_evidence": None,
                "extracted_entities": [],
            }

    # ============================== 短路策略（§3.3）==============================

    def _should_skip_llm(self, rule_result: str, entities: list) -> bool:
        """判断是否应跳过 LLM 调用（短路策略，成本控制关键）。

        Args:
            rule_result: Rule 层结果
            entities: 提取的实体列表

        Returns:
            True 表示跳过 LLM
        """
        # Rule 矛盾 → 跳过 LLM（正则已检测到明确矛盾，无需语义确认）
        if self.config.skip_llm_when_rule_contradicted and rule_result == "CONTRADICTED":
            return True

        # Rule 支持 + 有精确实体 → 跳过 LLM（数值/日期/专名精确匹配，LLM 不会更好）
        if self.config.skip_llm_when_rule_supported_with_entities and rule_result == "SUPPORTED" and entities:
            return True

        return False

    def _short_circuit_values(self, rule_result: str) -> tuple[str, str]:
        """根据短路规则返回 NLI/LLM 层的填充值。

        Args:
            rule_result: Rule 层结果

        Returns:
            (nli_result, llm_result) 元组
        """
        mapping = {
            "CONTRADICTED": ("contradiction", "CONTRADICTED"),
            "SUPPORTED": ("entailment", "SUPPORTED"),
        }
        return mapping.get(rule_result, ("neutral", "NOT_SUPPORTED"))

    # ============================== Batch 处理（§3.2）==============================

    async def _process_batches(
        self,
        results: list[PairResult],
        llm_pairs: list[tuple[int, PairInput]],
        all_pairs: list[PairInput],
    ) -> None:
        """将需要 LLM 验证的 pair 分组为 batch 并依次调用 LLM。

        Args:
            results: 结果列表（就地更新）
            llm_pairs: 需要 LLM 验证的 pair (original_index, pair) 列表
            all_pairs: 原始输入的所有 pair（用于完整日志）
        """
        batch_size = self.config.batch_size

        for batch_start in range(0, len(llm_pairs), batch_size):
            # 预算检查（每次 batch 前检查）
            if self._llm_calls_used >= self._max_llm_calls:
                logger.warning(
                    "[PairVerifier] LLM 预算耗尽，剩余 %d 个 pair 退化为 Rule 层",
                    len(llm_pairs) - batch_start,
                )
                for orig_idx, pair in llm_pairs[batch_start:]:
                    self._degrade_to_rule(results, orig_idx, pair)
                return

            batch = llm_pairs[batch_start:batch_start + batch_size]
            batch_indices = [orig_idx for orig_idx, _ in batch]
            batch_pairs = [pair for _, pair in batch]

            logger.debug(
                "[PairVerifier] Batch 处理: indices=%s, size=%d",
                batch_indices,
                len(batch_pairs),
            )

            # 调用 LLM
            batch_verdicts = await self._verify_batch(batch_pairs)

            # 回填结果
            for pair_idx_in_batch, orig_idx in enumerate(batch_indices):
                if pair_idx_in_batch < len(batch_verdicts) and batch_verdicts[pair_idx_in_batch] is not None:
                    verdict = batch_verdicts[pair_idx_in_batch]
                    pair = all_pairs[orig_idx]

                    # 使用 LLM 返回的结果
                    nli_result = verdict.get("nli", "neutral")
                    llm_result = verdict.get("llm", "NOT_SUPPORTED")

                    # 验证结果合法性
                    if nli_result not in ("entailment", "neutral", "contradiction"):
                        logger.warning(
                            "[PairVerifier] Batch pair %d 的 nli 结果非法: %s，降级为 neutral",
                            orig_idx,
                            nli_result,
                        )
                        nli_result = "neutral"

                    if llm_result not in ("SUPPORTED", "NOT_SUPPORTED", "CONTRADICTED"):
                        logger.warning(
                            "[PairVerifier] Batch pair %d 的 llm 结果非法: %s，降级为 NOT_SUPPORTED",
                            orig_idx,
                            llm_result,
                        )
                        llm_result = "NOT_SUPPORTED"

                    pair_status = self._aggregate_pair_status(
                        results[orig_idx].rule_result, nli_result, llm_result
                    )

                    results[orig_idx].nli_result = nli_result
                    results[orig_idx].llm_result = llm_result
                    results[orig_idx].pair_status = pair_status
                    results[orig_idx].verifier_status = VerifierStatus.OK.value
                    results[orig_idx].partial_failure = False
                else:
                    # 该 pair 在 batch 中解析失败 → 退化为 Rule 层
                    self._degrade_to_rule(results, orig_idx, all_pairs[orig_idx])

    async def _verify_batch(self, batch_pairs: list[PairInput]) -> list[dict[str, Any] | None]:
        """对一组 pair 执行一次 LLM 调用（包含 NLI + LLM 两层判断）。

        Args:
            batch_pairs: 同一 batch 的 pair 列表

        Returns:
            与 batch_pairs 对应的 verdict 列表（None 表示该 pair 解析失败）
        """
        # 构造 prompt
        prompt = self._build_batch_prompt(batch_pairs)

        # 调用 LLM
        response = await self._call_llm(prompt)

        if response is None:
            # 整批失败
            self._llm_calls_used += 1
            return [None] * len(batch_pairs)

        # 解析响应
        self._llm_calls_used += 1
        return self._parse_batch_response(response, len(batch_pairs))

    def _build_batch_prompt(self, batch_pairs: list[PairInput]) -> str:
        """构建 Batch 验证 prompt（§3.2 Batch 调用流程）。

        Args:
            batch_pairs: 同一 batch 的 pair 列表

        Returns:
            LLM prompt 字符串
        """
        pairs_text = []
        for i, pair in enumerate(batch_pairs, start=1):
            ev_text = pair.evidence_content[:2000] if pair.evidence_content else "(空)"
            pairs_text.append(
                f"Pair {i}:\n"
                f"  证据：{ev_text}\n"
                f"  声明：{pair.claim_text}"
            )

        prompt = (
            "请分别判断以下各组证据和声明的关系。\n\n"
            + "\n\n".join(pairs_text)
            + "\n\n"
            + "对每个 pair，请判断：\n"
            + "1. NLI（自然语言推理）：声明是否可以被证据蕴含或存在矛盾？\n"
            + "   - entailment：证据蕴含声明（证据支持声明）\n"
            + "   - neutral：证据不足以判断声明（证据未提及或不相关）\n"
            + "   - contradiction：声明与证据矛盾\n"
            + "2. LLM 语义支持度：声明是否被证据支持？\n"
            + "   - SUPPORTED：证据支持声明\n"
            + "   - NOT_SUPPORTED：证据不支持声明\n"
            + "   - CONTRADICTED：声明与证据矛盾\n\n"
            + "输出严格 JSON 格式（不要额外说明）：\n"
            + '[{"pair_index": 1, "nli": "entailment|neutral|contradiction", "llm": "SUPPORTED|NOT_SUPPORTED|CONTRADICTED"}, ...]'
        )
        return prompt

    def _parse_batch_response(
        self,
        response: str,
        expected_count: int,
    ) -> list[dict[str, Any] | None]:
        """解析 LLM batch 响应（§6.1 部分失败处理）。

        Args:
            response: LLM 返回的原始响应文本
            expected_count: 期望的 pair 数量

        Returns:
            与 batch_pairs 对应的 verdict 列表（None 表示解析失败）
        """
        if not response:
            return [None] * expected_count

        # 清理 markdown 代码块
        text = response.strip()
        import re
        text = re.sub(r"^.*?```json\s*", "", text, flags=re.DOTALL)
        text = re.sub(r"```\s*$", "", text, flags=re.DOTALL)
        text = text.strip()

        try:
            parsed = json_repair.loads(text) if json_repair is not None else json.loads(text)
        except Exception as e:
            logger.warning("[PairVerifier] Batch 响应 JSON 解析失败: %s", e)
            return [None] * expected_count

        if not isinstance(parsed, list):
            # 尝试从 dict 中提取列表
            if isinstance(parsed, dict):
                for key in ("pair_verdicts", "verdicts", "results", "pairs"):
                    if key in parsed and isinstance(parsed[key], list):
                        parsed = parsed[key]
                        break
                else:
                    return [None] * expected_count
            else:
                return [None] * expected_count

        # 按 pair_index 回填（支持乱序返回）
        result_map: dict[int, dict[str, Any]] = {}
        for item in parsed:
            if not isinstance(item, dict):
                continue
            pair_idx = item.get("pair_index")
            if pair_idx is None:
                continue
            # pair_index 可能为 1-based 或 0-based，兼容两种
            if isinstance(pair_idx, int) and 1 <= pair_idx <= expected_count:
                result_map[pair_idx] = item
            elif isinstance(pair_idx, int) and 0 <= pair_idx < expected_count:
                result_map[pair_idx + 1] = item

        # 构造结果列表（1-based → 0-based）
        results: list[dict[str, Any] | None] = []
        for i in range(1, expected_count + 1):
            item = result_map.get(i)
            if item is None:
                results.append(None)
                continue

            # 提取并验证 NLI 结果
            nli_raw = str(item.get("nli", item.get("NLI", ""))).lower().strip()
            nli: str | None = None
            if nli_raw in {"entailment", "neutral", "contradiction"}:
                nli = nli_raw
            elif "contradict" in nli_raw:
                nli = "contradiction"
            elif "entail" in nli_raw or "support" in nli_raw:
                nli = "entailment"
            elif "neutral" in nli_raw:
                nli = "neutral"
            else:
                nli = None

            # 提取并验证 LLM 结果
            llm_raw = str(item.get("llm", item.get("semantic", item.get("result", "")))).upper().strip()
            llm: str | None = None
            if llm_raw in {"SUPPORTED", "NOT_SUPPORTED", "CONTRADICTED"}:
                llm = llm_raw
            elif "CONTRADICT" in llm_raw:
                llm = "CONTRADICTED"
            elif "NOT" in llm_raw or "UNSUPPORTED" in llm_raw:
                llm = "NOT_SUPPORTED"
            elif "SUPPORTED" in llm_raw or "SUPPORT" in llm_raw:
                llm = "SUPPORTED"
            else:
                llm = None

            if nli is None or llm is None:
                # 部分解析失败
                logger.warning(
                    "[PairVerifier] Batch pair %d 部分解析失败: nli=%s, llm=%s",
                    i,
                    nli_raw,
                    llm_raw,
                )
                results.append(None)
            else:
                results.append({"nli": nli, "llm": llm})

        return results

    # ============================== LLM 调用 ==============================

    async def _call_llm(self, prompt: str) -> str | None:
        """调用 LLM 获取响应。

        支持通过 _llm_call_fn 注入测试函数。

        Args:
            prompt: LLM prompt

        Returns:
            LLM 响应文本，失败时返回 None
        """
        # 测试注入
        if self._llm_call_fn is not None:
            try:
                return await self._llm_call_fn(prompt)
            except Exception as e:
                logger.warning("[PairVerifier] 测试 LLM 调用失败: %s", e)
                return None

        try:
            return await self._real_llm_call(prompt)
        except Exception as e:
            logger.warning("[PairVerifier] LLM 调用失败: %s", e)
            return None

    async def _real_llm_call(self, prompt: str) -> str:
        """真实的 LLM 调用（通过 LLMBundle）。

        Returns:
            LLM 响应文本

        Raises:
            Exception: LLM 调用失败时抛出
        """
        chat_mdl = await self._get_llm()
        if chat_mdl is None:
            raise RuntimeError("LLM 未初始化")

        messages = [{"role": "user", "content": prompt}]
        gen_conf = {
            "temperature": float(self.config.temperature),
            "max_tokens": int(self.config.max_tokens),
        }

        # 尝试在事件循环中调用
        loop = asyncio.get_event_loop()
        if loop.is_running():
            ans = await asyncio.wait_for(
                chat_mdl.async_chat("", messages, gen_conf),
                timeout=self.config.timeout_seconds,
            )
        else:
            ans = await asyncio.wait_for(
                chat_mdl.async_chat("", messages, gen_conf),
                timeout=self.config.timeout_seconds,
            )

        return str(ans)

    async def _get_llm(self):
        """延迟加载 LLM 实例。

        Returns:
            LLMBundle 实例，失败时返回 None
        """
        if self._chat_mdl is not None:
            return self._chat_mdl

        try:
            from api.db.services.llm_service import LLMBundle
            from common.constants import LLMType
            from api.db.joint_services.tenant_model_service import (
                get_model_config_by_type_and_name,
            )

            model_config = get_model_config_by_type_and_name(
                self.tenant_id, LLMType.CHAT, self.llm_id
            )
            self._chat_mdl = LLMBundle(self.tenant_id, model_config)
            return self._chat_mdl
        except Exception as e:
            logger.error(
                "[PairVerifier] LLM 初始化失败: tenant=%s, llm_id=%s, error=%s",
                self.tenant_id,
                self.llm_id,
                e,
            )
            return None

    # ============================== 结果聚合（§3.1）==============================

    def _aggregate_pair_status(
        self,
        rule_result: str,
        nli_result: str,
        llm_result: str,
    ) -> str:
        """三层结果聚合为 pair_status（§3.1 _aggregate_pair_status）。

        聚合规则：
        - 任意层矛盾 → contradicted（矛盾信号优先）
        - 三层全支持 → supported
        - 其余 → insufficient

        Args:
            rule_result: Rule 层结果
            nli_result: NLI 层结果
            llm_result: LLM 层结果

        Returns:
            PairStatus 值
        """
        # 矛盾优先
        if (rule_result == "CONTRADICTED"
                or nli_result == "contradiction"
                or llm_result == "CONTRADICTED"):
            return PairStatus.CONTRADICTED.value

        # 三层全支持（与 VerdictMatrix._all_layers_supported 语义一致）
        if (rule_result == "SUPPORTED"
                and nli_result == "entailment"
                and llm_result == "SUPPORTED"):
            return PairStatus.SUPPORTED.value

        # 默认不足
        return PairStatus.INSUFFICIENT.value

    # ============================== 降级处理（§6 部分失败处理）==============================

    def _degrade_to_rule(
        self,
        results: list[PairResult],
        orig_idx: int,
        pair: PairInput,
    ) -> None:
        """将 pair 降级为纯 Rule 层结果（§6.1 部分失败 / §5.3 预算耗尽）。

        Args:
            results: 结果列表（就地更新）
            orig_idx: 原始索引
            pair: 原始 pair 输入
        """
        rule_result = results[orig_idx].rule_result
        nli_result, llm_result = self._short_circuit_values(rule_result)
        pair_status = self._aggregate_pair_status(rule_result, nli_result, llm_result)

        results[orig_idx].nli_result = nli_result
        results[orig_idx].llm_result = llm_result
        results[orig_idx].pair_status = pair_status
        results[orig_idx].verifier_status = (
            VerifierStatus.BUDGET_EXHAUSTED.value
            if self._llm_calls_used >= self._max_llm_calls
            else VerifierStatus.OK.value
        )
        results[orig_idx].partial_failure = True

        logger.debug(
            "[PairVerifier] pair %s 降级为 Rule 层: rule=%s",
            pair.evidence_id,
            rule_result,
        )

    # ============================== 预算管理 ==============================

    @property
    def llm_calls_used(self) -> int:
        """已使用的 LLM 调用次数。"""
        return self._llm_calls_used

    @property
    def llm_calls_remaining(self) -> int:
        """剩余 LLM 调用次数。"""
        return max(0, self._max_llm_calls - self._llm_calls_used)

    def set_max_llm_calls(self, max_calls: int) -> None:
        """设置最大 LLM 调用次数（由 VerificationBudget 驱动）。

        Args:
            max_calls: 最大 LLM 调用次数
        """
        self._max_llm_calls = max_calls

    def reset_budget(self) -> None:
        """重置预算计数（用于下一个验证轮次）。"""
        self._llm_calls_used = 0