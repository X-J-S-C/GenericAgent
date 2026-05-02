"""工具系统 - 可插拔的工具接口"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable, Type, get_type_hints
from enum import Enum, auto
import json
import inspect


class ToolCategory(Enum):
    """工具分类"""
    FILE = auto()
    CODE = auto()
    WEB = auto()
    USER = auto()
    MEMORY = auto()
    COMPUTATION = auto()
    CUSTOM = auto()


@dataclass
class ToolSchema:
    """工具 Schema - 用于 LLM 函数调用"""
    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    category: ToolCategory = ToolCategory.CUSTOM
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_openai_format(self) -> Dict[str, Any]:
        """转换为 OpenAI 函数调用格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }
    
    def to_anthropic_format(self) -> Dict[str, Any]:
        """转换为 Anthropic 工具格式"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }
    
    @classmethod
    def from_function(cls, func: Callable, name: Optional[str] = None,
                     description: Optional[str] = None) -> "ToolSchema":
        """从 Python 函数创建 ToolSchema"""
        func_name = name or func.__name__
        func_desc = description or func.__doc__ or ""
        
        hints = get_type_hints(func)
        params = {}
        sig = inspect.signature(func)
        
        for p_name, param in sig.parameters.items():
            if p_name in ("self", "cls"):
                continue
            
            param_type = hints.get(p_name, str)
            param_info = {
                "type": cls._python_type_to_json(param_type),
            }
            
            if param.default is not inspect.Parameter.empty:
                param_info["default"] = param.default
            
            params[p_name] = param_info
        
        return cls(
            name=func_name,
            description=func_desc.strip(),
            parameters={
                "type": "object",
                "properties": params,
                "required": [p for p, v in sig.parameters.items() 
                            if p not in ("self", "cls") and 
                            v.default is inspect.Parameter.empty]
            }
        )
    
    @staticmethod
    def _python_type_to_json(py_type: Type) -> str:
        """Python 类型转 JSON Schema 类型"""
        type_map = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            dict: "object",
            Any: "any",
        }
        return type_map.get(py_type, "string")


@dataclass
class ToolResult:
    """工具执行结果"""
    success: bool
    data: Any = None
    error: Optional[str] = None
    execution_time_ms: float = 0.0
    tool_name: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_error(self) -> bool:
        return not self.success
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "execution_time_ms": self.execution_time_ms,
            "tool_name": self.tool_name,
            "metadata": self.metadata,
        }
    
    def format_for_llm(self) -> str:
        """格式化为 LLM 可读的结果"""
        if self.success:
            return json.dumps(self.data, ensure_ascii=False, default=str)
        else:
            return f"Error: {self.error}"


class BaseTool(ABC):
    """工具基类"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
    
    @property
    @abstractmethod
    def schema(self) -> ToolSchema:
        """返回工具 Schema"""
        pass
    
    @property
    def name(self) -> str:
        return self.schema.name
    
    @property
    def description(self) -> str:
        return self.schema.description
    
    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """执行工具
        
        Args:
            **kwargs: 工具参数
            
        Returns:
            ToolResult: 执行结果
        """
        pass
    
    async def validate(self, **kwargs) -> bool:
        """验证参数是否合法
        
        默认实现检查必需参数。
        子类可重写以提供自定义验证。
        """
        schema = self.schema.parameters
        required = schema.get("required", [])
        
        for param in required:
            if param not in kwargs or kwargs[param] is None:
                return False
        
        return True


