"""状态机 - 管理 Agent 状态转换"""

from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field
from enum import Enum, auto
import asyncio

from oak_gkernel.core.agent_kernel import AgentState, Transition


class StateMachineError(Exception):
    """状态机错误"""
    pass


@dataclass
class TransitionRule:
    """转换规则"""
    from_state: AgentState
    to_state: AgentState
    event: str
    condition: Optional[Callable[[], bool]] = None
    before_action: Optional[Callable[[], None]] = None
    after_action: Optional[Callable[[], None]] = None


class StateMachine:
    """状态机 - 管理 Agent 状态转换
    
    支持：
    - 定义状态转换规则
    - 触发事件进行状态转换
    - 条件判断
    - 转换前后钩子
    """
    
    def __init__(self, initial_state: AgentState = AgentState.IDLE):
        self._current_state = initial_state
        self._rules: Dict[str, List[TransitionRule]] = {}  # event -> rules
        self._state_listeners: Dict[AgentState, List[Callable[[AgentState, AgentState], None]]] = {}
        self._transition_history: List[Dict[str, Any]] = []
    
    @property
    def current_state(self) -> AgentState:
        return self._current_state
    
    def add_rule(self, rule: TransitionRule) -> "StateMachine":
        """添加转换规则"""
        if rule.event not in self._rules:
            self._rules[rule.event] = []
        self._rules[rule.event].append(rule)
        return self
    
    def add_transition(self, from_state: AgentState, to_state: AgentState, 
                       event: str, condition: Optional[Callable[[], bool]] = None,
                       before_action: Optional[Callable[[], None]] = None,
                       after_action: Optional[Callable[[], None]] = None) -> "StateMachine":
        """添加转换（便捷方法）"""
        rule = TransitionRule(
            from_state=from_state,
            to_state=to_state,
            event=event,
            condition=condition,
            before_action=before_action,
            after_action=after_action
        )
        return self.add_rule(rule)
    
    def on_state_change(self, state: AgentState, listener: Callable[[AgentState, AgentState], None]) -> "StateMachine":
        """监听状态变化"""
        if state not in self._state_listeners:
            self._state_listeners[state] = []
        self._state_listeners[state].append(listener)
        return self
    
    def trigger(self, event: str, **kwargs) -> bool:
        """触发事件
        
        Args:
            event: 事件名称
            **kwargs: 传递给动作的额外参数
            
        Returns:
            bool: 是否成功转换
        """
        rules = self._rules.get(event, [])
        for rule in rules:
            if rule.from_state != self._current_state:
                continue
            
            if rule.condition and not rule.condition():
                continue
            
            self._execute_transition(rule, **kwargs)
            return True
        
        return False
    
    def _execute_transition(self, rule: TransitionRule, **kwargs) -> None:
        """执行状态转换"""
        old_state = self._current_state
        new_state = rule.to_state
        
        if rule.before_action:
            rule.before_action()
        
        self._current_state = new_state
        
        self._transition_history.append({
            "from": old_state,
            "to": new_state,
            "event": rule.event,
        })
        
        if rule.after_action:
            rule.after_action()
        
        for listener in self._state_listeners.get(new_state, []):
            listener(old_state, new_state)
    
    def can_transition(self, event: str) -> bool:
        """检查是否可以转换"""
        rules = self._rules.get(event, [])
        for rule in rules:
            if rule.from_state == self._current_state:
                if rule.condition is None or rule.condition():
                    return True
        return False
    
    def get_available_events(self) -> List[str]:
        """获取当前状态可用的事件"""
        events = []
        for event, rules in self._rules.items():
            for rule in rules:
                if rule.from_state == self._current_state:
                    if rule.condition is None or rule.condition():
                        events.append(event)
                        break
        return events
    
    def get_history(self) -> List[Dict[str, Any]]:
        """获取转换历史"""
        return self._transition_history.copy()
    
    def reset(self, state: AgentState = AgentState.IDLE) -> None:
        """重置状态机"""
        self._current_state = state
        self._transition_history = []


class AgentStateMachine(StateMachine):
    """Agent 专用状态机 - 预设常用转换规则"""
    
    def __init__(self):
        super().__init__(AgentState.IDLE)
        self._setup_default_rules()
    
    def _setup_default_rules(self) -> None:
        """设置默认转换规则"""
        self.add_transition(
            AgentState.IDLE, 
            AgentState.INITIALIZING, 
            "start",
            after_action=lambda: None
        )
        
        self.add_transition(
            AgentState.INITIALIZING,
            AgentState.RUNNING,
            "init_complete"
        )
        
        self.add_transition(
            AgentState.RUNNING,
            AgentState.WAITING_INPUT,
            "request_input",
            condition=lambda: self._current_state == AgentState.RUNNING
        )
        
        self.add_transition(
            AgentState.WAITING_INPUT,
            AgentState.RUNNING,
            "input_received"
        )
        
        self.add_transition(
            AgentState.RUNNING,
            AgentState.PAUSED,
            "pause"
        )
        
        self.add_transition(
            AgentState.PAUSED,
            AgentState.RUNNING,
            "resume"
        )
        
        self.add_transition(
            AgentState.RUNNING,
            AgentState.COMPLETED,
            "complete",
            condition=lambda: self._current_state == AgentState.RUNNING
        )
        
        self.add_transition(
            AgentState.RUNNING,
            AgentState.ERROR,
            "error",
            condition=lambda: self._current_state == AgentState.RUNNING
        )
        
        self.add_transition(
            AgentState.ERROR,
            AgentState.IDLE,
            "reset"
        )
        
        self.add_transition(
            AgentState.COMPLETED,
            AgentState.IDLE,
            "reset"
        )
