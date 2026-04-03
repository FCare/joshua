from dataclasses import dataclass
from typing import Dict, Any, Optional
from messages.base_message import BaseMessage


@dataclass(frozen=True)
class SystemPromptMessage(BaseMessage):
    """Message de mise à jour du system prompt"""
    
    @classmethod
    def create(cls, prompt: str, metadata: Optional[Dict] = None):
        # Créer data comme dict avec les informations du prompt
        data = {
            "prompt": prompt,
            "type": "system_prompt"
        }
        return cls(data=data, metadata=metadata)
    
    @property
    def prompt(self) -> str:
        return self.data.get("prompt", "")


@dataclass(frozen=True)
class ChatResponseMessage(BaseMessage):
    """Message de réponse de chat"""
    
    @classmethod
    def create(cls, text: str, is_partial: bool = False, metadata: Optional[Dict] = None):
        # Créer data comme dict avec les informations de la réponse
        data = {
            "text": text,
            "is_partial": is_partial,
            "type": "chat_response"
        }
        return cls(data=data, metadata=metadata)
    
    @property
    def text(self) -> str:
        return self.data.get("text", "")
    
    @property
    def is_partial(self) -> bool:
        return self.data.get("is_partial", False)


@dataclass(frozen=True)
class ToolsReadyMessage(BaseMessage):
    """Message indiquant que tous les outils sont prêts"""
    
    @classmethod
    def create(cls, tools_definitions: Dict[str, Any], metadata: Optional[Dict] = None):
        # Créer data comme dict avec les définitions d'outils
        data = {
            "tools_definitions": tools_definitions,
            "type": "tools_ready"
        }
        return cls(data=data, metadata=metadata)
    
    @property
    def tools_definitions(self) -> Dict[str, Any]:
        return self.data.get("tools_definitions", {})