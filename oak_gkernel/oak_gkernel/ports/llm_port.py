"""LLM 端口 - 模型网关接口"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, AsyncIterator, Union
from enum import Enum
import json


class MessageRole(Enum):
    """消息角色"""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    """消息对象"""
    role: MessageRole
    content: Any  # str | List[Dict] | None
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_results: Optional[List[Dict[str, Any]]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "role": self.role.value,
        }
        
        if isinstance(self.content, str):
            result["content"] = self.content
        elif isinstance(self.content, list):
            result["content"] = self.content
        else:
            result["content"] = str(self.content) if self.content else ""
        
        if self.name:
            result["name"] = self.name
        
        if self.tool_calls:
            result["tool_calls"] = self.tool_calls
        
        if self.tool_results:
            if isinstance(result.get("content"), list):
                result["content"].extend(self.tool_results)
            else:
                result["content"] = self.tool_results
        
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        role = MessageRole(data.get("role", "user"))
        return cls(
            role=role,
            content=data.get("content", ""),
            name=data.get("name"),
            tool_calls=data.get("tool_calls"),
            tool_results=data.get("tool_results"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class LLMResponse:
    """LLM 响应"""
    content: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    reasoning: Optional[str] = None
    usage: Dict[str, int] = field(default_factory=dict)
    model: Optional[str] = None
    raw: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "tool_calls": self.tool_calls,
            "reasoning": self.reasoning,
            "usage": self.usage,
            "model": self.model,
            "metadata": self.metadata,
        }


@dataclass
class LLMConfig:
    """LLM 配置"""
    model: str
    api_key: str
    api_base: str
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    timeout: int = 120
    max_retries: int = 3
    reasoning_effort: Optional[str] = None
    thinking_budget_tokens: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class LLMPort(ABC):
    """LLM 端口接口 - 六边形架构的端口
    
    定义 LLM 交互的标准接口，支持：
    - 多模型支持
    - 流式响应
    - 函数调用
    - 重试机制
    """
    
    @property
    @abstractmethod
    def config(self) -> LLMConfig:
        """返回 LLM 配置"""
        pass
    
    @property
    def model(self) -> str:
        return self.config.model
    
    @abstractmethod
    async def chat(self, messages: List[Union[Message, Dict]], 
                   tools: Optional[List[Dict]] = None,
                   **kwargs) -> LLMResponse:
        """发送对话请求
        
        Args:
            messages: 消息列表
            tools: 可用的工具列表（函数调用）
            **kwargs: 额外参数
            
        Returns:
            LLMResponse: LLM 响应
        """
        pass
    
    @abstractmethod
    async def stream(self, messages: List[Union[Message, Dict]],
                     tools: Optional[List[Dict]] = None,
                     **kwargs) -> AsyncIterator[str]:
        """流式响应
        
        Args:
            messages: 消息列表
            tools: 可用的工具列表
            **kwargs: 额外参数
            
        Yields:
            str: 增量输出
        """
        pass
    
    @abstractmethod
    async def count_tokens(self, text: str) -> int:
        """估算 token 数量
        
        Args:
            text: 文本内容
            
        Returns:
            int: 估算的 token 数量
        """
        pass


class BaseLLMAdapter(LLMPort):
    """LLM 适配器基类"""
    
    def __init__(self, config: LLMConfig):
        self._config = config
        self._message_history: List[Message] = []
    
    @property
    def config(self) -> LLMConfig:
        return self._config
    
    def _prepare_messages(self, messages: List[Union[Message, Dict]]) -> List[Dict]:
        """准备消息格式"""
        result = []
        for msg in messages:
            if isinstance(msg, Message):
                result.append(msg.to_dict())
            elif isinstance(msg, dict):
                result.append(msg)
        return result
    
    async def chat(self, messages: List[Union[Message, Dict]], 
                   tools: Optional[List[Dict]] = None,
                   **kwargs) -> LLMResponse:
        raise NotImplementedError
    
    async def stream(self, messages: List[Union[Message, Dict]],
                     tools: Optional[List[Dict]] = None,
                     **kwargs) -> AsyncIterator[str]:
        response = await self.chat(messages, tools, **kwargs)
        for char in response.content:
            yield char
    
    async def count_tokens(self, text: str) -> int:
        # 简单估算：中文约 2 字符/token，英文约 4 字符/token
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other_chars = len(text) - chinese_chars
        return chinese_chars // 2 + other_chars // 4


class ClaudeAdapter(BaseLLMAdapter):
    """Anthropic Claude 适配器"""
    
    async def chat(self, messages: List[Union[Message, Dict]], 
                   tools: Optional[List[Dict]] = None,
                   **kwargs) -> LLMResponse:
        import requests
        import time
        
        prepared = self._prepare_messages(messages)
        
        headers = {
            "x-api-key": self._config.api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        
        payload = {
            "model": self._config.model,
            "messages": prepared,
            "max_tokens": self._config.max_tokens or 8192,
            "temperature": self._config.temperature,
        }
        
        if self._config.reasoning_effort:
            payload["thinking"] = {
                "type": self._config.reasoning_effort
            }
        
        if tools:
            payload["tools"] = tools
        
        url = f"{self._config.api_base.rstrip('/')}/v1/messages"
        
        for attempt in range(self._config.max_retries):
            try:
                response = requests.post(
                    url, 
                    headers=headers, 
                    json=payload, 
                    timeout=self._config.timeout
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return self._parse_response(data)
                elif response.status_code in (408, 429, 500, 502, 503):
                    if attempt < self._config.max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                
                raise Exception(f"HTTP {response.status_code}: {response.text}")
                
            except requests.exceptions.Timeout:
                if attempt < self._config.max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
        
        raise Exception("Max retries exceeded")
    
    def _parse_response(self, data: Dict) -> LLMResponse:
        content_blocks = data.get("content", [])
        
        text_content = ""
        tool_calls = []
        thinking_content = None
        
        for block in content_blocks:
            if block.get("type") == "text":
                text_content += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "input": block.get("input", {}),
                })
            elif block.get("type") == "thinking":
                thinking_content = block.get("thinking", "")
        
        return LLMResponse(
            content=text_content,
            tool_calls=tool_calls,
            reasoning=thinking_content,
            usage=data.get("usage", {}),
            model=self._config.model,
            raw=data,
        )


class OpenAIAdapter(BaseLLMAdapter):
    """OpenAI 适配器"""
    
    async def chat(self, messages: List[Union[Message, Dict]], 
                   tools: Optional[List[Dict]] = None,
                   **kwargs) -> LLMResponse:
        import requests
        import time
        
        prepared = self._prepare_messages(messages)
        
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": self._config.model,
            "messages": prepared,
            "temperature": self._config.temperature,
            "stream": False,
        }
        
        if self._config.max_tokens:
            payload["max_tokens"] = self._config.max_tokens
        
        if tools:
            payload["tools"] = tools
        
        url = f"{self._config.api_base.rstrip('/')}/v1/chat/completions"
        
        for attempt in range(self._config.max_retries):
            try:
                response = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self._config.timeout
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return self._parse_response(data)
                elif response.status_code in (408, 429, 500, 502, 503):
                    if attempt < self._config.max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                
                raise Exception(f"HTTP {response.status_code}: {response.text}")
                
            except requests.exceptions.Timeout:
                if attempt < self._config.max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
        
        raise Exception("Max retries exceeded")
    
    def _parse_response(self, data: Dict) -> LLMResponse:
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        
        tool_calls = []
        for tc in message.get("tool_calls", []):
            tool_calls.append({
                "id": tc.get("id", ""),
                "name": tc.get("function", {}).get("name", ""),
                "input": tc.get("function", {}).get("arguments", {}),
            })
        
        return LLMResponse(
            content=message.get("content", ""),
            tool_calls=tool_calls,
            reasoning=message.get("reasoning_content"),
            usage=data.get("usage", {}),
            model=self._config.model,
            raw=data,
        )
