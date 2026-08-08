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
"""验证器网关协议（防腐层，RAG 增强能力融合 §3.2）。

定义 VerifierGateway 抽象基类，作为 LangGraph 主流程与 RAGFlow 验证组件
（HallucinationDetector / Grader）之间的防腐层接口。

设计意图：
  LangGraph 节点（hallucination / quality_check）只依赖本接口，不直接 import
  RAGFlow 组件。单体阶段由 LocalVerifierGateway 通过 import 调用组件；微服务
  拆分时替换为 HttpVerifierGateway 通过 HTTP 调用验证微服务，上层代码零改动。

接口契约：
  - verify_faithfulness：验证答案忠实度（幻觉检测），返回标准化结果字典
  - grade_retrieval：评估检索结果质量，返回标准化结果字典

返回值标准化：
  两个方法的返回字典字段固定，由 Gateway 实现负责将 RAGFlow 组件的原始输出
  转换为该标准格式，隔离组件输出结构变化对上层的影响。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class VerifierGateway(ABC):
    """验证器网关接口（防腐层）。

    封装幻觉检测和检索质量评估两类验证能力，使 LangGraph 节点通过接口调用
    而非直接依赖 RAGFlow 组件实现。

    实现类：
    - LocalVerifierGateway：进程内调用 RAGFlow 组件（单体阶段）
    - HttpVerifierGateway：HTTP 调用验证微服务（微服务阶段）
    - ShadowVerifierGateway：Local + Http 双跑对比（灰度阶段，未来实现）

    调用方通过 GatewayResolver.verifier_for(tenant_id) 获取实现实例，
    按租户运行时配置路由到 local / remote / shadow 模式。
    """

    @abstractmethod
    async def verify_faithfulness(
        self, answer: str, context: str, query: str, tenant_id: str
    ) -> dict[str, Any]:
        """验证答案忠实度（幻觉检测）。

        通过三层验证（规则 + NLI + LLM）检测生成答案中是否存在幻觉内容，
        并根据忠实度分数给出分级处置建议。

        Args:
            answer: 待验证的生成答案文本
            context: 检索到的参考上下文（已拼接的文档内容）
            query: 用户原始查询（用于生成重写 prompt 等场景）
            tenant_id: 租户 ID（用于解析 LLM 配置）

        Returns:
            包含以下字段的标准字典：
            - faithfulness_score (float): 忠实度分数，范围 0.0 ~ 1.0，
              值越高表示答案越被上下文支持
            - action (str): 处置建议，取值为 pass / filter / regenerate / refuse
            - claims (list[dict]): 论断列表，每条含 text / score / rule_result
              等字段，供上层做细粒度决策

        Raises:
            Exception: 组件调用失败时抛出，由调用方决定降级策略
              （如回退为字符重叠度评估）。
        """
        ...

    @abstractmethod
    async def grade_retrieval(
        self, query: str, chunks: list[dict], tenant_id: str
    ) -> dict[str, Any]:
        """评估检索结果质量。

        对检索返回的文档进行深层语义评估（LLM-as-Judge / NLI），判断文档
        是否真正能回答用户问题，而非仅语义相似。

        Args:
            query: 用户查询文本
            chunks: 检索到的文档块列表，每个 chunk 至少含 content 字段
            tenant_id: 租户 ID（用于解析 LLM 配置）

        Returns:
            包含以下字段的标准字典：
            - quality_score (float): 检索质量分数，范围 0.0 ~ 1.0，
              为所有文档评估分数的均值
            - has_relevant (bool): 是否存在至少一篇相关文档
            - relevant_count (int): 相关文档数量
            - graded_docs (list[dict]): 评估后的文档列表，每条含 relevance /
              score / reason / graded_by 等字段

        Raises:
            Exception: 组件调用失败时抛出，由调用方决定降级策略
              （如回退为 Rerank 分数阈值评估）。
        """
        ...
