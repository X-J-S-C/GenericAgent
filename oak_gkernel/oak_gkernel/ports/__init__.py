"""端口层 - 六边形架构的边

端口定义了内核与外部世界的接口。
每个端口都是一组相关操作的抽象。
"""

from oak_gkernel.ports.llm_port import (
    LLMPort, 
    Message, 
    MessageRole, 
    LLMResponse,
    ClaudeAdapter,
    OpenAIAdapter,
)
from oak_gkernel.ports.tool_port import (
    ToolPort,
    ToolRegistry,
    ToolSchema,
    ToolResult,
    BaseTool,
    tool,
)
from oak_gkernel.ports.memory_port import (
    MemoryPort,
    MemoryEntry,
    MemoryLevel,
    MemoryQuery,
    LayeredMemory,
)
from oak_gkernel.ports.agent_port import AgentPort, AgentConnection

__all__ = [
    # LLM 端口
    "LLMPort",
    "Message",
    "MessageRole",
    "LLMResponse",
    "ClaudeAdapter",
    "OpenAIAdapter",
    # 工具端口
    "ToolPort",
    "ToolRegistry",
    "ToolSchema",
    "ToolResult",
    "BaseTool",
    "tool",
    # 记忆端口
    "MemoryPort",
    "MemoryEntry",
    "MemoryLevel",
    "MemoryQuery",
    "LayeredMemory",
    # Agent 端口
    "AgentPort",
    "AgentConnection",
]
