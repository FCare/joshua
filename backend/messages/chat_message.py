from dataclasses import dataclass
from typing import Dict, Any, Optional
from .base_message import BaseMessage


@dataclass(frozen=True)
class SystemPromptMessage(BaseMessage):
    """Message de mise à jour du system prompt"""
    prompt: str


@dataclass(frozen=True)
class ChatResponseMessage(BaseMessage):
    """Message de réponse de chat"""
    text: str

@dataclass(frozen=True)
class ChatFinishMessage(BaseMessage):
    pass


@dataclass(frozen=True)
class ToolsReadyMessage(BaseMessage):
    """Message indiquant que tous les outils sont prêts"""
    tools_definitions: Dict[str, Any]