# ADR-0004: 向后兼容 GenericAgent

## 状态
已接受

## 背景
GenericAgent 已有用户基础，需要确保现有配置、工具和工作流能够继续工作。

## 决策
通过适配器层实现向后兼容，新架构可以运行 GenericAgent 原生代码。

## 理由
1. **降低迁移成本**：现有用户无需重写配置
2. **渐进式迁移**：可以逐步采用新特性
3. **保留投资**：用户的记忆库、Skills 等资产不受影响

## 结果
- `GenericAgentLLMAdapter`：封装原生 LLM 调用
- `GenericAgentToolAdapter`：保留 9 个原子工具
- `GenericAgentKernelAdapter`：封装原生 Agent 循环

## 代价
- 需要维护适配器代码
- 某些新特性可能无法完全兼容
