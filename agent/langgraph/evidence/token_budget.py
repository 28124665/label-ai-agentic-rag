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
"""Token 预算调度器。

多工具（RAG + DB + Web）Agentic RAG 系统的 Token 预算管理：
1. 按实际工具返回结果统计 Token 数
2. 基于 total token 溢出比例判定压缩级别
3. 单工具 ≤30K 自动豁免压缩，只压缩超标工具
4. 对 RAG chunks 执行 LLM 抽取式压缩
5. 对 DB 结果执行行级截断和列级裁剪
6. 逐条装配 Evidence 为 Prompt 文本，实时 Token 监控
7. 精简模式兜底

设计文档：docs/Token预算调度器设计方案.md (v2.0)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from common.token_utils import num_tokens_from_string

logger = logging.getLogger(__name__)

# ========== 配置常量 ==========

# 模型窗口
DEFAULT_MODEL_WINDOW = 128000
MODEL_WINDOW_RATIO = 0.85          # 留 15% 给输出
OUTPUT_RESERVE_TOKENS = 2000       # 输出预留，永远不可侵占

# 单工具豁免阈值
# 单个工具返回结果 ≤ 此值，不参与压缩
# 30K ≈ 60 个 RAG chunk 或 1000 行 DB 紧凑数据
PER_TOOL_EXEMPTION_TOKENS = 30000

# 溢出比例 → 压缩级别
# 设计依据：
#   0-30%:   差得不多，低价值过滤即可，不值得调用 LLM
#   30-80%:  需要实质性压缩，但还能保留关键信息
#   80-150%: 数据量远超窗口，必须激进压缩
#   >150%:   数据爆炸，只能留摘要兜底
OVERFLOW_THRESHOLDS = {
    "light":    0.30,   # 溢出 ≤ 30%
    "moderate": 0.80,   # 溢出 ≤ 80%
    "severe":   1.50,   # 溢出 ≤ 150%
    # > 150% → compact
}

# 压缩级别配置（prompt_key 映射）
COMPRESS_LEVELS = {
    "normal":   {"prompt_key": None},
    "light":    {"prompt_key": None},
    "moderate": {"prompt_key": "moderate"},
    "severe":   {"prompt_key": "severe"},
    "compact":  {"prompt_key": "compact"},
}

# 单条 Evidence 最低保留 Token（低于此值直接丢弃整条）
MIN_EVIDENCE_TOKENS = 50

# 来源装配顺序
SOURCE_ORDER = ["rag", "db", "web", "report"]

# LLM 抽取式压缩 Prompt
COMPRESS_PROMPTS = {
    "moderate": """你是一个精确的文本提取器。根据用户问题，从以下文本中提取与问题相关的句子。

规则：
1. 只提取原句，不要改写、不要总结、不要添加任何新信息
2. 保留原句顺序
3. 提取约一半的相关句子，优先保留包含关键事实、数据、定义的句子
4. 如果文本与问题完全无关，输出"无相关内容"

问题：{question}

文本：
{chunk_text}""",
    "severe": """你是一个精确的文本提取器。根据用户问题，从以下文本中提取最关键的 2-3 个句子。

规则：
1. 只提取原句，不要改写、不要总结、不要添加任何新信息
2. 优先提取包含直接答案、核心数据、关键定义的句子
3. 如果文本与问题无关，输出"无相关内容"

问题：{question}

文本：
{chunk_text}""",
    "compact": """根据用户问题，从以下文本中提取最关键的 1 个句子或短语。

规则：
1. 只提取原句，不要改写
2. 如果文本与问题无关，输出"无相关内容"

问题：{question}

