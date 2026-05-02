"""Agent 内核 - 六边形架构的核心"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict, Callable, AsyncIterator
from enum import Enum, auto
from datetime import datetime
import asyncio


class AgentState(Enum):
    """Agent 状态枚举"""
    IDLE = auto()
    INITIALIZING = auto()
    RUNNING = auto()
    WAITING_INPUT = auto()
    PAUSED = auto()
    COMPLETED = auto()
    ERROR = auto()


@dataclass
class Transition:
    """状态转换定义"""
    from_state: AgentState
    to_state: AgentState
    event: str
    condition: Optional[Callable[[], bool]] = None
    action: Optional[Callable[..., Any]] = None


@dataclass
class AgentResult:
    """Agent 执行结果"""
    success: bool
    output: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    steps: List[Dict] = field(default_factory=list)
    duration_ms: float = 0.0
    
    @property
    def is_complete(self) -> bool:
        return self.success and self.output is not None


@dataclass
class AgentConfig:
    """Agent 配置"""
    name: str = "agent"
    max_turns: int = 50
    timeout_seconds: int = 300
    verbose: bool = True
    planner: str = "react"
    memory_enabled: bool = True
    tools_enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


class AgentKernel(ABC):
    """Agent 内核接口 - 六边形架构的核心
    
    内核只负责运行 Agent 循环，不关心：
    - 使用什么 LLM
    - 使用什么工具
    - 如何存储记忆
    
    这些都由适配器提供，通过依赖注入传入。
    """
    
    @abstractmethod
    async def run(self, query: str, context: Optional[Dict[str, Any]] = None) -> AgentResult:
        """执行 Agent 循环
        
        Args:
            query: 用户输入的查询
            context: 额外的上下文信息
            
        Returns:
            AgentResult: 执行结果
        """
        pass
    
    @abstractmethod
    async def stream(self, query: str, context: Optional[Dict[str, Any]] = None) -> AsyncIterator[str]:
        """流式执行 Agent 循环
        
        Args:
            query: 用户输入的查询
            context: 额外的上下文信息
            
        Yields:
            str: 增量输出
        """
        pass
    
    @abstractmethod
    async def pause(self) -> None:
        """暂停执行"""
        pass
    
    @abstractmethod
    async def resume(self, input_data: Any) -> AgentResult:
        """恢复执行
        
        Args:
            input_data: 用户输入的数据
            
        Returns:
            AgentResult: 执行结果
        """
        pass
    
    @abstractmethod
    def get_state(self) -> AgentState:
        """获取当前状态"""
        pass
    
    @abstractmethod
    def get_history(self) -> List[Dict[str, Any]]:
        """获取执行历史"""
        pass


class BaseAgentKernel(AgentKernel):
    """Agent 内核基类 - 提供通用实现"""
    
    def __init__(self, config: AgentConfig):
        self.config = config
        self._state = AgentState.IDLE
        self._history: List[Dict[str, Any]] = []
        self._current_turn = 0
        self._paused_data: Any = None
        self._start_time: Optional[datetime] = None
    
    async def run(self, query: str, context: Optional[Dict[str, Any]] = None) -> AgentResult:
        """默认的串行执行实现"""
        self._state = AgentState.INITIALIZING
        self._start_time = datetime.now()
        self._current_turn = 0
        self._history = []
        
        try:
            self._state = AgentState.RUNNING
            async for _ in self.stream(query, context):
                pass
            self._state = AgentState.COMPLETED
            return self._build_result(success=True)
        except Exception as e:
            self._state = AgentState.ERROR
            return self._build_result(success=False, error=str(e))
    
    async def stream(self, query: str, context: Optional[Dict[str, Any]] = None) -> AsyncIterator[str]:
        """子类实现流式执行"""
        raise NotImplementedError
    
    async def pause(self) -> None:
        self._state = AgentState.PAUSED
        self._paused_data = {"turn": self._current_turn, "history": self._history.copy()}
    
    async def resume(self, input_data: Any) -> AgentResult:
        if self._state != AgentState.PAUSED:
            return self._build_result(success=False, error="Agent is not paused")
        
        self._state = AgentState.RUNNING
        self._paused_data = None
        
        try:
            async for _ in self.stream(input_data, {"resume": True}):
                pass
            self._state = AgentState.COMPLETED
            return self._build_result(success=True)
        except Exception as e:
            self._state = AgentState.ERROR
            return self._build_result(success=False, error=str(e))
    
    def get_state(self) -> AgentState:
        return self._state
    
    def get_history(self) -> List[Dict[str, Any]]:
        return self._history.copy()
    
    def _record_step(self, step_type: str, data: Dict[str, Any]) -> None:
        """记录执行步骤"""
        self._history.append({
            "type": step_type,
            "turn": self._current_turn,
            "timestamp": datetime.now().isoformat(),
            **data
        })
    
    def _build_result(self, success: bool, output: Any = None, error: Optional[str] = None) -> AgentResult:
        """构建结果对象"""
        duration = (datetime.now() - self._start_time).total_seconds() * 1000 if self._start_time else 0.0
        return AgentResult(
            success=success,
            output=output,
            error=error,
            metadata={
                "turns": self._current_turn,
                "config": self.config.__dict__,
            },
            steps=self._history.copy(),
            duration_ms=duration
        )
    
    def _increment_turn(self) -> None:
        self._current_turn += 1
        if self._current_turn >= self.config.max_turns:
            raise Exception(f"Maximum turns ({self.config.max_turns}) exceeded")


class CompositeAgentKernel(AgentKernel):
    """组合式 Agent 内核 - 支持多子 Agent"""
    
    def __init__(self, config: AgentConfig, sub_agents: List[AgentKernel]):
        self.config = config
        self.sub_agents = sub_agents
        self._state = AgentState.IDLE
        self._active_agent: Optional[AgentKernel] = None
    
    async def run(self, query: str, context: Optional[Dict[str, Any]] = None) -> AgentResult:
        results = []
        for agent in self.sub_agents:
            self._active_agent = agent
            result = await agent.run(query, context)
            results.append(result)
            
            if not result.success:
                return self._combine_results(results, success=False)
        
        return self._combine_results(results, success=True)
    
    async def stream(self, query: str, context: Optional[Dict[str, Any]] = None) -> AsyncIterator[str]:
        for agent in self.sub_agents:
            self._active_agent = agent
            async for chunk in agent.stream(query, context):
                yield chunk
    
    async def pause(self) -> None:
        if self._active_agent:
            await self._active_agent.pause()
        self._state = AgentState.PAUSED
    
    async def resume(self, input_data: Any) -> AgentResult:
        if self._active_agent:
            return await self._active_agent.resume(input_data)
        return AgentResult(success=False, error="No active agent to resume")
    
    def get_state(self) -> AgentState:
        if self._active_agent:
            return self._active_agent.get_state()
        return self._state
    
    def get_history(self) -> List[Dict[str, Any]]:
        if self._active_agent:
            return self._active_agent.get_history()
        return []
    
    def _combine_results(self, results: List[AgentResult], success: bool) -> AgentResult:
        return AgentResult(
            success=success,
            output=[r.output for r in results],
            metadata={"agent_count": len(self.sub_agents)},
            steps=[s for r in results for s in r.steps],
            duration_ms=sum(r.duration_ms for r in results)
        )
