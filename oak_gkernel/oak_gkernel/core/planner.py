"""规划器接口 - 策略模式实现"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable
from enum import Enum, auto
import json


class PlannerType(Enum):
    """规划器类型"""
    REACT = auto()
    TREE_OF_THOUGHT = auto()
    REFLEXION = auto()
    CUSTOM = auto()


@dataclass
class PlanStep:
    """规划步骤"""
    thought: str
    action: str
    args: Dict[str, Any] = field(default_factory=dict)
    expected_outcome: Optional[str] = None
    confidence: float = 1.0
    alternatives: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "thought": self.thought,
            "action": self.action,
            "args": self.args,
            "expected_outcome": self.expected_outcome,
            "confidence": self.confidence,
            "alternatives": self.alternatives,
            "metadata": self.metadata,
        }


@dataclass
class ExecutionResult:
    """执行结果"""
    step: PlanStep
    success: bool
    output: Any = None
    error: Optional[str] = None
    actual_outcome: Optional[str] = None
    reflection: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    """执行计划"""
    steps: List[PlanStep]
    original_query: str
    planner_type: PlannerType
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "steps": [s.to_dict() for s in self.steps],
            "original_query": self.original_query,
            "planner_type": self.planner_type.name,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }
    
    @property
    def is_empty(self) -> bool:
        return len(self.steps) == 0
    
    def __len__(self) -> int:
        return len(self.steps)


class BasePlanner(ABC):
    """规划器基类 - 使用策略模式
    
    规划器负责：
    1. 分析用户查询
    2. 生成执行计划
    3. 反思执行结果（可选）
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
    
    @property
    @abstractmethod
    def planner_type(self) -> PlannerType:
        """返回规划器类型"""
        pass
    
    @property
    def name(self) -> str:
        return self.__class__.__name__
    
    @abstractmethod
    async def plan(self, query: str, context: Dict[str, Any]) -> Plan:
        """生成执行计划
        
        Args:
            query: 用户查询
            context: 上下文信息
            
        Returns:
            Plan: 执行计划
        """
        pass
    
    async def reflect(self, history: List[ExecutionResult]) -> Dict[str, Any]:
        """反思执行结果
        
        默认实现返回空字典，子类可重写以提供更深入的反思。
        
        Args:
            history: 执行历史
            
        Returns:
            Dict: 反思结果
        """
        return {
            "success_rate": sum(1 for r in history if r.success) / len(history) if history else 0,
            "total_steps": len(history),
            "errors": [r.error for r in history if r.error],
        }
    
    async def revise_plan(self, plan: Plan, feedback: Dict[str, Any]) -> Plan:
        """修订计划
        
        基于反馈修订计划。默认实现直接返回原计划。
        
        Args:
            plan: 原计划
            feedback: 反馈信息
            
        Returns:
            Plan: 修订后的计划
        """
        return plan


class ReActPlanner(BasePlanner):
    """ReAct (Reasoning + Acting) 规划器
    
    交替进行推理和行动：
    Thought -> Action -> Observation -> Thought -> ...
    """
    
    @property
    def planner_type(self) -> PlannerType:
        return PlannerType.REACT
    
    async def plan(self, query: str, context: Dict[str, Any]) -> Plan:
        """生成 ReAct 风格的计划"""
        steps = []
        
        llm = context.get("llm")
        tools = context.get("tools", [])
        
        if llm:
            prompt = self._build_prompt(query, tools)
            response = await llm.chat([{"role": "user", "content": prompt}])
            
            steps = self._parse_response(response.content, query)
        
        return Plan(
            steps=steps,
            original_query=query,
            planner_type=self.planner_type,
            metadata={"method": "react"}
        )
    
    def _build_prompt(self, query: str, tools: List[Dict]) -> str:
        tool_desc = "\n".join([f"- {t['name']}: {t['description']}" for t in tools])
        return f"""分析以下任务并生成执行步骤：

任务：{query}

可用工具：
{tool_desc}

请按以下格式输出步骤：
1. [Thought] <思考>
2. [Action] <工具名> <参数>
3. [Expected] <预期结果>

开始：
"""
    
    def _parse_response(self, response: str, query: str) -> List[PlanStep]:
        """解析 LLM 响应为 PlanStep 列表"""
        steps = []
        lines = response.strip().split("\n")
        
        current_thought = ""
        current_action = ""
        current_args = {}
        
        for line in lines:
            line = line.strip()
            if line.startswith("[Thought]"):
                if current_action:
                    steps.append(PlanStep(
                        thought=current_thought,
                        action=current_action,
                        args=current_args
                    ))
                    current_action = ""
                    current_args = {}
                current_thought = line[9:].strip()
            elif line.startswith("[Action]"):
                parts = line[9:].strip().split(" ", 1)
                current_action = parts[0]
                if len(parts) > 1:
                    try:
                        current_args = json.loads(parts[1])
                    except:
                        current_args = {"raw": parts[1]}
        
        if current_action:
            steps.append(PlanStep(
                thought=current_thought,
                action=current_action,
                args=current_args
            ))
        
        return steps


