"""GenericAgent 记忆适配器 - 分层记忆实现"""

from typing import Any, Dict, List, Optional, AsyncIterator
from datetime import datetime
import json
import os

from oak_gkernel.core.memory import (
    MemoryPort,
    MemoryEntry,
    MemoryLevel,
    MemoryQuery,
    MemoryStats,
)


class GenericAgentMemoryAdapter(MemoryPort):
    """GenericAgent 分层记忆适配器
    
    实现 GenericAgent 的 L0-L4 五层记忆系统，
    保留其自我进化和经验结晶机制。
    """
    
    def __init__(self, base_path: str = "./memory"):
        self._base_path = base_path
        self._ensure_directories()
        self._memory_cache: Dict[str, MemoryEntry] = {}
        self._load_all()
    
    def _ensure_directories(self) -> None:
        """确保记忆目录存在"""
        for level in MemoryLevel:
            level_dir = os.path.join(self._base_path, level.name)
            os.makedirs(level_dir, exist_ok=True)
    
    def _load_all(self) -> None:
        """加载所有记忆"""
        for level in MemoryLevel:
            level_dir = os.path.join(self._base_path, level.name)
            if not os.path.exists(level_dir):
                continue
            
            for filename in os.listdir(level_dir):
                if filename.endswith('.json'):
                    filepath = os.path.join(level_dir, filename)
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            entry = MemoryEntry.from_dict(data)
                            self._memory_cache[entry.id] = entry
                    except Exception as e:
                        print(f"Failed to load {filepath}: {e}")
    
    async def store(self, entry: MemoryEntry) -> str:
        """存储记忆"""
        if not entry.id:
            import hashlib
            content = json.dumps(entry, sort_keys=True, default=str)
            entry.id = hashlib.md5(content.encode()).hexdigest()[:16]
        
        self._memory_cache[entry.id] = entry
        
        level_dir = os.path.join(self._base_path, entry.level.name)
        filepath = os.path.join(level_dir, f"{entry.id}.json")
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(entry.to_dict(), f, indent=2, ensure_ascii=False)
        
        return entry.id
    
    async def recall(self, memory_id: str) -> Optional[MemoryEntry]:
        """召回记忆"""
        entry = self._memory_cache.get(memory_id)
        if entry:
            entry.access_count += 1
            entry.last_accessed = datetime.now()
            await self.update(entry)
        return entry
    
    async def search(self, query: MemoryQuery) -> List[MemoryEntry]:
        """搜索记忆"""
        results = []
        
        levels_to_search = query.levels or ([query.level] if query.level else list(MemoryLevel))
        
        for entry in self._memory_cache.values():
            if entry.level not in levels_to_search:
                continue
            
            if query.tags:
                if not any(tag in entry.tags for tag in query.tags):
                    continue
            
            if query.min_confidence and entry.confidence < query.min_confidence:
                continue
            
            if query.since and entry.created_at < query.since:
                continue
            
            if query.until and entry.created_at > query.until:
                continue
            
            if query.keyword:
                keyword = query.keyword.lower()
                if keyword not in str(entry.key).lower() and keyword not in str(entry.value).lower():
                    continue
            
            if query.key_prefix and not entry.key.startswith(query.key_prefix):
                continue
            
            results.append(entry)
        
        results.sort(key=lambda e: (e.access_count, e.confidence), reverse=True)
        return results[query.offset:query.offset + query.limit]
    
    async def delete(self, memory_id: str) -> bool:
        """删除记忆"""
        entry = self._memory_cache.pop(memory_id, None)
        if entry:
            filepath = os.path.join(self._base_path, entry.level.name, f"{memory_id}.json")
            if os.path.exists(filepath):
                os.remove(filepath)
            return True
        return False
    
    async def update(self, entry: MemoryEntry) -> bool:
        """更新记忆"""
        if entry.id not in self._memory_cache:
            return False
        
        entry.updated_at = datetime.now()
        self._memory_cache[entry.id] = entry
        
        level_dir = os.path.join(self._base_path, entry.level.name)
        filepath = os.path.join(level_dir, f"{entry.id}.json")
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(entry.to_dict(), f, indent=2, ensure_ascii=False)
        
        return True
    
    async def crystallize(self, session_id: str) -> List[MemoryEntry]:
        """会话结晶 - GenericAgent 自我进化核心
        
        从会话历史中提取经验，固化为 Skill (L3)。
        """
        session_path = os.path.join(self._base_path, f"session_{session_id}.json")
        
        if not os.path.exists(session_path):
            return []
        
        try:
            with open(session_path, 'r', encoding='utf-8') as f:
                session_data = json.load(f)
        except Exception:
            return []
        
        patterns = self._extract_patterns(session_data)
        
        entries = []
        for pattern in patterns:
            entry = MemoryEntry(
                id="",
                level=MemoryLevel.L3_SKILL,
                key=pattern.get("key", "skill"),
                value=pattern.get("value", ""),
                tags=pattern.get("tags", ["crystallized"]),
                confidence=pattern.get("confidence", 0.8),
                source=f"session:{session_id}",
                metadata={
                    "extracted_from": session_id,
                    "pattern_type": pattern.get("type", "unknown"),
                }
            )
            await self.store(entry)
            entries.append(entry)
        
        os.remove(session_path)
        
        return entries
    
    async def get_stats(self) -> MemoryStats:
        """获取记忆统计"""
        by_level = {level.name: 0 for level in MemoryLevel}
        timestamps = []
        
        for entry in self._memory_cache.values():
            by_level[entry.level.name] += 1
            timestamps.append(entry.created_at)
        
        total_size = sum(
            len(json.dumps(e.to_dict()))
            for e in self._memory_cache.values()
        )
        
        return MemoryStats(
            total_entries=len(self._memory_cache),
            by_level=by_level,
            total_size_bytes=total_size,
            oldest_entry=min(timestamps) if timestamps else None,
            newest_entry=max(timestamps) if timestamps else None,
        )
    
    def _extract_patterns(self, session_data: List[Dict]) -> List[Dict]:
        """从会话数据中提取模式"""
        patterns = []
        
        actions = [d for d in session_data if d.get("type") == "action"]
        successful_actions = [a for a in actions if a.get("success")]
        
        if successful_actions:
            success_rate = len(successful_actions) / len(actions) if actions else 0
            
            patterns.append({
                "type": "action_sequence",
                "key": f"skill_{len(successful_actions)}_actions",
                "value": {
                    "actions": [a.get("action") for a in successful_actions],
                    "success_rate": success_rate,
                },
                "tags": ["action", "verified"],
                "confidence": success_rate,
            })
        
        tool_usage = {}
        for action in actions:
            tool = action.get("tool", "unknown")
            if tool not in tool_usage:
                tool_usage[tool] = {"count": 0, "success": 0}
            tool_usage[tool]["count"] += 1
            if action.get("success"):
                tool_usage[tool]["success"] += 1
        
        for tool, stats in tool_usage.items():
            if stats["count"] >= 2:
                patterns.append({
                    "type": "tool_pattern",
                    "key": f"tool_usage_{tool}",
                    "value": {
                        "tool": tool,
                        "usage_count": stats["count"],
                        "success_rate": stats["success"] / stats["count"],
                    },
                    "tags": ["tool", tool],
                    "confidence": stats["success"] / stats["count"],
                })
        
        return patterns
    
    async def store_session(self, session_id: str, data: List[Dict]) -> None:
        """临时存储会话数据"""
        session_path = os.path.join(self._base_path, f"session_{session_id}.json")
        
        with open(session_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