文本：
{chunk_text}""",
}

# 压缩并发上限
MAX_COMPRESS_CONCURRENCY = 5


class TokenBudgetScheduler:
    """Token 预算调度器。

    职责：
    1. 统计各工具实际返回的 Token 数
    2. 计算总溢出比例，确定压缩级别
    3. 识别超标工具（> 30K），豁免低量工具
    4. 对超标工具按级别执行压缩策略
    5. 对 RAG chunks 执行 LLM 抽取式压缩
    6. 对 DB 结果执行行级截断和列级裁剪
    7. 逐条装配 Evidence 为 Prompt 文本，实时 Token 监控
    8. 精简模式兜底
    """

    def __init__(self, total_budget: int | None = None):
        self.total_budget = total_budget or int(
            DEFAULT_MODEL_WINDOW * MODEL_WINDOW_RATIO
        )

    # ─── 工具 Token 计数 ───

    def _count_tool_tokens(self, state: dict) -> dict[str, int]:
        """统计每个工具返回结果的 Token 数。

        使用 tiktoken 精确计算，确保阈值判定准确。

        Args:
            state: AgentState dict

        Returns:
            {"rag": 12000, "db": 180000, "web": 5000}
        """
        counts = {}

        # RAG
        rag_docs = state.get("rag_docs", []) or []
        rag_tokens = 0
        for doc in rag_docs:
            rag_tokens += num_tokens_from_string(doc.get("content", ""))
        if rag_tokens > 0:
            counts["rag"] = rag_tokens

        # DB
        db_result = state.get("db_result") or {}
        db_tokens = 0
        for row in db_result.get("rows", []):
            db_tokens += num_tokens_from_string(str(row))
        if db_tokens > 0:
            counts["db"] = db_tokens

        # Web
        web_docs = state.get("web_docs", []) or []
        web_tokens = 0
        for doc in web_docs:
            web_tokens += num_tokens_from_string(doc.get("content", ""))
        if web_tokens > 0:
            counts["web"] = web_tokens

        return counts

    # ─── 溢出比例计算与压缩级别判定 ───

    def _calculate_overflow_ratio(
        self,
        tool_token_counts: dict[str, int],
        locked_tokens: int,
    ) -> float:
        """计算总溢出比例。

        Args:
            tool_token_counts: {"rag": 12000, "db": 180000, "web": 5000}
            locked_tokens: System + History 的 Token 数

        Returns:
            溢出比例，≤ 0 表示不溢出
        """
        total_evidence = sum(tool_token_counts.values())
        available = self.total_budget - locked_tokens - OUTPUT_RESERVE_TOKENS

        if total_evidence <= available:
            return 0.0

        return (total_evidence - available) / available

    def determine_level(self, overflow_ratio: float) -> str:
        """根据溢出比例确定压缩级别。

        Args:
            overflow_ratio: 溢出比例（0.0 = 刚好不溢出，1.0 = 溢出 100%）

        Returns:
            压缩级别字符串：normal / light / moderate / severe / compact
        """
        if overflow_ratio <= 0:
            return "normal"
        elif overflow_ratio <= OVERFLOW_THRESHOLDS["light"]:
            return "light"
        elif overflow_ratio <= OVERFLOW_THRESHOLDS["moderate"]:
            return "moderate"
        elif overflow_ratio <= OVERFLOW_THRESHOLDS["severe"]:
            return "severe"
        else:
            return "compact"

    # ─── 超标工具识别 ───

    def _identify_overflown_tools(
        self,
        tool_token_counts: dict[str, int],
    ) -> tuple[list[str], list[str]]:
        """识别超标工具和豁免工具。

        Args:
            tool_token_counts: 各工具 Token 数

        Returns:
            (overflown_tools, exempt_tools)
        """
        overflown = []
        exempt = []
        for tool, tokens in tool_token_counts.items():
            if tokens > PER_TOOL_EXEMPTION_TOKENS:
                overflown.append(tool)
            else:
                exempt.append(tool)
        return overflown, exempt

    # ─── LLM 抽取式压缩 ───

    async def compress_rag_chunks(
        self,
        chunks: list[dict],
        question: str,
        level: str,
        llm,  # 低成本 LLM 实例
    ) -> list[dict]:
        """对 RAG chunks 进行 LLM 抽取式压缩。

        并行处理多个 chunk，每个 chunk 独立压缩。
        仅提取与问题相关的原句，不做改写。

        Args:
            chunks: RAG 检索结果（已按 rerank 过滤）
            question: 用户原始问题
            level: 压缩级别（moderate/severe/compact）
            llm: 低成本 LLM 实例（如 GPT-4o-mini）

        Returns:
            压缩后的 chunks（content 字段被替换为压缩文本）
        """
        config = COMPRESS_LEVELS.get(level)
        if not config or config["prompt_key"] is None:
            return chunks  # normal/light 不压缩

        prompt_template = COMPRESS_PROMPTS[config["prompt_key"]]
        semaphore = asyncio.Semaphore(MAX_COMPRESS_CONCURRENCY)

        async def compress_one(chunk: dict) -> dict:
            async with semaphore:
                content = chunk.get("content", "")
                tokens = num_tokens_from_string(content)

                # 短 chunk（< 100 tokens）不需要压缩
                if tokens < 100:
                    return chunk

                prompt = prompt_template.format(
                    question=question,
                    chunk_text=content,
                )

                try:
                    response = await llm.chat(prompt)
                    compressed = response.strip()

                    compressed_tokens = num_tokens_from_string(compressed)

                    # 安全检查：压缩后不应比原文更长
                    if compressed_tokens > tokens * 0.9:
                        return chunk

                    # 检查是否返回了"无相关内容"
                    if "无相关内容" in compressed:
                        chunk["content"] = ""
                        chunk["compressed"] = True
                        return chunk

                    chunk["content"] = compressed
                    chunk["compressed"] = True
                    chunk["compression_ratio"] = compressed_tokens / tokens
                    return chunk

                except Exception:
                    logger.warning(
                        f"[TokenBudgetScheduler] 压缩 chunk 失败，保留原文",
                        exc_info=True,
                    )
                    return chunk  # 失败时保留原文

        return await asyncio.gather(*[compress_one(c) for c in chunks])

    # ─── DB 行级截断 + 表级配额 ───

    def compress_db_evidence(
        self,
        db_result: dict,
        quota: int,
        level: str,
    ) -> dict:
        """对 DB 查询结果进行行级截断和列级裁剪。

        策略层级：
        - normal/light: 保留所有行，Markdown 表格
        - moderate: 行截断到 50 行，表级配额分配
        - severe: 紧凑格式，10 行，丢弃大文本列
        - compact: 仅摘要（行数+数值统计+1 条样本）

        Args:
            db_result: {"rows": [...], "columns": [...], "sql": "...", "table_name": "..."}
            quota: 分配给该表结果的 Token 配额
            level: 压缩级别

        Returns:
            {"text": str, "truncated": bool, "original_rows": int, "kept_rows": int}
        """
        rows = db_result.get("rows", [])
        columns = db_result.get("columns", [])
        if not rows:
            return {
                "text": "（查询无结果）",
                "truncated": False,
                "original_rows": 0,
                "kept_rows": 0,
            }

        original_rows = len(rows)

        if level in ("normal", "light"):
            text = self._format_db_table(rows, columns)
            # 如果超出配额，按 Token 逐步截断
            if num_tokens_from_string(text) > quota:
                return self._fit_db_to_quota(rows, columns, quota, original_rows)
            return {
                "text": text,
                "truncated": False,
                "original_rows": original_rows,
                "kept_rows": original_rows,
            }

        elif level == "moderate":
            max_rows = min(original_rows, 50)
            kept_rows = rows[:max_rows]
            text = self._format_db_table(kept_rows, columns)
            # 如果 50 行仍超出配额，进一步截断
            if num_tokens_from_string(text) > quota:
                return self._fit_db_to_quota(
                    kept_rows, columns, quota, original_rows
                )
            return {
                "text": text,
                "truncated": original_rows > max_rows,
                "original_rows": original_rows,
                "kept_rows": len(kept_rows),
            }

        elif level == "severe":
            max_rows = min(original_rows, 10)
            text_columns = self._identify_text_columns(rows, columns)
            keep_columns = [c for c in columns if c not in text_columns]
            if not keep_columns:
                keep_columns = columns

            kept_rows = rows[:max_rows]
            compact_text = self._format_compact_rows(kept_rows, keep_columns)
            if num_tokens_from_string(compact_text) > quota:
                # 进一步减少行数
                kept_rows = rows[: max(1, max_rows // 2)]
                compact_text = self._format_compact_rows(kept_rows, keep_columns)
            return {
                "text": compact_text,
                "truncated": True,
                "original_rows": original_rows,
                "kept_rows": len(kept_rows),
            }

        else:  # compact
            summary_text = self._format_db_summary(rows, columns)
            return {
                "text": summary_text,
                "truncated": True,
                "original_rows": original_rows,
                "kept_rows": 1,
            }

    # ─── DB 压缩结果（返回 rows，给 normalize 使用） ───

    def _compress_db_result(
        self,
        db_result: dict,
        quota: int,
        level: str,
    ) -> dict:
        """对 DB 查询结果进行行级截断和列级裁剪，返回压缩后的 rows 结构。

        与 compress_db_evidence 的区别：
        - compress_db_evidence 返回格式化文本（用于 Prompt 装配）
        - _compress_db_result 返回压缩后的 rows + columns（用于 Evidence 标准化）

        策略层级：
        - normal/light: 保留所有行和列
        - moderate: 行数截断到 50 行
        - severe: 行数截断到 10 行，删除大文本列，保留必要列
        - compact: 仅保留 1 行代表性样本

        Args:
            db_result: {"rows": [...], "columns": [...], "sql": "..."}
            quota: 分配给该表结果的 Token 配额
            level: 压缩级别

        Returns:
            {"rows": [...], "columns": [...], "truncated": bool,
             "original_row_count": int, "kept_row_count": int}
        """
        rows = list(db_result.get("rows", []) or [])
        columns = list(db_result.get("columns", []) or [])
        original_row_count = len(rows)

        if not rows:
            return {
                "rows": [],
                "columns": columns,
                "truncated": False,
                "original_row_count": 0,
                "kept_row_count": 0,
            }

        if level in ("normal", "light"):
            kept_rows = rows
            kept_columns = columns
            truncated = False

        elif level == "moderate":
            max_rows = min(original_row_count, 50)
            kept_rows = rows[:max_rows]
            kept_columns = columns
            truncated = original_row_count > max_rows

        elif level == "severe":
            max_rows = min(original_row_count, 10)
            text_columns = self._identify_text_columns(rows, columns)
            kept_columns = [c for c in columns if c not in text_columns]
            if not kept_columns:
                kept_columns = columns
            kept_rows = rows[:max_rows]
            truncated = True

        else:  # compact
            kept_rows = rows[:1]  # 仅保留 1 行代表性样本
            kept_columns = columns
            truncated = True

        # 用 tiktoken 计行数 Token，如果超配额则进一步减少行数
        while len(kept_rows) > 1:
            text = self._format_compact_rows(kept_rows, kept_columns)
            if num_tokens_from_string(text) <= quota:
                break
            kept_rows = kept_rows[: max(1, len(kept_rows) // 2)]

        return {
            "rows": kept_rows,
            "columns": kept_columns,
            "truncated": truncated,
            "original_row_count": original_row_count,
            "kept_row_count": len(kept_rows),
        }

    def _fit_db_to_quota(
        self,
        rows: list[dict],
        columns: list[str],
        quota: int,
        original_rows: int,
    ) -> dict:
        """按 Token 配额逐步减少行数，直到不超过配额或只剩 1 行。"""
        kept = len(rows)
        while kept > 1:
            text = self._format_db_table(rows[:kept], columns)
            if num_tokens_from_string(text) <= quota:
                break
            kept = max(1, kept // 2)
        return {
            "text": self._format_db_table(rows[:kept], columns),
            "truncated": kept < original_rows,
            "original_rows": original_rows,
            "kept_rows": kept,
        }

    def _format_db_table(self, rows: list[dict], columns: list[str]) -> str:
        """格式化为 Markdown 表格。"""
        if not rows:
            return "（空结果）"
        header = "| " + " | ".join(columns) + " |"
        sep = "|" + "|".join(["---" for _ in columns]) + "|"
        body = "\n".join(
            "| " + " | ".join(str(row.get(c, "")) for c in columns) + " |"
            for row in rows
        )
        return f"{header}\n{sep}\n{body}"

    def _format_compact_rows(self, rows: list[dict], columns: list[str]) -> str:
        """紧凑行格式：col=val, col=val（每行一行）。"""
        lines = []
        for i, row in enumerate(rows):
            parts = [f"{c}={row.get(c, '')}" for c in columns]
            lines.append(f"[{i + 1}] {', '.join(parts)}")
        return "\n".join(lines)

    def _format_db_summary(self, rows: list[dict], columns: list[str]) -> str:
        """总结格式：行数 + 数值列统计 + 1 条样本。"""
        if not rows:
            return "（空结果）"
        parts = [f"共 {len(rows)} 行。"]
        numeric_cols = self._identify_numeric_columns(rows, columns)
        for nc in numeric_cols:
            values = [
                float(row[nc]) for row in rows if row.get(nc) is not None
            ]
            if values:
                parts.append(
                    f"{nc}: sum={sum(values):.2f}, avg={sum(values) / len(values):.2f}"
                )
        sample = rows[0]
        parts.append(
            f"样本: {self._format_compact_rows([sample], columns[:6])}"
        )
        return "\n".join(parts)

    @staticmethod
    def _identify_text_columns(
        rows: list[dict], columns: list[str]
    ) -> list[str]:
        """识别大文本列（平均长度 > 100 字符）。"""
        text_cols = []
        for col in columns:
            lengths = [
                len(str(row.get(col, "")))
                for row in rows
                if row.get(col) is not None
            ]
            if lengths and sum(lengths) / len(lengths) > 100:
                text_cols.append(col)
        return text_cols

    @staticmethod
    def _identify_numeric_columns(
        rows: list[dict], columns: list[str]
    ) -> list[str]:
        """识别数值列。"""
        numeric_cols = []
        for col in columns:
            for row in rows:
                val = row.get(col)
                if val is not None and isinstance(val, (int, float)):
                    numeric_cols.append(col)
                    break
        return numeric_cols

    def allocate_db_quota(
        self,
        table_results: list[dict],
        db_quota: int,
    ) -> list[int]:
        """对多个表查询结果分配 Token 配额。

        原则：
        1. 按行数加权分配（行数多的表数据量大，需要更多配额）
        2. 每个表至少保留 200 tokens（约 5 行紧凑格式）
        3. 仅 1 张表时得全部配额

        Args:
            table_results: 多个表的查询结果列表
            db_quota: DB 工具的总 Token 配额

        Returns:
            每个表对应的配额列表
        """
        if not table_results:
            return []
        if len(table_results) == 1:
            return [db_quota]

        row_counts = [len(t.get("rows", [])) for t in table_results]
        total_rows = sum(row_counts) or 1

        quotas = []
        for rc in row_counts:
            q = max(200, int(db_quota * rc / total_rows))
            quotas.append(q)

        # 调整使总和不超过 db_quota
        total_q = sum(quotas)
        if total_q > db_quota:
            scale = db_quota / total_q
            quotas = [max(200, int(q * scale)) for q in quotas]

        return quotas

    # ─── 逐条装配 + 实时 Token 监控 ───

    def assemble(
        self,
        evidences: list[dict],
        tool_token_counts: dict[str, int],
        locked_tokens: int = 0,
    ) -> tuple[str, bool, int]:
        """按 Token 预算逐条装配 Evidence 为 Prompt 文本。

        装配顺序：rag → db → web → report
        每组内按 _evidence_weight 降序
        逐条格式化，累计 Token 数，超过配额或剩余 < OUTPUT_RESERVE 时停止

        Args:
            evidences: 融合后的 Evidence 列表
            tool_token_counts: 各工具实际 Token 数（压缩后），用作装配配额
            locked_tokens: 已锁定的 Token（System + History）

        Returns:
            (assembled_text, compact_mode, total_tokens_used)
        """
        available = self.total_budget - locked_tokens

        if available <= OUTPUT_RESERVE_TOKENS:
            return self._compact_assemble(evidences, locked_tokens)

        # 按 source_type 分组
        groups: dict[str, list] = {}
        for ev in evidences:
            st = ev.get("source_type", "unknown")
            groups.setdefault(st, []).append(ev)

        # 组内按权重降序
        from agent.langgraph.evidence.fusion import _evidence_weight

        for st in groups:
            groups[st].sort(key=_evidence_weight, reverse=True)

        parts = ["【参考资料】"]
        compact_mode = False
        total_used = 0

        for st in SOURCE_ORDER:
            group = groups.get(st)
            if not group:
                continue
            quota = tool_token_counts.get(st, 0)
            tool_used = 0

            for ev in group:
                if tool_used >= quota:
                    break
                if (
                    total_used + locked_tokens
                    >= self.total_budget - OUTPUT_RESERVE_TOKENS
                ):
                    compact_mode = True
                    break

                content = self._format_evidence(ev, st)
                line_tokens = num_tokens_from_string(content)

                if tool_used + line_tokens > quota:
                    continue

                if (
                    total_used + line_tokens + locked_tokens
                    > self.total_budget - OUTPUT_RESERVE_TOKENS
                ):
                    compact_mode = True
                    break

                parts.append(content)
                tool_used += line_tokens
                total_used += line_tokens

        assembled = "\n".join(parts)
        return assembled, compact_mode, total_used

    def _format_evidence(self, ev: dict, source_type: str) -> str:
        """格式化单条 Evidence 为带编号的引用行。

        与 prompt_assembly._format_evidence_context 保持一致的编号风格：
        [n] (来源:st, 相关度:score) content
        """
        content = ev.get("content", "")
        title = ev.get("title", "")
        score = ev.get("relevance_score") or ev.get("confidence", 0.0)

        if source_type == "rag":
            return f"(相关度:{score:.2f}) {content}"
        elif source_type == "db":
            return f"(来源:db, 相关度:{score:.2f}) [{title}] {content}"
        elif source_type == "web":
            return f"(来源:web, 相关度:{score:.2f}) [{title}] {content}"
        else:
            return f"(来源:{source_type}, 相关度:{score:.2f}) {content}"

    # ─── 精简模式兜底 ───

    def _compact_assemble(
        self, evidences: list[dict], locked_tokens: int
    ) -> tuple[str, bool, int]:
        """精简模式：仅保留 top 3 标题 + 首句。"""
        from agent.langgraph.evidence.fusion import _evidence_weight

        if not evidences:
            return "（无足够上下文，请直接回答）", True, 0

        sorted_evs = sorted(evidences, key=_evidence_weight, reverse=True)[:3]
        parts = ["【精简参考资料】"]
        for i, ev in enumerate(sorted_evs, 1):
            title = ev.get("title", "")
            content = ev.get("content", "")
            source_type = ev.get("source_type", "")
            first_sentence = (
                content.split("。")[0][:200] if content else ""
            )
            parts.append(
                f"[{i}] ({source_type}) {title}: {first_sentence}..."
            )

        text = "\n".join(parts)
        return text, True, num_tokens_from_string(text)


def _detect_active_tools(state: dict) -> list[str]:
    """检测当前请求激活了哪些工具。

    从 AgentState 中读取 tool_results 或分散字段判断。

    Args:
        state: AgentState dict

    Returns:
        激活的工具列表，如 ["rag", "db", "web"]
    """
    active = []

    # 优先从 tool_results 检测（计划执行器路径）
    tool_results = state.get("tool_results", []) or []
    if tool_results:
        for tr in tool_results:
            tool = tr.get("tool", "")
            if tool in ("rag", "rag_search") and "rag" not in active:
                active.append("rag")
            elif tool in ("database", "db", "db_query") and "db" not in active:
                active.append("db")
            elif tool in ("web", "web_search") and "web" not in active:
                active.append("web")
        if active:
            return active

    # 回退到分散字段
    if state.get("rag_docs"):
        active.append("rag")
    if state.get("db_result") and state["db_result"].get("rows"):
        active.append("db")
    if state.get("web_docs"):
        active.append("web")

    return active or ["rag"]