# Checkpoints

## 基础能力建设

- [x] `pyproject.toml` 中已添加 `opencc-python-reimplemented` 依赖
- [x] `rag/res/tw_to_cn.json` 存在且包含至少 12 条台湾用语→大陆用语对照（实际 48 条）
- [x] `rag/res/proper_nouns.json` 存在且包含初始专有名词白名单（实际 27 条）
- [x] `rag/nlp/t2s.py` 实现了 `TraditionalToSimplifiedConverter` 类，支持字形转换、词汇转换、专有名词保护
- [x] `rag/nlp/t2s.py` 提供模块级单例 `default_converter`
- [x] `rag/nlp/lang_detect.py` 实现 `detect_language(text)` 函数，返回 `zh-simplified`/`zh-traditional`/`en`
- [x] `test/test_t2s.py` 覆盖纯繁体字形、台湾用语、专有名词保护、简体保持不变、空字符串场景
- [x] `test/test_lang_detect.py` 覆盖繁/简/英/空文本/混合文本场景

## Chunk 预处理与向量库适配

- [x] `api/utils/chunk_preprocessor.py` 实现 `preprocess_chunk(chunk) -> dict`，输出包含 `original_text`/`original_lang`/`search_text`/`metadata`
- [x] 繁体 chunk 的 `search_text` 为简体；简体/英文 chunk 的 `search_text` 与 `original_text` 相同
- [x] 向量库 Schema 包含 `original_text`/`original_lang`/`search_text` 字段（Infinity / ES / OpenSearch 三套 mapping 均已适配）
- [x] 向量库支持 `original_lang` 字段过滤（secondary 索引 / keyword 类型），保留原有按 kb_id 分区策略
- [x] 提供 `index_chunk(chunk, kb=None, tenant_id=None)` 函数，生成适配新 schema 的 chunk（embedding 模型切换保留为运行时配置）
- [x] `test/test_chunk_preprocessor.py` 覆盖繁/简/英三种 chunk 的字段正确性

## 检索流程改造

- [x] `api/utils/query_preprocessor.py` 实现 `preprocess_query(query) -> dict`，返回 `query_lang`/`query_simplified`/`partition`/`embedding_model`
- [x] 中文查询走 `t2s.convert`；英文查询保持不变
- [x] `api/utils/multilingual_reranker.py` 封装 Rerank 模型，pairs 为 `(query_simplified, result["search_text"])`
- [x] `api/utils/prompt_builder.py` 实现 `build_prompt(query, context, query_lang)`，context 使用 `original_text`
- [x] Prompt 根据 `query_lang` 选择输出语言指令（繁体/简体/英文）
- [x] 检索链路 `agent/tools/retrieval.py::_retrieve_kb` 已接入查询预处理、Rerank；`agent/component/llm.py::_prepare_prompt_variables` 已接入 `prompt_builder.build_prompt`（仅当 chunks 包含 `original_text` 字段时启用，否则保留原 `citation_prompt` 路径）
- [x] `test/test_query_preprocessor.py` 覆盖三种语言的路由决策正确性
- [x] `test/test_multilingual_reranker.py` 验证 pairs 构造与排序正确性
- [x] `test/test_prompt_builder.py` 验证三种语言的输出指令正确

## 集成测试与文档

- [x] 端到端集成测试 `test/test_e2e_retrieval_flow.py` 模拟繁体查询→检索→Rerank→Prompt→返回全流程
- [x] 繁体查询返回繁体答案；简体查询返回简体答案；英文查询返回英文答案
- [x] 检索结果包含 `original_text` 和 `original_lang` 字段
- [x] `uv run pytest` 所有新增测试通过（53 passed in 0.43s）
- [x] `ruff check` 与 `ruff format` 通过（All checks passed; 15 files already formatted）
- [x] 所有新增模块包含模块级文档字符串，说明对应设计文档章节与实现方式
