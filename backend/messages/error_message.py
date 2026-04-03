from dataclasses import dataclass
from typing import Dict, Any, Optional
from messages.base_message import BaseMessage


@dataclass(frozen=True)
class ErrorMessage(BaseMessage):
    """Message d'erreur avec informations de debug"""
    
    def __init__(self, error: str, step_name: str, metadata: Optional[Dict] = None):
        # Créer data comme dict avec les informations d'erreur
        data = {
            "error": error,
            "step_name": step_name,
            "type": "error"
        }
        super().__init__(data=data, metadata=metadata)
    
    @property
    def error(self) -> str:
        return self.data.get("error", "")
    
    @property
    def step_name(self) -> str:
        return self.data.get("step_name", "")