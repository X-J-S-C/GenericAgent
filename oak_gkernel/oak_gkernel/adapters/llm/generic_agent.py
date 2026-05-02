"""GenericAgent LLM 适配器 - 保留原生 LLM 调用能力"""

from typing import Any, Dict, List, Optional, AsyncIterator, Union
from dataclasses import dataclass

from oak_gkernel.ports.llm_port import (
    BaseLLMAdapter,
    LLMPort,
    LLMConfig,
    LLMResponse,
    Message,
    MessageRole,
)


class GenericAgentLLMAdapter(BaseLLMAdapter):
    """GenericAgent 原生 LLM 适配器
    
    封装 GenericAgent 的 llmcore.py 中的 LLM 调用逻辑，
    使其符合新的端口接口。
    
    支持：
    - ClaudeSession
    - LLMSession  
    - NativeClaudeSession
    - NativeOAISession
    - MixinSession
    """
    
    def __init__(self, config: LLMConfig, session_class: str = "auto"):
        super().__init__(config)
        self._session = None
        self._session_class = session_class
        self._init_session()
    
    def _init_session(self) -> None:
        """初始化 LLM 会话"""
        # 延迟导入以避免循环依赖
        try:
            import llmcore as ga_llm
            from llmcore import (
                ClaudeSession, 
                LLMSession, 
                NativeClaudeSession,
                NativeOAISession,
                MixinSession
            )
            
            cfg = {
                'apikey': self._config.api_key,
                'apibase': self._config.api_base,
                'model': self._config.model,
                'temperature': self._config.temperature,
                'max_tokens': self._config.max_tokens,
                'timeout': self._config.timeout,
                'max_retries': self._config.max_retries,
            }
            
            if 'native' in self._session_class.lower() and 'claude' in self._session_class.lower():
                self._session = NativeClaudeSession(cfg=cfg)
            elif 'native' in self._session_class.lower() and 'oai' in self._session_class.lower():
                self._session = NativeOAISession(cfg=cfg)
            elif 'claude' in self._session_class.lower():
                self._session = ClaudeSession(cfg=cfg)
            elif 'mixin' in self._session_class.lower():
                self._session = MixinSession([self._session], cfg)
            else:
                self._session = LLMSession(cfg=cfg)
                
        except ImportError:
            # GenericAgent 模块不可用，降级到标准实现
            self._session = None
    
    async def chat(self, messages: List[Union[Message, Dict]], 
                   tools: Optional[List[Dict]] = None,
                   **kwargs) -> LLMResponse:
        """使用 GenericAgent 原生会话发送请求"""
        if self._session is None:
            raise RuntimeError("GenericAgent session not initialized")
        
        # 转换消息格式
        prepared = self._prepare_messages_for_ga(messages)
        
        # 设置工具
        if tools:
            self._session.tools = tools
        
        # 调用 GenericAgent 原生方法
        result = await self._call_ga_session(prepared, tools)
        
        return self._parse_ga_response(result)
    
    def _prepare_messages_for_ga(self, messages: List[Union[Message, Dict]]) -> List[Dict]:
        """准备 GenericAgent 格式的消息"""
        prepared = []
        for msg in messages:
            if isinstance(msg, Message):
                prepared.append(msg.to_dict())
            elif isinstance(msg, dict):
                prepared.append(msg)
        
        return prepared
    
    async def _call_ga_session(self, messages: List[Dict], tools: Optional[List[Dict]] = None):
        """调用 GenericAgent 会话"""
        import asyncio
        
        def _sync_call():
            if hasattr(self._session, 'ask'):
                result = self._session.ask(messages[-1]['content'])
                return result
            return ""
        
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _sync_call)
        return result
    
    def _parse_ga_response(self, response: Any) -> LLMResponse:
        """解析 GenericAgent 响应"""
        if hasattr(response, 'content'):
            content = response.content
            tool_calls = []
            if hasattr(response, 'tool_calls'):
                for tc in response.tool_calls:
                    tool_calls.append({
                        'id': getattr(tc, 'id', ''),
                        'name': getattr(tc.function, 'name', ''),
                        'input': getattr(tc.function, 'arguments', {}),
                    })
            reasoning = getattr(response, 'thinking', None)
        else:
            content = str(response) if response else ""
            tool_calls = []
            reasoning = None
        
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            reasoning=reasoning,
            model=self._config.model,
        )
    
    async def stream(self, messages: List[Union[Message, Dict]],
                     tools: Optional[List[Dict]] = None,
                     **kwargs) -> AsyncIterator[str]:
        """流式响应"""
        if self._session is None:
            raise RuntimeError("GenericAgent session not initialized")
        
        prepared = self._prepare_messages_for_ga(messages)
        
        if tools:
            self._session.tools = tools
        
        # 使用 GenericAgent 的流式方法
        for chunk in self._session.ask(prepared[-1]['content'], stream=True):
            yield chunk
