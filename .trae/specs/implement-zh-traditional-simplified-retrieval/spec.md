# 简繁中文检索与向量库存储适配 Spec

## Why

RAG 系统服务于鸿海（富士康）集团管理层，知识库中繁体中文占 60%、简体中文占 30%、英文占 10%。当前系统未对繁简差异做适配，导致：
- 繁体查询与简体文档之间因字形/词汇差异（如"軟體/软件"、"記憶體/内存"）导致召回率仅 60-70%
- 用户查询语言与返回答案语言不一致，台湾管理层体验差
- 主流中文 Embedding 模型训练数据偏向简体，直接对繁体做 Embedding 效果有限

通过双字段存储（`original_text` + `search_text`）+ 语言路由方案，可在不破坏原文展示的前提下让检索统一在简体语义空间，同时为英文文档单独建索引以保留精度，目标召回率 ≥ 90%。

## What Changes

- **新增** `rag/nlp/t2s.py`：繁简转换工具模块（字形转换 + 台湾用语词汇转换 + 专有名词白名单保护）
- **新增** `rag/nlp/lang_detect.py`：语言检测模块（区分 zh-simplified / zh-traditional / en）
- **新增** `rag/res/tw_to_cn.json`：台湾用语 → 大陆用语词典
- **新增** `rag/res/proper_nouns.json`：专有名词白名单（不做繁简转换）
- **新增** `api/utils/chunk_preprocessor.py`：Chunk 预处理模块（生成 `original_text` + `search_text` 双字段）
- **新增** `api/utils/query_preprocessor.py`：查询预处理模块（语言检测 + 繁转简 + 路由决策）
- **新增** `api/utils/multilingual_reranker.py`：多语言 Rerank 封装（基于 `search_text` 统一排序）
- **新增** `api/utils/prompt_builder.py`：Prompt 构建器（用 `original_text` + 输出语言指令）
- **修改** `rag/nlp/search.py`：检索入口支持按语言路由（中文/英文 Partition）
- **修改** `agent/component/retrieval.py`（或等价位置）：集成双字段存储与语言路由
- **新增测试** `test/test_t2s.py`、`test/test_lang_detect.py`、`test/test_chunk_preprocessor.py`、`test/test_query_preprocessor.py`、`test/test_multilingual_reranker.py`、`test/test_prompt_builder.py`

## Impact

- **Affected specs**: 无（独立模块）
- **Affected code**:
  - `rag/nlp/`：新增 t2s、lang_detect 模块，补充 res 资源
  - `api/utils/`：新增 4 个工具模块
  - `rag/nlp/search.py`、`agent/component/retrieval.py`：检索链路接入新模块
  - 向量库 Schema：需支持 `original_text`、`original_lang`、`search_text`、`vector` 字段及按语言分区
- **外部依赖**：新增 `opencc-python-reimplemented`（或 `OpenCC`）用于繁简字形转换

## ADDED Requirements

### Requirement: 繁简转换模块

系统 SHALL 提供繁体转简体的转换能力，同时完成字形转换与台湾用语词汇转换，并保护专有名词不被转换。

#### Scenario: 纯繁体字形转换
- **WHEN** 输入 "供應鏈管理是企業競爭力的核心"
- **THEN** 输出 "供应链管理是企业竞争力的核心"

#### Scenario: 台湾用语词汇转换
- **WHEN** 输入 "伺服器的軟體需要更新"
- **THEN** 输出 "服务器的软件需要更新"

#### Scenario: 专有名词保护
- **WHEN** 输入 "鴻海精密的 iPhone 16 供應鏈"
- **THEN** "鴻海" 不被转换（在白名单中），输出 "鴻海精密的 iPhone 16 供应链"

#### Scenario: 简体原文保持不变
- **WHEN** 输入已经是简体 "供应链管理优化方案"
- **THEN** 输出保持 "供应链管理优化方案"

### Requirement: 语言检测模块

系统 SHALL 能够检测文本语言，输出 `zh-simplified`、`zh-traditional`、`en` 三种标识之一。

#### Scenario: 检测繁体中文
- **WHEN** 输入 "供應鏈管理的最佳實踐"
- **THEN** 返回 `zh-traditional`

