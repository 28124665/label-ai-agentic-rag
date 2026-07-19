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
"""RAG performance baseline test.

Uses Python ``requests`` + ``threading`` to drive load against the RAGFlow
HTTP API.  Supports both real endpoints and a dry-run (mock) mode that
generates synthetic latency samples for report template validation.

Run (with uv from repo root):

  uv run test/perf/baseline_test.py --host http://127.0.0.1:9380 --kb_id <KB_ID>

Dry-run example:

  uv run test/perf/baseline_test.py --dry-run --dataset test/perf/datasets/queries.json

The default concurrency model follows P2-NFR-01:
  1. warm-up:     5 QPS  for 2 minutes
  2. stable-10:  10 QPS  for 10 minutes
  3. stable-30:  30 QPS  for 10 minutes
  4. stable-50:  50 QPS  for 10 minutes
  5. burst:      10 -> 100 QPS ramp over 5 minutes
  6. recovery:   10 QPS  for 5 minutes
"""

import argparse
import json
import logging
import math
import random
import statistics
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


ROOT = Path(__file__).resolve().parent
DEFAULT_QUERIES = ROOT / "datasets" / "queries.json"
DEFAULT_REPORT_DIR = ROOT / "reports"

DEFAULT_STAGES = [
    {"name": "warm-up", "qps": 5, "duration": 120},
    {"name": "stable-10", "qps": 10, "duration": 600},
    {"name": "stable-30", "qps": 30, "duration": 600},
    {"name": "stable-50", "qps": 50, "duration": 600},
    {"name": "burst", "start_qps": 10, "end_qps": 100, "duration": 300},
    {"name": "recovery", "qps": 10, "duration": 300},
]

MAX_RETRIES = 3
TIMEOUT_SECONDS = 60.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Sample:
    stage: str
    query_id: str
    complexity: str
    latency_ms: float
    status_code: Optional[int] = None
    error: Optional[str] = None
    timeout: bool = False
    degraded: bool = False
    degraded_reason: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class StageResult:
    name: str
    qps: float
    duration: int
    samples: List[Sample] = field(default_factory=list)

    @property
    def total_requests(self) -> int:
        return len(self.samples)

    @property
    def successful(self) -> int:
        return sum(1 for s in self.samples if s.error is None and not s.timeout)

    @property
    def error_count(self) -> int:
        return sum(1 for s in self.samples if s.error is not None and not s.timeout)

    @property
    def timeout_count(self) -> int:
        return sum(1 for s in self.samples if s.timeout)

    @property
    def degraded_count(self) -> int:
        return sum(1 for s in self.samples if s.degraded)

    def _latencies(self) -> List[float]:
        return [s.latency_ms for s in self.samples if s.error is None]

    def _percentile(self, p: float) -> Optional[float]:
        values = sorted(self._latencies())
        if not values:
            return None
        k = max(0, math.ceil((p / 100.0) * len(values)) - 1)
        return values[k]

    def summary(self) -> Dict[str, Any]:
        latencies = self._latencies()
        total = self.total_requests
        return {
            "name": self.name,
            "target_qps": self.qps,
            "duration_seconds": self.duration,
            "total_requests": total,
            "successful": self.successful,
            "error_count": self.error_count,
            "timeout_count": self.timeout_count,
            "degraded_count": self.degraded_count,
            "error_rate": round(self.error_count / total, 4) if total else 0.0,
            "timeout_rate": round(self.timeout_count / total, 4) if total else 0.0,
            "degradation_rate": round(self.degraded_count / total, 4) if total else 0.0,
            "latency_ms": {
                "count": len(latencies),
                "avg": round(statistics.mean(latencies), 2) if latencies else None,
                "min": round(min(latencies), 2) if latencies else None,
                "max": round(max(latencies), 2) if latencies else None,
                "p50": round(self._percentile(50.0), 2) if latencies else None,
                "p95": round(self._percentile(95.0), 2) if latencies else None,
                "p99": round(self._percentile(99.0), 2) if latencies else None,
            },
            "tokens": {
                "prompt_tokens": sum(s.prompt_tokens for s in self.samples),
                "completion_tokens": sum(s.completion_tokens for s in self.samples),
                "total_tokens": sum(s.total_tokens for s in self.samples),
            },
        }


