# OAK-GenericAgent

融合 OpenAkita 企业级多 Agent 协作与 GenericAgent 极简自我进化的新一代 Agent 框架。

## 核心理念

- **六边形架构**：内核与外部依赖完全解耦
- **策略模式规划器**：支持 ReAct、Tree-of-Thought、Reflexion
- **分层记忆系统**：L0-L4 五层记忆，支持自我进化
- **向后兼容**：保留 GenericAgent 全部能力

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                    应用层 (Application)                      │
│  CLI / Web UI / Bot / API Server                           │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    端口层 (Ports)                            │
│  AgentPort / ToolPort / MemoryPort / LLMPort               │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    核心层 (Core)                             │
│  Agent Kernel / State Machine / Event System / Planner     │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    适配器层 (Adapters)                       │
│  GenericAgent / LangCrew / Claude / OpenAI / LangGraph     │
└─────────────────────────────────────────────────────────────┘
```

## 快速开始

```python
from oak_gkernel.main import create_agent, create_layered_memory, create_llm_adapter

# 创建组件
memory = create_layered_memory()
llm = create_llm_adapter("claude", {
    "model": "claude-opus-4",
    "api_key": "your-api-key",
})

# 创建 Agent
agent = create_agent({
    "name": "my-agent",
    "planner": "react",
    "max_turns": 50,
})

# 运行
result = await agent.run("帮我分析今天的天气")
```

## 核心模块

### Agent Kernel
`oak_gkernel/core/agent_kernel.py`

Agent 执行引擎，负责运行 Agent 循环、管理状态和事件。

### Planner (策略模式)
`oak_gkernel/core/planner.py`

| 规划器 | 描述 |
|--------|------|
| ReActPlanner | 交替推理和行动 |
| TreeOfThoughtPlanner | 探索多条推理路径 |
| ReflexionPlanner | 反思式规划（基于 GA 自我进化）|

### Memory (分层记忆)
`oak_gkernel/core/memory.py`

| 层级 | 名称 | 持久化 |
|------|------|--------|
| L0 | 元规则 | 永久 |
| L1 | 记忆索引 | 永久 |
| L2 | 全局事实 | 永久 |
| L3 | 任务 Skills | 永久 |
| L4 | 会话归档 | 临时 |

### Tools (工具系统)
`oak_gkernel/core/tools.py`

保留 GenericAgent 的 9 个原子工具：
- `code_run`: 执行代码
- `file_read/write/patch`: 文件操作
- `web_scan/execute_js`: 浏览器控制
- `ask_user`: 人机协作

## 项目结构

```
oak_gkernel/
├── core/               # 核心领域逻辑
│   ├── agent_kernel.py
│   ├── state_machine.py
│   ├── event_system.py
│   ├── planner.py
│   ├── memory.py
│   └── tools.py
├── ports/              # 端口层接口
│   ├── llm_port.py
│   ├── tool_port.py
│   ├── memory_port.py
│   └── agent_port.py
├── adapters/           # 适配器实现
│   ├── llm/
│   │   ├── generic_agent.py
│   │   ├── anthropic.py
│   │   └── openai.py
│   └── tools/
│       └── generic_agent.py
└── main.py             # 入口函数
```

## 架构决策记录 (ADR)

- [ADR-0001: 采用六边形架构](docs/architecture/adr/ADR-0001-hexagon-architecture.md)
- [ADR-0002: 策略模式的规划器设计](docs/architecture/adr/ADR-0002-planner-strategy.md)
- [ADR-0003: 分层记忆系统](docs/architecture/adr/ADR-0003-memory-levels.md)
- [ADR-0004: 向后兼容](docs/architecture/adr/ADR-0004-backward-compatibility.md)

## 迁移策略

### Phase 0: 原型验证（1-2 周）
- [x] 搭建项目骨架
- [x] 定义核心接口
- [ ] 实现最小可运行版本

### Phase 1: 骨架建设（2-4 周）
- [ ] 完善 Agent 内核
- [ ] 实现 LLM 端口
- [ ] 集成 GenericAgent 工具

### Phase 2: 增量迁移（持续）
- [ ] 迁移记忆系统
- [ ] 添加 LangCrew 多 Agent 支持
- [ ] 完善前端和可视化

## 开发指南

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest tests/

# 代码检查
ruff check oak_gkernel/

# 类型检查
mypy oak_gkernel/
```

## License

MIT License