class TreeOfThoughtPlanner(BasePlanner):
    """思维树 (Tree of Thought) 规划器
    
    探索多条推理路径，选择最优：
           Root
          / | \
        P1 P2 P3
        |  |  |
       ... ... ...
    """
    
    @property
    def planner_type(self) -> PlannerType:
        return PlannerType.TREE_OF_THOUGHT
    
    async def plan(self, query: str, context: Dict[str, Any]) -> Plan:
        """生成 ToT 风格的计划"""
        max_depth = self.config.get("max_depth", 3)
        branching_factor = self.config.get("branching_factor", 3)
        
        llm = context.get("llm")
        tools = context.get("tools", [])
        
        if not llm:
            return Plan(steps=[], original_query=query, planner_type=self.planner_type)
        
        root_prompt = f"分析并提出3个不同的解决方向：\n\n任务：{query}"
        root_response = await llm.chat([{"role": "user", "content": root_prompt}])
        
        directions = self._parse_directions(root_response.content)
        
        all_steps = []
        for direction in directions[:branching_factor]:
            steps = await self._explore_branch(query, direction, llm, tools, depth=0, max_depth=max_depth)
            all_steps.extend(steps)
        
        all_steps.sort(key=lambda s: s.confidence, reverse=True)
        
        return Plan(
            steps=all_steps[:self.config.get("max_steps", 10)],
            original_query=query,
            planner_type=self.planner_type,
            metadata={"method": "tree_of_thought", "directions": len(directions)}
        )
    
    async def _explore_branch(self, query: str, direction: str, llm, 
                              tools: List[Dict], depth: int, max_depth: int) -> List[PlanStep]:
        """探索单个分支"""
        if depth >= max_depth:
            return []
        
        prompt = f"""在方向「{direction}」下，继续推理下一步：

任务：{query}

深度：{depth}/{max_depth}
"""
        response = await llm.chat([{"role": "user", "content": prompt}])
        
        steps = self._parse_response(response.content, query)
        
        for step in steps[:3]:
            sub_steps = await self._explore_branch(query, direction, llm, tools, depth + 1, max_depth)
            steps.extend(sub_steps)
        
        return steps[:5]
    
    def _parse_directions(self, response: str) -> List[str]:
        """解析方向列表"""
        lines = response.strip().split("\n")
        directions = []
        for line in lines:
            line = line.strip()
            if line and (line[0].isdigit() or line.startswith("-")):
                direction = line.lstrip("0123456789.-) ").strip()
                directions.append(direction)
        return directions
    
    def _parse_response(self, response: str, query: str) -> List[PlanStep]:
        """解析响应"""
        steps = []
        lines = response.strip().split("\n")
        
        for line in lines[:5]:
            line = line.strip()
            if line and len(line) > 10:
                steps.append(PlanStep(
                    thought=line,
                    action="unknown",
                    args={},
                    confidence=0.8
                ))
        
        return steps


class ReflexionPlanner(BasePlanner):
    """反思式规划器 - 基于 GenericAgent 的自我进化
    
    核心思想：
    1. 执行任务
    2. 反思结果
    3. 从反思中学习
    4. 改进下一步
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._learned_patterns: List[Dict[str, Any]] = []
        self._failed_strategies: List[Dict[str, Any]] = []
    
    @property
    def planner_type(self) -> PlannerType:
        return PlannerType.REFLEXION
    
    async def plan(self, query: str, context: Dict[str, Any]) -> Plan:
        """生成反思式计划"""
        memory = context.get("memory")
        llm = context.get("llm")
        
        learned = []
        if memory:
            learned = await memory.search(f"类似任务经验: {query}", limit=5)
        
        base_steps = []
        if llm:
            prompt = self._build_self_evolving_prompt(query, learned)
            response = await llm.chat([{"role": "user", "content": prompt}])
            base_steps = self._parse_response(response.content)
        
        for step in base_steps:
            if learned:
                step.confidence = 0.9
                step.metadata["learned_from"] = "memory"
        
        return Plan(
            steps=base_steps,
            original_query=query,
            planner_type=self.planner_type,
            metadata={"method": "reflexion", "learned_count": len(learned)}
        )
    
    async def reflect(self, history: List[ExecutionResult]) -> Dict[str, Any]:
        """深度反思执行历史"""
        successes = [r for r in history if r.success]
        failures = [r for r in history if not r.success]
        
        reflection = {
            "total": len(history),
            "successes": len(successes),
            "failures": len(failures),
            "patterns": [],
            "learned_actions": [],
            "avoid_actions": [],
        }
        
        if failures:
            for failure in failures:
                self._failed_strategies.append({
                    "step": failure.step.action,
                    "error": failure.error,
                    "context": failure.metadata,
                })
                reflection["avoid_actions"].append(failure.step.action)
        
        if successes:
            for success in successes:
                reflection["learned_actions"].append({
                    "action": success.step.action,
                    "pattern": success.step.thought,
                })
        
        return reflection
    
    def _build_self_evolving_prompt(self, query: str, learned: List[Any]) -> str:
        learned_str = "\n".join([f"- {l}" for l in learned[:3]]) if learned else "无"
        return f"""作为自我进化的 Agent，分析任务并生成执行计划：

任务：{query}

已学习的相关经验：
{learned_str}

请生成执行步骤，并标注：
- 新颖之处（之前没做过）
- 复用之处（可以借鉴）
"""
    
    def _parse_response(self, response: str) -> List[PlanStep]:
        """解析响应"""
        steps = []
        lines = response.strip().split("\n")
        
        for line in lines:
            line = line.strip()
            if line and (line[0].isdigit() or line.startswith("-")):
                content = line.lstrip("0123456789.-) ").strip()
                steps.append(PlanStep(
                    thought=content,
                    action="analyze",
                    args={"content": content},
                    confidence=0.85
                ))
        
        return steps[:10]
