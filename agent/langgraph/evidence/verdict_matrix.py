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
"""Claim 判定矩阵（v2.1 §4.4，P0-6 修复）。

设计目标：
    将 Claim 的 ``final_status`` 计算从硬编码 if-else 改为可配置的强类型规则 DSL，
    支持按优先级匹配条件并产出判定结果。

核心修复（v2 评审 P0-6）：
    1. 强类型规则 DSL —— 条件与结果均用枚举定义，不再用 dict 字符串约定，
       避免拼写错误导致的静默失配。
    2. ``_match_rule`` 不再无条件 ``return True`` —— v1 在第一条未命中
       contradiction 时仍返回 true，导致所有 Claim 都被判为 contradicted。
       本版本每个条件独立判断，不匹配返回 False。
    3. verifier_error 优先级高于 contradiction —— 验证器异常产生的错误
       contradiction 不应被当成真实冲突，必须先于 contradiction 判定。
    4. DEFAULT 规则只匹配最后一个 catch-all —— 启动时校验其唯一性与位置。

类比 Java 中的策略模式：
    ``RuleCondition`` 类似策略标识，``VerdictMatrix`` 类似 ``@Component``
    分发器，``_match_rule`` 是策略路由方法。新增条件只需扩展枚举与分支，
    无需修改 ``judge`` 主流程（对扩展开放，对修改封闭）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent.langgraph.evidence.claim import Claim, VerifierStatus


class VerdictResult(str, Enum):
    """判定结果枚举（与 ``ClaimFinalStatus`` 的取值对齐）。

    取值含义：
    - ``VERIFIER_ERROR``：验证器异常，不能映射成 supported / contradicted
    - ``CONTRADICTED``：资料冲突
    - ``INSUFFICIENT``：资料不足
    - ``SUPPORTED``：资料明确支持
    """

    VERIFIER_ERROR = "verifier_error"
    CONTRADICTED = "contradicted"
    INSUFFICIENT = "insufficient"
    SUPPORTED = "supported"


class RuleCondition(str, Enum):
    """规则条件类型枚举（强类型 DSL，不用 dict 字符串约定）。

    取值含义：
    - ``VERIFIER_STATUS_IN``：claim 的 verifier_status 命中 params['statuses']
    - ``ANY_LAYER_CONTRADICTED``：rule / nli / llm 任意层出现矛盾
    - ``RULE_AND_NLI_SUPPORTED``：rule + nli 两层支持（无 LLM 时的宽松路径）
    - ``ALL_LAYERS_SUPPORTED``：rule + nli + llm 三层全支持
    - ``NO_VERIFIED_SUPPORT``：verified_support_ids 为空（无验证通过的支持）
    - ``DEFAULT``：catch-all，匹配所有未命中其它规则的 claim
    """

    VERIFIER_STATUS_IN = "verifier_status_in"
    ANY_LAYER_CONTRADICTED = "any_layer_contradicted"
    RULE_AND_NLI_SUPPORTED = "rule_and_nli_supported"
    ALL_LAYERS_SUPPORTED = "all_layers_supported"
    NO_VERIFIED_SUPPORT = "no_verified_support"
    DEFAULT = "default"


@dataclass
class VerdictRule:
    """单条判定规则（强类型）。

    Attributes:
        condition: 触发条件类型
        verdict: 命中后产出的判定结果
        params: 条件参数（如 VERIFIER_STATUS_IN 需要 statuses 列表）
    """

    condition: RuleCondition
    verdict: VerdictResult
    params: dict[str, Any] = field(default_factory=dict)


class VerdictMatrix:
    """可配置的 Claim 判定矩阵（v2 修复 P0-6）。

    使用方式：
        matrix = VerdictMatrix()              # 使用默认规则
        claim.final_status = matrix.judge(claim)

    设计决策：
    - 规则按列表顺序匹配，命中即返回（短路语义），因此顺序即优先级。
    - 默认规则将 verifier_error 置于最高优先级，contradiction 次之，
      以避免验证器异常污染真实冲突判定。
    - ``_match_rule`` 对未知条件返回 False（而非 True），确保新增枚举值
      未实现分支时不会误判为命中。
    """

    # ---- 层结果取值的归一化集合（小写匹配，兼容不同 verifier 输出）----
    # rule / llm 层 contradiction 取值
    _CONTRADICTION_VALUES: frozenset[str] = frozenset({"contradicted", "contradiction"})
    # rule / llm 层 supported 取值
    _RULE_LLM_SUPPORT_VALUES: frozenset[str] = frozenset({"supported"})
    # nli 层支持取值（entailment）
    _NLI_SUPPORT_VALUES: frozenset[str] = frozenset({"entailment"})

    # 触发 VERIFIER_ERROR 的 verifier_status 集合（P0-6: 优先级最高）
    # 注：unknown 不计入错误（unknown 表示尚未聚合，不等于异常）
    _DEFAULT_ERROR_STATUSES: frozenset[str] = frozenset({
        VerifierStatus.TIMEOUT.value,
        VerifierStatus.ERROR.value,
        VerifierStatus.MIXED.value,
        VerifierStatus.BUDGET_EXHAUSTED.value,
    })

    def __init__(self, rules: list[VerdictRule] | None = None):
        """初始化判定矩阵。

        Args:
            rules: 自定义规则列表；为 None 时使用 ``_default_rules``。
                传入的规则会经 ``validate_rules`` 校验，非法时抛 ValueError。
        """
        if rules is None:
            rules = self._default_rules()
        errors = self.validate_rules(rules)
        if errors:
            # 启动期硬失败：规则非法不应带入运行时
            raise ValueError("判定矩阵规则校验失败: " + "; ".join(errors))
        self.rules = rules

    def _default_rules(self) -> list[VerdictRule]:
        """默认判定规则（按优先级，匹配即返回）。

        ★ v2 修复 P0-6：verifier_error 优先级高于 contradiction
        （验证器异常产生的错误 contradiction 不应被当成真实冲突）。

        顺序说明：
            a. VERIFIER_STATUS_IN  → VERIFIER_ERROR  （最高优先级）
            b. ANY_LAYER_CONTRADICTED → CONTRADICTED
            c. NO_VERIFIED_SUPPORT → INSUFFICIENT
            d. ALL_LAYERS_SUPPORTED → SUPPORTED
            e. DEFAULT → INSUFFICIENT                （catch-all，必须最后）
        """
        return [
            # a. verifier 异常最高优先级：不映射成 supported，也不映射成 contradicted
            VerdictRule(
                condition=RuleCondition.VERIFIER_STATUS_IN,
                verdict=VerdictResult.VERIFIER_ERROR,
                params={"statuses": list(self._DEFAULT_ERROR_STATUSES)},
            ),
            # b. 任一层 contradiction → contradicted（仅在 verifier 正常时才判定）
            VerdictRule(
                condition=RuleCondition.ANY_LAYER_CONTRADICTED,
                verdict=VerdictResult.CONTRADICTED,
            ),
            # c. 无验证通过的支持 → insufficient
            VerdictRule(
                condition=RuleCondition.NO_VERIFIED_SUPPORT,
                verdict=VerdictResult.INSUFFICIENT,
            ),
            # d. rule + nli + llm 三层全 supported → supported
            VerdictRule(
                condition=RuleCondition.ALL_LAYERS_SUPPORTED,
                verdict=VerdictResult.SUPPORTED,
            ),
            # e. 默认 catch-all → insufficient（必须唯一且在最后）
            VerdictRule(
                condition=RuleCondition.DEFAULT,
                verdict=VerdictResult.INSUFFICIENT,
            ),
        ]

    def validate_rules(self, rules: list[VerdictRule]) -> list[str]:
        """启动时验证规则合法性，返回错误信息列表（空列表表示合法）。

        校验项：
            1. 规则列表非空
            2. 每条规则为 VerdictRule 实例，且 condition / verdict 枚举类型正确
            3. DEFAULT 规则必须唯一且位于最后
            4. VERIFIER_STATUS_IN 规则必须提供 params['statuses']

        Args:
            rules: 待校验的规则列表。

        Returns:
            list[str]: 错误信息列表；为空表示全部合法。
        """
        errors: list[str] = []

        if not rules:
            errors.append("规则列表不能为空")
            return errors

        # 1. 类型与枚举校验
        for i, rule in enumerate(rules):
            if not isinstance(rule, VerdictRule):
                errors.append(f"规则 #{i} 不是 VerdictRule 类型: {rule!r}")
                continue
            if not isinstance(rule.condition, RuleCondition):
                errors.append(f"规则 #{i} 非法条件类型: {rule.condition!r}")
            if not isinstance(rule.verdict, VerdictResult):
                errors.append(f"规则 #{i} 非法判定结果: {rule.verdict!r}")

        # 2. DEFAULT 规则必须唯一且在最后（★ P0-6: catch-all 只能有一个且在末尾）
        default_indices = [
            i for i, r in enumerate(rules)
            if isinstance(r, VerdictRule) and r.condition == RuleCondition.DEFAULT
        ]
        if not default_indices:
            errors.append("缺少 DEFAULT 规则（必须有一个 catch-all）")
        elif len(default_indices) > 1:
            errors.append(f"存在 {len(default_indices)} 个 DEFAULT 规则（必须唯一）")
        elif default_indices[0] != len(rules) - 1:
            errors.append("DEFAULT 规则必须在最后")

        # 3. VERIFIER_STATUS_IN 必须提供 statuses 参数
        for i, rule in enumerate(rules):
            if not isinstance(rule, VerdictRule):
                continue
            if rule.condition == RuleCondition.VERIFIER_STATUS_IN:
                statuses = rule.params.get("statuses") if isinstance(rule.params, dict) else None
                if not statuses or not isinstance(statuses, (list, tuple, set, frozenset)):
                    errors.append(f"规则 #{i} VERIFIER_STATUS_IN 缺少 params['statuses']")

        return errors

    def judge(self, claim: Claim) -> str:
        """根据判定矩阵计算 final_status。

        按规则顺序逐条匹配，命中即返回对应 verdict（短路语义）。
        若所有规则均未命中（理论上不会发生，因 DEFAULT 为 catch-all），
        兜底返回 ``insufficient``。

        Args:
            claim: 待判定的 Claim（类型见 ``agent.langgraph.evidence.claim.Claim``）。

        Returns:
            str: final_status 取值（与 ``VerdictResult`` 枚举值一致）。
        """
        for rule in self.rules:
            if self._match_rule(claim, rule):
                return rule.verdict.value
        # 理论上不可达：DEFAULT 规则 catch-all 保证最终命中
        return VerdictResult.INSUFFICIENT.value

    def _match_rule(self, claim: Claim, rule: VerdictRule) -> bool:
        """检查 claim 是否匹配规则条件。

        ★ 关键修复 P0-6：不再无条件 ``return True``，每个条件类型独立判断，
        不匹配返回 False。v1 在此处无条件返回 True，导致第一条未命中
        contradiction 的规则仍被判为命中，所有 Claim 都变成 contradicted。

        Args:
            claim: 待判定的 Claim。
            rule: 待匹配的规则。

        Returns:
            bool: 是否命中。
        """
        if rule.condition == RuleCondition.DEFAULT:
            # DEFAULT 是 catch-all，匹配所有 claim（仅作兜底，位置由 validate_rules 保证在最后）
            return True

        if rule.condition == RuleCondition.VERIFIER_STATUS_IN:
            statuses = rule.params.get("statuses", [])
            status_set = {str(s) for s in statuses}
            return claim.verifier_status in status_set

        if rule.condition == RuleCondition.ANY_LAYER_CONTRADICTED:
            return self._any_layer_contradicted(claim)

        if rule.condition == RuleCondition.NO_VERIFIED_SUPPORT:
            # verified_support_ids 为空 → 无验证通过的支持
            return not claim.has_verified_support

        if rule.condition == RuleCondition.ALL_LAYERS_SUPPORTED:
            return self._all_layers_supported(claim)

        if rule.condition == RuleCondition.RULE_AND_NLI_SUPPORTED:
            # 兼容保留条件：rule + nli 两层支持（无 LLM 时的宽松路径，默认规则未启用）
            layers = self._aggregate_layers(claim)
            return layers["rule_supported"] and layers["nli_supported"]

        # ★ 未知条件不匹配（不是 True），避免新增枚举值未实现分支时误判
        return False

    # ---- 层结果聚合辅助方法 ----
    # Claim 本身不持有 claim 级 rule/nli/llm 结果，需从 pair_verdicts 跨 pair 聚合。
    # 聚合语义：某层在任意 pair 上出现支持/矛盾，则该层视为支持/矛盾（claim 级）。

    def _any_layer_contradicted(self, claim: Claim) -> bool:
        """任意 pair 的任意层（rule / nli / llm）出现矛盾。

        Args:
            claim: 待判定的 Claim。

        Returns:
            bool: 任一层矛盾即为 True。
        """
        for v in claim.pair_verdicts:
            if self._is_contradiction(v.rule_result):
                return True
            if self._is_contradiction(v.nli_result):
                return True
            if self._is_contradiction(v.llm_result):
                return True
        return False

    def _all_layers_supported(self, claim: Claim) -> bool:
        """rule + nli + llm 三层均存在支持（跨 pair 聚合）。

        聚合策略：三层各自在任意 pair 上出现支持即可。该条件排在
        NO_VERIFIED_SUPPORT 之后，到达此处时 claim 已有验证通过的支持且无矛盾，
        三层均支持即为强支持信号。

        Args:
            claim: 待判定的 Claim。

        Returns:
            bool: 三层均存在支持为 True。
        """
        layers = self._aggregate_layers(claim)
        return layers["rule_supported"] and layers["nli_supported"] and layers["llm_supported"]

    def _aggregate_layers(self, claim: Claim) -> dict[str, bool]:
        """跨 pair 聚合各层支持情况。

        Args:
            claim: 待判定的 Claim。

        Returns:
            dict[str, bool]: rule_supported / nli_supported / llm_supported 三个标志。
        """
        rule_supported = False
        nli_supported = False
        llm_supported = False
        for v in claim.pair_verdicts:
            if self._is_rule_llm_support(v.rule_result):
                rule_supported = True
            if self._is_nli_support(v.nli_result):
                nli_supported = True
            if self._is_rule_llm_support(v.llm_result):
                llm_supported = True
        return {
            "rule_supported": rule_supported,
            "nli_supported": nli_supported,
            "llm_supported": llm_supported,
        }

    @classmethod
    def _is_contradiction(cls, value: Any) -> bool:
        """判断层结果是否为矛盾（大小写无关）。"""
        return isinstance(value, str) and value.lower() in cls._CONTRADICTION_VALUES

    @classmethod
    def _is_rule_llm_support(cls, value: Any) -> bool:
        """判断 rule / llm 层结果是否为支持（大小写无关）。"""
        return isinstance(value, str) and value.lower() in cls._RULE_LLM_SUPPORT_VALUES

    @classmethod
    def _is_nli_support(cls, value: Any) -> bool:
        """判断 nli 层结果是否为支持（entailment，大小写无关）。"""
        return isinstance(value, str) and value.lower() in cls._NLI_SUPPORT_VALUES