class RequestSender:
    def __init__(self, host: str, kb_id: Optional[str], dataset_path: Path, api_key: Optional[str] = None):
        self.host = host.rstrip("/")
        self.kb_id = kb_id
        self.dataset_path = dataset_path
        self.api_key = api_key
        self.queries: List[Dict[str, Any]] = []
        self._load_queries()

    def _load_queries(self) -> None:
        data = json.loads(self.dataset_path.read_text(encoding="utf-8"))
        self.queries = data.get("queries", data if isinstance(data, list) else [])
        if not self.queries:
            raise ValueError(f"No queries found in {self.dataset_path}")

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _pick_query(self) -> Dict[str, Any]:
        return random.choice(self.queries)

    def send(self, stage: str) -> Sample:
        raise NotImplementedError


class MockSender(RequestSender):
    """Simulate latency distributions without calling a real service."""

    # Latency distributions by complexity (ms)
    LATENCY_PROFILES = {
        "simple": {"base": 600, "sigma": 250, "timeout_p": 0.002, "error_p": 0.003},
        "medium": {"base": 2200, "sigma": 700, "timeout_p": 0.005, "error_p": 0.005},
        "complex": {"base": 5200, "sigma": 1600, "timeout_p": 0.012, "error_p": 0.008},
    }

    def send(self, stage: str) -> Sample:
        query = self._pick_query()
        complexity = query.get("complexity", "medium")
        profile = self.LATENCY_PROFILES.get(complexity, self.LATENCY_PROFILES["medium"])
        latency_ms = max(50.0, random.gauss(profile["base"], profile["sigma"]))
        timeout = random.random() < profile["timeout_p"]
        error = random.random() < profile["error_p"] and not timeout
        degraded = random.random() < 0.03 and not error and not timeout

        prompt_tokens = random.randint(200, 1200)
        completion_tokens = random.randint(80, 600)
        return Sample(
            stage=stage,
            query_id=query["id"],
            complexity=complexity,
            latency_ms=latency_ms,
            status_code=200 if not error else 500,
            error="mock_error" if error else None,
            timeout=timeout,
            degraded=degraded,
            degraded_reason="mock_fallback" if degraded else None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )


class RetrievalSender(RequestSender):
    """Send requests to /api/v1/retrieval."""

    def send(self, stage: str) -> Sample:
        query = self._pick_query()
        payload = {
            "question": query["query"],
            "dataset_ids": [self.kb_id] if self.kb_id else [],
        }
        t0 = time.perf_counter()
        error: Optional[str] = None
        timeout = False
        status_code: Optional[int] = None
        try:
            response = requests.post(
                f"{self.host}/api/v1/retrieval",
                headers=self._headers(),
                json=payload,
                timeout=TIMEOUT_SECONDS,
            )
            status_code = response.status_code
            response.raise_for_status()
            data = response.json()
            if data.get("code") != 0:
                error = data.get("message", "business_error")
        except requests.exceptions.Timeout:
            timeout = True
            error = "timeout"
        except requests.exceptions.RequestException as exc:
            error = str(exc)
        t1 = time.perf_counter()

        degraded, degraded_reason = self._extract_degradation(data if 'data' in locals() else {})
        tokens = self._extract_tokens(data if 'data' in locals() else {})
        return Sample(
            stage=stage,
            query_id=query["id"],
            complexity=query.get("complexity", "medium"),
            latency_ms=(t1 - t0) * 1000.0,
            status_code=status_code,
            error=error,
            timeout=timeout,
            degraded=degraded,
            degraded_reason=degraded_reason,
            prompt_tokens=tokens.get("prompt_tokens", 0),
            completion_tokens=tokens.get("completion_tokens", 0),
            total_tokens=tokens.get("total_tokens", 0),
        )

    @staticmethod
    def _extract_degradation(data: Any) -> tuple:
        degraded = bool(data.get("degraded")) if isinstance(data, dict) else False
        reason = data.get("degraded_reason") if isinstance(data, dict) else None
        return degraded, reason

    @staticmethod
    def _extract_tokens(data: Any) -> Dict[str, int]:
        if not isinstance(data, dict):
            return {}
        usage = data.get("usage") or {}
        return {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }


