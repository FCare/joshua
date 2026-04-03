from dataclasses import dataclass
from typing import Dict, Any, Optional
from messages.base_message import BaseMessage


@dataclass(frozen=True)
class InputMessage(BaseMessage):
    """Message d'entrée générique (utilisé par DuplicatorStep)"""
    
    @classmethod
    def create(cls, data: Any, metadata: Optional[Dict] = None):
        return cls(data=data, metadata=metadata)


@dataclass(frozen=True)
class OutputMessage(BaseMessage):
    """Message de sortie générique (utilisé par DuplicatorStep)"""
    
    @classmethod
    def create(cls, data: Any, metadata: Optional[Dict] = None):
        return cls(data=data, metadata=metadata)