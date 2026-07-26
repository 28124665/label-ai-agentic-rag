# Tasks

## 1. 常量定义与枚举扩展
- [x] 1.1 在 `web/src/constants/agent.tsx` 的 `Operator` 枚举中添加6个新操作符：
  - `Grader = 'Grader'`
  - `HallucinationDetector = 'HallucinationDetector'`
  - `QueryRewriter = 'QueryRewriter'`
  - `SubQueryDecomposer = 'SubQueryDecomposer'`
  - `HyDE = 'HyDE'`
  - `RetryController = 'RetryController'`
- [x] 1.2 在 `web/src/constants/agent.tsx` 中为每个新操作符定义初始表单值：
  - `initialGraderValues`
  - `initialHallucinationDetectorValues`
  - `initialQueryRewriterValues`
  - `initialSubQueryDecomposerValues`
  - `initialHyDEValues`
  - `initialRetryControllerValues`

## 2. 节点组件创建
- [x] 2.1 创建 `web/src/pages/agent/canvas/node/grader-node.tsx`
- [x] 2.2 创建 `web/src/pages/agent/canvas/node/hallucination-detector-node.tsx`
- [x] 2.3 创建 `web/src/pages/agent/canvas/node/query-rewriter-node.tsx`
- [x] 2.4 创建 `web/src/pages/agent/canvas/node/sub-query-decomposer-node.tsx`
- [x] 2.5 创建 `web/src/pages/agent/canvas/node/hyde-node.tsx`
- [x] 2.6 创建 `web/src/pages/agent/canvas/node/retry-controller-node.tsx`

## 3. 表单组件创建
- [x] 3.1 创建 `web/src/pages/agent/form/grader-form/index.tsx`
  - 包含评估模式选择（llm/cross_encoder/local_nli）
  - 批量大小配置
  - 相关性阈值配置
  - 超时时间配置
  - 降级策略配置
- [x] 3.2 创建 `web/src/pages/agent/form/hallucination-detector-form/index.tsx`
  - 检测阈值配置
  - 处置策略配置
  - 权重分配配置
- [x] 3.3 创建 `web/src/pages/agent/form/query-rewriter-form/index.tsx`
  - 复杂度分析方式配置
  - 重写策略选择
  - 同义词扩展配置
- [x] 3.4 创建 `web/src/pages/agent/form/sub-query-decomposer-form/index.tsx`
  - 拆解数量配置
  - RRF 参数配置
  - 语义去重阈值配置
- [x] 3.5 创建 `web/src/pages/agent/form/hyde-form/index.tsx`
  - 启用开关
  - 生成 prompt 配置
  - 温度参数配置
- [x] 3.6 创建 `web/src/pages/agent/form/retry-controller-form/index.tsx`
  - 最大重试次数配置
  - Token 成本上限配置
  - 最小相关文档数配置

## 4. 节点注册与图标配置
- [x] 4.1 在 `web/src/pages/agent/constant/index.tsx` 的 `NodeMap` 中注册6个新节点类型
- [x] 4.2 在 `web/src/pages/agent/operator-icon.tsx` 的 `OperatorIconMap` 中为6个新操作符配置图标

## 5. 操作符面板集成
- [x] 5.1 在 `web/src/pages/agent/canvas/node/dropdown/accordion-operators.tsx` 的 `AccordionOperators` 组件中添加新节点到合适的分类
- [x] 5.2 在 `web/src/pages/agent/hooks/use-add-node.ts` 的 `initialFormValuesMap` 中添加6个新操作符的初始值

## 6. 国际化支持
- [x] 6.1 在 `web/src/locales/en.ts` 中添加英文翻译
- [x] 6.2 在 `web/src/locales/zh.ts` 中添加中文翻译
- [x] 6.3 在 `web/src/locales/en.ts` 和 `web/src/locales/zh.ts` 中添加节点描述文本

## 7. 测试验证
- [x] 7.1 验证所有6个新节点可以在画布上正常显示
- [x] 7.2 验证所有6个新节点的配置表单可以正常打开和保存
- [x] 7.3 验证节点之间的连接和参数传递正常
- [x] 7.4 验证操作符面板中新节点的显示和拖拽功能正常

# Task Dependencies
- Task 2 依赖 Task 1（需要先定义枚举和初始值）
- Task 3 依赖 Task 1（需要初始值定义）
- Task 4 依赖 Task 1（需要枚举定义）
- Task 5 依赖 Task 1、Task 3（需要初始值和表单组件）
- Task 6 依赖 Task 1（需要枚举定义用于翻译键）
- Task 7 依赖 Task 1-6（所有功能完成后进行验证）
