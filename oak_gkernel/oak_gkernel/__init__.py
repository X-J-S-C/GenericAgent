"""OAK-GenericAgent: 融合 OpenAkita 与 GenericAgent 的 Agent 框架

六边形架构实现：
- core: 核心领域逻辑（无外部依赖）
- ports: 端口层（定义接口）
- adapters: 适配器层（实现端口）
"""

__version__ = "0.1.0"
__author__ = "OpenAkita-GenericAgent Team"

from oak_gkernel.core.agent_kernel import AgentKernel, AgentState, AgentResult
from oak_gkernel.core.planner import BasePlanner, PlanStep
from oak_gkernel.core.memory import MemoryEntry, MemoryLevel
from oak_gkernel.core.tools import BaseTool, ToolResult, ToolSchema

__all__ = [
    "AgentKernel",
    "AgentState", 
    "AgentResult",
    "BasePlanner",
    "PlanStep",
    "MemoryEntry",
    "MemoryLevel",
    "BaseTool",
    "ToolResult",
    "ToolSchema",
]