class ChatSender(RequestSender):
    """Send streaming chat completion requests (OpenAI compatible)."""

    def __init__(
        self,
        host: str,
        kb_id: Optional[str],
        dataset_path: Path,
        chat_id: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        super().__init__(host, kb_id, dataset_path, api_key)
        self.chat_id = chat_id
        self.model = model

    def send(self, stage: str) -> Sample:
        query = self._pick_query()
        payload = {
            "model": self.model or "glm-4-flash@ZHIPU-AI",
            "messages": [{"role": "user", "content": query["query"]}],
            "stream": True,
        }
        url = f"{self.host}/api/v1/chats_openai/{self.chat_id}/chat/completions"
        t0 = time.perf_counter()
        error: Optional[str] = None
        timeout = False
        status_code: Optional[int] = None
        prompt_tokens = 0
        completion_tokens = 0
        degraded = False
        degraded_reason: Optional[str] = None
        try:
            response = requests.post(
                url,
                headers=self._headers(),
                json=payload,
                timeout=TIMEOUT_SECONDS,
                stream=True,
            )
            status_code = response.status_code
            if response.status_code != 200:
                error = f"http_{response.status_code}"
            else:
                for raw_line in response.iter_lines(decode_unicode=True):
                    if not raw_line:
                        continue
                    line = raw_line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    choice = choices[0] if choices else {}
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        completion_tokens += 1
                    usage = chunk.get("usage") or {}
                    if usage:
                        prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                        completion_tokens = usage.get("completion_tokens", completion_tokens)
                response.close()
        except requests.exceptions.Timeout:
            timeout = True
            error = "timeout"
        except requests.exceptions.RequestException as exc:
            error = str(exc)
        t1 = time.perf_counter()

        return Sample(
            stage=stage,
            query_id=query["id"],
            complexity=query.get("complexity", "medium"),
            latency_ms=(t1 - t0) * 1000.0,
            status_code=status_code,
            error=error,
            timeout=timeout,
            degraded=degraded,
            degraded_reason=degraded_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )


def _qps_for_stage(stage: Dict[str, Any], elapsed: float) -> float:
    if "start_qps" in stage:
        ratio = min(1.0, elapsed / stage["duration"])
        return stage["start_qps"] + (stage["end_qps"] - stage["start_qps"]) * ratio
    return stage["qps"]


def run_stage(stage: Dict[str, Any], sender: RequestSender, max_workers: int) -> StageResult:
    result = StageResult(name=stage["name"], qps=stage.get("qps", stage.get("end_qps")), duration=stage["duration"])
    token_queue: queue.Queue = queue.Queue(maxsize=max_workers * 2)
    stop_event = threading.Event()
    samples_lock = threading.Lock()
    samples: List[Sample] = []

    def worker() -> None:
        while not stop_event.is_set():
            try:
                token = token_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if token is None:
                break
            sample = sender.send(stage["name"])
            with samples_lock:
                samples.append(sample)
            token_queue.task_done()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(max_workers)]
    for t in threads:
        t.start()

    start = time.monotonic()
    next_second = start + 1.0
    issued_this_second = 0.0
    target_this_second = _qps_for_stage(stage, 0.0)

    while time.monotonic() - start < stage["duration"]:
        now = time.monotonic()
        if now >= next_second:
            elapsed = now - start
            target_this_second = _qps_for_stage(stage, elapsed)
            issued_this_second = 0.0
            next_second = now + 1.0

        if issued_this_second < target_this_second:
            try:
                token_queue.put_nowait(True)
                issued_this_second += 1.0
            except queue.Full:
                time.sleep(0.001)
        else:
            sleep_time = max(0.0001, next_second - now)
            time.sleep(sleep_time)

    stop_event.set()
    for _ in threads:
        try:
            token_queue.put_nowait(None)
        except queue.Full:
            break
    for t in threads:
        t.join(timeout=2.0)

    result.samples = samples
    return result


def build_report(
    results: List[StageResult],
    host: str,
    kb_id: Optional[str],
    dataset_path: Path,
    dry_run: bool,
) -> Dict[str, Any]:
    return {
        "metadata": {
            "title": "RAG Performance Baseline Report",
            "generated_at": _now_iso(),
            "host": host,
            "kb_id": kb_id,
            "dataset": str(dataset_path),
            "dry_run": dry_run,
        },
        "objectives": {
            "simple_query_p95_ms": 2000,
            "single_retrieval_p95_ms": 5000,
            "complex_query_p95_ms": 10000,
            "error_rate": 0.01,
            "timeout_rate": 0.01,
        },
        "stages": [r.summary() for r in results],
        "overall": _overall_summary(results),
    }


