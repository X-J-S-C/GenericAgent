# ADR-0002: 采用策略模式的规划器设计

## 状态
已接受

## 背景
Agent 需要不同的推理策略来应对不同类型的任务：
- 简单任务：适合 ReAct（推理 + 行动）
- 复杂推理：适合 Tree of Thought（思维树）
- 自我进化：适合 Reflexion（反思式）

GenericAgent 主要使用简单的 ReAct 模式，而 LangCrew 可能需要更复杂的协作策略。

## 决策
采用 **策略模式（Strategy Pattern）** 设计规划器，允许运行时选择不同的推理策略。

## 理由
1. **灵活性**：可以根据任务类型动态选择规划策略
2. **可扩展性**：新策略只需实现基类接口
3. **保留 GenericAgent 特性**：ReflexionPlanner 继承自我进化机制
4. **统一接口**：所有规划器遵循相同的接口契约

## 结果
定义了 `BasePlanner` 接口，支持：
- `ReActPlanner`：交替推理和行动
- `TreeOfThoughtPlanner`：探索多条推理路径
- `ReflexionPlanner`：基于 GenericAgent 的自我进化

## 代价
- 需要在运行时选择合适的策略
- 策略选择逻辑可能增加复杂度
