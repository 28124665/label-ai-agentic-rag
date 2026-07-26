# 二期 RAG 增强功能前端节点支持 - 检查清单

## 功能完整性检查
- [ ] 6 个新操作符已添加到 Operator 枚举（Grader, HallucinationDetector, QueryRewriter, SubQueryDecomposer, HyDE, RetryController）
- [ ] 6 个新操作符的初始表单值已定义
- [ ] 6 个节点组件文件已创建（grader-node.tsx, hallucination-detector-node.tsx, query-rewriter-node.tsx, sub-query-decomposer-node.tsx, hyde-node.tsx, retry-controller-node.tsx）
- [ ] 6 个表单组件文件已创建（grader-form, hallucination-detector-form, query-rewriter-form, sub-query-decomposer-form, hyde-form, retry-controller-form）
- [ ] NodeMap 中已注册 6 个新节点类型
- [ ] OperatorIconMap 中已为 6 个新操作符配置图标
- [ ] AccordionOperators 中已将新节点添加到合适的分类
- [ ] initialFormValuesMap 中已添加 6 个新操作符的初始值
- [ ] 国际化文件（en.ts, zh.ts）中已添加英文和中文翻译
- [ ] 节点描述文本已添加到国际化文件

## 功能验证检查
- [ ] 所有 6 个新节点可以在画布上正常显示
- [ ] 所有 6 个新节点的配置表单可以正常打开和保存
- [ ] 节点之间的连接和参数传递正常
- [ ] 操作符面板中新节点的显示和拖拽功能正常
- [ ] 前端代码无 TypeScript 编译错误
- [ ] 前端代码无 ESLint 错误

## 代码质量检查
- [ ] 节点组件遵循现有代码风格和模式
- [ ] 表单组件使用 React Hook Form 和 Zod 验证
- [ ] 组件正确使用了国际化（i18next）
- [ ] 图标配置与现有节点保持一致
- [ ] 表单字段与后端组件参数对应

## 文档检查
- [ ] spec.md 完整描述了需求变更
- [ ] tasks.md 包含所有实施任务和依赖关系
- [ ] checklist.md 包含所有验证检查点
