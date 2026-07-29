#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
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
"""受限 ReAct 子图模块。

提供 ReAct 子图的核心能力：
- models: ReactState、ReactAction、ReactExecutionResult 数据模型
- policy: Policy Guard 策略校验（白名单、权限、SQL 安全）
- budget: Budget Controller 预算控制（步骤、调用、token、耗时）
- step: ReAct Step 推理（LLM JSON 输出协议）
- executor: 工具执行和 Observation 标准化
- graph: ReAct 子图状态机构建

设计原则（来自 docs/受限ReAct子图落地设计.md）：
- ReAct 不作为默认路径，只处理复杂任务
- ReAct 不直接返回最终答案，只返回 evidence、步骤摘要和建议
- 所有工具调用必须经过 Policy Guard
- 必须有最大步骤和预算
- 每一步必须可观测、可审计
- 工具 Observation 必须截断和脱敏
"""
