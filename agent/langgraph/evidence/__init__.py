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
"""Evidence 标准化入口。

该文件为 evidence 模块对外的轻量门面，保留扩展空间。
详细实现见 models.py（normalizer 工厂）和 fusion.py / answerability.py。
"""
from __future__ import annotations

from agent.langgraph.evidence.models import (
    Evidence,
    normalize_db_evidence,
    normalize_rag_evidence,
    normalize_tool_result,
    normalize_web_evidence,
)

__all__ = [
    "Evidence",
    "normalize_db_evidence",
    "normalize_rag_evidence",
    "normalize_tool_result",
    "normalize_web_evidence",
]
