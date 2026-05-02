"""Claude 适配器 - Anthropic Claude"""

from typing import Any, Dict, List, Optional, AsyncIterator, Union
import requests
import time
import asyncio

from oak_gkernel.ports.llm_port import (
    BaseLLMAdapter,
    LLMConfig,
    LLMResponse,
)


class ClaudeAdapter(BaseLLMAdapter):
    """Anthropic Claude API 适配器"""
    
    async def chat(self, messages: List[Union[Dict, Any]], 
                   tools: Optional[List[Dict]] = None,
                   **kwargs) -> LLMResponse:
        """发送 Claude API 请求"""
        headers = {
            "x-api-key": self._config.api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "prompt-caching-2024-07-31",
        }
        
        payload = self._build_payload(messages, tools)
        
        url = f"{self._config.api_base.rstrip('/')}/v1/messages"
        
        for attempt in range(self._config.max_retries):
            try:
                response = await self._make_request(url, headers, payload)
                if response.status_code == 200:
                    return self._parse_response(response.json())
                
                if response.status_code in (408, 429, 500, 502, 503):
                    if attempt < self._config.max_retries - 1:
                        await asyncio.sleep(min(30, 2 ** attempt))
                        continue
                
                raise Exception(f"HTTP {response.status_code}: {response.text}")
                
            except requests.exceptions.RequestException as e:
                if attempt < self._config.max_retries - 1:
                    await asyncio.sleep(min(30, 2 ** attempt))
                    continue
                raise
        
        raise Exception("Max retries exceeded")
    
    async def stream(self, messages: List[Union[Dict, Any]],
                     tools: Optional[List[Dict]] = None,
                     **kwargs) -> AsyncIterator[str]:
        """流式响应"""
        import httpx
        
        headers = {
            "x-api-key": self._config.api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        
        payload = self._build_payload(messages, tools)
        payload["stream"] = True
        
        url = f"{self._config.api_base.rstrip('/')}/v1/messages"
        
        async with httpx.AsyncClient(timeout=self._config.timeout) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            import json
                            data = json.loads(data_str)
                            delta = data.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    yield text
                        except:
                            continue
    
    def _build_payload(self, messages: List[Dict], tools: Optional[List[Dict]]) -> Dict:
        """构建请求载荷"""
        payload = {
            "model": self._config.model,
            "messages": self._prepare_messages(messages),
            "max_tokens": self._config.max_tokens or 8192,
            "temperature": self._config.temperature,
        }
        
        if self._config.reasoning_effort:
            if self._config.reasoning_effort == "enabled":
                if self._config.thinking_budget_tokens:
                    payload["thinking"] = {
                        "type": "enabled",
                        "budget_tokens": self._config.thinking_budget_tokens
                    }
            else:
                payload["thinking"] = {"type": self._config.reasoning_effort}
        
        if tools:
            payload["tools"] = tools
        
        # 应用消息缓存
        if self._config.metadata.get("cache_control"):
            user_msgs = [m for m in payload["messages"] if m.get("role") == "user"]
            if user_msgs:
                last_two = user_msgs[-2:]
                for msg in last_two:
                    content = msg.get("content", [])
                    if isinstance(content, str):
                        msg["content"] = [{"type": "text", "text": content}]
                    if isinstance(content, list) and content:
                        content[-1]["cache_control"] = {"type": "ephemeral"}
        
        return payload
    
    async def _make_request(self, url: str, headers: Dict, payload: Dict):
        """发送 HTTP 请求"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: requests.post(url, headers=headers, json=payload)
        )
    
    def _parse_response(self, data: Dict) -> LLMResponse:
        """解析 Claude API 响应"""
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
