"""Section Generator（章节内容生成器）。

按 docs/报告生成Tool设计.md 第 6.6 节设计：
- 输入：ReportPlan 章节定义 + evidence 列表
- 输出：ReportSection 列表（含 content、evidence_refs、confidence）
- 职责：基于 evidence 生成章节内容，**严格引用 evidence_id**

设计原则（避免幻觉）：
- LLM 输出受 evidence 约束（prompt 提示）
- 每个章节至少引用 1 个 evidence_id
- LLM 失败时降级为基于 evidence 的模板化内容
- 章节级 confidence 评估
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from agent.langgraph.tools.report.models import ReportSection, SectionType

logger = logging.getLogger(__name__)


# ========== 章节生成 prompt 模板 ==========
SECTION_GENERATION_PROMPT = """你是一名专业的报告章节生成助手。请基于以下 evidence 生成章节正文。

**章节信息**：
- 章节 ID：{section_id}
- 章节标题：{title}
- 章节类型：{section_type}
- 章节目标：{objective}

**可用 Evidence**（必须严格引用，禁止编造）：
{evidence_text}

**输出要求**（JSON 格式）：
{{
  "content": "章节正文（中文/英文，2-4 段，引用 evidence 事实，禁止编造）",
  "evidence_refs": ["ev_xxx", "ev_yyy"],
  "confidence": 0.85
}}

