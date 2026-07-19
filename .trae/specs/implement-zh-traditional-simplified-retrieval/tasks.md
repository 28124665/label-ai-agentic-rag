# Tasks

## 阶段一：基础能力建设

- [x] Task 1: 准备依赖与资源文件
  - [x] SubTask 1.1: 在 `pyproject.toml` 添加 `opencc-python-reimplemented` 依赖（或 `OpenCC`）
  - [x] SubTask 1.2: 创建 `rag/res/tw_to_cn.json`，初始包含台湾用语→大陆用语对照（軟體/软件、硬體/硬件、網路/网络、資料庫/数据库、記憶體/内存、伺服器/服务器、專案/项目、品管/质检、供應鏈/供应链、企業/企业、競爭/竞争、優化/优化 等）
  - [x] SubTask 1.3: 创建 `rag/res/proper_nouns.json`，初始包含富士康/鴻海/台积电/夏普/iPhone/SAP/ERP/MES 等专有名词

- [x] Task 2: 实现繁简转换模块 `rag/nlp/t2s.py`
  - [x] SubTask 2.1: 实现 `TraditionalToSimplifiedConverter` 类，封装 OpenCC 实例（懒加载）
  - [x] SubTask 2.2: 实现 `convert(text)` 方法：字形转换 + 台湾用语词汇转换
  - [x] SubTask 2.3: 实现专有名词白名单保护逻辑（用 placeholder 替换→转换→恢复）
  - [x] SubTask 2.4: 提供模块级单例 `default_converter` 供外部调用
  - [x] SubTask 2.5: 添加模块级文档字符串，说明设计文档对应章节

- [x] Task 3: 实现语言检测模块 `rag/nlp/lang_detect.py`
  - [x] SubTask 3.1: 实现 `detect_language(text)` 函数，返回 `zh-simplified` / `zh-traditional` / `en`
  - [x] SubTask 3.2: 基于字符比例统计：英文 > 50% 判定 en；中文占比 > 30% 时用繁体特征字占比 > 30% 判定繁体
  - [x] SubTask 3.3: 空文本或无中文时默认返回 `zh-simplified`
  - [x] SubTask 3.4: 添加模块级文档字符串说明判定规则

- [x] Task 4: 为基础模块编写单元测试
  - [x] SubTask 4.1: `test/test_t2s.py`：覆盖纯繁体字形、台湾用语、专有名词保护、简体保持不变、空字符串等场景
  - [x] SubTask 4.2: `test/test_lang_detect.py`：覆盖繁/简/英/空文本/混合文本场景

## 阶段二：Chunk 预处理与向量库适配

- [x] Task 5: 实现 Chunk 预处理模块 `api/utils/chunk_preprocessor.py`
  - [x] SubTask 5.1: 实现 `preprocess_chunk(chunk: dict) -> dict`，返回包含 `original_text`/`original_lang`/`search_text`/`metadata` 的结构
  - [x] SubTask 5.2: 繁体走 `t2s.convert` 生成 `search_text`；简体/英文 `search_text` 与 `original_text` 相同
  - [x] SubTask 5.3: 添加模块级文档字符串说明双字段存储设计

- [x] Task 6: 向量库 Schema 与分区适配
  - [x] SubTask 6.1: 在 `conf/infinity_mapping.json`、`conf/mapping.json`、`conf/os_mapping.json` 中增加 `original_text`/`original_lang`/`search_text` 字段定义
  - [x] SubTask 6.2: 在 schema 中增加 `original_lang` 字段（带 secondary 索引/keyword 类型）用于语言过滤，保留按 kb_id 分区策略不变（最小侵入式适配）
  - [x] SubTask 6.3: 提供 `index_chunk(chunk, kb=None, tenant_id=None)` 函数：调用 preprocess_chunk 生成适配新 schema 的 chunk；embedding 模型切换保留为运行时配置（最小实现，保持向后兼容）

- [x] Task 7: 为 Chunk 预处理编写单元测试
  - [x] SubTask 7.1: `test/test_chunk_preprocessor.py`：覆盖繁/简/英三种 chunk 的预处理输出字段正确性

## 阶段三：检索流程改造

