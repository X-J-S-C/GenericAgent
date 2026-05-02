# OAK-GenericAgent 融合架构设计

## 一、项目定位与愿景

融合 **OpenAkita (OAK/LangCrew)** 的企业级多 Agent 协作能力与 **GenericAgent** 的极简自我进化特性，打造一个既具备强大协作能力又保持轻量级可进化特性的新一代 Agent 框架。

**核心理念**：
- 保留 GenericAgent 的"不预设技能，靠进化获得"哲学
- 引入 OpenAkita 的多 Agent 协作和可视化能力
- 采用六边形架构实现高度解耦和可插拔性

## 二、架构概览

```
┌─────────────────────────────────────────────────────────────────────┐
│                         应用层 (Application)                          │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────────────────────┐│
│  │ CLI     │  │ Web UI  │  │ Bot     │  │ API Server              ││
│  └─────────┘  └─────────┘  └─────────┘  └─────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    端口层 (Ports / Interfaces)                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐│
│  │ AgentPort│  │ToolPort │  │MemoryPort│  │LLMPort  │  │UI Port ││
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └────────┘│
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    领域层 (Domain / Core)                             │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    Agent Kernel                                │   │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────────────────┐  │   │
│  │  │ Agent Loop │  │ State Mgmt │  │ Event System           │  │   │
│  │  └────────────┘  └────────────┘  └────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    Planner (Strategy Pattern)                  │   │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────────────────┐  │   │
│  │  │ ReAct      │  │ ToT        │  │ Reflexion             │  │   │
│  │  └────────────┘  └────────────┘  └────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    适配器层 (Adapters)                              │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────────────────┐ │
│  │ Tool Adapters │  │Memory Adapters│  │ LLM Provider Adapters    │ │
│  │  - GenericAgent│  │  - GA Memory  │  │  - Claude                │ │
│  │  - LangCrew   │  │  - LangGraph  │  │  - OpenAI                │ │
│  │  - Custom     │  │  - Redis      │  │  - Kimi/MiniMax/etc     │ │
│  └───────────────┘  └───────────────┘  └───────────────────────────┘ │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────────────────┐ │
│  │ Multi-Agent   │  │ Frontend      │  │ Observability            │ │
│  │  - Crew       │  │ Adapters      │  │  - Langfuse              │ │
│  │  - Network    │  │  - Streamlit  │  │  - LangSmith             │ │
│  │  - MessageBus│  │  - React      │  │  - OpenTelemetry         │ │
│  └───────────────┘  └───────────────┘  └───────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

## 三、核心模块设计

### 3.1 Agent Kernel (内核)

**职责**：运行可配置的 Agent 循环，管理状态和事件

```python
# oak_gkernel/core/agent_kernel.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict, Callable
from enum import Enum, auto

class AgentState(Enum):
    IDLE = auto()
    RUNNING = auto()
    WAITING_INPUT = auto()
    PAUSED = auto()
    COMPLETED = auto()
    ERROR = auto()

@dataclass
class Transition:
    from_state: AgentState
    to_state: AgentState
    event: str
    action: Optional[Callable] = None

class AgentKernel(ABC):
    """Agent 内核接口 - 六边形架构的核心"""
    
    @abstractmethod
    def run(self, query: str, context: Optional[Dict] = None) -> 'AgentResult':
        """执行 Agent 循环"""
        pass
    
    @abstractmethod
    def pause(self) -> None:
        """暂停执行"""
        pass
    
    @abstractmethod
    def resume(self, input_data: Any) -> None:
        """恢复执行"""
        pass
    
    @abstractmethod
    def get_state(self) -> AgentState:
        """获取当前状态"""
        pass
```

### 3.2 Planner 接口 (策略模式)

**职责**：定义规划策略接口，支持多种推理方式

```python
# oak_gkernel/core/planner.py
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

@dataclass
class PlanStep:
    thought: str
    action: str
    args: Dict[str, Any]
    expected_outcome: Optional[str] = None

