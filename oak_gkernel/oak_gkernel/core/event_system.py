"""事件系统 - Agent 内核的解耦机制"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar
from enum import Enum, auto
from datetime import datetime
import asyncio
from collections import defaultdict


T = TypeVar('T')


class EventType(Enum):
    """预定义事件类型"""
    AGENT_START = auto()
    AGENT_END = auto()
    AGENT_ERROR = auto()
    TOOL_CALL = auto()
    TOOL_RESULT = auto()
    LLM_REQUEST = auto()
    LLM_RESPONSE = auto()
    MEMORY_STORE = auto()
    MEMORY_RECALL = auto()
    USER_INPUT = auto()
    STATE_CHANGE = auto()
    CUSTOM = auto()


@dataclass
class Event:
    """事件对象"""
    type: EventType
    source: str
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    correlation_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if isinstance(self.type, str):
            self.type = EventType[self.type] if hasattr(EventType, self.type) else EventType.CUSTOM


class EventHandler(ABC):
    """事件处理器接口"""
    
    @abstractmethod
    async def handle(self, event: Event) -> None:
        """处理事件"""
        pass
    
    @abstractmethod
    def can_handle(self, event: Event) -> bool:
        """检查是否能处理此事件"""
        pass


class BaseEventHandler(EventHandler):
    """事件处理器基类"""
    
    def __init__(self, event_types: List[EventType]):
        self._event_types = set(event_types)
    
    async def handle(self, event: Event) -> None:
        if self.can_handle(event):
            await self._handle(event)
    
    @abstractmethod
    async def _handle(self, event: Event) -> None:
        """实际处理逻辑，子类实现"""
        pass
    
    def can_handle(self, event: Event) -> bool:
        if EventType.CUSTOM in self._event_types:
            return True
        return event.type in self._event_types


class EventBus:
    """事件总线 - 发布/订阅模式实现"""
    
    def __init__(self, async_mode: bool = True):
        self._async_mode = async_mode
        self._handlers: Dict[EventType, List[EventHandler]] = defaultdict(list)
        self._wildcard_handlers: List[EventHandler] = []
        self._middleware: List[Callable[[Event], Event]] = []
        self._event_history: List[Event] = []
        self._max_history: int = 1000
        self._lock = asyncio.Lock() if async_mode else None
    
    def subscribe(self, handler: EventHandler, event_type: Optional[EventType] = None) -> None:
        """订阅事件
        
        Args:
            handler: 事件处理器
            event_type: 事件类型，None 表示订阅所有事件
        """
        if event_type is None:
            self._wildcard_handlers.append(handler)
        else:
            self._handlers[event_type].append(handler)
    
    def unsubscribe(self, handler: EventHandler, event_type: Optional[EventType] = None) -> None:
        """取消订阅"""
        if event_type is None:
            if handler in self._wildcard_handlers:
                self._wildcard_handlers.remove(handler)
        else:
            if handler in self._handlers.get(event_type, []):
                self._handlers[event_type].remove(handler)
    
    def add_middleware(self, middleware: Callable[[Event], Event]) -> None:
        """添加中间件"""
        self._middleware.append(middleware)
    
    async def publish(self, event: Event) -> None:
        """发布事件
        
        Args:
            event: 事件对象
        """
        for middleware in self._middleware:
            event = middleware(event)
        
        handlers = list(self._handlers.get(event.type, [])) + self._wildcard_handlers
        
        if self._lock:
            async with self._lock:
                self._event_history.append(event)
                if len(self._event_history) > self._max_history:
                    self._event_history.pop(0)
        else:
            self._event_history.append(event)
            if len(self._event_history) > self._max_history:
                self._event_history.pop(0)
        
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler.handle):
                    await handler.handle(event)
                else:
                    handler.handle(event)
            except Exception as e:
                print(f"Error in event handler {handler}: {e}")
    
    def publish_sync(self, event: Event) -> None:
        """同步发布事件（用于非异步环境）"""
        for middleware in self._middleware:
            event = middleware(event)
        
        handlers = list(self._handlers.get(event.type, [])) + self._wildcard_handlers
        
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history.pop(0)
        
        for handler in handlers:
            try:
                handler.handle(event)
            except Exception as e:
                print(f"Error in event handler {handler}: {e}")
    
    def get_handlers(self, event_type: EventType) -> List[EventHandler]:
        """获取事件类型的处理器"""
        return list(self._handlers.get(event_type, [])) + self._wildcard_handlers
    
    def get_history(self, event_type: Optional[EventType] = None, 
                    limit: Optional[int] = None) -> List[Event]:
        """获取事件历史"""
        history = self._event_history
        if event_type:
            history = [e for e in history if e.type == event_type]
        if limit:
            history = history[-limit:]
        return history
    
    def clear_history(self) -> None:
        """清空事件历史"""
        self._event_history.clear()


class LoggingEventHandler(BaseEventHandler):
    """日志事件处理器"""
    
    def __init__(self):
        super().__init__([EventType.AGENT_START, EventType.AGENT_END, 
                         EventType.AGENT_ERROR, EventType.TOOL_CALL])
        self._logger = print
    
    def set_logger(self, logger: Callable[[str], None]) -> None:
        """设置日志器"""
        self._logger = logger
    
    async def _handle(self, event: Event) -> None:
        self._logger(f"[{event.type.name}] {event.source}: {event.data}")


class MetricsEventHandler(BaseEventHandler):
    """指标事件处理器 - 收集可观测性数据"""
    
    def __init__(self):
        super().__init__([
            EventType.AGENT_START, EventType.AGENT_END, 
            EventType.TOOL_CALL, EventType.LLM_REQUEST,
            EventType.LLM_RESPONSE
        ])
        self._metrics: Dict[str, int] = defaultdict(int)
        self._durations: Dict[str, List[float]] = defaultdict(list)
    
    async def _handle(self, event: Event) -> None:
        if event.type == EventType.AGENT_START:
            self._metrics["agent_started"] += 1
        elif event.type == EventType.AGENT_END:
            self._metrics["agent_completed"] += 1
        elif event.type == EventType.TOOL_CALL:
            tool_name = event.data.get("tool_name", "unknown")
            self._metrics[f"tool_{tool_name}"] += 1
        elif event.type == EventType.LLM_REQUEST:
            self._metrics["llm_requests"] += 1
        elif event.type == EventType.LLM_RESPONSE:
            duration = event.metadata.get("duration_ms", 0)
            self._durations["llm_response"].append(duration)
    
    def get_metrics(self) -> Dict[str, Any]:
        """获取指标"""
        return {
            "counters": dict(self._metrics),
            "histograms": {
                k: {"count": len(v), "avg": sum(v)/len(v) if v else 0}
                for k, v in self._durations.items()
            }
        }
