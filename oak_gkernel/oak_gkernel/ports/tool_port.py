"""工具端口 - 工具系统接口"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type, Callable
from enum import Enum

from oak_gkernel.core.tools import (
    BaseTool,
    ToolSchema,
    ToolResult,
    ToolRegistry,
    ToolCategory,
    tool as tool_decorator,
)

__all__ = [
    "ToolPort",
    "ToolRegistry",
    "ToolSchema",
    "ToolResult",
    "BaseTool",
    "ToolCategory",
    "tool",
]


class ToolPort(ABC):
    """工具端口接口 - 六边形架构的端口
    
    定义工具系统的标准接口。
    """
    
    @property
    @abstractmethod
    def registry(self) -> ToolRegistry:
        """返回工具注册表"""
        pass
    
    @abstractmethod
    async def execute(self, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        """执行工具"""
        pass
    
    @abstractmethod
    async def execute_batch(self, calls: List[Dict[str, Any]]) -> List[ToolResult]:
        """批量执行工具"""
        pass
    
    @abstractmethod
    def get_schema(self, tool_name: str) -> Optional[ToolSchema]:
        """获取工具 Schema"""
        pass
