"""Report Exporter（报告导出器）。

按 docs/报告生成Tool设计.md 第 6.10 节设计：
- Markdown：直接字符串模板拼接
- HTML：使用 Jinja2 渲染（用户内容必须转义）
- 阶段一仅实现 Markdown / HTML

设计原则：
- 用户内容**必须 HTML 转义**（防 XSS）
- 禁止加载外部脚本
- 包含 evidence_refs 引用
"""
from __future__ import annotations

import html
import logging
from datetime import datetime
from typing import Any

from agent.langgraph.tools.report.models import (
    ChartSpec,
    ReportArtifact,
    ReportSection,
    TableSpec,
)

logger = logging.getLogger(__name__)


# ========== Markdown 模板 ==========
def _render_markdown_section(section: ReportSection, include_refs: bool = True) -> str:
    """渲染单个章节为 Markdown。"""
    parts = []
    parts.append(f"## {section.get('title', '未命名章节')}")
    parts.append("")
    parts.append(section.get("content", ""))
    parts.append("")

    if include_refs and section.get("evidence_refs"):
        refs = section["evidence_refs"]
        ref_links = ", ".join(f"[{ref}](#{ref})" for ref in refs)
        parts.append(f"> **引用依据**: {ref_links}")
        parts.append("")

    return "\n".join(parts)


def _render_markdown_chart(chart: ChartSpec) -> str:
    """渲染图表为 Markdown 表格。"""
    parts = []
    parts.append(f"### {chart.get('title', '图表')}")
    parts.append("")

    chart_type = chart.get("chart_type", "table")
    data = chart.get("data", []) or []

    if chart_type == "kpi":
        # KPI: 单行多指标
        series = chart.get("series", []) or []
        if series:
            kpi_line = " | ".join(
                f"**{s.get('name', '')}**: {s.get('value', '')}"
                for s in series
            )
            parts.append(f"> {kpi_line}")
    elif data and isinstance(data[0], dict):
        # 表格形式
        columns = list(data[0].keys())
        parts.append("| " + " | ".join(columns) + " |")
        parts.append("| " + " | ".join(["---"] * len(columns)) + " |")
        for row in data[:20]:
            parts.append("| " + " | ".join(str(row.get(c, "")) for c in columns) + " |")
    else:
        parts.append("（无数据）")

    if chart.get("evidence_refs"):
        refs = chart["evidence_refs"]
        parts.append("")
        parts.append(f"> 来源: {', '.join(refs)}")

    parts.append("")
    return "\n".join(parts)


def _render_markdown_table(table: TableSpec) -> str:
    """渲染表格为 Markdown。"""
    parts = []
    parts.append(f"### {table.get('title', '数据表')}")
    parts.append("")

    columns = table.get("columns", [])
    rows = table.get("rows", []) or []

    if not columns or not rows:
        parts.append("（无数据）")
    else:
        parts.append("| " + " | ".join(columns) + " |")
        parts.append("| " + " | ".join(["---"] * len(columns)) + " |")
        for row in rows[:50]:
            parts.append("| " + " | ".join(str(c) for c in row) + " |")

    if table.get("evidence_refs"):
        refs = table["evidence_refs"]
        parts.append("")
        parts.append(f"> 来源: {', '.join(refs)}")

    parts.append("")
    return "\n".join(parts)


def render_markdown(artifact: ReportArtifact, include_refs: bool = True) -> str:
    """渲染 ReportArtifact 为 Markdown 文本。

    Args:
        artifact: ReportArtifact
        include_refs: 是否在文末列出所有 evidence 引用

    Returns:
        str: Markdown 文本
    """
    parts: list[str] = []

    # 标题
    parts.append(f"# {artifact.get('title', '未命名报告')}")
    parts.append("")

    # 元信息
    metadata_lines = []
    if artifact.get("report_type"):
        metadata_lines.append(f"**报告类型**: {artifact['report_type']}")
    if artifact.get("created_at"):
        metadata_lines.append(f"**生成时间**: {artifact['created_at']}")
    if artifact.get("tenant_id"):
        metadata_lines.append(f"**租户**: {artifact['tenant_id']}")
    if metadata_lines:
        parts.append("  \n".join(metadata_lines))
        parts.append("")

    # 摘要
    if artifact.get("summary"):
        parts.append(f"> **摘要**: {artifact['summary']}")
        parts.append("")

    parts.append("---")
    parts.append("")

    # 章节
    for section in artifact.get("sections", []) or []:
        parts.append(_render_markdown_section(section, include_refs=include_refs))

    # 图表
    for chart in artifact.get("charts", []) or []:
        parts.append(_render_markdown_chart(chart))

    # 表格
    for table in artifact.get("tables", []) or []:
        parts.append(_render_markdown_table(table))

    # 附录
    for app in artifact.get("appendix", []) or []:
        if isinstance(app, dict):
            parts.append(f"## 附录: {app.get('title', '未命名')}")
            parts.append("")
            parts.append(app.get("content", ""))
            parts.append("")

    # 验证结果
    vr = artifact.get("verification_result", {})
    if vr:
        parts.append("---")
        parts.append("## 验证结果")
        parts.append("")
        parts.append(f"- 验证通过: {'是' if vr.get('passed') else '否'}")
        parts.append(f"- 质量分: {vr.get('score', 0.0):.2f}")
        if vr.get("issues"):
            parts.append(f"- 问题数: {len(vr['issues'])}")
        parts.append("")

    # evidence 引用清单
    if include_refs and artifact.get("evidence_refs"):
        parts.append("---")
        parts.append("## Evidence 引用")
        parts.append("")
        for ref in artifact["evidence_refs"]:
            parts.append(f"- `{ref}`")
        parts.append("")

    return "\n".join(parts)