class BasePlanner(ABC):
    """规划器基类 - 使用策略模式"""
    
    @abstractmethod
    async def plan(self, query: str, context: Dict) -> List[PlanStep]:
        """生成执行计划"""
        pass
    
    @abstractmethod
    async def reflect(self, history: List[Dict]) -> Dict[str, Any]:
        """反思执行结果"""
        pass

class ReActPlanner(BasePlanner):
    """ReAct (Reasoning + Acting) 规划器"""
    pass

class TreeOfThoughtPlanner(BasePlanner):
    """思维树规划器"""
    pass

class ReflexionPlanner(BasePlanner):
    """反思式规划器 - 基于 GenericAgent 的自我进化"""
    pass
```

### 3.3 Tool Port (工具端口)

**职责**：定义工具接口，支持可插拔的工具系统

```python
# oak_gkernel/ports/tool_port.py
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Type
from dataclasses import dataclass
import json

@dataclass
class ToolSchema:
    name: str
    description: str
    parameters: Dict[str, Any]
    
@dataclass
class ToolResult:
    success: bool
    data: Any
    error: Optional[str] = None
    metadata: Optional[Dict] = None

class ToolPort(ABC):
    """工具端口接口"""
    
    @abstractmethod
    def get_schema(self) -> List[ToolSchema]:
        """获取工具 schema"""
        pass
    
    @abstractmethod
    async def execute(self, tool_name: str, args: Dict) -> ToolResult:
        """执行工具"""
        pass

class BaseTool(ABC):
    """工具基类"""
    
    @property
    @abstractmethod
    def schema(self) -> ToolSchema:
        pass
    
    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        pass
```

### 3.4 Memory Port (记忆端口)

**职责**：定义记忆系统接口，支持多种记忆实现

```python
# oak_gkernel/ports/memory_port.py
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from enum import Enum, auto

class MemoryLevel(Enum):
    L0_META = auto()      # 元规则
    L1_INSIGHT = auto()   # 记忆索引
    L2_FACTS = auto()     # 全局事实
    L3_SKILL = auto()     # 任务技能
    L4_SESSION = auto()   # 会话归档

@dataclass
class MemoryEntry:
    level: MemoryLevel
    key: str
    value: Any
    metadata: Dict = None
    
class MemoryPort(ABC):
    """记忆端口接口"""
    
    @abstractmethod
    async def store(self, entry: MemoryEntry) -> bool:
        """存储记忆"""
        pass
    
    @abstractmethod
    async def recall(self, level: MemoryLevel, key: str) -> Optional[Any]:
        """召回记忆"""
        pass
    
    @abstractmethod
    async def search(self, query: str, levels: List[MemoryLevel] = None) -> List[MemoryEntry]:
        """搜索记忆"""
        pass
    
    @abstractmethod
    async def crystallize(self, session_id: str) -> List[MemoryEntry]:
        """将会话结晶为长期记忆"""
        pass

class LAMemory(MemoryPort):
    """GenericAgent 分层记忆实现"""
    
    async def crystallize(self, session_id: str) -> List[MemoryEntry]:
        """从会话中提取经验，固化为 Skill"""
        # 实现 GenericAgent 的自我进化机制
        pass

class LangGraphMemory(MemoryPort):
    """LangGraph 记忆实现"""
    pass
```

### 3.5 LLM Port (模型端口)

**职责**：定义 LLM 接口，支持多种模型提供者

```python
# oak_gkernel/ports/llm_port.py
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, AsyncIterator
from dataclasses import dataclass
from enum import Enum