#### Scenario: 检测简体中文
- **WHEN** 输入 "供应链管理的最佳实践"
- **THEN** 返回 `zh-simplified`

#### Scenario: 检测英文
- **WHEN** 输入 "Supply chain management best practices"
- **THEN** 返回 `en`

#### Scenario: 空文本兜底
- **WHEN** 输入空字符串
- **THEN** 返回 `zh-simplified`（默认值）

### Requirement: Chunk 双字段存储

系统 SHALL 为每个 chunk 同时存储 `original_text`（原文）和 `search_text`（简体或英文），且 `vector` 字段基于 `search_text` 计算。

#### Scenario: 繁体文档入库
- **WHEN** 入库 chunk 文本为 "供應鏈管理是企業競爭力的核心"
- **THEN** 存储 `original_text="供應鏈管理是企業競爭力的核心"`，`original_lang="zh-traditional"`，`search_text="供应链管理是企业竞争力的核心"`

#### Scenario: 简体文档入库
- **WHEN** 入库 chunk 文本为 "供应链管理优化方案"
- **THEN** 存储 `original_text="供应链管理优化方案"`，`original_lang="zh-simplified"`，`search_text="供应链管理优化方案"`

#### Scenario: 英文文档入库
- **WHEN** 入库 chunk 文本为 "Supply chain management best practices"
- **THEN** 存储 `original_text="Supply chain management best practices"`，`original_lang="en"`，`search_text="Supply chain management best practices"`

### Requirement: 查询预处理与语言路由

系统 SHALL 在查询时先检测语言，对中文做繁转简，然后路由到对应语言的向量库分区。

#### Scenario: 繁体查询路由
- **WHEN** 用户查询 "供應鏈管理的最佳實踐是什麼？"
- **THEN** 查询预处理为 "供应链管理的最佳实践是什么？" 并路由到 `chinese` 分区，使用中文 Embedding 模型

#### Scenario: 英文查询路由
- **WHEN** 用户查询 "What are the best practices in supply chain management?"
- **THEN** 查询保持不变并路由到 `english` 分区，使用英文 Embedding 模型

### Requirement: Rerank 基于 search_text

系统 SHALL 在 Rerank 阶段使用 `search_text` 作为文档文本与（已转换为简体的）查询组成 pair 进行打分。

#### Scenario: Rerank 用 search_text
- **WHEN** 对检索结果做 Rerank
- **THEN** Rerank 模型输入 pair 为 `(query_simplified, result["search_text"])`，保证查询和文档在同一语义空间

### Requirement: Prompt 构建与输出语言控制

系统 SHALL 在构建 LLM Prompt 时使用 `original_text` 作为参考资料，并根据查询语言添加输出语言指令。

#### Scenario: 繁体查询输出繁体答案
- **WHEN** 查询语言为 `zh-traditional`
- **THEN** Prompt 中参考资料使用 `original_text`，并包含指令 "请用繁体中文回答。"

#### Scenario: 简体查询输出简体答案
- **WHEN** 查询语言为 `zh-simplified`
- **THEN** Prompt 包含指令 "请用简体中文回答。"

#### Scenario: 英文查询输出英文答案
- **WHEN** 查询语言为 `en`
- **THEN** Prompt 包含指令 "Please answer in English."

### Requirement: 向量库分区设计

系统 SHALL 在 Milvus 中按语言创建 Partition（`chinese` / `english`），中文文档用 `bge-large-zh`，英文文档用 `text-embedding-3-small`。

#### Scenario: 中文文档入中文分区
- **WHEN** 文档语言为 `zh-simplified` 或 `zh-traditional`
- **THEN** 使用 `bge-large-zh` 生成 vector 并写入 `chinese` 分区

#### Scenario: 英文文档入英文分区
- **WHEN** 文档语言为 `en`
- **THEN** 使用 `text-embedding-3-small` 生成 vector 并写入 `english` 分区

## MODIFIED Requirements

### Requirement: 检索结果字段返回

检索结果除原有字段外，SHALL 额外返回 `original_text`、`original_lang`、`search_text`，供前端展示和上层 Rerank/Prompt 使用。

## REMOVED Requirements

无（本 spec 为新增能力，不删除现有功能）。
