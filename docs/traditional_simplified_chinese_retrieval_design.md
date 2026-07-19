# 简繁中文检索与向量库存储适配方案设计

## 一、背景与目标

### 1.1 业务背景

RAG 系统服务于鸿海（富士康）集团管理层，用户群体以台湾高层领导为主，使用繁体中文进行查询。知识库中的文档语言分布如下：

| 语言类型 | 占比 | 说明 |
|---------|------|------|
| 繁体中文 | 60% | 台湾管理层报告、会议纪要等 |
| 简体中文 | 30% | 大陆工厂报告、生产文档等 |
| 英文 | 10% | 国际文档、技术手册、合同等 |


### 1.2 核心挑战

1. **繁简差异**：台湾用语与大陆用语存在大量差异（如"軟體/软件"、"硬體/硬件"、"網路/网络"）
2. **字形差异**：繁体与简体字形不同，影响向量检索的语义匹配
3. **Embedding 模型偏向**：主流中文 Embedding 模型训练数据以简体为主，对繁体支持有限
4. **多语言混合**：知识库中存在繁简英三种语言混合的情况

### 1.3 设计目标

| 目标 | 指标 |
|------|------|
| **检索准确率** | 繁简中文检索召回率 ≥ 90% |
| **用户体验** | 用户查询语言与返回答案语言一致 |
| **性能** | 查询延迟 < 500ms |
| **成本** | 存储成本增加 ≤ 30% |
| **可维护性** | 支持未来新增语言，无需重构核心逻辑 |

---

## 二、方案概述

### 2.1 核心设计原则

```
┌─────────────────────────────────────────────────────────────────┐
│  存储层：统一简体（检索用）                                       │
│  显示层：保留原文（展示用）                                       │
│  查询层：繁转简（匹配用）                                        │
│  输出层：指令控制（体验用）                                       │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 方案选型对比

| 方案 | 索引策略 | 召回率 | 成本 | 复杂度 | 推荐度 |
|------|---------|--------|------|--------|--------|
| A. 统一转简体 | 全部转简体，1 个索引 | 80-85% | 1x | 低 | ⭐⭐⭐⭐ |
| B. 单索引 + 多语言 Embedding | 保留原文，1 个索引 | 85-90% | 1x | 中 | ⭐⭐⭐ |
| C. 双索引（繁简各一份） | 繁/简各建一份 | 90-95% | 2x | 高 | ⭐⭐⭐ |
| **D. 双字段存储 + 语言路由** | **原文 + 简体，2 个索引（中/英）** | **90-95%** | **1.3x** | **中** | **⭐⭐⭐⭐⭐** |

### 2.3 最终方案：双字段存储 + 语言路由

```
┌──────────────────────────────────────────────────────────────────────┐
│  中文索引（主索引，90% 文档）                                         │
│  ├─ 简体文档(30%) → 原文索引                                         │
│  ├─ 繁体文档(60%) → 转简体后索引                                     │
│  └─ 每个 chunk 存储：original_text（原文）+ search_text（简体）        │
│                                                                      │
│  英文索引（辅索引，10% 文档）                                         │
│  ├─ 英文文档(10%) → 原文索引                                         │
│  └─ 使用英文专用 Embedding 模型                                       │
│                                                                      │
│  查询路由：                                                           │
│  ├─ 中文查询（繁/简）→ 转简体 → 中文索引                             │
│  └─ 英文查询 → 直接 → 英文索引                                       │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 三、详细设计

### 3.1 数据存储设计

#### 3.1.1 Chunk 存储结构

每个文档 chunk 在向量库中存储为以下结构：

```json
{
  "chunk_id": "abc123",
  "kb_id": "knowledge_base_001",
  
  "original_text": "供應鏈管理是企業競爭力的核心",
  "original_lang": "zh-traditional",
  
  "search_text": "供应链管理是企业竞争力的核心",
  
  "vector": [0.12, -0.34, 0.56, ...],
  
  "metadata": {
    "doc_title": "2024年供應鏈管理報告",
    "page": 15,
    "department": "供應鏈管理中心",
    "author": "張三"
  }
}
```