class MessageRole(Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

@dataclass
class Message:
    role: MessageRole
    content: Any
    tool_calls: Optional[List[Dict]] = None
    tool_results: Optional[List[Dict]] = None

@dataclass
class LLMResponse:
    content: str
    tool_calls: List[Dict]
    reasoning: Optional[str] = None
    raw: Any = None

class LLMPort(ABC):
    """LLM 端口接口"""
    
    @abstractmethod
    async def chat(self, messages: List[Message], 
                   tools: Optional[List[Dict]] = None,
                   **kwargs) -> LLMResponse:
        """发送对话请求"""
        pass
    
    @abstractmethod
    async def stream(self, messages: List[Message],
                     tools: Optional[List[Dict]] = None,
                     **kwargs) -> AsyncIterator[str]:
        """流式响应"""
        pass

class ClaudeAdapter(LLMPort):
    """Anthropic Claude 适配器"""
    pass

class OpenAIAdapter(LLMPort):
    """OpenAI 适配器"""
    pass

class GenericAgentLLMAdapter(LLMPort):
    """GenericAgent 原生 LLM 适配器"""
    # 保持与现有 GenericAgent LLM 实现的兼容性
    pass
```

## 四、适配器设计

### 4.1 GenericAgent 适配器

**目的**：保留 GenericAgent 的核心能力，作为新架构的适配器

```python
# oak_gkernel/adapters/generic_agent/
from oak_gkernel.core import AgentKernel, BaseTool, ToolPort, MemoryPort
from typing import List, Dict, Any
import ga  # 导入 GenericAgent 核心模块

class GenericAgentKernelAdapter(AgentKernel):
    """GenericAgent 内核适配器"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.handler = ga.GenericAgentHandler(...)
        self.llm_adapter = GenericAgentLLMAdapter(config.get('llm'))
    
    def run(self, query: str, context: Optional[Dict] = None):
        # 封装 GenericAgent 的 agent_runner_loop
        pass

class GenericAgentToolAdapter(BaseTool):
    """GenericAgent 工具适配器"""
    
    TOOLS = [
        'code_run', 'file_read', 'file_write', 'file_patch',
        'web_scan', 'web_execute_js', 'ask_user',
        'update_working_checkpoint', 'start_long_term_update'
    ]
    
    async def execute(self, tool_name: str, args: Dict) -> ToolResult:
        if tool_name not in self.TOOLS:
            raise ValueError(f"Unknown tool: {tool_name}")
        # 调用 ga 模块中的对应方法
        pass
```

### 4.2 LangCrew/Crew 适配器

**目的**：集成 OpenAkita 的多 Agent 协作能力

```python
# oak_gkernel/adapters/langcrew/
from typing import List, Dict, Any
from oak_gkernel.core import AgentKernel, MemoryPort

class CrewAdapter:
    """CrewAI/LangCrew 多 Agent 协作适配器"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.agents: List[AgentKernel] = []
        self.workflow = config.get('workflow', 'sequential')
    
    def add_agent(self, agent: AgentKernel, role: str, goal: str):
        """添加 Agent 到 Crew"""
        pass
    
    async def execute(self, task: str) -> Any:
        """执行多 Agent 协作任务"""
        if self.workflow == 'sequential':
            return await self._sequential_execute(task)
        elif self.workflow == 'hierarchical':
            return await self._hierarchical_execute(task)
        else:
            return await self._parallel_execute(task)
```

## 五、项目结构

```
oak-genericagent/
├── pyproject.toml
├── README.md
├── LICENSE
├── docs/
│   ├── architecture/
│   │   ├── overview.md
│   │   ├── hexagon_architecture.md
│   │   └── adr/
│   │       ├── 0001-hexagon-architecture.md
│   │       ├── 0002-planner-strategy.md
│   │       └── 0003-memory-levels.md
│   └── guides/
│       ├── quickstart.md
│       ├── migration_from_genericagent.md
│       └── multi_agent_collaboration.md
│
├── oak_gkernel/                    # 核心包
│   ├── __init__.py
│   │
│   ├── core/                       # 核心领域逻辑
│   │   ├── __init__.py
│   │   ├── agent_kernel.py         # Agent 内核
│   │   ├── state_machine.py        # 状态机
│   │   ├── event_system.py         # 事件系统
│   │   ├── planner.py              # 规划器接口
│   │   ├── memory.py               # 记忆接口
│   │   └── tools.py                # 工具接口
│   │
│   ├── ports/                      # 端口层 (六边形的边)
│   │   ├── __init__.py
│   │   ├── llm_port.py             # LLM 端口
│   │   ├── tool_port.py            # 工具端口
│   │   ├── memory_port.py          # 记忆端口
│   │   ├── agent_port.py           # Agent 端口
│   │   └── ui_port.py              # UI 端口
│   │
│   ├── adapters/                   # 适配器层
│   │   ├── __init__.py
│   │   ├── llm/                    # LLM 适配器
│   │   │   ├── __init__.py
│   │   │   ├── base.py
│   │   │   ├── anthropic.py        # Claude
│   │   │   ├── openai.py          # OpenAI
│   │   │   ├── openai_compat.py   # OpenAI 兼容 (Kimi, etc)
│   │   │   └── generic_agent.py   # GenericAgent 原生
│   │   │
│   │   ├── tools/                  # 工具适配器
│   │   │   ├── __init__.py
│   │   │   ├── base.py
│   │   │   ├── generic_agent.py   # GA 原子工具
│   │   │   └── langcrew.py        # LangCrew 工具
│   │   │
│   │   ├── memory/                 # 记忆适配器
│   │   │   ├── __init__.py
│   │   │   ├── layered.py          # GA 分层记忆
│   │   │   ├── langgraph.py        # LangGraph 记忆
│   │   │   ├── redis.py            # Redis 向量记忆
│   │   │   └── file_based.py       # 文件系统记忆
│   │   │
│   │   ├── kernel/                 # 内核适配器
│   │   │   ├── __init__.py
│   │   │   ├── generic_agent.py    # GA 内核
│   │   │   └── langcrew.py         # LangCrew 内核
│   │   │
│   │   └── multi_agent/           # 多 Agent 适配器
│   │       ├── __init__.py
│   │       ├── crew.py             # CrewAI
│   │       └── network.py          # OpenAgents Network
│   │
│   ├── planners/                  # 规划器实现
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── react.py               # ReAct
│   │   ├── tot.py                 # Tree of Thought
│   │   └── reflexion.py           # 反思式
│   │
│   ├── frontends/                  # 前端接口
│   │   ├── __init__.py
│   │   ├── streamlit.py
│   │   ├── rest.py                # FastAPI
│   │   ├── cli.py
│   │   └── websocket.py
│   │
│   └── utils/                     # 工具函数
│       ├── __init__.py
│       ├── config.py
│       └── logging.py
│
├── integrations/                   # 第三方集成
│   ├── __init__.py
│   ├── langfuse/
│   ├── langsmith/
│   └── opentelemetry/
│
├── examples/                       # 示例
│   ├── simple_agent.py
│   ├── self_evolving_agent.py
│   ├── multi_agent_team.py
│   └── browser_automation.py
│
└── tests/                          # 测试
    ├── unit/
    ├── integration/
    └── e2e/
```

## 六、关键设计决策

### 6.1 六边形架构的优势

1. **高度解耦**：内核与外部依赖通过端口隔离
2. **可测试性**：每个组件可独立测试
3. **可替换性**：可随时替换某个适配器而不影响其他部分
4. **渐进式迁移**：可逐步将 GenericAgent 能力迁移到新架构

### 6.2 策略模式在规划器中的应用

使用策略模式允许运行时选择不同的规划策略：
- 简单任务：ReAct
- 复杂推理：Tree of Thought
- 自我进化：Reflexion（基于 GenericAgent）

### 6.3 记忆层次的设计

保留并扩展 GenericAgent 的 L0-L4 记忆层次：
- L0: 元规则（Meta Rules）
- L1: 记忆索引（Insight Index）
- L2: 全局事实（Global Facts）
- L3: 任务 Skills（Skill Tree）
- L4: 会话归档（Session Archive）

### 6.4 向后兼容性

通过适配器确保：
- 现有 GenericAgent 配置继续有效
- 现有工具系统可继续使用
- 渐进式迁移而非大爆炸

## 七、迁移策略

### Phase 0: 原型验证（1-2 周）
1. 创建新仓库骨架
2. 实现核心端口接口
3. 适配 GenericAgent 核心循环

### Phase 1: 骨架建设（2-4 周）
1. 完善 Agent 内核
2. 实现 LLM 端口（Claude, OpenAI）
3. 集成 GenericAgent 工具

### Phase 2: 增量迁移（持续）
1. 迁移记忆系统
2. 添加 LangCrew 多 Agent 支持
3. 完善前端和可视化

### Phase 3: 稳定与优化
1. 性能优化
2. 文档完善
3. 社区建设
