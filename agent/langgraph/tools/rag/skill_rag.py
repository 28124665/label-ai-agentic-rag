"""RetrievalSkill-guided preparation and execution for :mod:`rag_tool`."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, TypedDict

from agent.langgraph.skills import RetrievalSkill, SkillRegistry
from agent.langgraph.tools.rag_tool import RAGToolInput, RAGToolOutput, get_rag_tool

_TEMPLATE_VARIABLE_PATTERN = re.compile(r"{([^{}]+)}")


class SkillRAGInput(TypedDict, total=False):
    """Input accepted by the RetrievalSkill-constrained RAG path."""

    query: str
    kb_ids: list[str]
    tenant_id: str
    retrieval_skill_id: str
    rag_target_id: str
    query_template_id: str
    query_template_ids: list[str]
    document_filters: dict[str, Any]
    metadata_filters: dict[str, Any]
    top_k: int
    section_id: str
    time_range: dict[str, Any]
    report_date: str
    context: dict[str, Any]


class SkillRAGContextError(ValueError):
    """Raised when declared RetrievalSkill context is unavailable."""


@dataclass(frozen=True)
class PreparedSkillRAGRequest:
    """A single RAG request constrained to one declared skill target."""

    target_id: str
    input_data: RAGToolInput


class SkillRAGExecutor:
    """Resolve RetrievalSkill configuration before invoking the legacy RAGTool."""

    def __init__(self, registry: SkillRegistry | None = None) -> None:
        self._registry = registry or SkillRegistry()

    def prepare(
        self, input_data: SkillRAGInput
    ) -> PreparedSkillRAGRequest | None:
        """Build an immutable, skill-constrained RAG request.

        ``kb_ids`` and ``top_k`` supplied by a caller are intentionally ignored:
        the selected ``rag_target`` is their sole authority.
        """
        retrieval_skill_id = input_data.get("retrieval_skill_id")
        if not retrieval_skill_id:
            raise ValueError("retrieval_skill_id is required for skill-guided RAG")
        skill = self._registry.get_by_id(retrieval_skill_id)
        if not isinstance(skill, RetrievalSkill):
            raise ValueError(f"RetrievalSkill not found: {retrieval_skill_id}")

        context = self._build_context(input_data)
        self._require_skill_context(skill, context)
        target = self._select_target(skill, input_data.get("rag_target_id"))
        query = self._build_query(skill, target, input_data, context)
        if query is None:
            return None

        rag_input = RAGToolInput(
            query=query,
            tenant_id=str(context.get("tenant_id", "")),
            kb_ids=[str(target["kb_id"])],
            top_k=int(target.get("top_k", 5)),
            document_filters=self._render_value(
                target.get("document_filters", {}), context
            ),
            metadata_filters=self._render_value(
                target.get("metadata_filters", {}), context
            ),
            # ★ §5.9 补齐：从 skill target 声明读取此前遗漏的参数
            # skill target 声明 > 内置默认值
            similarity_threshold=float(target.get("similarity_threshold", 0.2)),
            keywords_similarity_weight=float(
                target.get("keywords_similarity_weight", 0.5)
            ),
            rerank_id=str(target.get("rerank_id", "")),
        )
        return PreparedSkillRAGRequest(str(target["target_id"]), rag_input)

    async def invoke(self, input_data: SkillRAGInput) -> RAGToolOutput:
        """Execute a prepared request, returning empty output for skipped targets."""
        prepared = self.prepare(input_data)
        if prepared is None:
            return self.skipped_result()
        return await self.invoke_prepared(prepared)

    async def invoke_prepared(
        self, prepared: PreparedSkillRAGRequest
    ) -> RAGToolOutput:
        """Invoke RAGTool with an already validated constrained request."""
        result = await get_rag_tool().invoke(prepared.input_data)
        return self._apply_filters(result, prepared.input_data)

    @staticmethod
    def _build_context(input_data: SkillRAGInput) -> dict[str, Any]:
        context = input_data.get("context", {})
        if not isinstance(context, dict):
            raise ValueError("SkillRAGInput.context must be a dictionary")
        return {**context, **input_data}

    @staticmethod
    def _require_skill_context(
        skill: RetrievalSkill, context: dict[str, Any]
    ) -> None:
        required = skill.query_context_requirements.get("required_context", [])
        missing = [name for name in required if not context.get(name)]
        if missing:
            raise SkillRAGContextError(
                f"Missing required RetrievalSkill context for "
                f"'{skill.skill_id}': {', '.join(sorted(missing))}"
            )

    @staticmethod
    def _select_target(
        skill: RetrievalSkill, requested_target_id: str | None
    ) -> dict[str, Any]:
        targets = {
            str(target.get("target_id")): target for target in skill.rag_targets
        }
        if requested_target_id:
            target = targets.get(requested_target_id)
            if target is None:
                raise ValueError(
                    f"RAG target '{requested_target_id}' is not declared by "
                    f"RetrievalSkill '{skill.skill_id}'"
                )
            return target
        required_targets = [
            target for target in skill.rag_targets if target.get("required")
        ]
        if len(required_targets) == 1:
            return required_targets[0]
        raise ValueError(
            f"rag_target_id is required for RetrievalSkill '{skill.skill_id}'"
        )

    def _build_query(
        self,
        skill: RetrievalSkill,
        target: dict[str, Any],
        input_data: SkillRAGInput,
        context: dict[str, Any],
    ) -> str | None:
        templates = self._select_templates(target, input_data)
        if not templates:
            query = str(input_data.get("query", "")).strip()
            if query:
                return query
            return self._missing_template_context(skill, target, ["query"])

        queries: list[str] = []
        for template in templates:
            query_template = str(template.get("query", ""))
            missing = [
                name
                for name in _TEMPLATE_VARIABLE_PATTERN.findall(query_template)
                if not context.get(name)
            ]
            if missing:
                if target.get("required"):
                    return self._missing_template_context(skill, target, missing)
                continue
            queries.append(str(self._render_value(query_template, context)))
        if queries:
            return "\n".join(queries)
        return None

    @staticmethod
    def _select_templates(
        target: dict[str, Any], input_data: SkillRAGInput
    ) -> list[dict[str, Any]]:
        template_id = input_data.get("query_template_id")
        template_ids = input_data.get("query_template_ids", [])
        selected_ids = set(template_ids)
        if template_id:
            selected_ids.add(template_id)
        templates = target.get("query_templates", [])
        if not selected_ids:
            return templates
        selected = [
            template
            for template in templates
            if template.get("template_id") in selected_ids
        ]
        if len(selected) != len(selected_ids):
            unknown = selected_ids - {
                template.get("template_id") for template in templates
            }
            raise ValueError(
                f"Unknown query template(s) for RAG target "
                f"'{target['target_id']}': {', '.join(sorted(unknown))}"
            )
        return selected

    @staticmethod
    def _missing_template_context(
        skill: RetrievalSkill, target: dict[str, Any], missing: list[str]
    ) -> None:
        raise SkillRAGContextError(
            f"Missing required context for RAG target '{target['target_id']}' "
            f"in RetrievalSkill '{skill.skill_id}': {', '.join(sorted(missing))}"
        )

    @staticmethod
    def _render_value(value: Any, context: dict[str, Any]) -> Any:
        if isinstance(value, str):
            return _TEMPLATE_VARIABLE_PATTERN.sub(
                lambda match: str(context[match.group(1)]), value
            )
        if isinstance(value, list):
            return [SkillRAGExecutor._render_value(item, context) for item in value]
        if isinstance(value, dict):
            return {
                key: SkillRAGExecutor._render_value(item, context)
                for key, item in value.items()
            }
        return value

    @staticmethod
    def _apply_filters(
        result: RAGToolOutput, input_data: RAGToolInput
    ) -> RAGToolOutput:
        """Post-filter returned docs when their metadata is available."""
        document_filters = input_data.get("document_filters", {})
        metadata_filters = input_data.get("metadata_filters", {})
        docs = [
            doc
            for doc in result.get("docs", [])
            if SkillRAGExecutor._matches_filters(
                doc, document_filters, metadata_filters
            )
        ][: input_data.get("top_k", len(result.get("docs", [])))]
        return RAGToolOutput(**{**result, "docs": docs})

    @staticmethod
    def _matches_filters(
        doc: dict[str, Any],
        document_filters: dict[str, Any],
        metadata_filters: dict[str, Any],
    ) -> bool:
        metadata = doc.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        for key, allowed_values in document_filters.items():
            value = doc.get(key, metadata.get(key))
            if value is None or value not in allowed_values:
                return False
        for key, expected_value in metadata_filters.items():
            value = doc.get(key, metadata.get(key))
            if value is None or value != expected_value:
                return False
        return True

    @staticmethod
    def skipped_result() -> RAGToolOutput:
        return RAGToolOutput(
            docs=[],
            quality_score=0.0,
            has_relevant=False,
            relevant_count=0,
            top_score=0.0,
            rewrite_history=[],
            query_simplified="",
            detected_lang="zh_CN",
            retrieval_time_ms=0,
            retrieval_error_code="",
            retrieval_mode_used="",
        )