- [x] Task 8: 实现查询预处理模块 `api/utils/query_preprocessor.py`
  - [x] SubTask 8.1: 实现 `preprocess_query(query) -> dict`，返回 `query_lang`、`query_simplified`、`partition`、`embedding_model`
  - [x] SubTask 8.2: 中文走 `t2s.convert`；英文保持不变
  - [x] SubTask 8.3: 添加模块级文档字符串说明查询预处理与路由决策

- [x] Task 9: 实现多语言 Rerank 封装 `api/utils/multilingual_reranker.py`
  - [x] SubTask 9.1: 封装 `bge-reranker-v2-m3`（或可注入的 Cross-Encoder 模型）
  - [x] SubTask 9.2: 实现 `rerank(query_simplified, results, top_k)`，pairs 为 `(query_simplified, result["search_text"])`
  - [x] SubTask 9.3: 添加模块级文档字符串说明为何用 `search_text` 而非 `original_text`

- [x] Task 10: 实现 Prompt 构建器 `api/utils/prompt_builder.py`
  - [x] SubTask 10.1: 实现 `build_prompt(query, context, query_lang) -> str`，context 由 `original_text` 拼接
  - [x] SubTask 10.2: 根据 `query_lang` 选择输出语言指令（繁体/简体/英文）
  - [x] SubTask 10.3: 添加模块级文档字符串说明输出语言控制策略

- [x] Task 11: 检索链路集成
  - [x] SubTask 11.1: 修改 `agent/tools/retrieval.py` 的 `_retrieve_kb` 方法，接入 `preprocess_query` → 用 `query_simplified` 做检索 → 返回双字段结果
  - [x] SubTask 11.2: 在检索后调用 `multilingual_reranker.rerank` 做重排（仅当 rerank_mdl 不为 None 时）
  - [x] SubTask 11.3: 在 `agent/component/llm.py` 新增 `_build_enhanced_prompt` 方法，调用 `prompt_builder.build_prompt`；仅当 chunks 包含 `original_text` 字段时启用，否则保留原 `citation_prompt` 路径

- [x] Task 12: 为检索流程编写单元测试
  - [x] SubTask 12.1: `test/test_query_preprocessor.py`：覆盖繁/简/英三种查询的路由决策正确性
  - [x] SubTask 12.2: `test/test_multilingual_reranker.py`：Mock Rerank 模型，验证 pairs 构造与排序正确性
  - [x] SubTask 12.3: `test/test_prompt_builder.py`：验证三种语言的输出指令正确

## 阶段四：集成测试与文档

- [x] Task 13: 端到端集成测试
  - [x] SubTask 13.1: `test/test_e2e_retrieval_flow.py`：模拟繁体查询→检索→Rerank→Prompt→返回的全流程
  - [x] SubTask 13.2: 验证繁体查询返回繁体答案、简体查询返回简体答案、英文查询返回英文答案
  - [x] SubTask 13.3: 验证检索结果包含 `original_text` 和 `original_lang` 字段

- [x] Task 14: 运行测试与 lint
  - [x] SubTask 14.1: 运行 `uv run pytest test/test_t2s.py test/test_lang_detect.py test/test_chunk_preprocessor.py test/test_query_preprocessor.py test/test_multilingual_reranker.py test/test_prompt_builder.py test/test_e2e_retrieval_flow.py`（53 passed in 0.43s）
  - [x] SubTask 14.2: 运行 `ruff check` 与 `ruff format` 保证代码规范（All checks passed; 15 files already formatted）

# Task Dependencies

- Task 2、Task 3 依赖 Task 1（依赖资源文件）
- Task 4 依赖 Task 2、Task 3
- Task 5 依赖 Task 2、Task 3
- Task 6 依赖 Task 5
- Task 7 依赖 Task 5
- Task 8 依赖 Task 2、Task 3
- Task 9、Task 10 依赖 Task 8
- Task 11 依赖 Task 6、Task 8、Task 9、Task 10
- Task 12 依赖 Task 8、Task 9、Task 10
- Task 13 依赖 Task 11
- Task 14 依赖 Task 4、Task 7、Task 12、Task 13

# Parallelizable Work

- Task 2 与 Task 3 可并行
- Task 9 与 Task 10 可并行
- Task 4、Task 7、Task 12 三个测试任务可并行（在依赖的源码任务完成后）
