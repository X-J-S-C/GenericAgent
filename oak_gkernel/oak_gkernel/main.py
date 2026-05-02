"""OAK-GenericAgent 主入口"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

__version__ = "0.1.0"

from oak_gkernel import (
    AgentKernel,
    AgentState,
    AgentResult,
    BasePlanner,
    PlanStep,
    MemoryEntry,
    MemoryLevel,
    BaseTool,
    ToolResult,
    ToolSchema,
)

__all__ = [
    "__version__",
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


def create_agent(config: dict = None):
    """创建 Agent 实例
    
    Args:
        config: Agent 配置
        
    Returns:
        Agent 内核实例
    """
    from oak_gkernel.core.agent_kernel import AgentConfig, BaseAgentKernel
    
    cfg = config or {}
    agent_config = AgentConfig(
        name=cfg.get("name", "agent"),
        max_turns=cfg.get("max_turns", 50),
        timeout_seconds=cfg.get("timeout_seconds", 300),
        verbose=cfg.get("verbose", True),
        planner=cfg.get("planner", "react"),
        memory_enabled=cfg.get("memory_enabled", True),
        tools_enabled=cfg.get("tools_enabled", True),
    )
    
    return BaseAgentKernel(agent_config)


def create_layered_memory(config: dict = None):
    """创建分层记忆实例
    
    Args:
        config: 记忆配置
        
    Returns:
        LayeredMemory 实例
    """
    from oak_gkernel.core.memory import LayeredMemory
    
    return LayeredMemory(config)


def create_tool_registry(registry_type: str = "generic_agent"):
    """创建工具注册表
    
    Args:
        registry_type: 注册表类型
            - "generic_agent": GenericAgent 原子工具
            - "empty": 空注册表
            
    Returns:
        ToolRegistry 实例
    """
    if registry_type == "generic_agent":
        from oak_gkernel.adapters.tools.generic_agent import GenericAgentToolRegistry
        return GenericAgentToolRegistry()
    else:
        from oak_gkernel.core.tools import ToolRegistry
        return ToolRegistry()


def create_llm_adapter(adapter_type: str, config: dict):
    """创建 LLM 适配器
    
    Args:
        adapter_type: 适配器类型
            - "claude": Claude API
            - "openai": OpenAI API
            - "generic_agent": GenericAgent 原生
        config: LLM 配置
        
    Returns:
        LLMPort 实例
    """
    from oak_gkernel.ports.llm_port import LLMConfig, ClaudeAdapter, OpenAIAdapter
    from oak_gkernel.adapters.llm.generic_agent import GenericAgentLLMAdapter
    
    llm_config = LLMConfig(
        model=config["model"],
        api_key=config["api_key"],
        api_base=config.get("api_base", "https://api.anthropic.com"),
        temperature=config.get("temperature", 0.7),
        max_tokens=config.get("max_tokens"),
        timeout=config.get("timeout", 120),
        max_retries=config.get("max_retries", 3),
    )
    
    if adapter_type == "claude":
        return ClaudeAdapter(llm_config)
    elif adapter_type == "openai":
        return OpenAIAdapter(llm_config)
    elif adapter_type == "generic_agent":
        return GenericAgentLLMAdapter(llm_config)
    else:
        raise ValueError(f"Unknown adapter type: {adapter_type}")


def create_planner(planner_type: str, config: dict = None):
    """创建规划器
    
    Args:
        planner_type: 规划器类型
            - "react": ReAct 规划器
            - "tot": Tree of Thought 规划器
            - "reflexion": 反思式规划器
        config: 规划器配置
        
    Returns:
        BasePlanner 实例
    """
    from oak_gkernel.core.planner import (
        BasePlanner,
        ReActPlanner,
        TreeOfThoughtPlanner,
        ReflexionPlanner,
        PlannerType,
    )
    
    cfg = config or {}
    
    if planner_type == "react":
        return ReActPlanner(cfg)
    elif planner_type == "tot":
        return TreeOfThoughtPlanner(cfg)
    elif planner_type == "reflexion":
        return ReflexionPlanner(cfg)
    else:
        raise ValueError(f"Unknown planner type: {planner_type}")
