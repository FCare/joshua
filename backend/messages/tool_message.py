from dataclasses import dataclass
from typing import Dict, Any, Optional
from .base_message import BaseMessage


@dataclass(frozen=True)
class ToolCallMessage(BaseMessage):
    """Message d'appel d'outil par le LLM"""
    tool_name: str
    tool_call_id: str
    parameters: Dict[str, Any]


@dataclass(frozen=True)
class ToolResponseMessage(BaseMessage):
    """Message de réponse d'un outil"""
    tool_call_id: str
    tool_name: str
    result: Any = None
    error: Optional[str] = None


@dataclass(frozen=True)
class ToolRegistrationMessage(BaseMessage):
    """Message d'enregistrement d'un outil"""
    tool_definition: Dict[str, Any]
    source_step: str


@dataclass(frozen=True)
class ToolsReadyMessage(BaseMessage):
    """Message indiquant que tous les outils sont prêts"""
    tools_definitions: Dict[str, Any]