def _overall_summary(results: List[StageResult]) -> Dict[str, Any]:
    all_samples: List[Sample] = []
    for r in results:
        all_samples.extend(r.samples)
    latencies = sorted([s.latency_ms for s in all_samples if s.error is None])
    total = len(all_samples)
    errors = sum(1 for s in all_samples if s.error is not None and not s.timeout)
    timeouts = sum(1 for s in all_samples if s.timeout)
    degraded = sum(1 for s in all_samples if s.degraded)
    by_complexity: Dict[str, List[float]] = {}
    for s in all_samples:
        if s.error is None:
            by_complexity.setdefault(s.complexity, []).append(s.latency_ms)

    def _p(values: List[float], p: float) -> Optional[float]:
        if not values:
            return None
        sorted_vals = sorted(values)
        k = max(0, math.ceil((p / 100.0) * len(sorted_vals)) - 1)
        return sorted_vals[k]

    return {
        "total_requests": total,
        "error_count": errors,
        "timeout_count": timeouts,
        "degraded_count": degraded,
        "error_rate": round(errors / total, 4) if total else 0.0,
        "timeout_rate": round(timeouts / total, 4) if total else 0.0,
        "degradation_rate": round(degraded / total, 4) if total else 0.0,
        "latency_ms": {
            "p50": round(_p(latencies, 50.0), 2) if latencies else None,
            "p95": round(_p(latencies, 95.0), 2) if latencies else None,
            "p99": round(_p(latencies, 99.0), 2) if latencies else None,
        },
        "by_complexity": {
            comp: {
                "count": len(vals),
                "p50": round(_p(vals, 50.0), 2),
                "p95": round(_p(vals, 95.0), 2),
                "p99": round(_p(vals, 99.0), 2),
            }
            for comp, vals in by_complexity.items()
        },
    }


def print_report(report: Dict[str, Any]) -> None:
    print("\n========== RAG Performance Baseline Report ==========")
    meta = report["metadata"]
    print(f"Host:      {meta['host']}")
    print(f"KB ID:     {meta['kb_id']}")
    print(f"Dataset:   {meta['dataset']}")
    print(f"Dry run:   {meta['dry_run']}")
    print(f"Generated: {meta['generated_at']}")
    print("-" * 55)
    for stage in report["stages"]:
        print(
            f"{stage['name']:<14} QPS={stage['target_qps']:>5.1f}  "
            f"req={stage['total_requests']:>6}  ok={stage['successful']:>6}  "
            f"err={stage['error_count']:>4}({stage['error_rate']*100:>5.2f}%)  "
            f"to={stage['timeout_count']:>4}({stage['timeout_rate']*100:>5.2f}%)  "
            f"deg={stage['degraded_count']:>4}({stage['degradation_rate']*100:>5.2f}%)  "
            f"P95={stage['latency_ms']['p95']!s:>8}ms  P99={stage['latency_ms']['p99']!s:>8}ms"
        )
    print("-" * 55)
    overall = report["overall"]
    print(
        f"OVERALL  req={overall['total_requests']}  "
        f"err={overall['error_rate']*100:.2f}%  to={overall['timeout_rate']*100:.2f}%  "
        f"deg={overall['degradation_rate']*100:.2f}%  "
        f"P95={overall['latency_ms']['p95']}ms  P99={overall['latency_ms']['p99']}ms"
    )
    print("By complexity:")
    for comp, stats in overall["by_complexity"].items():
        print(f"  {comp:<8} count={stats['count']:<6} P95={stats['p95']}ms  P99={stats['p99']}ms")
    print("=====================================================\n")


def parse_stages(stages_json: Optional[str]) -> List[Dict[str, Any]]:
    if not stages_json:
        return DEFAULT_STAGES
    try:
        return json.loads(stages_json)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"Invalid stages JSON: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG performance baseline test")
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
        help="JSON list of stages (see DEFAULT_STAGES in script).",
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
        help="Path to write JSON report. Defaults to test/perf/reports/baseline_report_<timestamp>.json.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use synthetic latency data instead of calling a real service.",
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
        sender: RequestSender = MockSender(args.host, args.kb_id, args.dataset)
    elif args.mode == "chat":
        if not args.chat_id:
            parser.error("--chat_id is required when mode=chat.")
        sender = ChatSender(args.host, args.kb_id, args.dataset, args.chat_id, args.model, args.api_key)
    else:
        sender = RetrievalSender(args.host, args.kb_id, args.dataset, args.api_key)

    logging.info("Starting baseline test against %s (mode=%s, dry_run=%s)", args.host, args.mode, args.dry_run)
    results: List[StageResult] = []
    try:
        for stage in stages:
            logging.info("Stage '%s' starting (target %.1f QPS, %ds)", stage["name"], stage.get("qps", stage.get("end_qps")), stage["duration"])
            result = run_stage(stage, sender, args.max_workers)
            results.append(result)
            logging.info("Stage '%s' finished: %d requests", stage["name"], result.total_requests)
    except KeyboardInterrupt:
        logging.warning("Interrupted by user, generating partial report...")

    report = build_report(results, args.host, args.kb_id, args.dataset, args.dry_run)
    print_report(report)

    report_path = args.report or DEFAULT_REPORT_DIR / f"baseline_report_{int(time.time())}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Report written to %s", report_path)


if __name__ == "__main__":
    main()
