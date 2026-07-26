# 二期 RAG 增强功能前端节点支持 Spec

## Why

二期开发实现了6个新的后端组件用于增强 RAG 检索质量（Grader、HallucinationDetector、QueryRewriter、SubQueryDecomposer、HyDE、RetryController），但前端可视化工作流编辑器尚未支持这些组件。用户无法通过 UI 拖拽配置这些节点，限制了二期功能的实际使用。

## What Changes

- 在 `Operator` 枚举中添加6个新的操作符类型
- 为每个新组件创建对应的节点组件（Node Component）
- 为每个新组件创建配置表单（Form Component）
- 在节点注册表（NodeMap）中注册新节点类型
- 在操作符图标映射（OperatorIconMap）中配置图标
- 在操作符面板（AccordionOperators）中添加新节点选项
- 在初始化参数映射（initialFormValuesMap）中配置默认值
- 添加国际化翻译文本

## Impact

- Affected specs: implement-phase2-rag-enhancements
- Affected code: 
  - 前端常量定义：`web/src/constants/agent.tsx`
  - 节点组件：`web/src/pages/agent/canvas/node/`
  - 表单组件：`web/src/pages/agent/form/`
  - 节点注册：`web/src/pages/agent/constant/index.tsx`
  - 图标映射：`web/src/pages/agent/operator-icon.tsx`
  - 操作符面板：`web/src/pages/agent/canvas/node/dropdown/accordion-operators.tsx`
  - 初始化参数：`web/src/pages/agent/hooks/use-add-node.ts`
  - 国际化文件：`web/src/locales/en.ts`, `web/src/locales/zh.ts`

## ADDED Requirements

### Requirement: Grader 节点支持
The system SHALL provide a Grader node in the visual workflow editor for configuring retrieval result evaluation.

#### Scenario: 用户拖拽 Grader 节点到画布
- **WHEN** 用户从操作符面板拖拽 Grader 节点到画布
- **THEN** 画布上显示 Grader 节点，可配置评估模式、批量大小、相关性阈值等参数

### Requirement: HallucinationDetector 节点支持
The system SHALL provide a HallucinationDetector node for configuring hallucination detection and faithfulness verification.

#### Scenario: 用户配置幻觉检测参数
- **WHEN** 用户点击 HallucinationDetector 节点
- **THEN** 弹出配置表单，可配置检测阈值、处置策略、权重分配等参数

### Requirement: QueryRewriter 节点支持
The system SHALL provide a QueryRewriter node for configuring query complexity analysis and rewriting strategies.

#### Scenario: 用户配置查询重写策略
- **WHEN** 用户拖拽 QueryRewriter 节点到画布
- **THEN** 可配置复杂度分析方式、重写策略选择、同义词扩展等参数

### Requirement: SubQueryDecomposer 节点支持
The system SHALL provide a SubQueryDecomposer node for configuring sub-query decomposition and RRF merging.

#### Scenario: 用户配置子查询拆解
- **WHEN** 用户拖拽 SubQueryDecomposer 节点到画布
- **THEN** 可配置拆解数量、RRF 参数、语义去重阈值等参数

### Requirement: HyDE 节点支持
The system SHALL provide a HyDE node for configuring hypothetical document embedding generation.

#### Scenario: 用户配置 HyDE 生成
- **WHEN** 用户拖拽 HyDE 节点到画布
- **THEN** 可配置是否启用、生成 prompt、温度参数等

### Requirement: RetryController 节点支持
The system SHALL provide a RetryController node for configuring retry logic and fallback strategies.

#### Scenario: 用户配置重试控制
- **WHEN** 用户拖拽 RetryController 节点到画布
- **THEN** 可配置最大重试次数、Token 成本上限、最小相关文档数等参数

## MODIFIED Requirements

### Requirement: 操作符面板分类
The system SHALL organize all operators into logical categories in the accordion panel.

**Modified:** 在"数据操作"分类下添加二期新增的6个组件：
- QueryRewriter（查询重写）
- SubQueryDecomposer（子查询拆解）
- HyDE（假设文档嵌入）
- Grader（检索结果评估）
- HallucinationDetector（幻觉检测）
- RetryController（重试控制）

## REMOVED Requirements

N/A