**重要约束**：
1. 每个论断必须基于 evidence，禁止凭空编造数字
2. evidence_refs 必须从上述 evidence_id 列表中选取
3. confidence 表示内容可信度（0.0-1.0）
4. 如果 evidence 不足，content 说明"证据不足"并 confidence 低于 0.5
"""


def _format_evidence_for_prompt(evidence: list[dict]) -> str:
    """将 evidence 格式化为 prompt 文本。"""
    if not evidence:
        return "（无 evidence）"

    lines = []
    for ev in evidence:
        ev_id = ev.get("evidence_id", "ev_unknown")
        title = ev.get("title", "")
        content = ev.get("content", "")[:500]
        lines.append(f"- [{ev_id}] {title}\n  {content}")

    return "\n".join(lines)


class SectionGenerator:
    """章节内容生成器。

    支持两种生成模式：
    1. LLM 模式：调用 LLM 生成章节内容（推荐）
    2. 模板模式：基于 evidence 直接拼接（LLM 不可用时降级）
    """

    def __init__(
        self,
        llm_callable: Optional[Callable] = None,
    ):
        """初始化生成器。

        Args:
            llm_callable: LLM 调用函数，签名 async def(prompt: str) -> str
                          如果为 None，使用模板模式
        """
        self._llm_callable = llm_callable

    async def generate_sections(
        self,
        plan: dict[str, Any],
        evidence: list[dict],
    ) -> tuple[list[ReportSection], list[dict]]:
        """生成所有章节。

        Args:
            plan: ReportPlan（含 sections 列表）
            evidence: 全部 evidence 列表

        Returns:
            tuple: (sections, failed_sections)
                - sections: 成功生成的 ReportSection 列表
                - failed_sections: 失败的章节定义
        """
        sections: list[ReportSection] = []
        failed: list[dict] = []

        plan_sections = plan.get("sections", []) or []

        for plan_section in plan_sections:
            try:
                section = await self._generate_one(plan_section, evidence)
                sections.append(section)
            except Exception as e:
                logger.warning(
                    f"[SectionGenerator] 章节 {plan_section.get('section_id')} 生成失败: {e}"
                )
                failed.append(
                    {
                        "section_id": plan_section.get("section_id"),
                        "title": plan_section.get("title"),
                        "error": str(e),
                    }
                )

        logger.info(
            f"[SectionGenerator] 生成完成: success={len(sections)}, failed={len(failed)}"
        )
        return sections, failed

    async def _generate_one(
        self,
        plan_section: dict[str, Any],
        evidence: list[dict],
    ) -> ReportSection:
        """生成单个章节。"""
        section_id = plan_section.get("section_id", "unknown")
        title = plan_section.get("title", "")
        section_type = plan_section.get("section_type", "analysis")
        objective = plan_section.get("objective", "")

        # 筛选本章节相关的 evidence
        required_types = set(plan_section.get("required_evidence_types", []))
        relevant_evidence = [
            ev for ev in evidence
            if not required_types or ev.get("source_type") in required_types
        ]

        if self._llm_callable:
            return await self._generate_with_llm(
                section_id, title, section_type, objective, relevant_evidence
            )
        else:
            return self._generate_with_template(
                section_id, title, section_type, relevant_evidence
            )

    async def _generate_with_llm(
        self,
        section_id: str,
        title: str,
        section_type: str,
        objective: str,
        evidence: list[dict],
    ) -> ReportSection:
        """使用 LLM 生成章节。"""
        evidence_text = _format_evidence_for_prompt(evidence)
        prompt = SECTION_GENERATION_PROMPT.format(
            section_id=section_id,
            title=title,
            section_type=section_type,
            objective=objective or "无",
            evidence_text=evidence_text,
        )

        # 调用 LLM
        result_text = await self._llm_callable(prompt)

        # 解析 JSON 输出
        result = self._parse_llm_output(result_text, section_id, evidence)

        return {
            "section_id": section_id,
            "title": title,
            "order": -1,  # 由 caller 设置
            "section_type": section_type,
            "content": result["content"],
            "evidence_refs": result["evidence_refs"],
            "chart_refs": [],
            "table_refs": [],
            "confidence": result.get("confidence", 0.7),
        }

    def _generate_with_template(
        self,
        section_id: str,
        title: str,
        section_type: str,
        evidence: list[dict],
    ) -> ReportSection:
        """基于模板生成章节（LLM 不可用时降级）。

        输出结构：
        - content: "根据 X 条 evidence，" + 拼接 evidence 摘要
        - evidence_refs: 所有 evidence_id
        - confidence: 0.5（无 LLM 评估，保守）
        """
        if not evidence:
            content = f"证据不足，无法生成「{title}」章节的详细内容。"
            refs: list[str] = []
            confidence = 0.3
        else:
            refs = [ev.get("evidence_id", "") for ev in evidence if ev.get("evidence_id")]
            evidence_summary = "；".join(
                f"[{ev.get('evidence_id', '')}] {ev.get('title', '')}: {(ev.get('content', '') or '')[:200]}"
                for ev in evidence[:5]
            )
            content = (
                f"根据 {len(evidence)} 条 evidence，{title} 的关键发现如下：\n"
                f"{evidence_summary}\n\n"
                f"（注：本章节由模板生成，未使用 LLM 优化表达）"
            )
            confidence = 0.5

        return {
            "section_id": section_id,
            "title": title,
            "order": -1,
            "section_type": section_type,
            "content": content,
            "evidence_refs": refs,
            "chart_refs": [],
            "table_refs": [],
            "confidence": confidence,
        }

    def _parse_llm_output(
        self,
        result_text: str,
        section_id: str,
        evidence: list[dict],
    ) -> dict[str, Any]:
        """解析 LLM JSON 输出，容错处理。

        支持：
        - 标准 JSON
        - ```json``` 代码块
        - 裸文本（降级为 content）
        """
        import json
        import re

        text = result_text.strip()

        # 1. 尝试提取 ```json ... ``` 代码块
        code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if code_block:
            text = code_block.group(1)

        # 2. 尝试提取最外层 {...}
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            text = json_match.group(0)

        try:
            data = json.loads(text)
            content = data.get("content", "")
            refs = data.get("evidence_refs", [])
            confidence = data.get("confidence", 0.7)
        except json.JSONDecodeError:
            # 3. 降级：把整段文本作为 content
            content = result_text
            refs = [ev.get("evidence_id", "") for ev in evidence if ev.get("evidence_id")]
            confidence = 0.5

        # 校验 evidence_refs 必须在 evidence 列表中存在
        valid_ev_ids = {ev.get("evidence_id") for ev in evidence}
        refs = [r for r in refs if r in valid_ev_ids]

        # 至少留 1 个 reference
        if not refs and evidence:
            refs = [evidence[0].get("evidence_id", "")]

        return {
            "content": content,
            "evidence_refs": refs,
            "confidence": float(confidence) if confidence is not None else 0.5,
        }
