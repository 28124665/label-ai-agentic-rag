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
"""ClaimClassifier — 服务端 Claim 分级重新计算（v2.1 §4.2.3，任务二建议 2）。

设计目标：
    不信任模型输出的 claim_type / is_required，由服务端基于正则规则重新计算
    server_claim_type / server_is_required / risk_level，防止模型将关键金额
    标记为非关键从而绕过验证（v2 评审 P1-D：Claim 数量比例可被操纵）。

核心约束：
    - 仅写 server_* 字段与 risk_level；model_claim_type / model_is_required
      保留原值供审计对比（“只覆盖服务端字段，不污染模型原始声明”）。
    - 分类规则为纯函数式正则匹配，无外部依赖，可独立单测。
    - first-match-wins：按优先级顺序匹配，命中即止，保证确定性。

参考文档：
    docs/Claim级可追溯幻觉检测设计.md §4.2.3

类比 Java 中的领域服务（Domain Service）：
    ``@Service class ClaimClassifier { ... }``，无状态，可单例复用。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from agent.langgraph.evidence.claim import Claim, ClaimType

logger = logging.getLogger(__name__)


class ClaimClassifier:
    """服务端重新判定 claim_type / risk_level / is_required。

    v2 评审建议（任务二建议 2）：不能信任模型输出的 is_required，服务端必须
    基于规则重新计算；模型声明仅保留于 model_* 字段供审计，不参与决策。
    """

    # 分类正则模式（按优先级排序，first-match-wins）。
    #
    # 优先级设计决策：
    #   1. 高风险类型（COMPLIANCE / AMOUNT）优先，确保关键事实不被低风险类型淹没；
    #   2. AMOUNT 先于 DATE / RATIO：AMOUNT 必须命中“货币单位”（亿/万/元/USD/RMB），
    #      纯日期 “2024年” 不会误命中 AMOUNT，故可安全前置——避免 “2024年营收15.3亿”
    #      被降级为 DATE(medium) 而丢失 AMOUNT(high) 的风险定级；
    #   3. IDENTITY / DATE / RATIO 模式互斥性较强，按显式度排列；
    #   4. CONCLUSION 置于实质类型之后：结论标记（因此/综上）常作前缀，当 Claim
    #      同时含具体事实时，事实类型应优先；
    #   5. STATEMENT 为兜底默认。
    _PATTERNS: list[tuple[ClaimType, list[str]]] = [
        # 合规声明：法律/监管/合规类，幻觉代价最高 → high
        (
            ClaimType.COMPLIANCE,
            [
                r"符合",
                r"违反|违犯",
                r"合规|违规",
                r"守法|违法",
                r"监管|处罚|惩处|罚款",
            ],
        ),
        # 金额：数字 + 货币单位（亿/万/元/USD/RMB 等）。
        # 注意：必须命中货币单位；纯量级计数（如 “100万用户”）也会命中，属保守判定
        # （将可核验的量级数字视为高风险），符合幻觉检测的保守原则。
        (
            ClaimType.AMOUNT,
            [
                r"\d[\d,]*\.?\d*\s*[亿万圆元]",
                r"\d[\d,]*\.?\d*\s*(?:USD|RMB|美元|人民币|美金)",
                r"(?:USD|RMB)\s?\d[\d,]*\.?\d*",
                r"\$\s?\d[\d,]*\.?\d*",
            ],
        ),
        # 身份声明：适用于/属于/是X类 等
        (
            ClaimType.IDENTITY,
            [
                r"适用于",
                r"属于",
                r"是.{0,10}类",
                r"归属|归类|划分为|划入",
                r"身份|类别为",
            ],
        ),
        # 日期：YYYY年 / Q1-Q4 / 上半年下半年 等
        (
            ClaimType.DATE,
            [
                r"\d{4}\s*年",
                r"\d{4}\s*[-/.年]\s*\d{1,2}\s*月?",
                r"\d{1,2}\s*月份?",
                r"Q[1-4]",
                r"[上下]半年",
                r"截至|截止|生效日期|到期日",
            ],
        ),
        # 比率：百分比 / 增长下降比率
        (
            ClaimType.RATIO,
            [
                r"\d+\.?\d*\s*[%％]",
                r"增长|下降|上升|回落|同比|环比|增幅|降幅|增速|跌幅|涨幅",
            ],
        ),
        # 结论标记：综上/因此/结论是 等（常作前缀，故置于实质类型之后）
        (
            ClaimType.CONCLUSION,
            [
                r"综上",
                r"因此",
                r"结论[是为：:]",
                r"总而言之|由此可见|综合来看|据此",
            ],
        ),
    ]

    # is_required 映射：除 STATEMENT 外均为关键 Claim（需强制验证）。
    # 设计决策：金额/日期/比率/身份/合规/结论均为可核验或决策性事实，必须验证；
    # 一般陈述（STATEMENT）允许宽松处理，避免琐碎 Claim 挤占验证预算（P1-D）。
    _REQUIRED_TYPES: frozenset[ClaimType] = frozenset(
        {
            ClaimType.AMOUNT,
            ClaimType.DATE,
            ClaimType.RATIO,
            ClaimType.IDENTITY,
            ClaimType.COMPLIANCE,
            ClaimType.CONCLUSION,
        }
    )

    # risk_level 映射：决定验证预算分配与一票否决权重（v2 §P1-D）。
    #   - high  ：AMOUNT / COMPLIANCE —— 金额错误与合规误判代价最高；
    #   - medium：DATE / RATIO / IDENTITY / CONCLUSION —— 可核验但影响有限；
    #   - low   ：STATEMENT —— 一般陈述。
    _RISK_LEVELS: dict[ClaimType, str] = {
        ClaimType.AMOUNT: "high",
        ClaimType.COMPLIANCE: "high",
        ClaimType.DATE: "medium",
        ClaimType.RATIO: "medium",
        ClaimType.IDENTITY: "medium",
        ClaimType.CONCLUSION: "medium",
        ClaimType.STATEMENT: "low",
    }

    def __init__(self) -> None:
        # 预编译正则（IGNORECASE：兼容 USD/usd、Q1/q1 等大小写变体），
        # 避免 classify_claims 批量分类时对同一模式重复编译。
        self._compiled: list[tuple[ClaimType, list[re.Pattern[str]]]] = [
            (ctype, [re.compile(p, re.IGNORECASE) for p in patterns])
            for ctype, patterns in self._PATTERNS
        ]

    def classify(self, claim_text: str) -> dict[str, Any]:
        """对单条 Claim 文本进行服务端分级计算。

        不信任模型输出，仅依据文本正则匹配重新判定类型/关键性/风险。

        Args:
            claim_text: Claim 原始文本（不含引用标记）。

        Returns:
            dict: 包含三个服务端字段：
                - server_claim_type: ClaimType 的 value（如 "amount"）
                - server_is_required: 是否为关键 Claim
                - risk_level: 风险等级（low / medium / high）
        """
        # 空文本兜底为 STATEMENT，避免空指针；不视为关键 Claim。
        if not claim_text:
            return {
                "server_claim_type": ClaimType.STATEMENT.value,
                "server_is_required": False,
                "risk_level": self._RISK_LEVELS[ClaimType.STATEMENT],
            }

        # first-match-wins：按优先级命中第一个类型即止，未命中则兜底 STATEMENT。
        detected_type = ClaimType.STATEMENT
        for ctype, patterns in self._compiled:
            if any(p.search(claim_text) for p in patterns):
                detected_type = ctype
                break

        return {
            "server_claim_type": detected_type.value,
            "server_is_required": detected_type in self._REQUIRED_TYPES,
            "risk_level": self._RISK_LEVELS[detected_type],
        }

    def classify_claims(self, claims: list[Claim]) -> list[Claim]:
        """批量重新计算 Claim 分级（原地写回 server_* 字段与 risk_level）。

        仅覆盖服务端字段，model_claim_type / model_is_required 保留原值供审计对比。
        原地修改并返回同一列表，与 reclassify 语义一致，便于链式调用。

        Args:
            claims: Claim dataclass 列表。

        Returns:
            list[Claim]: 写回分级结果后的同一列表。
        """
        for claim in claims:
            result = self.classify(claim.text)
            claim.server_claim_type = result["server_claim_type"]
            claim.server_is_required = result["server_is_required"]
            claim.risk_level = result["risk_level"]
        logger.debug("ClaimClassifier.classify_claims: 已分级 %d 条 Claim", len(claims))
        return claims
