"""ReportTool 主入口。

按 docs/报告生成Tool设计.md 第 3-7 节设计：
串联：
1. Input Validator（输入校验）
2. Evidence Readiness Check（证据充分性检查）
3. Report Planner（章节规划）
4. Section Generator / Chart Builder / Table Builder（内容生成）
5. Report Verifier（验证器）
6. Report Exporter（导出 Markdown / HTML）
7. Artifact Storage（产物存储）

错误码体系（docs/报告生成Tool设计.md 第 11.1 节）：
- REPORT_INPUT_INVALID
- REPORT_EVIDENCE_INSUFFICIENT
- REPORT_PLAN_FAILED
- REPORT_SECTION_GENERATION_FAILED
- REPORT_CHART_BUILD_FAILED
- REPORT_TABLE_BUILD_FAILED
- REPORT_VERIFICATION_FAILED
- REPORT_EXPORT_FAILED
- REPORT_STORAGE_FAILED
- REPORT_PERMISSION_DENIED
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Callable, Optional

from agent.langgraph.skills import (
    DataSkill,
    RetrievalSkill,
    ResolvedSkillSet,
    SkillRegistry,
    SkillResolver,
)
from agent.langgraph.skills.models import SkillResolveContext
from agent.langgraph.tools.report.chart_builder import ChartBuilder
from agent.langgraph.tools.report.exporter import ReportExporter
from agent.langgraph.tools.report.generator import SectionGenerator
from agent.langgraph.tools.report.models import (
    ReportArtifact,
    ReportToolInput,
    ReportToolOutput,
)
from agent.langgraph.tools.report.planner import ReportPlanner
from agent.langgraph.tools.report.storage import (
    LocalArtifactStorage,
    generate_report_id,
)
from agent.langgraph.tools.report.table_builder import TableBuilder
from agent.langgraph.tools.report.templates import get_default_title
from agent.langgraph.tools.report.verifier import ReportVerifier

logger = logging.getLogger(__name__)


# ========== 错误码定义 ==========
REPORT_INPUT_INVALID = "REPORT_INPUT_INVALID"
REPORT_EVIDENCE_INSUFFICIENT = "REPORT_EVIDENCE_INSUFFICIENT"
REPORT_PLAN_FAILED = "REPORT_PLAN_FAILED"
REPORT_SECTION_GENERATION_FAILED = "REPORT_SECTION_GENERATION_FAILED"
REPORT_CHART_BUILD_FAILED = "REPORT_CHART_BUILD_FAILED"
REPORT_TABLE_BUILD_FAILED = "REPORT_TABLE_BUILD_FAILED"
REPORT_VERIFICATION_FAILED = "REPORT_VERIFICATION_FAILED"
REPORT_EXPORT_FAILED = "REPORT_EXPORT_FAILED"
REPORT_STORAGE_FAILED = "REPORT_STORAGE_FAILED"
REPORT_PERMISSION_DENIED = "REPORT_PERMISSION_DENIED"


# 允许的 report_type 与 report_format
ALLOWED_REPORT_TYPES = {
    "business_analysis",
    "quality_analysis",
    "operations_analysis",
    "factory_performance",
    "delivery_analysis",
    "production_daily",
    "cost_analysis",
    "trend_analysis",
    "comparison",
    "knowledge_summary",
    "management_briefing",
}
ALLOWED_FORMATS_STAGE1 = {"markdown", "html"}


class ReportTool:
    """Report Tool 主类。

    作为受控工具接入 Plan Executor 调度体系，
    仅基于已收集 evidence 生成结构化报告，**不自行访问数据源**。
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        llm_callable: Optional[Callable] = None,
        storage: Optional[LocalArtifactStorage] = None,
    ):
        """初始化 ReportTool。

        Args:
            config: 配置字典
            llm_callable: LLM 调用函数（用于 Section Generator）
            storage: 存储后端（默认本地）
        """
        self._config = config or {}
        self._planner = ReportPlanner(self._config)
        self._section_generator = SectionGenerator(llm_callable=llm_callable)
        self._chart_builder = ChartBuilder()
        self._table_builder = TableBuilder(
            max_rows=self._config.get("max_table_rows", 100)
        )
        self._verifier = ReportVerifier(self._config)
        self._exporter = ReportExporter()
        self._storage = storage or LocalArtifactStorage(
            root_path=self._config.get("storage_root", "./reports"),
            download_base_url=self._config.get("download_base_url", "/api/v1/reports"),
        )

    async def invoke(self, input_data: ReportToolInput) -> ReportToolOutput:
        """ReportTool 主入口。

        按 docs/报告生成Tool设计.md 第 3.2 节数据流执行：
        1. 输入校验
        2. 证据充分性检查
        3. 章节规划
        4. 内容生成（sections + charts + tables）
        5. 验证
        6. 导出
        7. 存储
        8. 返回 ReportToolOutput

        Args:
            input_data: ReportToolInput

        Returns:
            ReportToolOutput
        """
        start_time = time.time()
        generation_start = start_time

        # ========== Step 1: Input Validation ==========
        effective_input = dict(input_data)
        skill_set, skill_error = self._resolve_skill_set(effective_input)
        if skill_error:
            return self._error_output(skill_error, REPORT_INPUT_INVALID, start_time)
        if skill_set is not None:
            effective_input.setdefault("report_type", skill_set.report_skill.report_type)
            effective_input.setdefault(
                "format", skill_set.report_skill.default_format or "markdown"
            )

        validation_error = self._validate_input(effective_input)
        if validation_error:
            return self._error_output(
                validation_error,
                REPORT_INPUT_INVALID,
                start_time,
            )

        # ========== Step 2: Evidence Readiness Check ==========
        evidence = effective_input.get("evidence", []) or []
        min_evidence = self._config.get("min_evidence_count", 1)
        if skill_set is not None:
            min_evidence = max(
                min_evidence, skill_set.report_skill.min_evidence_count or 0
            )
            missing_evidence = self._missing_required_evidence(skill_set, evidence)
            if missing_evidence:
                return self._skill_evidence_error(
                    skill_set,
                    missing_evidence,
                    start_time,
                )
        if len(evidence) < min_evidence:
            return self._error_output(
                f"证据不足：当前 {len(evidence)} 条，期望至少 {min_evidence} 条",
                REPORT_EVIDENCE_INSUFFICIENT,
                start_time,
            )

        # ========== Step 3: Plan ==========
        try:
            title = effective_input.get("title") or get_default_title(
                effective_input["report_type"]
            )
            plan = (
                self._planner.plan_from_skill(
                    report_skill=skill_set.report_skill,
                    title=title,
                    evidence=evidence,
                    objective=effective_input.get("objective", ""),
                    max_sections=effective_input.get("max_sections"),
                )
                if skill_set is not None
                else self._planner.plan(
                    report_type=effective_input["report_type"],
                    title=title,
                    evidence=evidence,
                    objective=effective_input.get("objective", ""),
                    max_sections=effective_input.get("max_sections"),
                )
            )
        except Exception as e:
            logger.error(f"[ReportTool] 章节规划失败: {e}")
            return self._error_output(
                f"章节规划失败: {e}",
                REPORT_PLAN_FAILED,
                start_time,
            )

        # ========== Step 4: Generate Content ==========
        try:
            sections, failed_sections = await self._section_generator.generate_sections(
                plan=plan,
                evidence=evidence,
            )
        except Exception as e:
            logger.error(f"[ReportTool] 章节生成失败: {e}")
            return self._error_output(
                f"章节生成失败: {e}",
                REPORT_SECTION_GENERATION_FAILED,
                start_time,
            )

        # 设置章节 order
        for idx, section in enumerate(sections):
            section["order"] = idx

        # 图表
        charts, failed_charts = self._chart_builder.build(
            evidence_list=evidence,
            max_charts=effective_input.get("max_charts", 6),
        )

        # 表格
        tables, failed_tables = self._table_builder.build(
            evidence_list=evidence,
            max_tables=effective_input.get("max_tables", 8),
        )
        if skill_set is not None:
            self._apply_metric_definitions(
                charts, tables, skill_set.report_skill.metric_definitions
            )

        # ========== Step 5: Build Artifact ==========
        report_id = generate_report_id()
        now_iso = datetime.utcnow().isoformat() + "Z"

        artifact: ReportArtifact = {
            "report_id": report_id,
            "title": plan["title"],
            "report_type": effective_input["report_type"],
            "format": effective_input.get("format", "markdown"),
            "language": effective_input.get("language", "zh_CN"),
            "objective": effective_input.get("objective", ""),
            "audience": effective_input.get("audience", ""),
            "summary": self._build_summary(sections),
            "sections": sections,
            "charts": charts,
            "tables": tables,
            "appendix": [],
            "evidence_refs": self._collect_evidence_refs(sections, charts, tables),
            "source_summary": [],
            "verification_result": {},
            "metadata": {
                "generation_time_ms": int((time.time() - generation_start) * 1000),
                "section_count": len(sections),
                "chart_count": len(charts),
                "table_count": len(tables),
                "skill_id": skill_set.report_skill.skill_id if skill_set else None,
                "skill_version": skill_set.report_skill.version if skill_set else None,
            },
            "tenant_id": effective_input.get("tenant_id", ""),
            "user_id": effective_input.get("user_id", ""),
            "created_at": now_iso,
            "file_uri": "",
            "file_size_bytes": 0,
            "partial": bool(failed_sections),
            "failed_sections": [s.get("section_id", "") for s in failed_sections],
            "coverage_score": self._compute_coverage(sections, plan),
        }

        generation_time_ms = int((time.time() - generation_start) * 1000)

        # ========== Step 6: Verify ==========
        scan_sensitive = self._config.get("safety", {}).get("scan_sensitive_data", True)
        verification_result = self._verifier.verify(
            artifact=artifact,
            evidence=evidence,
            max_sections=effective_input.get("max_sections", 10),
            max_charts=effective_input.get("max_charts", 6),
            max_tables=effective_input.get("max_tables", 8),
            scan_sensitive=scan_sensitive,
            claim_rules=skill_set.report_skill.claim_rules if skill_set else None,
            publish_policy=skill_set.report_skill.publish_policy if skill_set else None,
            core_sections=skill_set.report_skill.core_sections if skill_set else None,
        )
        artifact["verification_result"] = verification_result

        # ========== Step 7: Check Verification ==========
        require_verified = self._config.get("safety", {}).get(
            "require_verified_evidence", True
        )
        if require_verified and not verification_result["passed"]:
            # 验证失败 → 拒绝导出
            logger.warning(
                f"[ReportTool] 报告 {report_id} 验证未通过: "
                f"score={verification_result['score']}, issues={len(verification_result['issues'])}"
            )
            return {
                "success": False,
                "error_message": "报告验证未通过，无法导出",
                "error_code": REPORT_VERIFICATION_FAILED,
                "artifact": artifact,
                "summary": "报告生成过程中发现部分数字无法与数据来源匹配，当前未导出正式报告。",
                "quality_score": verification_result["score"],
                "section_count": len(sections),
                "chart_count": len(charts),
                "table_count": len(tables),
                "evidence_count": len(evidence),
                "generation_time_ms": generation_time_ms,
                "export_time_ms": 0,
                "file_uri": "",
                "download_url": "",
                "partial": True,
                "publish_mode": "partial",
                "missing_required_evidence": [],
            }

        # ========== Step 8: Export ==========
        export_start = time.time()
        try:
            content, extension = self._exporter.export(
                artifact=artifact,
                format=artifact["format"],
            )
        except Exception as e:
            logger.error(f"[ReportTool] 导出失败: {e}")
            return {
                "success": False,
                "error_message": f"导出失败: {e}",
                "error_code": REPORT_EXPORT_FAILED,
                "artifact": artifact,
                "summary": "报告生成成功但导出失败。",
                "quality_score": verification_result["score"],
                "section_count": len(sections),
                "chart_count": len(charts),
                "table_count": len(tables),
                "evidence_count": len(evidence),
                "generation_time_ms": generation_time_ms,
                "export_time_ms": 0,
                "file_uri": "",
                "download_url": "",
                "partial": True,
                "publish_mode": "partial",
                "missing_required_evidence": [],
            }
        export_time_ms = int((time.time() - export_start) * 1000)

        # ========== Step 9: Store ==========
        try:
            storage_result = self._storage.save(
                tenant_id=artifact["tenant_id"] or "unknown",
                report_id=report_id,
                content=content,
                extension=extension,
            )
            artifact["file_uri"] = storage_result["file_uri"]
            artifact["file_size_bytes"] = storage_result["file_size_bytes"]
        except Exception as e:
            logger.error(f"[ReportTool] 存储失败: {e}")
            return {
                "success": True,  # 内容已生成
                "error_message": f"存储失败: {e}",
                "error_code": REPORT_STORAGE_FAILED,
                "artifact": artifact,
                "summary": "报告生成成功但存储失败，可内联查看。",
                "quality_score": verification_result["score"],
                "section_count": len(sections),
                "chart_count": len(charts),
                "table_count": len(tables),
                "evidence_count": len(evidence),
                "generation_time_ms": generation_time_ms,
                "export_time_ms": export_time_ms,
                "file_uri": "",
                "download_url": "",
                "partial": True,
                "publish_mode": "partial",
                "missing_required_evidence": [],
            }

        # ========== Step 10: Success Output ==========
        return {
            "success": True,
            "error_message": "",
            "error_code": "",
            "artifact": artifact,
            "summary": artifact["summary"],
            "quality_score": verification_result["score"],
            "section_count": len(sections),
            "chart_count": len(charts),
            "table_count": len(tables),
            "evidence_count": len(evidence),
            "generation_time_ms": generation_time_ms,
            "export_time_ms": export_time_ms,
            "file_uri": storage_result["file_uri"],
            "download_url": storage_result["download_url"],
            "partial": artifact.get("partial", False),
            "publish_mode": "partial" if artifact.get("partial", False) else "full",
            "missing_required_evidence": [],
        }

    # ========== 辅助方法 ==========
    def _resolve_skill_set(
        self, input_data: dict[str, Any]
    ) -> tuple[ResolvedSkillSet | None, str | None]:
        """Resolve explicit report skill input without changing legacy requests."""
        supplied = input_data.get("skill_set")
        if supplied is not None:
            try:
                skill_set = (
                    supplied
                    if isinstance(supplied, ResolvedSkillSet)
                    else ResolvedSkillSet.model_validate(supplied)
                )
            except (TypeError, ValueError) as exc:
                return None, f"无效的 skill_set: {exc}"
        elif not any(
            input_data.get(key)
            for key in ("skill_id", "data_skill_id", "retrieval_skill_id")
        ):
            return None, None
        else:
            registry = SkillRegistry()
            result = SkillResolver(registry).resolve(
                SkillResolveContext(
                    user_question=input_data.get("objective", ""),
                    tenant_id=input_data.get("tenant_id", ""),
                    skill_id=input_data.get("skill_id"),
                    report_type=input_data.get("report_type"),
                    route_decision=input_data.get("route_decision"),
                )
            )
            if not result.resolved or result.skill_set is None:
                return None, result.reason
            skill_set = result.skill_set

            data_skill_id = input_data.get("data_skill_id")
            if data_skill_id:
                data_skill = registry.get_by_id(data_skill_id)
                if not isinstance(data_skill, DataSkill):
                    return None, f"DATA_SKILL_NOT_FOUND: {data_skill_id}"
                skill_set = skill_set.model_copy(update={"data_skill": data_skill})
            retrieval_skill_id = input_data.get("retrieval_skill_id")
            if retrieval_skill_id:
                retrieval_skill = registry.get_by_id(retrieval_skill_id)
                if not isinstance(retrieval_skill, RetrievalSkill):
                    return None, f"RETRIEVAL_SKILL_NOT_FOUND: {retrieval_skill_id}"
                skill_set = skill_set.model_copy(
                    update={"retrieval_skill": retrieval_skill}
                )
        return skill_set, None

    @staticmethod
    def _missing_required_evidence(
        skill_set: ResolvedSkillSet, evidence: list[dict]
    ) -> list[str]:
        """Return IDs of required skill evidence not represented in input evidence."""
        requirements: list[dict] = []
        if skill_set.data_skill is not None:
            requirements.extend(skill_set.data_skill.evidence_requirements)
        if skill_set.retrieval_skill is not None:
            requirements.extend(skill_set.retrieval_skill.evidence_requirements)

        missing: list[str] = []
        for requirement in requirements:
            if not requirement.get("required", False):
                continue
            evidence_id = requirement.get("evidence_id", "")
            target_id = requirement.get("source_target_id")
            table_name = requirement.get("table_name")
            found = any(
                item.get("evidence_id") == evidence_id
                or (
                    target_id is not None
                    and (item.get("metadata") or {}).get("source_target_id") == target_id
                )
                or (
                    table_name is not None
                    and (item.get("metadata") or {}).get("table_name") == table_name
                )
                for item in evidence
            )
            if not found:
                missing.append(evidence_id)

        report_skill = skill_set.report_skill
        source_counts = {
            source_type: sum(
                item.get("source_type") == source_type for item in evidence
            )
            for source_type in ("db", "rag")
        }
        if source_counts["db"] < (report_skill.min_db_evidence_count or 0):
            missing.append("minimum_db_evidence")
        if source_counts["rag"] < (report_skill.min_rag_evidence_count or 0):
            missing.append("minimum_rag_evidence")
        return list(dict.fromkeys(missing))

    def _skill_evidence_error(
        self,
        skill_set: ResolvedSkillSet,
        missing_evidence: list[str],
        start_time: float,
    ) -> ReportToolOutput:
        """Block full publication when the skill's required evidence is unavailable."""
        policy = skill_set.report_skill.publish_policy
        publish_mode = (
            "summary_only"
            if policy.get("allow_summary_only", False)
            else "partial"
            if policy.get("allow_partial", False)
            else "partial"
        )
        result = self._error_output(
            f"Skill '{skill_set.report_skill.skill_id}' 缺少必需证据: {', '.join(missing_evidence)}",
            REPORT_EVIDENCE_INSUFFICIENT,
            start_time,
        )
        result.update(
            {
                "partial": True,
                "publish_mode": publish_mode,
                "missing_required_evidence": missing_evidence,
            }
        )
        return result

    @staticmethod
    def _apply_metric_definitions(
        charts: list[dict], tables: list[dict], metric_definitions: list[dict]
    ) -> None:
        """Annotate visual artifacts with skill metrics backed by their columns."""
        for artifact in [*charts, *tables]:
            fields = set(artifact.get("columns", []))
            fields.update(
                field
                for series in artifact.get("series", [])
                for field in [series.get("name")]
                if field
            )
            metric_ids = [
                metric["metric_id"]
                for metric in metric_definitions
                if set(metric.get("source_fields", [])).intersection(fields)
            ]
            if metric_ids:
                artifact["metric_ids"] = metric_ids

    def _validate_input(self, input_data: ReportToolInput) -> str | None:
        """校验输入参数。

        Returns:
            str: 错误信息（无错误返回 None）
        """
        if not input_data:
            return "input_data 不能为空"

        # report_type
        report_type = input_data.get("report_type")
        if not report_type:
            return "report_type 不能为空"
        if report_type not in ALLOWED_REPORT_TYPES:
            return f"不支持的 report_type: {report_type}"

        # format
        fmt = input_data.get("format", "markdown")
        if fmt not in ALLOWED_FORMATS_STAGE1:
            return f"阶段一不支持的报告格式: {fmt}（仅支持 markdown / html）"

        # tenant_id
        if not input_data.get("tenant_id"):
            return "tenant_id 不能为空"

        return None

    def _error_output(
        self,
        error_message: str,
        error_code: str,
        start_time: float,
    ) -> ReportToolOutput:
        """构造错误输出。"""
        return {
            "success": False,
            "error_message": error_message,
            "error_code": error_code,
            "artifact": {},
            "summary": "",
            "quality_score": 0.0,
            "section_count": 0,
            "chart_count": 0,
            "table_count": 0,
            "evidence_count": 0,
            "generation_time_ms": int((time.time() - start_time) * 1000),
            "export_time_ms": 0,
            "file_uri": "",
            "download_url": "",
            "partial": False,
            "publish_mode": "full",
            "missing_required_evidence": [],
        }

    def _build_summary(self, sections: list[dict]) -> str:
        """基于章节内容生成报告摘要（前 200 字）。"""
        if not sections:
            return "报告章节未生成。"

        texts: list[str] = []
        for sec in sections:
            content = (sec.get("content") or "").strip()
            if content:
                # 取首段
                first_para = content.split("\n\n")[0]
                texts.append(first_para)
        combined = "\n".join(texts)
        if len(combined) > 500:
            combined = combined[:500] + "…"
        return combined

    def _collect_evidence_refs(
        self,
        sections: list[dict],
        charts: list[dict],
        tables: list[dict],
    ) -> list[str]:
        """收集所有 evidence_refs（去重、保序）。"""
        refs: list[str] = []
        seen: set[str] = set()
        for sec in sections:
            for ref in sec.get("evidence_refs", []) or []:
                if ref and ref not in seen:
                    seen.add(ref)
                    refs.append(ref)
        for chart in charts:
            for ref in chart.get("evidence_refs", []) or []:
                if ref and ref not in seen:
                    seen.add(ref)
                    refs.append(ref)
        for table in tables:
            for ref in table.get("evidence_refs", []) or []:
                if ref and ref not in seen:
                    seen.add(ref)
                    refs.append(ref)
        return refs

    def _compute_coverage(self, sections: list[dict], plan: dict) -> float:
        """计算报告覆盖率。"""
        plan_sections = plan.get("sections", []) or []
        if not plan_sections:
            return 0.0
        ready = sum(1 for s in plan_sections if s.get("evidence_ready"))
        return round(ready / len(plan_sections), 2)


# 全局默认实例
_default_report_tool: ReportTool | None = None


def get_report_tool(
    config: dict[str, Any] | None = None,
    llm_callable: Optional[Callable] = None,
) -> ReportTool:
    """获取 ReportTool 默认实例（单例）。"""
    global _default_report_tool
    if _default_report_tool is None:
        _default_report_tool = ReportTool(config=config, llm_callable=llm_callable)
    return _default_report_tool
