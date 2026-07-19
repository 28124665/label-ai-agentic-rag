#!/usr/bin/env python3
# Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""RAG performance pass/fail test.

Reuses the load generation logic from ``baseline_test.py`` and adds a pass/fail
evaluation against the Phase 2 performance targets:

  - simple query P95 < 2,000 ms
  - single retrieval (medium) P95 < 5,000 ms
  - complex query P95 < 10,000 ms
  - error rate < 1%
  - timeout rate < 1%

Run against a real service:

  uv run test/perf/pass_test.py \
    --host http://127.0.0.1:9380 \
    --kb_id <KB_ID> \
    --dataset test/perf/datasets/queries.json

Run with synthetic data (example report):

  uv run test/perf/pass_test.py --dry-run --dataset test/perf/datasets/queries.json
"""

import argparse
import json
import logging
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from baseline_test import (
    DEFAULT_QUERIES,
    DEFAULT_REPORT_DIR,
    DEFAULT_STAGES,
    ChatSender,
    MockSender,
    RequestSender,
    RetrievalSender,
    Sample,
    StageResult,
    build_report,
    parse_stages,
    print_report,
    run_stage,
)


ROOT = Path(__file__).resolve().parent

# Phase 2 performance targets (P2-NFR-01 / Task 11).
PASS_CRITERIA = {
    "simple_query_p95_ms": {"threshold": 2_000.0, "complexity": "simple", "description": "简单查询 P95"},
    "single_retrieval_p95_ms": {"threshold": 5_000.0, "complexity": "medium", "description": "单次检索 P95"},
    "complex_query_p95_ms": {"threshold": 10_000.0, "complexity": "complex", "description": "复杂查询 P95"},
    "error_rate": {"threshold": 0.01, "description": "错误率"},
    "timeout_rate": {"threshold": 0.01, "description": "超时率"},
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class OptimizedMockSender(MockSender):
    """Simulate post-optimization latency distributions.

    Used only for example reports when no real service is available.  The
    distributions are intentionally tighter and have lower error/timeout
    probabilities than the baseline mock profile to demonstrate that the
    implemented optimizations move the needle.
    """

    LATENCY_PROFILES = {
        "simple": {"base": 480, "sigma": 180, "timeout_p": 0.001, "error_p": 0.001},
        "medium": {"base": 1_800, "sigma": 500, "timeout_p": 0.002, "error_p": 0.002},
        "complex": {"base": 4_200, "sigma": 1_200, "timeout_p": 0.005, "error_p": 0.004},
    }

    def send(self, stage: str) -> Sample:
        sample = super().send(stage)
        # Keep the optimized profile consistent: do not let the parent class
        # randomly degrade too many requests.
        if sample.degraded and random.random() < 0.5:
            sample.degraded = False
            sample.degraded_reason = None
        return sample


def evaluate_pass_fail(report: Dict[str, Any]) -> Dict[str, Any]:
    """Compare a report against PASS_CRITERIA and return per-item results."""
    overall = report["overall"]
    by_complexity = overall.get("by_complexity", {})
    checks: List[Dict[str, Any]] = []
    all_pass = True

    for key, cfg in PASS_CRITERIA.items():
        if "complexity" in cfg:
            value = by_complexity.get(cfg["complexity"], {}).get("p95")
            metric_name = cfg["description"]
        elif key == "error_rate":
            value = overall.get("error_rate")
            metric_name = cfg["description"]
        elif key == "timeout_rate":
            value = overall.get("timeout_rate")
            metric_name = cfg["description"]
        else:
            value = None
            metric_name = key

        if value is None:
            passed = False
            all_pass = False
        else:
            passed = value < cfg["threshold"]
            all_pass = all_pass and passed

        checks.append(
            {
                "metric": metric_name,
                "key": key,
                "value": value,
                "threshold": cfg["threshold"],
                "unit": "ms" if "_ms" in key else "ratio",
                "passed": passed,
            }
        )

    return {
        "overall_pass": all_pass,
        "verdict": "PASS" if all_pass else "FAIL",
        "checks": checks,
        "evaluated_at": _now_iso(),
    }


def _format_value(value: Optional[float], unit: str) -> str:
    if value is None:
        return "N/A"
    if unit == "ms":
        return f"{value:,.0f} ms"
    return f"{value * 100:.2f}%"


def generate_markdown_report(
    report: Dict[str, Any],
    criteria_result: Dict[str, Any],
    report_path: Optional[Path] = None,
) -> str:
    """Render a human-readable pass/fail report in Markdown."""
    meta = report["metadata"]
    overall = report["overall"]
    stages = report["stages"]

    lines: List[str] = []
    lines.append("# RAG 性能达标测试报告")
    lines.append("")

    if meta.get("dry_run"):
        lines.append(
            "> **说明：本报告基于模拟数据生成，用于演示优化后的达标测试输出格式。**"
        )
        lines.append(
            "> 真实环境测试请使用 `uv run test/perf/pass_test.py` 连接实际服务后生成。"
        )
        lines.append("")

    lines.append("## 1. 测试概览")
    lines.append("")
    lines.append(f"- **测试时间**：{meta.get('generated_at', _now_iso())}")
    lines.append(f"- **目标服务**：{meta.get('host', 'N/A')}")
    lines.append(f"- **知识库 ID**：{meta.get('kb_id', 'N/A')}")
    lines.append(f"- **测试数据集**：{meta.get('dataset', 'N/A')}")
    lines.append(f"- **是否为模拟数据**：{'是' if meta.get('dry_run') else '否'}")
    lines.append(f"- **最终判定**：**{criteria_result['verdict']}**")
    lines.append("")

    lines.append("## 2. 性能目标与判定结果")
    lines.append("")
    lines.append("| 指标 | 目标值 | 实测值 | 是否达标 |")
    lines.append("|------|--------|--------|----------|")
    for check in criteria_result["checks"]:
        threshold = check["threshold"]
        value = check["value"]
        unit = check["unit"]
        target_str = f"< {_format_value(threshold, unit)}"
        actual_str = _format_value(value, unit)
        status = "✅ 达标" if check["passed"] else "❌ 未达标"
        lines.append(f"| {check['metric']} | {target_str} | {actual_str} | {status} |")
    lines.append("")

    lines.append("## 3. 阶段汇总")
    lines.append("")
    lines.append(
        "| 阶段 | 目标 QPS | 持续时长 | 总请求 | 成功 | 错误数 | 错误率 | 超时数 | 超时率 | P50(ms) | P95(ms) | P99(ms) |"
    )
    lines.append(
        "|------|---------|---------|--------|------|--------|--------|--------|--------|---------|---------|---------|"
    )
    for stage in stages:
        latency = stage["latency_ms"]
        lines.append(
            f"| {stage['name']} | {stage['target_qps']} | {stage['duration_seconds']}s "
            f"| {stage['total_requests']} | {stage['successful']} "
            f"| {stage['error_count']} | {stage['error_rate'] * 100:.2f}% "
            f"| {stage['timeout_count']} | {stage['timeout_rate'] * 100:.2f}% "
            f"| {latency['p50']} | {latency['p95']} | {latency['p99']} |"
        )
    lines.append("")

    lines.append("## 4. 总体指标")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 总请求数 | {overall['total_requests']} |")
    lines.append(f"| 错误率 | {overall['error_rate'] * 100:.2f}% |")
    lines.append(f"| 超时率 | {overall['timeout_rate'] * 100:.2f}% |")
    lines.append(f"| 降级触发率 | {overall['degradation_rate'] * 100:.2f}% |")
    lines.append(f"| 整体 P50 | {overall['latency_ms']['p50']} ms |")
    lines.append(f"| 整体 P95 | {overall['latency_ms']['p95']} ms |")
    lines.append(f"| 整体 P99 | {overall['latency_ms']['p99']} ms |")
    lines.append("")

    lines.append("## 5. 按查询复杂度")
    lines.append("")
    lines.append("| 复杂度 | 请求数 | P50(ms) | P95(ms) | P99(ms) |")
    lines.append("|--------|--------|---------|---------|---------|")
    for comp, stats in overall.get("by_complexity", {}).items():
        lines.append(
            f"| {comp} | {stats['count']} | {stats['p50']} | {stats['p95']} | {stats['p99']} |"
        )
    lines.append("")

    if criteria_result["verdict"] == "PASS":
        lines.append("## 6. 结论")
        lines.append("")
        lines.append(
            "本次达标测试所有指标均满足二期性能目标（P2-NFR-01）。建议在真实生产负载下持续观察，"
            "并结合 `test/perf/optimization_recommendations.md` 中的优化项进一步提升性能冗余度。"
        )
    else:
        failed = [c for c in criteria_result["checks"] if not c["passed"]]
        lines.append("## 6. 未达标项与建议")
        lines.append("")
        lines.append("以下指标未满足二期性能目标，需结合优化建议逐项改进后复测：")
        lines.append("")
        for check in failed:
            lines.append(
                f"- **{check['metric']}**：实测 {check['value']}，目标 < {check['threshold']}。"
                "参考 `test/perf/optimization_recommendations.md` 中的对应优化项。"
            )
    lines.append("")

    lines.append("## 7. 相关文件")
    lines.append("")
    lines.append("- 优化建议：`test/perf/optimization_recommendations.md`")
    lines.append("- 基线报告：`test/perf/reports/baseline_report.md`")
    lines.append("- 测试脚本：`test/perf/pass_test.py`")
    if report_path:
        lines.append(f"- JSON 报告：`{report_path}`")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG performance pass/fail test")
    parser.add_argument("--host", default="http://127.0.0.1:9380", help="Base URL of the RAGFlow API server.")
    parser.add_argument("--kb_id", default=None, help="Knowledge base / dataset ID for retrieval/chat tests.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_QUERIES, help="Path to queries.json.")
    parser.add_argument(
        "--mode",
        choices=["retrieval", "chat"],
        default="retrieval",
        help="API endpoint to benchmark.",
    )
    parser.add_argument("--chat_id", default=None, help="Existing chat ID when mode=chat.")
    parser.add_argument("--model", default=None, help="Chat model name when mode=chat.")
    parser.add_argument("--api-key", default=None, help="API key for Authorization header.")
    parser.add_argument(
        "--qps",
        type=float,
        default=None,
        help="Override target QPS for a single custom stage (requires --duration).",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=None,
        help="Override stage duration in seconds (requires --qps).",
    )
    parser.add_argument(
        "--stages",
        type=parse_stages,
        default=None,
        help="JSON list of stages (see DEFAULT_STAGES in baseline_test.py).",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=128,
        help="Maximum number of concurrent worker threads.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Path to write JSON report. Defaults to test/perf/reports/pass_report_<timestamp>.json.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use synthetic latency data instead of calling a real service.",
    )
    parser.add_argument(
        "--optimized",
        action="store_true",
        help="When --dry-run is set, use the post-optimization mock latency profile.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    if args.qps is not None or args.duration is not None:
        if args.qps is None or args.duration is None:
            parser.error("--qps and --duration must be provided together.")
        stages = [{"name": "custom", "qps": args.qps, "duration": args.duration}]
    else:
        stages = args.stages or DEFAULT_STAGES

    if args.dry_run:
        if args.optimized:
            sender: RequestSender = OptimizedMockSender(args.host, args.kb_id, args.dataset)
            logging.info("Using post-optimization mock sender")
        else:
            sender = MockSender(args.host, args.kb_id, args.dataset)
            logging.info("Using baseline mock sender")
    elif args.mode == "chat":
        if not args.chat_id:
            parser.error("--chat_id is required when mode=chat.")
        sender = ChatSender(args.host, args.kb_id, args.dataset, args.chat_id, args.model, args.api_key)
    else:
        sender = RetrievalSender(args.host, args.kb_id, args.dataset, args.api_key)

    logging.info("Starting pass test against %s (mode=%s, dry_run=%s)", args.host, args.mode, args.dry_run)
    results: List[StageResult] = []
    try:
        for stage in stages:
            logging.info(
                "Stage '%s' starting (target %.1f QPS, %ds)",
                stage["name"],
                stage.get("qps", stage.get("end_qps")),
                stage["duration"],
            )
            result = run_stage(stage, sender, args.max_workers)
            results.append(result)
            logging.info("Stage '%s' finished: %d requests", stage["name"], result.total_requests)
    except KeyboardInterrupt:
        logging.warning("Interrupted by user, generating partial report...")

    report = build_report(results, args.host, args.kb_id, args.dataset, args.dry_run)
    report["metadata"]["title"] = "RAG Performance Pass Test Report"
    criteria_result = evaluate_pass_fail(report)
    report["pass_evaluation"] = criteria_result

    print_report(report)
    print("\n========== Pass/Fail Evaluation ==========")
    for check in criteria_result["checks"]:
        status = "PASS" if check["passed"] else "FAIL"
        print(
            f"{check['metric']:<20} "
            f"{_format_value(check['value'], check['unit']):>12} "
            f"< {_format_value(check['threshold'], check['unit']):>12}  [{status}]"
        )
    print(f"\nOVERALL VERDICT: {criteria_result['verdict']}")
    print("==========================================\n")

    report_path = args.report or DEFAULT_REPORT_DIR / f"pass_report_{int(time.time())}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("JSON report written to %s", report_path)

    md_path = report_path.with_suffix(".md")
    md_content = generate_markdown_report(report, criteria_result, report_path)
    md_path.write_text(md_content, encoding="utf-8")
    logging.info("Markdown report written to %s", md_path)

    return 0 if criteria_result["overall_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