#### 3.1.2 字段说明

| 字段 | 类型 | 说明 | 用途 |
|------|------|------|------|
| `chunk_id` | string | chunk 唯一标识 | 索引管理 |
| `kb_id` | string | 知识库 ID | 多知识库隔离 |
| `original_text` | string | 原文（保留原语言） | 显示给用户、写进 Prompt |
| `original_lang` | string | 原文语言标识 | 前端渲染样式、语言过滤 |
| `search_text` | string | 简体翻译文本 | 用于检索匹配 |
| `vector` | float[] | 基于 search_text 的向量 | 向量检索 |
| `metadata` | object | 文档元数据 | 过滤、排序、展示 |

#### 3.1.3 语言标识规范

| 标识 | 说明 | 示例 |
|------|------|------|
| `zh-simplified` | 简体中文 | "供应链管理" |
| `zh-traditional` | 繁体中文 | "供應鏈管理" |
| `en` | 英文 | "Supply chain management" |

### 3.2 入库流程设计

#### 3.2.1 流程图

```
┌─────────────────────────────────────────────────────────────────────┐
│  文档入库流程                                                         │
│                                                                     │
│  原始文档                                                             │
│    ↓                                                                │
│  ① 文档分块（Chunking）                                              │
│    ↓                                                                │
│  ② 语言检测（Language Detection）                                    │
│    ↓                                                                │
│  ③ 文本预处理                                                        │
│    ├─ 中文文档 → 繁简转换 → search_text（简体）                      │
│    └─ 英文文档 → 保持不变 → search_text（英文）                      │
│    ↓                                                                │
│  ④ Embedding                                                         │
│    ├─ 中文文档 → bge-large-zh → vector                              │
│    └─ 英文文档 → text-embedding-3-small → vector                    │
│    ↓                                                                │
│  ⑤ 存入向量库                                                        │
│    ├─ 中文文档 → 中文索引（Milvus partition: chinese）               │
│    └─ 英文文档 → 英文索引（Milvus partition: english）               │
└─────────────────────────────────────────────────────────────────────┘
```

#### 3.2.2 核心代码

```python
import opencc
import re
from typing import Dict, Any

# 繁简转换器
t2s_converter = opencc.OpenCC('t2s')  # 繁体转简体

# 台湾用语 → 大陆用语 自定义词典
TW_TO_CN_DICT = {
    "軟体": "软件",
    "硬體": "硬件",
    "網路": "网络",
    "資料庫": "数据库",
    "記憶體": "内存",
    "伺服器": "服务器",
    "專案": "项目",
    "品管": "质检",
    "供應鏈": "供应链",
    "企業": "企业",
    "競爭": "竞争",
    "優化": "优化",
}


def detect_language(text: str) -> str:
    """检测文本语言"""
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    traditional_chars = len(re.findall(r'[體學軟體網路資]', text))
    english_chars = len(re.findall(r'[a-zA-Z]', text))
    total = len(text)
    
    if total == 0:
        return "zh-simplified"
    
    if english_chars / total > 0.5:
        return "en"
    elif chinese_chars / total > 0.3:
        if traditional_chars / max(chinese_chars, 1) > 0.3:
            return "zh-traditional"
        else:
            return "zh-simplified"
    else:
        return "zh-simplified"


def convert_tw_to_cn(text: str) -> str:
    """繁体转简体（字形 + 词汇）"""
    # 1. 字形转换
    text = t2s_converter.convert(text)
    
    # 2. 词汇转换（台湾用语 → 大陆用语）
    for tw, cn in TW_TO_CN_DICT.items():
        text = text.replace(tw, cn)
    
    return text


def preprocess_chunk(chunk: Dict[str, Any]) -> Dict[str, Any]:
    """文档 chunk 入库预处理"""
    original_text = chunk["text"]
    lang = detect_language(original_text)
    
    # 生成 search_text
    if lang in ["zh-simplified", "zh-traditional"]:
        search_text = convert_tw_to_cn(original_text)
    else:
        search_text = original_text
    
    return {
        "chunk_id": chunk["chunk_id"],
        "kb_id": chunk["kb_id"],
        "original_text": original_text,
        "original_lang": lang,
        "search_text": search_text,
        "metadata": chunk.get("metadata", {}),
    }


def index_chunk(chunk: Dict[str, Any]):
    """将 chunk 索引到向量库"""
    # 1. 预处理
    processed = preprocess_chunk(chunk)
    
    # 2. Embedding
    if processed["original_lang"] in ["zh-simplified", "zh-traditional"]:
        vector = chinese_embedding_model.encode(processed["search_text"])
        partition = "chinese"
    else:
        vector = english_embedding_model.encode(processed["search_text"])
        partition = "english"
    
    # 3. 存入向量库
    vector_store.insert(
        partition=partition,
        data={
            "chunk_id": processed["chunk_id"],
            "kb_id": processed["kb_id"],
            "original_text": processed["original_text"],
            "original_lang": processed["original_lang"],
            "search_text": processed["search_text"],
            "vector": vector,
            "metadata": processed["metadata"],
        }
    )
```

