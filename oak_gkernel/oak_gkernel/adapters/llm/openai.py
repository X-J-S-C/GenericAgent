"""OpenAI 适配器"""

from typing import Any, Dict, List, Optional, AsyncIterator, Union
import requests
import asyncio

from oak_gkernel.ports.llm_port import (
    BaseLLMAdapter,
    LLMConfig,
    LLMResponse,
)


class OpenAIAdapter(BaseLLMAdapter):
    """OpenAI API 适配器"""
    
    async def chat(self, messages: List[Union[Dict, Any]], 
                   tools: Optional[List[Dict]] = None,
                   **kwargs) -> LLMResponse:
        """发送 OpenAI API 请求"""
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }
        
        payload = self._build_payload(messages, tools)
        
        url = f"{self._config.api_base.rstrip('/')}/v1/chat/completions"
        
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
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }
        
        payload = self._build_payload(messages, tools)
        payload["stream"] = True
        
        url = f"{self._config.api_base.rstrip('/')}/v1/chat/completions"
        
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
                            choices = data.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                if delta.get("content"):
                                    yield delta["content"]
                        except:
                            continue
    
    def _build_payload(self, messages: List[Dict], tools: Optional[List[Dict]]) -> Dict:
        """构建请求载荷"""
        payload = {
            "model": self._config.model,
            "messages": self._prepare_messages(messages),
            "temperature": self._config.temperature,
            "stream": False,
        }
        
        if self._config.max_tokens:
            model_lower = self._config.model.lower()
            if model_lower.startswith(("o1", "o2", "o3", "o4", "gpt-5")):
                payload["max_completion_tokens"] = self._config.max_tokens
            else:
                payload["max_tokens"] = self._config.max_tokens
        
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        
        return payload
    
    async def _make_request(self, url: str, headers: Dict, payload: Dict):
        """发送 HTTP 请求"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: requests.post(url, headers=headers, json=payload)
        )
    
    def _parse_response(self, data: Dict) -> LLMResponse:
        """解析 OpenAI API 响应"""
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        
        tool_calls = []
        for tc in message.get("tool_calls", []):
            tool_calls.append({
                "id": tc.get("id", ""),
                "name": tc.get("function", {}).get("name", ""),
                "input": tc.get("function", {}).get("arguments", {}),
            })
        
        reasoning = message.get("reasoning_content") or choice.get("reasoning_content")
        
        return LLMResponse(
            content=message.get("content", ""),
            tool_calls=tool_calls,
            reasoning=reasoning,
            usage=data.get("usage", {}),
            model=self._config.model,
            raw=data,
        )
