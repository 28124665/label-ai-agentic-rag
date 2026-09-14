# Label AI · Agentic RAG Platform

English | [简体中文](./README_zh.md)

An enterprise question-answering platform that combines unstructured knowledge retrieval (RAG) with structured data queries (SQL), orchestrated by a LangGraph agent and verified by claim-level hallucination detection.

Built for manufacturing scenarios (MES / QMS / OMS / ERP / HR business databases + SOP / policy / work-instruction knowledge bases), based on [RAGFlow](https://github.com/infiniflow/ragflow).

## Why this project

Business users ask questions in plain language — "why did the on-time delivery rate drop 8% last month, and how do we fix it?" Answering requires **both** a database lookup (the numbers) **and** document retrieval (the causes and the playbook). This platform routes each question to the right tool, or a multi-step plan across tools, then checks every claim in the answer against collected evidence before rendering it.

## Architecture

```
question_input
     │
     ▼
intent_router ──── 4-layer routing: complexity gate → rules → LLM → planner
     │
     ├── chitchat / clarify ──────────────► direct reply
     ├── rag_tool ──► evidence_fusion
     ├── db_tool ───► evidence_fusion
     ├── plan_executor (multi-step) ──────► evidence_fusion
     │
     ▼
evidence_fusion (immutable numbered evidence snapshot)
     │
     ▼
quality_check ── retry tools if evidence is insufficient
     │
     ▼
prompt_assembly → llm_generate → hallucination_check
     │                                    │
     ▼                                    ▼
answer_renderer (supported claims      reject / regenerate
with [1][2] citations)                 (fail-closed)
```

## Core modules

| Module | Location | What it does |
|---|---|---|
| Intent router | `agent/langgraph/routers/` | 4-layer routing: complexity gate for multi-tool questions, regex rules, LLM routing with confidence, planner for multi-step questions |
| Orchestration | `agent/langgraph/graph.py` | LangGraph state machine: route → tools → evidence fusion → quality check → generation → verification → rendering |
| Tool system | `agent/langgraph/tools/` | Registry + strategy + template-method: `@register_tool` self-registration, O(1) dispatch, `BaseTool` skeleton (timing / validation / error handling). Adding a new tool = one executor class, zero dispatcher changes |
| Built-in tools | `agent/langgraph/tools/executors/` | `rag` (parent-child chunk retrieval + rerank + LLM/NLI quality scoring), `database` (NL2SQL with progressive schema disclosure + SQL-Agent fallback), `web`, `rest` (ERP/MCP), `report` |
| GraphTool | `agent/langgraph/tools/graph_tool.py` | Graph query tool connected to the main flow through `agent/langgraph/nodes/graph_tool_node.py` |
| Evidence fusion | `agent/langgraph/evidence/` | Merges multi-tool output into one immutable, numbered evidence snapshot shared by prompt, verifier and citations |
| Hallucination detection | `agent/langgraph/` (citation binder / verdict matrix / policy engine) | Claim-level verification: each sentence judged as supported / contradicted / insufficient, counter-evidence scan, fail-closed policy (reject on key-claim errors or verifier outage) |
| Observability | `api/utils/`, `deployment/` | Langfuse tracing, OpenTelemetry, Prometheus metrics + alert rules, Grafana dashboards |

## Project layout

```
agent/langgraph/   # LangGraph agent: routers, nodes, tools, skills, evidence
api/               # Flask/FastAPI backend services
rag/               # RAG core: chunking, retrieval, rerank, quality grading
deepdoc/           # Document parsing and OCR
web/               # React frontend
conf/              # Runtime configuration (service_conf.yaml is git-ignored)
docker/            # Docker deployment
deployment/        # Prometheus / Grafana / OTel observability stack
test/              # Backend tests
```

## Quick start

Prerequisites: Python 3.10+, [uv](https://docs.astral.sh/uv/), Node.js 18+, Docker.

```bash
# 1. Start dependencies (MySQL/PostgreSQL, Elasticsearch, Redis, MinIO/OSS)
docker compose -f docker/docker-compose-base.yml up -d

# 2. Prepare local config (never commit real credentials)
cp docker/service_conf.yaml.template conf/service_conf.yaml  # fill in your values
cp docker/.env.example docker/.env

# 3. Backend
uv sync --python 3.12 --all-extras
source .venv/bin/activate
export PYTHONPATH=$(pwd)
bash docker/launch_backend_service.sh

# 4. Frontend
cd web && npm install && npm run dev
```

## Testing

```bash
uv run pytest                       # backend tests
cd web && npm run test              # frontend tests
ruff check && ruff format           # lint
```

## Security notes

- `docker/.env*` and `conf/service_conf.yaml` are git-ignored on purpose — never commit real credentials.
- Secrets in git history have been scrubbed; rotate any credential that ever entered a repository.

## License

Apache-2.0 (inherited from RAGFlow).

## Acknowledgements

This platform is a heavy refactoring and extension of [RAGFlow](https://github.com/infiniflow/ragflow) by InfiniFlow. See the upstream project for the document-understanding foundation (deepdoc, rag, parser pipelines).