### 3.3 检索流程设计

#### 3.3.1 流程图

```
┌─────────────────────────────────────────────────────────────────────┐
│  查询流程                                                             │
│                                                                     │
│  用户查询："供應鏈管理的最佳實踐是什麼？"（繁体）                       │
│    ↓                                                                │
│  ① 语言检测                                                          │
│    → zh-traditional                                                  │
│    ↓                                                                │
│  ② 查询预处理                                                        │
│    ├─ 中文查询 → 繁转简 → "供应链管理的最佳实践是什么？"               │
│    └─ 英文查询 → 保持不变                                            │
│    ↓                                                                │
│  ③ 路由决策                                                          │
│    ├─ 中文 → 中文索引（partition: chinese）                          │
│    └─ 英文 → 英文索引（partition: english）                          │
│    ↓                                                                │
│  ④ Embedding + 向量检索                                              │
│    → 返回 Top-K 结果（每个结果包含 original_text + search_text）      │
│    ↓                                                                │
│  ⑤ Rerank（用 search_text）                                          │
│    → rerank_pairs = [(query_simplified, result["search_text"])]      │
│    → 按 Rerank 分数排序                                              │
│    ↓                                                                │
│  ⑥ 构建 Prompt（用 original_text）                                   │
│    → context = "\n".join([result["original_text"] for result in ...])│
│    → 添加指令："请用繁体中文回答"                                     │
│    ↓                                                                │
│  ⑦ 调用 LLM                                                         │
│    → 返回繁体答案                                                    │
└─────────────────────────────────────────────────────────────────────┘
```

#### 3.3.2 核心代码

```python
def search(query: str, top_k: int = 5) -> Dict[str, Any]:
    """完整的 RAG 检索流程"""
    
    # ① 语言检测
    query_lang = detect_language(query)
    
    # ② 查询预处理
    if query_lang in ["zh-simplified", "zh-traditional"]:
        query_simplified = convert_tw_to_cn(query)
        partition = "chinese"
        embedding_model = chinese_embedding_model
    else:
        query_simplified = query
        partition = "english"
        embedding_model = english_embedding_model
    
    # ③ Embedding + 向量检索
    query_vector = embedding_model.encode(query_simplified)
    results = vector_store.search(
        partition=partition,
        vector=query_vector,
        top_k=20  # 先检索 Top-20，后续 Rerank 筛选
    )
    
    # ④ Rerank（用 search_text）
    rerank_pairs = [(query_simplified, result["search_text"]) for result in results]
    rerank_scores = reranker.predict(rerank_pairs)
    
    # 按 Rerank 分数排序
    ranked_results = sorted(
        zip(results, rerank_scores),
        key=lambda x: x[1],
        reverse=True
    )[:top_k]
    
    # ⑤ 构建 Prompt（用 original_text）
    context = "\n\n".join([
        result["original_text"] for result, score in ranked_results
    ])
    
    # ⑥ 调用 LLM
    prompt = build_prompt(query, context, query_lang)
    answer = llm.generate(prompt)
    
    return {
        "answer": answer,
        "references": [
            {
                "text": result["original_text"],
                "lang": result["original_lang"],
                "score": score,
                "metadata": result["metadata"],
            }
            for result, score in ranked_results
        ]
    }


def build_prompt(query: str, context: str, query_lang: str) -> str:
    """构建 LLM Prompt"""
    
    # 根据查询语言决定输出语言指令
    if query_lang == "zh-traditional":
        output_instruction = "请用繁体中文回答。"
    elif query_lang == "zh-simplified":
        output_instruction = "请用简体中文回答。"
    else:
        output_instruction = "Please answer in English."
    
    prompt = f"""
请根据以下参考资料回答问题。

参考资料：
{context}

用户问题：{query}

{output_instruction}
"""
    return prompt
```

