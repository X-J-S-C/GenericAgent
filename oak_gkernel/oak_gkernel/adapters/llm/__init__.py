"""LLM 适配器"""

from oak_gkernel.adapters.llm.generic_agent import GenericAgentLLMAdapter
from oak_gkernel.adapters.llm.anthropic import ClaudeAdapter as AnthropicClaudeAdapter
from oak_gkernel.adapters.llm.openai import OpenAIAdapter as OpenAIAdapterImpl

__all__ = [
    "GenericAgentLLMAdapter",
    "AnthropicClaudeAdapter",
    "OpenAIAdapterImpl",
]