# ========== HTML 模板 ==========
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="{language}">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;">
<title>{title}</title>
<style>
body {{ font-family: -apple-system, "Helvetica Neue", "Microsoft YaHei", sans-serif; max-width: 960px; margin: 24px auto; padding: 0 16px; color: #1a1a1a; line-height: 1.6; }}
h1, h2, h3 {{ color: #2c3e50; }}
h1 {{ border-bottom: 2px solid #2c3e50; padding-bottom: 8px; }}
h2 {{ border-bottom: 1px solid #ddd; padding-bottom: 4px; margin-top: 32px; }}
.meta {{ color: #666; font-size: 0.9em; margin-bottom: 24px; }}
.summary {{ background: #f5f7fa; border-left: 4px solid #4a90e2; padding: 12px 16px; margin: 16px 0; }}
.evidence {{ background: #fafafa; padding: 8px 12px; margin: 8px 0; font-size: 0.9em; color: #555; border-left: 2px solid #ddd; }}
table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
th {{ background: #f5f7fa; }}
.section {{ margin: 24px 0; }}
.chart {{ background: #fafbfc; padding: 12px; margin: 16px 0; border: 1px solid #eee; }}
.kpi {{ font-size: 1.1em; padding: 8px 0; }}
.footnote {{ color: #888; font-size: 0.85em; margin-top: 32px; border-top: 1px solid #eee; padding-top: 16px; }}
</style>
</head>
<body>
<h1>{safe_title}</h1>
<div class="meta">
{metadata_html}
</div>
{summary_html}
{content_html}
<div class="footnote">
<p>报告 ID: <code>{report_id}</code> | 生成时间: {created_at}</p>
{verification_html}
</div>
</body>
</html>
"""


def _render_html_section(section: ReportSection, include_refs: bool = True) -> str:
    """渲染单个章节为 HTML。"""
    title_escaped = html.escape(section.get("title", "未命名章节"))
    content_escaped = _markdown_to_html_safe(section.get("content", ""))

    refs_html = ""
    if include_refs and section.get("evidence_refs"):
        ref_links = " ".join(
            f"<code>{html.escape(ref)}</code>" for ref in section["evidence_refs"]
        )
        refs_html = f'<div class="evidence"><strong>引用依据</strong>: {ref_links}</div>'

    return (
        f'<div class="section">'
        f'<h2>{title_escaped}</h2>'
        f'{content_escaped}'
        f'{refs_html}'
        f'</div>'
    )


def _render_html_chart(chart: ChartSpec) -> str:
    """渲染图表为 HTML（表格形式）。"""
    title_escaped = html.escape(chart.get("title", "图表"))
    chart_type = chart.get("chart_type", "table")
    data = chart.get("data", []) or []

    inner = ""
    if chart_type == "kpi":
        series = chart.get("series", []) or []
        if series:
            kpis = " | ".join(
                f"<strong>{html.escape(str(s.get('name', '')))}</strong>: "
                f"{html.escape(str(s.get('value', '')))}"
                for s in series
            )
            inner = f'<div class="kpi">{kpis}</div>'
    elif data and isinstance(data[0], dict):
        columns = list(data[0].keys())
        thead = "".join(f"<th>{html.escape(str(c))}</th>" for c in columns)
        tbody = ""
        for row in data[:20]:
            tds = "".join(f"<td>{html.escape(str(row.get(c, '')))}</td>" for c in columns)
            tbody += f"<tr>{tds}</tr>"
        inner = f"<table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>"
    else:
        inner = "<em>（无数据）</em>"

    refs_html = ""
    if chart.get("evidence_refs"):
        ref_links = " ".join(
            f"<code>{html.escape(ref)}</code>" for ref in chart["evidence_refs"]
        )
        refs_html = f'<div class="evidence">来源: {ref_links}</div>'

    return f'<div class="chart"><h3>{title_escaped}</h3>{inner}{refs_html}</div>'


def _render_html_table(table: TableSpec) -> str:
    """渲染表格为 HTML。"""
    title_escaped = html.escape(table.get("title", "数据表"))
    columns = table.get("columns", [])
    rows = table.get("rows", []) or []

    if not columns or not rows:
        return f'<div class="chart"><h3>{title_escaped}</h3><em>（无数据）</em></div>'

    thead = "".join(f"<th>{html.escape(str(c))}</th>" for c in columns)
    tbody = ""
    for row in rows[:50]:
        tds = "".join(f"<td>{html.escape(str(c))}</td>" for c in row)
        tbody += f"<tr>{tds}</tr>"

    refs_html = ""
    if table.get("evidence_refs"):
        ref_links = " ".join(
            f"<code>{html.escape(ref)}</code>" for ref in table["evidence_refs"]
        )
        refs_html = f'<div class="evidence">来源: {ref_links}</div>'

    return f'<div class="chart"><h3>{title_escaped}</h3><table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>{refs_html}</div>'


def _markdown_to_html_safe(text: str) -> str:
    """将 Markdown 简单文本转 HTML（安全转义）。

    注意：不做完整 Markdown 解析，只做：
    - HTML 转义
    - 段落分隔
    - 简单换行
    """
    if not text:
        return ""
    # HTML 转义（关键安全步骤）
    escaped = html.escape(text)
    # 段落分隔
    paragraphs = escaped.split("\n\n")
    return "".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs if p.strip())


def render_html(artifact: ReportArtifact, include_refs: bool = True) -> str:
    """渲染 ReportArtifact 为 HTML 文本。

    安全保证：
    - 所有用户内容 HTML 转义
    - CSP meta 禁止 script-src
    - 禁止加载外部脚本

    Args:
        artifact: ReportArtifact
        include_refs: 是否在文末列出所有 evidence 引用

    Returns:
        str: HTML 文本
    """
    language = artifact.get("language", "zh_CN")
    title = artifact.get("title", "未命名报告")
    safe_title = html.escape(title)

    # 元信息
    metadata_parts = []
    if artifact.get("report_type"):
        metadata_parts.append(f"报告类型: {html.escape(str(artifact['report_type']))}")
    if artifact.get("created_at"):
        metadata_parts.append(f"生成时间: {html.escape(str(artifact['created_at']))}")
    if artifact.get("tenant_id"):
        metadata_parts.append(f"租户: {html.escape(str(artifact['tenant_id']))}")
    metadata_html = " | ".join(metadata_parts)

    # 摘要
    summary_html = ""
    if artifact.get("summary"):
        summary_html = (
            f'<div class="summary"><strong>摘要</strong>: '
            f'{_markdown_to_html_safe(artifact["summary"])}</div>'
        )

    # 内容：sections + charts + tables
    content_parts: list[str] = []
    for section in artifact.get("sections", []) or []:
        content_parts.append(_render_html_section(section, include_refs=include_refs))
    for chart in artifact.get("charts", []) or []:
        content_parts.append(_render_html_chart(chart))
    for table in artifact.get("tables", []) or []:
        content_parts.append(_render_html_table(table))
    content_html = "\n".join(content_parts)

    # 验证结果
    vr = artifact.get("verification_result", {})
    verification_html = ""
    if vr:
        verification_html = (
            f"<p>验证: {'通过' if vr.get('passed') else '未通过'} | "
            f"质量分: {vr.get('score', 0.0):.2f} | "
            f"问题数: {len(vr.get('issues', []))}</p>"
        )

    # evidence 引用
    if include_refs and artifact.get("evidence_refs"):
        ref_list = " ".join(
            f"<code>{html.escape(ref)}</code>" for ref in artifact["evidence_refs"]
        )
        verification_html += f"<p>Evidence 引用: {ref_list}</p>"

    html_text = HTML_TEMPLATE.format(
        language=html.escape(language),
        title=safe_title,
        safe_title=safe_title,
        metadata_html=metadata_html,
        summary_html=summary_html,
        content_html=content_html,
        report_id=html.escape(str(artifact.get("report_id", ""))),
        created_at=html.escape(str(artifact.get("created_at", ""))),
        verification_html=verification_html,
    )

    return html_text


class ReportExporter:
    """报告导出器。"""

    def export(
        self,
        artifact: ReportArtifact,
        format: str = "markdown",
    ) -> tuple[str, str]:
        """导出报告为指定格式。

        Args:
            artifact: ReportArtifact
            format: 导出格式（markdown / html）

        Returns:
            tuple: (content, extension)
                - content: 文件内容
                - extension: 文件扩展名

        Raises:
            ValueError: 不支持的格式
        """
        fmt = format.lower()
        if fmt == "markdown" or fmt == "md":
            return render_markdown(artifact), "md"
        if fmt == "html":
            return render_html(artifact), "html"
        raise ValueError(
            f"不支持的报告格式: {format}（阶段一仅支持 markdown / html）"
        )
