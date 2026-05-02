"""记忆系统 - 分层记忆接口"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, AsyncIterator, Type
from enum import Enum, auto
from datetime import datetime
import json


class MemoryLevel(Enum):
    """记忆层次枚举
    
    保留 GenericAgent 的 L0-L4 层次设计：
    - L0: 元规则（Meta Rules）
    - L1: 记忆索引（Insight Index）
    - L2: 全局事实（Global Facts）
    - L3: 任务 Skills / SOPs
    - L4: 会话归档（Session Archive）
    """
    L0_META = auto()
    L1_INSIGHT = auto()
    L2_FACTS = auto()
    L3_SKILL = auto()
    L4_SESSION = auto()
    
    @property
    def persistence(self) -> str:
        """记忆持久化策略"""
        return {
            MemoryLevel.L0_META: "permanent",
            MemoryLevel.L1_INSIGHT: "permanent",
            MemoryLevel.L2_FACTS: "permanent",
            MemoryLevel.L3_SKILL: "permanent",
            MemoryLevel.L4_SESSION: "temporary",
        }[self]
    
    @property
    def ttl_seconds(self) -> Optional[int]:
        """TTL（Time To Live）"""
        return {
            MemoryLevel.L0_META: None,
            MemoryLevel.L1_INSIGHT: None,
            MemoryLevel.L2_FACTS: None,
            MemoryLevel.L3_SKILL: None,
            MemoryLevel.L4_SESSION: 7 * 24 * 3600,  # 7 days
        }[self]


@dataclass
class MemoryEntry:
    """记忆条目"""
    id: str
    level: MemoryLevel
    key: str
    value: Any
    tags: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    access_count: int = 0
    last_accessed: Optional[datetime] = None
    confidence: float = 1.0
    source: str = "system"
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "level": self.level.name,
            "key": self.key,
            "value": self.value,
            "tags": self.tags,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "access_count": self.access_count,
            "confidence": self.confidence,
            "source": self.source,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        data = data.copy()
        if isinstance(data.get("level"), str):
            data["level"] = MemoryLevel[data["level"]]
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if isinstance(data.get("updated_at"), str):
            data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        return cls(**data)


@dataclass
class MemoryQuery:
    """记忆查询条件"""
    level: Optional[MemoryLevel] = None
    levels: Optional[List[MemoryLevel]] = None
    key_prefix: Optional[str] = None
    tags: Optional[List[str]] = None
    keyword: Optional[str] = None
    limit: int = 10
    offset: int = 0
    min_confidence: float = 0.0
    since: Optional[datetime] = None
    until: Optional[datetime] = None


@dataclass
class MemoryStats:
    """记忆统计"""
    total_entries: int
    by_level: Dict[str, int]
    total_size_bytes: int
    oldest_entry: Optional[datetime]
    newest_entry: Optional[datetime]


class MemoryPort(ABC):
    """记忆端口接口 - 六边形架构的端口
    
    定义记忆系统的标准接口，支持：
    - 分层存储
    - 语义检索
    - 经验结晶
    """
    
    @abstractmethod
    async def store(self, entry: MemoryEntry) -> str:
        """存储记忆
        
        Args:
            entry: 记忆条目
            
        Returns:
            str: 记忆 ID
        """
        pass
    
    @abstractmethod
    async def recall(self, memory_id: str) -> Optional[MemoryEntry]:
        """根据 ID 召回记忆
        
        Args:
            memory_id: 记忆 ID
            
        Returns:
            Optional[MemoryEntry]: 记忆条目
        """
        pass
    
    @abstractmethod
    async def search(self, query: MemoryQuery) -> List[MemoryEntry]:
        """搜索记忆
        
        Args:
            query: 查询条件
            
        Returns:
            List[MemoryEntry]: 匹配的记忆列表
        """
        pass
    
    @abstractmethod
    async def delete(self, memory_id: str) -> bool:
        """删除记忆
        
        Args:
            memory_id: 记忆 ID
            
        Returns:
            bool: 是否成功删除
        """
        pass
    
    @abstractmethod
    async def update(self, entry: MemoryEntry) -> bool:
        """更新记忆
        
        Args:
            entry: 记忆条目（需包含 ID）
            
        Returns:
            bool: 是否成功更新
        """
        pass
    
    async def crystallize(self, session_id: str) -> List[MemoryEntry]:
        """将会话结晶为长期记忆
        
        这是 GenericAgent 自我进化机制的核心。
        从会话历史中提取经验，固化为 Skill。
        
        Args:
            session_id: 会话 ID
            
        Returns:
            List[MemoryEntry]: 提取的记忆列表
        """
        return []
    
    @abstractmethod
    async def get_stats(self) -> MemoryStats:
        """获取记忆统计"""
        pass
    
    async def stream_all(self, level: Optional[MemoryLevel] = None) -> AsyncIterator[MemoryEntry]:
        """流式遍历所有记忆
        
        Args:
            level: 可选，限制只遍历特定层次
            
        Yields:
            MemoryEntry: 记忆条目
        """
        query = MemoryQuery(level=level, limit=100)
        while True:
            results = await self.search(query)
            if not results:
                break
            for entry in results:
                yield entry
            query.offset += len(results)


class LayeredMemory(MemoryPort):
    """分层记忆实现 - 保留 GenericAgent 的设计
    
    实现 L0-L4 五层记忆系统。
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self._storage: Dict[str, MemoryEntry] = {}
        self._index: Dict[MemoryLevel, Dict[str, str]] = {
            level: {} for level in MemoryLevel
        }
        self._session_storage: Dict[str, List[Dict]] = {}
    
    async def store(self, entry: MemoryEntry) -> str:
        entry.id = entry.id or self._generate_id(entry)
        self._storage[entry.id] = entry
        self._index[entry.level][entry.key] = entry.id
        return entry.id
    
    async def recall(self, memory_id: str) -> Optional[MemoryEntry]:
        entry = self._storage.get(memory_id)
        if entry:
            entry.access_count += 1
            entry.last_accessed = datetime.now()
        return entry
    
    async def search(self, query: MemoryQuery) -> List[MemoryEntry]:
        results = []
        levels_to_search = query.levels or ([query.level] if query.level else list(MemoryLevel))
        
        for level in levels_to_search:
            for entry_id, entry in self._storage.items():
                if entry.level != level:
                    continue
                if query.tags and not any(t in entry.tags for t in query.tags):
                    continue
                if query.min_confidence and entry.confidence < query.min_confidence:
                    continue
                if query.since and entry.created_at < query.since:
                    continue
                if query.until and entry.created_at > query.until:
                    continue
                if query.keyword and query.keyword.lower() not in str(entry.value).lower():
                    continue
                if query.key_prefix and not entry.key.startswith(query.key_prefix):
                    continue
                results.append(entry)
        
        results.sort(key=lambda e: (e.access_count, e.confidence), reverse=True)
        return results[query.offset:query.offset + query.limit]
    
    async def delete(self, memory_id: str) -> bool:
        if memory_id not in self._storage:
            return False
        entry = self._storage[memory_id]
        del self._index[entry.level][entry.key]
        del self._storage[memory_id]
        return True
    
    async def update(self, entry: MemoryEntry) -> bool:
        if entry.id not in self._storage:
            return False
        entry.updated_at = datetime.now()
        self._storage[entry.id] = entry
        return True
    
    async def crystallize(self, session_id: str) -> List[MemoryEntry]:
        """从会话中提取经验，固化为 Skill"""
        session_data = self._session_storage.get(session_id, [])
        if not session_data:
            return []
        
        patterns = self._extract_patterns(session_data)
        
        entries = []
        for pattern in patterns:
            entry = MemoryEntry(
                id=self._generate_id(pattern),
                level=MemoryLevel.L3_SKILL,
                key=pattern.get("key", "unknown"),
                value=pattern.get("value", ""),
                tags=pattern.get("tags", []),
                confidence=pattern.get("confidence", 0.8),
                source=f"session:{session_id}",
                metadata={"extracted_from": session_id}
            )
            await self.store(entry)
            entries.append(entry)
        
        del self._session_storage[session_id]
        return entries
    
    async def get_stats(self) -> MemoryStats:
        by_level = {level.name: 0 for level in MemoryLevel}
        for entry in self._storage.values():
            by_level[entry.level.name] += 1
        
        timestamps = [e.created_at for e in self._storage.values()]
        
        return MemoryStats(
            total_entries=len(self._storage),
            by_level=by_level,
            total_size_bytes=len(json.dumps([e.to_dict() for e in self._storage.values()])),
            oldest_entry=min(timestamps) if timestamps else None,
            newest_entry=max(timestamps) if timestamps else None,
        )
    
    def store_session(self, session_id: str, data: List[Dict]) -> None:
        """临时存储会话数据"""
        self._session_storage[session_id] = data
    
    def _generate_id(self, entry: Any) -> str:
        import hashlib
        content = json.dumps(entry, sort_keys=True, default=str)
        return hashlib.md5(content.encode()).hexdigest()[:16]
    
    def _extract_patterns(self, session_data: List[Dict]) -> List[Dict]:
        """从会话数据中提取模式"""
        patterns = []
        
        actions = [d for d in session_data if d.get("type") == "action"]
        successes = [a for a in actions if a.get("success")]
        
        if successes:
            patterns.append({
                "key": "successful_action_sequence",
                "value": [a.get("action") for a in successes],
                "tags": ["action", "verified"],
                "confidence": len(successes) / len(actions) if actions else 0.5,
            })
        
        return patterns
