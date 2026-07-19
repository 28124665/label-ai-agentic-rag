# PRD 需求 vs 当前项目实现 — 差距分析

> 基于 `prd.md`（Agentic RAG 智能知识问答系统需求规格说明书）与当前 RAGFlow 项目代码的逐条比对
>
> 分析日期：2026-07-07

---

## 📊 总览

| 状态 | 数量 | 说明 |
|------|------|------|
| ✅ 已覆盖 | 7 | 功能已实现，满足需求 |
| 🔶 部分实现 | 5 | 核心能力有，但细节需调整 |
| ❌ 未实现 | 2 | 当前项目无对应功能 |

---

## 一、功能需求（FR）逐条分析

### FR-01：用户输入接收与状态初始化

**状态：🔶 部分实现（细节需调整）**

| 需求点 | 当前实现 | 匹配度 |
|--------|---------|--------|
| REST API 接收用户问题 | `api/apps/conversation_app.py` 提供完整的对话 API | ✅ 已实现 |
| 流式输出（SSE） | `conversation_app.py` 支持 SSE streaming 响应 | ✅ 已实现 |
| 同步输出 | 支持同步和流式两种模式 | ✅ 已实现 |
| thread_id 会话隔离 | `sdk/session.py`、`sdk/chat.py` 通过 session_id 隔离 | ✅ 已实现 |
| State 结构（messages, query, retrieved_docs, graded_docs, retry_count 等） | RAGFlow 使用自己的状态管理机制，不是 PRD 描述的 LangGraph State 结构 | 🔶 需调整 |
| max_retries 可配置 | 对话配置中有相关参数 | ✅ 已实现 |

**差距说明**：
- RAGFlow 不使用 LangGraph 的 StateGraph，而是使用自有的 Canvas/Agent 状态管理
- State 字段命名和结构与 PRD 描述不完全一致，但功能等价
- 若需严格对齐 PRD，需在 Agent 组件中显式定义 `graded_docs`、`retry_count`、`is_hallucination` 等字段

---

### FR-02：意图分析与查询路由

**状态：✅ 已覆盖**

| 需求点 | 当前实现 | 匹配度 |
|--------|---------|--------|
| 意图分类 | `agent/component/categorize.py` — Categorize 组件，LLM 驱动的意图分类 | ✅ 已实现 |
| 支持 direct/vector/web/graph 分类 | Categorize 组件支持自定义分类类别，通过配置 Prompt 实现 | ✅ 已实现 |
| 动态路由 | `agent/component/switch.py` — Switch 组件，条件分支路由 | ✅ 已实现 |
| 路由决策记录 | Agent Canvas 的执行日志记录路由过程 | ✅ 已实现 |
| 可通过配置新增意图类别 | Categorize 组件的类别通过 Prompt 配置，可灵活调整 | ✅ 已实现 |