### 3.4 Rerank 策略

#### 3.4.1 为什么用 search_text 做 Rerank？

| 选项 | 优势 | 劣势 |
|------|------|------|
| **search_text（推荐）** | 查询和文档在同一语义空间（简体），Rerank 效果最好 | 翻译可能丢失信息 |
| original_text | 保留原文语义 | 查询（简体）vs 文档（繁体）不在同一语义空间，效果差 |

#### 3.4.2 Rerank 模型选择

| 模型 | 支持语言 | 说明 |
|------|---------|------|
| `BAAI/bge-reranker-v2-m3` | 100+ 语言 | **推荐**，多语言 Rerank 最优 |
| `cohere/rerank-multilingual-v3.0` | 100+ 语言 | API 调用，成本高 |

### 3.5 输出语言控制

#### 3.5.1 策略：Prompt 指令控制

```python
# 根据查询语言决定输出语言
if query_lang == "zh-traditional":
    output_instruction = "请用繁体中文回答。"
elif query_lang == "zh-simplified":
    output_instruction = "请用简体中文回答。"
else:
    output_instruction = "Please answer in English."
```

#### 3.5.2 为什么不用后处理转换？

| 方式 | 优势 | 劣势 |
|------|------|------|
| **Prompt 指令（推荐）** | 简单直接，模型能力强 | 可能混合英文 |
| 后处理转换 | 精确控制 | 可能引入转换错误，增加延迟 |

---

## 四、向量库设计

### 4.1 Milvus 分区设计

```python
from pymilvus import Collection, Partition

# 创建 Collection
collection = Collection(name="knowledge_base")

# 创建 Partition（按语言分区）
partition_zh = Partition(collection, name="chinese")
partition_en = Partition(collection, name="english")

# Schema 设计
schema = CollectionSchema(
    fields=[
        FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=64, is_primary=True),
        FieldSchema(name="kb_id", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="original_text", dtype=DataType.VARCHAR, max_length=65535),
        FieldSchema(name="original_lang", dtype=DataType.VARCHAR, max_length=32),
        FieldSchema(name="search_text", dtype=DataType.VARCHAR, max_length=65535),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=1024),
        FieldSchema(name="metadata", dtype=DataType.JSON),
    ]
)
```

### 4.2 Embedding 模型选择

| 语言 | 模型 | 维度 | 说明 |
|------|------|------|------|
| 中文 | `BAAI/bge-large-zh` | 1024 | 中文最优，繁简混合训练 |
| 英文 | `openai/text-embedding-3-small` | 1536 | 英文最优，成本低 |

---

## 五、性能评估

### 5.1 延迟分析

| 阶段 | 延迟 | 说明 |
|------|------|------|
| 语言检测 | ~5ms | 正则匹配 |
| 查询预处理 | ~10ms | 繁简转换 |
| Embedding | ~50ms | 单次推理 |
| 向量检索 | ~100ms | Milvus 查询 |
| Rerank | ~150ms | Cross-Encoder 推理 |
| LLM 生成 | ~500ms | 流式输出 |
| **总计** | **~815ms** | 可接受范围 |

