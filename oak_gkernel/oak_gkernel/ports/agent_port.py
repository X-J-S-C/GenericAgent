"""Agent 端口 - 多 Agent 通信接口"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, AsyncIterator, Callable
from enum import Enum
import asyncio
import json


class MessageType(Enum):
    """消息类型"""
    REQUEST = "request"
    RESPONSE = "response"
    BROADCAST = "broadcast"
    EVENT = "event"
    TOOL_CALL = "tool_call"


@dataclass
class AgentMessage:
    """Agent 消息"""
    id: str
    sender_id: str
    receiver_id: Optional[str]  # None 表示广播
    type: MessageType
    content: Any
    correlation_id: Optional[str] = None
    ttl_seconds: int = 300
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "sender_id": self.sender_id,
            "receiver_id": self.receiver_id,
            "type": self.type.value,
            "content": self.content,
            "correlation_id": self.correlation_id,
            "ttl_seconds": self.ttl_seconds,
            "metadata": self.metadata,
        }


@dataclass 
class AgentInfo:
    """Agent 信息"""
    agent_id: str
    name: str
    capabilities: List[str] = field(default_factory=list)
    status: str = "online"
    metadata: Dict[str, Any] = field(default_factory=dict)


class AgentConnection(ABC):
    """Agent 连接接口"""
    
    @property
    @abstractmethod
    def agent_id(self) -> str:
        """返回 Agent ID"""
        pass
    
    @abstractmethod
    async def send(self, message: AgentMessage) -> bool:
        """发送消息"""
        pass
    
    @abstractmethod
    async def receive(self, timeout: Optional[float] = None) -> Optional[AgentMessage]:
        """接收消息"""
        pass
    
    @abstractmethod
    async def broadcast(self, message: AgentMessage) -> bool:
        """广播消息"""
        pass
    
    @abstractmethod
    async def get_peers(self) -> List[AgentInfo]:
        """获取对等节点"""
        pass


class AgentPort(ABC):
    """Agent 端口接口 - 六边形架构的端口
    
    定义多 Agent 协作的标准接口。
    """
    
    @abstractmethod
    async def connect(self, connection: AgentConnection) -> None:
        """建立连接"""
        pass
    
    @abstractmethod
    async def disconnect(self, agent_id: str) -> None:
        """断开连接"""
        pass
    
    @abstractmethod
    async def send_message(self, receiver_id: str, content: Any, 
                          msg_type: MessageType = MessageType.REQUEST) -> Optional[AgentMessage]:
        """发送消息"""
        pass
    
    @abstractmethod
    async def receive_message(self, timeout: Optional[float] = None) -> Optional[AgentMessage]:
        """接收消息"""
        pass
    
    @abstractmethod
    async def broadcast(self, content: Any, msg_type: MessageType = MessageType.BROADCAST) -> None:
        """广播消息"""
        pass
    
    @abstractmethod
    def get_peers(self) -> List[AgentInfo]:
        """获取对等 Agent"""
        pass


class LocalAgentPort(AgentPort):
    """本地 Agent 端口实现 - 用于单机多 Agent"""
    
    def __init__(self, agent_id: str):
        self._agent_id = agent_id
        self._connections: Dict[str, AgentConnection] = {}
        self._message_queues: Dict[str, asyncio.Queue] = {}
        self._lock = asyncio.Lock()
    
    @property
    def agent_id(self) -> str:
        return self._agent_id
    
    async def connect(self, connection: AgentConnection) -> None:
        async with self._lock:
            self._connections[connection.agent_id] = connection
            self._message_queues[connection.agent_id] = asyncio.Queue()
    
    async def disconnect(self, agent_id: str) -> None:
        async with self._lock:
            self._connections.pop(agent_id, None)
            self._message_queues.pop(agent_id, None)
    
    async def send_message(self, receiver_id: str, content: Any,
                          msg_type: MessageType = MessageType.REQUEST) -> Optional[AgentMessage]:
        import uuid
        
        if receiver_id not in self._message_queues:
            return None
        
        message = AgentMessage(
            id=str(uuid.uuid4()),
            sender_id=self._agent_id,
            receiver_id=receiver_id,
            type=msg_type,
            content=content,
        )
        
        await self._message_queues[receiver_id].put(message)
        return message
    
    async def receive_message(self, timeout: Optional[float] = None) -> Optional[AgentMessage]:
        own_queue = self._message_queues.get(self._agent_id)
        if own_queue:
            try:
                return await asyncio.wait_for(own_queue.get(), timeout)
            except asyncio.TimeoutError:
                return None
        return None
    
    async def broadcast(self, content: Any, msg_type: MessageType = MessageType.BROADCAST) -> None:
        import uuid
        
        message = AgentMessage(
            id=str(uuid.uuid4()),
            sender_id=self._agent_id,
            receiver_id=None,
            type=msg_type,
            content=content,
        )
        
        for agent_id, queue in self._message_queues.items():
            if agent_id != self._agent_id:
                await queue.put(message)
    
    def get_peers(self) -> List[AgentInfo]:
        return [
            AgentInfo(agent_id=aid, name=aid)
            for aid in self._connections.keys()
            if aid != self._agent_id
        ]


class MessageBusAgentPort(AgentPort):
    """消息总线 Agent 端口 - 用于分布式多 Agent"""
    
    def __init__(self, agent_id: str, bus_url: str):
        self._agent_id = agent_id
        self._bus_url = bus_url
        self._subscribed_topics: List[str] = []
        self._message_handler: Optional[Callable] = None
    
    @property
    def agent_id(self) -> str:
        return self._agent_id
    
    async def connect(self, connection: AgentConnection) -> None:
        # 连接消息总线
        pass
    
    async def disconnect(self, agent_id: str) -> None:
        # 断开连接
        pass
    
    async def send_message(self, receiver_id: str, content: Any,
                          msg_type: MessageType = MessageType.REQUEST) -> Optional[AgentMessage]:
        import uuid
        
        message = AgentMessage(
            id=str(uuid.uuid4()),
            sender_id=self._agent_id,
            receiver_id=receiver_id,
            type=msg_type,
            content=content,
        )
        
        # 发送到消息总线
        topic = f"agent.{receiver_id}"
        await self._publish_to_bus(topic, message)
        
        return message
    
    async def receive_message(self, timeout: Optional[float] = None) -> Optional[AgentMessage]:
        if self._message_handler:
            return await asyncio.wait_for(self._message_handler(), timeout)
        return None
    
    async def broadcast(self, content: Any, msg_type: MessageType = MessageType.BROADCAST) -> None:
        import uuid
        
        message = AgentMessage(
            id=str(uuid.uuid4()),
            sender_id=self._agent_id,
            receiver_id=None,
            type=msg_type,
            content=content,
        )
        
        await self._publish_to_bus("agent.broadcast", message)
    
    def get_peers(self) -> List[AgentInfo]:
        return []
    
    async def _publish_to_bus(self, topic: str, message: AgentMessage) -> None:
        pass
    
    def _subscribe_to_bus(self, topic: str) -> None:
        pass
