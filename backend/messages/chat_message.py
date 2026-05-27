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


@dataclass(frozen=True)
class DiscussionHistoryMessage(BaseMessage):
    """Historique complet d'une session, émis à la déconnexion"""
    history: tuple


@dataclass(frozen=True)
class AgentTopicMessage(BaseMessage):
    """Données reçues sur un topic read-access annoncé par un agent via agent_topics"""
    topic: str
    description: str
    payload: Any
    priority: int = 20
    is_response: bool = False


@dataclass(frozen=True)
class MqttWriteMessage(BaseMessage):
    """Demande d'écriture sur un topic MQTT write-access"""
    topic: str
    payload: Any


@dataclass(frozen=True)
class MqttToolUpdateMessage(BaseMessage):
    """Mise à jour de la définition du tool générique write_topic"""
    tool_definition: Dict[str, Any]