**实现位置**：
- 意图分类：[agent/component/categorize.py](file:///Users/renwk/workspace/data-knowledge-api/agent/component/categorize.py)
- 条件路由：[agent/component/switch.py](file:///Users/renwk/workspace/data-knowledge-api/agent/component/switch.py)

---

### FR-03：多工具调用框架（Tool Calling）

**状态：✅ 已覆盖**

| 需求点 | 当前实现 | 匹配度 |
|--------|---------|--------|
| 统一 Tool 抽象接口 | `agent/tools/base.py` — ToolBase 基类，定义 name/description/execute | ✅ 已实现 |
| RAGTool | `agent/tools/retrieval.py` — 知识库检索工具 | ✅ 已实现 |
| WebSearchTool | `agent/tools/tavily.py`、`agent/tools/google.py`、`agent/tools/searxng.py` | ✅ 已实现 |
| DirectAnswerTool | Agent 可直接调用 LLM 回答，无需检索 | ✅ 已实现 |
| Agent 自主选择工具 | `agent/component/agent_with_tools.py` — LLM 决策工具选择 | ✅ 已实现 |
| 工具组合调用 | Agent 组件支持多工具顺序/组合调用 | ✅ 已实现 |
| 调用结果记录 | 工具执行结果写入 Agent 状态 | ✅ 已实现 |

**实现位置**：
- 工具基类：[agent/tools/base.py](file:///Users/renwk/workspace/data-knowledge-api/agent/tools/base.py)
- Agent 工具调用：[agent/component/agent_with_tools.py](file:///Users/renwk/workspace/data-knowledge-api/agent/component/agent_with_tools.py)
- 预置工具目录：[agent/tools/](file:///Users/renwk/workspace/data-knowledge-api/agent/tools)

---

### FR-04：RAG Tool — 混合检索（Hybrid Search）

**状态：✅ 已覆盖**

| 需求点 | 当前实现 | 匹配度 |
|--------|---------|--------|
| 向量语义检索 | ES/Infinity 的向量检索，基于 Embedding 模型 | ✅ 已实现 |
| BM25 关键词检索 | ES 全文检索，基于 BM25 算法 | ✅ 已实现 |
| 双路并行检索 | `internal/service/search.go` 和 `internal/engine/` 实现双路检索 | ✅ 已实现 |
| 动态权重分配 | 支持配置向量与全文的权重比例（`similarity` 参数） | ✅ 已实现 |
| RRF 融合算法 | 检索结果融合排序 | ✅ 已实现 |
| Top-K 可配置 | 知识库配置中可设置检索数量 | ✅ 已实现 |

**差距说明**：
- PRD 要求根据查询类型（短查询/长查询/事实性/概念性）自动调整权重配比，RAGFlow 的权重是静态配置的，非动态自适应
- 若需完全对齐，需在检索前增加"查询类型判断"节点，动态调整权重

**实现位置**：
- Go 搜索引擎：[internal/engine/](file:///Users/renwk/workspace/data-knowledge-api/internal/engine)
- ES 检索：[internal/engine/elasticsearch/search.go](file:///Users/renwk/workspace/data-knowledge-api/internal/engine/elasticsearch/search.go)
- Infinity 检索：[internal/engine/infinity/search.go](file:///Users/renwk/workspace/data-knowledge-api/internal/engine/infinity/search.go)

---

### FR-05：RAG Tool — 重排序（Rerank）

**状态：✅ 已覆盖**

| 需求点 | 当前实现 | 匹配度 |
|--------|---------|--------|
| Cross-Encoder 模型 | `rag/llm/rerank_model.py` 支持多种 Rerank 模型 | ✅ 已实现 |
| 支持 BGE-reranker | 支持 BGE Reranker 系列 | ✅ 已实现 |
| Top-N 可配置 | 可配置重排序后截断数量 | ✅ 已实现 |
| 相关性分数记录 | Rerank 结果包含分数 | ✅ 已实现 |
| Go 层 Reranker | `internal/service/nlp/reranker.go` 提供高性能重排序 | ✅ 已实现 |

**实现位置**：
- Python Rerank：[rag/llm/rerank_model.py](file:///Users/renwk/workspace/data-knowledge-api/rag/llm/rerank_model.py)
- Go Reranker：[internal/service/nlp/reranker.go](file:///Users/renwk/workspace/data-knowledge-api/internal/service/nlp/reranker.go)

---

### FR-06：Web Search Tool — 网络搜索兜底

**状态：✅ 已覆盖**

| 需求点 | 当前实现 | 匹配度 |
|--------|---------|--------|
| 集成第三方搜索 API | Tavily（`agent/tools/tavily.py`）、Google（`agent/tools/google.py`）、SearxNG（`agent/tools/searxng.py`） | ✅ 已实现 |
| 搜索结果格式对齐 | 搜索结果包含标题、摘要、来源链接 | ✅ 已实现 |
| 搜索结果经相关性评估 | 可在 Agent 工作流中将搜索结果送入评估节点 | ✅ 已实现 |
| 返回数量可配置 | 搜索工具参数可配置 | ✅ 已实现 |
| 仅在无结果或 Agent 决策时触发 | 通过 Categorize + Switch 组件实现条件触发 | ✅ 已实现 |

**实现位置**：
- Tavily 工具：[agent/tools/tavily.py](file:///Users/renwk/workspace/data-knowledge-api/agent/tools/tavily.py)
- Tavily 连接器：[rag/utils/tavily_conn.py](file:///Users/renwk/workspace/data-knowledge-api/rag/utils/tavily_conn.py)
- Google 搜索：[agent/tools/google.py](file:///Users/renwk/workspace/data-knowledge-api/agent/tools/google.py)
- SearxNG：[agent/tools/searxng.py](file:///Users/renwk/workspace/data-knowledge-api/agent/tools/searxng.py)

---

### FR-07：检索结果相关性评估（Grader）

**状态：🔶 部分实现（细节需调整）**

| 需求点 | 当前实现 | 匹配度 |
|--------|---------|--------|
| LLM 对文档二分类评估 | Agent 工作流中可通过 LLM 组件实现评估逻辑，但没有独立的 Grader 组件 | 🔶 需新增 |
| 评估结果写入 graded_docs | 无显式的 graded_docs 字段 | 🔶 需调整 |
| 根据评估结果路由 | Switch 组件可实现条件路由，但需与 Grader 联动 | 🔶 需调整 |
| 全部不相关时触发重写/兜底 | 需在 Agent Canvas 中手动编排此逻辑 | 🔶 需编排 |
| Prompt 可配置 | LLM 组件的 Prompt 可配置 | ✅ 已实现 |

**差距说明**：
- RAGFlow 没有独立的 "Grader" 组件，相关性评估需要通过 LLM 组件 + Switch 组件组合实现
- 建议新增一个专用的 `Grader` 组件，封装相关性评估逻辑，使其可复用
- 当前没有显式的 `graded_docs` 状态字段，评估结果分散在 Agent 执行过程中

**建议**：新增 `agent/component/grader.py`，实现独立的相关性评估组件

---

### FR-08：查询重写与重试机制（Query Rewriting）

**状态：🔶 部分实现（细节需调整）**

| 需求点 | 当前实现 | 匹配度 |
|--------|---------|--------|
| 查询重写 | `rag/nlp/query.py` 有查询处理逻辑；Agent 中可通过 LLM 组件重写查询 | 🔶 需封装 |
| 重试计数 | Agent Canvas 的 Loop 组件可计数，但没有专用的 retry_count | 🔶 需调整 |
| 最大重试次数可配置 | 可配置 | ✅ 已实现 |
| 同义词扩展 | `rag/nlp/synonym.py` 有同义词处理 | ✅ 已实现 |
| 子查询拆解 | 无显式的子查询拆解组件 | ❌ 未实现 |
| HyDE（假设性答案检索） | 无 HyDE 实现 | ❌ 未实现 |
| 重试后重新执行检索→评估 | Loop 组件可实现循环，但需手动编排 | 🔶 需编排 |

**差距说明**：
- RAGFlow 有 `loop.py`（循环）和 `iteration.py`（迭代）组件，可以构建重试循环
- 但缺少 PRD 要求的**分级重试策略**（第1次同义词扩展 → 第2次子查询拆解 → 第3次 HyDE）
- 需要新增：
  1. 子查询拆解组件
  2. HyDE 组件
  3. 将重试策略封装为可配置的组件

**实现位置**：
- 循环组件：[agent/component/loop.py](file:///Users/renwk/workspace/data-knowledge-api/agent/component/loop.py)
- 迭代组件：[agent/component/iteration.py](file:///Users/renwk/workspace/data-knowledge-api/agent/component/iteration.py)
- 同义词：[rag/nlp/synonym.py](file:///Users/renwk/workspace/data-knowledge-api/rag/nlp/synonym.py)

---

### FR-09：答案生成（Generator）

**状态：✅ 已覆盖**

| 需求点 | 当前实现 | 匹配度 |
|--------|---------|--------|
| Prompt 包含角色设定+上下文+问题 | `rag/prompts/generator.py` 定义生成器 Prompt 模板 | ✅ 已实现 |
| 仅根据上下文作答 | Prompt 中包含约束指令 | ✅ 已实现 |
| 流式输出 | LLM 组件支持 streaming 模式 | ✅ 已实现 |
| 引用标记 | 生成内容包含引用标记（如 `[doc1]`） | ✅ 已实现 |
| 结果写入 final_answer | Agent 执行结果包含最终答案 | ✅ 已实现 |

**实现位置**：
- LLM 组件：[agent/component/llm.py](file:///Users/renwk/workspace/data-knowledge-api/agent/component/llm.py)
- 生成器 Prompt：[rag/prompts/generator.py](file:///Users/renwk/workspace/data-knowledge-api/rag/prompts/generator.py)

---

### FR-10：幻觉检测（Hallucination Detection）

**状态：❌ 未实现**

| 需求点 | 当前实现 | 匹配度 |
|--------|---------|--------|
| LLM 忠实度评估 | 无独立的幻觉检测组件 | ❌ 未实现 |
| 论断拆解与逐一验证 | 无此逻辑 | ❌ 未实现 |
| is_hallucination 字段 | 无此状态字段 | ❌ 未实现 |
| 验证失败触发重新生成 | 无此闭环逻辑 | ❌ 未实现 |
| 重新生成最多 2 次 | 无此机制 | ❌ 未实现 |

**差距说明**：
- RAGFlow 当前**没有幻觉检测功能**
- 这是 PRD 中的 P0 需求，需要重点开发
- 建议实现方案：
  1. 新增 `agent/component/hallucination_detector.py` 组件
  2. 使用 LLM 对答案进行忠实度评估
  3. 在 Agent Canvas 中编排：生成 → 幻觉检测 → (失败)重新生成 的闭环

---

### FR-11：多轮对话记忆（Memory）

**状态：✅ 已覆盖**

| 需求点 | 当前实现 | 匹配度 |
|--------|---------|--------|
| 对话历史管理 | `memory/services/messages.py` 管理对话消息 | ✅ 已实现 |
| thread_id 隔离 | `memory_api.py` 通过 thread_id 隔离会话 | ✅ 已实现 |
| 持久化存储 | 支持 ES/Infinity/PostgreSQL/OceanBase 多种存储后端 | ✅ 已实现 |
| 滑动窗口记忆 | 支持配置记忆窗口大小 | ✅ 已实现 |
| 摘要记忆 | `memory/utils/aggregation_utils.py` 支持记忆聚合与摘要 | ✅ 已实现 |

**实现位置**：
- Memory 服务：[memory/services/](file:///Users/renwk/workspace/data-knowledge-api/memory/services)
- Memory API：[api/apps/restful_apis/memory_api.py](file:///Users/renwk/workspace/data-knowledge-api/api/apps/restful_apis/memory_api.py)
- 记忆聚合：[memory/utils/aggregation_utils.py](file:///Users/renwk/workspace/data-knowledge-api/memory/utils/aggregation_utils.py)

---

### FR-12：状态持久化与检查点（Checkpointer）

**状态：🔶 部分实现（细节需调整）**

| 需求点 | 当前实现 | 匹配度 |
|--------|---------|--------|
| 节点执行后保存状态 | Agent Canvas 执行过程中有状态管理 | 🔶 部分实现 |
| 断点续跑 | Go 层 Canvas 支持 Redis CheckPoint（官方已实现） | ✅ 已实现 |
| 时间旅行（回溯历史状态） | 无明确的时间旅行功能 | ❌ 未实现 |
| 检查点存储 | 使用 Redis 存储检查点 | ✅ 已实现 |

**差距说明**：
- RAGFlow 的 Go 层 Canvas 已实现基于 Redis 的检查点机制（官方 PR #16035）
- 但缺少"时间旅行"功能（回溯到任意历史状态进行调试）
- 建议：在管理后台增加检查点查看和回溯功能

**实现位置**：
- Canvas 引擎：[agent/canvas.py](file:///Users/renwk/workspace/data-knowledge-api/agent/canvas.py)
- Canvas 版本管理：[api/db/services/user_canvas_version.py](file:///Users/renwk/workspace/data-knowledge-api/api/db/services/user_canvas_version.py)

---

### FR-13：答案溯源与引用（Citation）

**状态：✅ 已覆盖**

| 需求点 | 当前实现 | 匹配度 |
|--------|---------|--------|
| 答案包含引用标记 | 生成答案包含 `[docN]` 引用标记 | ✅ 已实现 |
| 引用详情（文档名称、页码、原文片段） | 返回结果包含 chunk_id、doc_id、similarity 等引用信息 | ✅ 已实现 |
| 前端引用高亮展示 | 前端支持引用展示和跳转 | ✅ 已实现 |
| 引用可追溯到源文档 | 引用链接到具体的文档和段落 | ✅ 已实现 |

**实现位置**：
- 引用 Prompt：[rag/prompts/citation_prompt.md](file:///Users/renwk/workspace/data-knowledge-api/rag/prompts/citation_prompt.md)
- 对话 API：[api/apps/conversation_app.py](file:///Users/renwk/workspace/data-knowledge-api/api/apps/conversation_app.py)

---

### FR-14：可观测性与监控（Observability）

**状态：🔶 部分实现（细节需调整）**

| 需求点 | 当前实现 | 匹配度 |
|--------|---------|--------|
| 链路追踪 | 集成 Langfuse，支持 LLM 调用追踪 | ✅ 已实现 |
| 各节点延迟指标 | Langfuse 可追踪部分延迟，但非全链路 | 🔶 需增强 |
| 检索命中率 | 无自动采集 | ❌ 未实现 |
| 幻觉检测通过率 | 无幻觉检测，故无此指标 | ❌ 未实现 |
| 结构化日志 | Promtail 采集日志，但非结构化格式 | 🔶 需调整 |
| 告警配置 | 无告警机制 | ❌ 未实现 |
| 可视化 Dashboard | Langfuse 提供部分 Dashboard | 🔶 部分实现 |

**差距说明**：
- Langfuse 集成已实现，但主要覆盖 LLM 调用追踪
- 缺少业务指标采集（命中率、幻觉率、重试率等）
- 缺少告警机制
- 建议：
  1. 增加 Prometheus metrics 导出
  2. 集成 Grafana Dashboard
  3. 配置告警规则（如命中率 < 阈值告警）

**实现位置**：
- Langfuse API：[api/apps/langfuse_app.py](file:///Users/renwk/workspace/data-knowledge-api/api/apps/langfuse_app.py)
- Langfuse 服务：[api/db/services/langfuse_service.py](file:///Users/renwk/workspace/data-knowledge-api/api/db/services/langfuse_service.py)
- 日志采集：[deployment/promtail-config-prod.yaml](file:///Users/renwk/workspace/data-knowledge-api/deployment/promtail-config-prod.yaml)

---

## 二、非功能需求（NFR）逐条分析

### NFR-01：性能要求

**状态：🔶 部分实现**

| 指标 | 目标值 | 当前实现 | 匹配度 |
|------|--------|---------|--------|
| 端到端延迟 P95 | < 10s | Go 服务层提升性能；Embedding LRU 缓存减少重复调用 | 🔶 需验证 |
| 检索延迟 P95 | < 2s | Go 搜索引擎 + C++ 分词引擎，性能较高 | ✅ 可满足 |
| Rerank 延迟 P95 | < 1s | 支持多种 Rerank 模型 | 🔶 需验证 |
| 意图分析延迟 | < 500ms | Categorize 组件调用 LLM，取决于模型响应速度 | 🔶 需验证 |
| 并发吞吐量 ≥ 50 QPS | Go 服务层支持高并发 | ✅ 可满足 |

**建议**：进行压力测试，验证各项延迟指标是否达标

---

### NFR-02：可用性要求

**状态：🔶 部分实现**

| 需求点 | 当前实现 | 匹配度 |
|--------|---------|--------|
| 系统可用性 ≥ 99.5% | Docker + K8s 部署，支持多副本 | ✅ 基础设施支持 |
| 优雅降级（LLM 不可用时返回检索结果） | 无明确的降级逻辑 | ❌ 需新增 |
| 熔断机制 | 无熔断实现 | ❌ 需新增 |

**建议**：
- 在 LLM 调用层增加超时和降级逻辑
- 集成熔断库（如 Python 的 `pybreaker` 或 Go 的 `sony/gobreaker`）

---

### NFR-03：扩展性要求

**状态：✅ 已覆盖**

| 需求点 | 当前实现 | 匹配度 |
|--------|---------|--------|
| 向量数据库水平扩展 | ES/Infinity 支持分片+副本 | ✅ 已实现 |
| 工作流节点动态注册 | Agent 组件系统支持新增组件 | ✅ 已实现 |
| Tool 插件化 | `agent/plugin/` 插件系统 | ✅ 已实现 |

---

### NFR-04：安全要求

**状态：✅ 已覆盖**

| 需求点 | 当前实现 | 匹配度 |
|--------|---------|--------|
| API Key 认证 | `api/apps/auth/` 支持多种认证方式 | ✅ 已实现 |
| RBAC 角色访问控制 | `admin/server/roles.py` 实现角色管理 | ✅ 已实现 |
| OAuth/GitHub/OIDC 认证 | `api/apps/auth/github.py`、`oauth.py`、`oidc.py` | ✅ 已实现 |
| 敏感信息通过环境变量配置 | `conf/service_conf.yaml` 支持环境变量 | ✅ 已实现 |
| 审计日志 | Pipeline 操作日志 + Langfuse 追踪 | ✅ 已实现 |

**实现位置**：
- 认证模块：[api/apps/auth/](file:///Users/renwk/workspace/data-knowledge-api/api/apps/auth)
- 角色管理：[admin/server/roles.py](file:///Users/renwk/workspace/data-knowledge-api/admin/server/roles.py)

---

### NFR-05：部署要求

**状态：✅ 已覆盖**

| 需求点 | 当前实现 | 匹配度 |
|--------|---------|--------|
| Docker 容器化部署 | `Dockerfile`、`Dockerfile-Honghai` | ✅ 已实现 |
| Kubernetes 编排 | `helm/` 目录提供 Helm Charts | ✅ 已实现 |
| 一键部署脚本 | `docker/docker-compose.yml`、`start.sh` | ✅ 已实现 |

---

## 三、完整流程拓扑对比

### PRD 要求的流程

```
用户输入 → 意图分析 → [路由] → RAG Tool / Web Search / Direct / Graph
                                    ↓
                            混合检索 → Rerank → 相关性评估(Grader)
                                                    ↓
                                    ┌───────────────┼───────────────┐
                                    ↓               ↓               ↓
                              有相关文档      不相关&重试<3次    不相关&重试=3次
                                    ↓               ↓               ↓
                              答案生成        查询重写→重新检索    Web搜索兜底
                                    ↓
                              幻觉检测
                                    ↓
                          ┌─────────┼─────────┐
                          ↓                   ↓
                    验证通过            验证失败&重试<2次
                          ↓                   ↓
                    返回答案+引用        重新生成答案
```

### 当前项目可编排的流程

```
用户输入 → Begin → Categorize(意图分析) → Switch(路由)
                                              ↓
                              ┌───────────────┼───────────────┐
                              ↓               ↓               ↓
                        Retrieval       SearxNG/Tavily     LLM(直接回答)
                        (混合检索)       (Web搜索)
                              ↓               ↓
                        Rerank(重排序)    ─────┘
                              ↓
                    ┌── LLM(相关性评估) ──┐  ← 需新增 Grader 组件
                    ↓                      ↓
              有相关文档              不相关
                    ↓                      ↓
              LLM(答案生成)     ┌── Loop(循环) ──┐  ← 需编排重试策略
              + 引用标记        ↓                │
                    ↓        查询重写(LLM)      │
              ┌── 结束 ──┐     ↓                │
              ↓          ↓  重新检索 → Rerank ──┘
        验证通过    验证失败
        返回答案    重新生成(需手动编排)
```

**流程差距**：
- ❌ 缺少独立的 Grader 组件（相关性评估）
- ❌ 缺少幻觉检测闭环
- ❌ 缺少分级重试策略（同义词→子查询→HyDE）
- 🔶 现有 Loop 组件可实现重试循环，但需手动编排

---

## 四、开发优先级建议

### 🔴 P0 — 必须实现（核心闭环缺失）

| 需求 | 工作量 | 建议方案 |
|------|--------|---------|
| **FR-10 幻觉检测** | 中 | 新增 `agent/component/hallucination_detector.py`，使用 LLM 评估忠实度 |
| **FR-07 Grader 组件** | 小 | 新增 `agent/component/grader.py`，封装相关性二分类评估 |
| **FR-08 分级重试策略** | 中 | 新增子查询拆解组件 + HyDE 组件，封装重试策略 |

### 🟡 P1 — 建议实现（增强功能）

| 需求 | 工作量 | 建议方案 |
|------|--------|---------|
| FR-04 动态权重 | 小 | 在检索前增加查询类型判断，动态调整向量/BM25 权重 |
| FR-12 时间旅行 | 中 | 在管理后台增加检查点查看和回溯 UI |
| FR-14 指标采集+告警 | 中 | 集成 Prometheus + Grafana，配置告警规则 |
| NFR-02 优雅降级+熔断 | 小 | LLM 调用层增加超时降级和熔断逻辑 |

### 🟢 P2 — 可选优化

| 需求 | 工作量 | 建议方案 |
|------|--------|---------|
| FR-01 State 结构对齐 | 小 | 在 Agent 状态中显式定义 PRD 要求的字段 |
| NFR-01 性能验证 | - | 进行压力测试，输出性能报告 |

---

## 五、总结

### 当前项目已具备的能力

1. ✅ **完整的 Agent 编排系统**：22 个组件，支持循环、分支、迭代
2. ✅ **混合检索能力**：向量 + BM25，支持多引擎
3. ✅ **Rerank 重排序**：多种 Cross-Encoder 模型
4. ✅ **多工具调用**：Tavily/Google/SearxNG 等搜索工具
5. ✅ **多轮对话记忆**：完整的 Memory 系统
6. ✅ **答案引用溯源**：引用标记 + 前端展示
7. ✅ **安全与部署**：RBAC、OAuth、Docker、K8s

### 需要补充的核心能力

1. ❌ **幻觉检测闭环**（FR-10）— P0 缺失
2. 🔶 **独立的相关性 Grader 组件**（FR-07）— 需用组件封装
3. 🔶 **分级查询重写策略**（FR-08）— 缺 HyDE 和子查询拆解
4. 🔶 **动态检索权重**（FR-04）— 当前为静态配置
5. 🔶 **业务指标监控与告警**（FR-14）— 缺 Prometheus/Grafana

### 架构差异说明

PRD 基于 **LangChain + LangGraph** 设计，而 RAGFlow 使用**自有的 Canvas/Agent 架构**。两者在概念上等价：

| PRD 概念 | RAGFlow 等价物 |
|---------|---------------|
| LangGraph StateGraph | Agent Canvas |
| State 字段 | Canvas 变量/共享数据 |
| Node | Agent Component |
| Edge/Conditional Edge | Switch Component |
| Checkpointer | Redis CheckPoint（Go 层） |
| Tool | agent/tools/ 工具 |
| MessagesState | Memory 系统 |

因此，实现 PRD 需求时，不需要引入 LangGraph，而是利用 RAGFlow 现有的 Agent 组件系统进行编排和扩展。