### 5.2 存储成本

| 项目 | 成本 | 说明 |
|------|------|------|
| 向量存储 | 1x | 每个 chunk 一个向量 |
| 文本存储 | 1.3x | original_text + search_text |
| **总计** | **~1.3x** | 可接受范围 |

---

## 六、风险评估与应对

### 6.1 风险清单

| 风险 | 影响 | 概率 | 应对措施 |
|------|------|------|---------|
| 繁简转换错误 | 检索失败 | 中 | 维护自定义词典，持续积累 |
| 专有名词转换错误 | 信息丢失 | 中 | 专有名词白名单，跳过转换 |
| 英文术语翻译不准 | 检索失败 | 低 | 英文文档单独建索引 |
| Rerank 模型效果差 | 排序不准 | 低 | 使用 bge-reranker-v2-m3 |

### 6.2 专有名词白名单

```python
# 不要转换的专有名词
PROPER_NOUNS = {
    "富士康", "鴻海", "台积电", "夏普",
    "iPhone", "SAP", "ERP", "MES",
    # ... 持续积累
}

def convert_tw_to_cn(text: str) -> str:
    # 1. 保护专有名词
    placeholders = {}
    for i, noun in enumerate(PROPER_NOUNS):
        if noun in text:
            placeholder = f"__PROPER_NOUN_{i}__"
            placeholders[placeholder] = noun
            text = text.replace(noun, placeholder)
    
    # 2. 繁简转换
    text = t2s_converter.convert(text)
    for tw, cn in TW_TO_CN_DICT.items():
        text = text.replace(tw, cn)
    
    # 3. 恢复专有名词
    for placeholder, noun in placeholders.items():
        text = text.replace(placeholder, noun)
    
    return text
```

---

## 七、实施计划

### 7.1 阶段划分

| 阶段 | 任务 | 周期 |
|------|------|------|
| **阶段一** | 基础能力建设 | 2 周 |
| | - 繁简转换模块 | |
| | - 自定义词典 | |
| | - 语言检测 | |
| **阶段二** | 向量库改造 | 2 周 |
| | - 双字段存储 | |
| | - 分区设计 | |
| | - 数据迁移 | |
| **阶段三** | 检索流程改造 | 2 周 |
| | - 查询预处理 | |
| | - Rerank 集成 | |
| | - Prompt 构建 | |
| **阶段四** | 测试与优化 | 2 周 |
| | - 效果测试 | |
| | - 性能优化 | |
| | - 词典迭代 | |

### 7.2 关键里程碑

| 里程碑 | 时间 | 交付物 |
|--------|------|--------|
| M1 | 第 2 周 | 繁简转换模块 + 词典 |
| M2 | 第 4 周 | 向量库改造完成 |
| M3 | 第 6 周 | 检索流程改造完成 |
| M4 | 第 8 周 | 测试通过，上线 |

---

## 八、总结

### 8.1 核心设计要点

1. **双字段存储**：`original_text`（原文）+ `search_text`（简体）
2. **语言路由**：中文查询 → 中文索引，英文查询 → 英文索引
3. **Rerank 用简体**：查询和文档在同一语义空间，效果最好
4. **Prompt 用原文**：保留原文语义，模型理解更准确
5. **输出指令控制**：根据查询语言决定输出语言

### 8.2 预期收益

| 指标 | 优化前 | 优化后 | 提升 |
|------|--------|--------|------|
| 繁体检索召回率 | 60-70% | 90%+ | +30% |
| 用户体验 | 简繁混合 | 语言一致 | 显著提升 |
| 检索延迟 | ~500ms | ~800ms | 可接受 |

### 8.3 后续优化方向

1. **词典持续积累**：根据实际检索效果，持续补充台湾用语词典
2. **Embedding 模型微调**：用富士康内部文档微调 Embedding 模型
3. **跨语言检索**：支持中文查询检索英文文档（翻译查询）
4. **个性化排序**：根据用户部门/角色，调整检索结果排序
