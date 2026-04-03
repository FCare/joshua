from dataclasses import dataclass
from typing import Dict, Any, Optional
from messages.base_message import BaseMessage


@dataclass(frozen=True)
class ToolCallMessage(BaseMessage):
    """Message d'appel d'outil par le LLM"""
    
    def __init__(self, tool_name: str, tool_call_id: str, parameters: Dict[str, Any], metadata: Optional[Dict] = None):
        # Créer data comme dict avec les informations de l'outil
        data = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "parameters": parameters
        }
        super().__init__(data=data, metadata=metadata)
    
    @property
    def tool_name(self) -> str:
        return self.data.get("tool_name")
    
    @property
    def tool_call_id(self) -> str:
        return self.data.get("tool_call_id")
    
    @property
    def parameters(self) -> Dict[str, Any]:
        return self.data.get("parameters", {})


@dataclass(frozen=True)
class ToolResponseMessage(BaseMessage):
    """Message de réponse d'un outil"""
    
    def __init__(self, tool_call_id: str, tool_name: str, result: Any = None, error: Optional[str] = None, metadata: Optional[Dict] = None):
        # Créer data comme dict avec les informations de la réponse
        data = {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "result": result,
            "error": error
        }
        super().__init__(data=data, metadata=metadata)
    
    @property
    def tool_name(self) -> str:
        return self.data.get("tool_name")
    
    @property
    def tool_call_id(self) -> str:
        return self.data.get("tool_call_id")
    
    @property
    def result(self) -> Any:
        return self.data.get("result")
    
    @property
    def error(self) -> Optional[str]:
        return self.data.get("error")


@dataclass(frozen=True)
class ToolRegistrationMessage(BaseMessage):
    """Message d'enregistrement d'un outil"""
    
    @classmethod
    def create(cls, tool_definition: Dict[str, Any], source_step: str, metadata: Optional[Dict] = None):
        # Créer data comme dict avec les informations d'enregistrement
        data = {
            "tool_definition": tool_definition,
            "source_step": source_step
        }
        return cls(data=data, metadata=metadata)
    
    @property
    def tool_definition(self) -> Dict[str, Any]:
        return self.data.get("tool_definition", {})
    
    @property
    def source_step(self) -> str:
        return self.data.get("source_step")