class ToolRegistry:
    """工具注册表 - 管理所有可用工具"""
    
    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
        self._categories: Dict[ToolCategory, List[str]] = {cat: [] for cat in ToolCategory}
        self._tags_index: Dict[str, List[str]] = {}
    
    def register(self, tool: BaseTool, name: Optional[str] = None) -> None:
        """注册工具"""
        tool_name = name or tool.name
        self._tools[tool_name] = tool
        
        for tag in tool.schema.tags:
            if tag not in self._tags_index:
                self._tags_index[tag] = []
            self._tags_index[tag].append(tool_name)
        
        cat = tool.schema.category
        if tool_name not in self._categories[cat]:
            self._categories[cat].append(tool_name)
    
    def unregister(self, name: str) -> Optional[BaseTool]:
        """注销工具"""
        tool = self._tools.pop(name, None)
        if tool:
            for tag in tool.schema.tags:
                if tag in self._tags_index:
                    self._tags_index[tag].remove(name)
            for cat_tools in self._categories.values():
                if name in cat_tools:
                    cat_tools.remove(name)
        return tool
    
    def get(self, name: str) -> Optional[BaseTool]:
        """获取工具"""
        return self._tools.get(name)
    
    def get_by_category(self, category: ToolCategory) -> List[BaseTool]:
        """按分类获取工具"""
        return [self._tools[name] for name in self._categories.get(category, [])
                if name in self._tools]
    
    def get_by_tag(self, tag: str) -> List[BaseTool]:
        """按标签获取工具"""
        return [self._tools[name] for name in self._tags_index.get(tag, [])
                if name in self._tools]
    
    def search(self, keyword: str) -> List[BaseTool]:
        """搜索工具"""
        keyword = keyword.lower()
        results = []
        for tool in self._tools.values():
            if keyword in tool.name.lower():
                results.append(tool)
            elif keyword in tool.description.lower():
                results.append(tool)
        return results
    
    def list_all(self) -> List[str]:
        """列出所有工具名称"""
        return list(self._tools.keys())
    
    def get_schemas(self) -> List[ToolSchema]:
        """获取所有工具的 Schema"""
        return [tool.schema for tool in self._tools.values()]
    
    def get_all_categories(self) -> List[ToolCategory]:
        """获取所有有工具的分类"""
        return [cat for cat, tools in self._categories.items() if tools]


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
        """执行工具
        
        Args:
            tool_name: 工具名称
            args: 工具参数
            
        Returns:
            ToolResult: 执行结果
        """
        pass
    
    @abstractmethod
    async def execute_batch(self, calls: List[Dict[str, Any]]) -> List[ToolResult]:
        """批量执行工具
        
        Args:
            calls: [(tool_name, args), ...]
            
        Returns:
            List[ToolResult]: 执行结果列表
        """
        pass
    
    @abstractmethod
    def get_schema(self, tool_name: str) -> Optional[ToolSchema]:
        """获取工具 Schema"""
        pass


class StandardToolPort(ToolPort):
    """标准工具端口实现"""
    
    def __init__(self, registry: Optional[ToolRegistry] = None):
        self._registry = registry or ToolRegistry()
    
    @property
    def registry(self) -> ToolRegistry:
        return self._registry
    
    async def execute(self, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        import time
        start = time.time()
        
        tool = self._registry.get(tool_name)
        if not tool:
            return ToolResult(
                success=False,
                error=f"Tool '{tool_name}' not found",
                tool_name=tool_name
            )
        
        try:
            if not await tool.validate(**args):
                return ToolResult(
                    success=False,
                    error=f"Invalid arguments for tool '{tool_name}'",
                    tool_name=tool_name
                )
            
            result = await tool.execute(**args)
            result.execution_time_ms = (time.time() - start) * 1000
            result.tool_name = tool_name
            return result
            
        except Exception as e:
            return ToolResult(
                success=False,
                error=str(e),
                execution_time_ms=(time.time() - start) * 1000,
                tool_name=tool_name
            )
    
    async def execute_batch(self, calls: List[Dict[str, Any]]) -> List[ToolResult]:
        results = []
        for call in calls:
            tool_name = call.get("name") or call.get("tool_name", "")
            args = call.get("args") or call.get("arguments") or {}
            result = await self.execute(tool_name, args)
            results.append(result)
        return results
    
    def get_schema(self, tool_name: str) -> Optional[ToolSchema]:
        tool = self._registry.get(tool_name)
        return tool.schema if tool else None


def tool(name: Optional[str] = None, description: Optional[str] = None,
         category: ToolCategory = ToolCategory.CUSTOM,
         tags: Optional[List[str]] = None):
    """装饰器：创建工具
    
    Usage:
        @tool(name="my_tool", description="Does something")
        async def my_tool(arg1: str, arg2: int = 10) -> dict:
            ...
    """
    def decorator(func: Callable) -> Type[BaseTool]:
        class WrappedTool(BaseTool):
            @property
            def schema(self) -> ToolSchema:
                return ToolSchema.from_function(func, name, description)
            
            async def execute(self, **kwargs) -> ToolResult:
                try:
                    result = await func(**kwargs)
                    return ToolResult(success=True, data=result)
                except Exception as e:
                    return ToolResult(success=False, error=str(e))
        
        WrappedTool.__name__ = name or func.__name__
        return WrappedTool()
    
    return decorator


from typing import Optional
