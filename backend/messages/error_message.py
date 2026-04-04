from dataclasses import dataclass
from typing import Dict, Any, Optional
from .base_message import BaseMessage


@dataclass(frozen=True)
class ErrorMessage(BaseMessage):
    """Message d'erreur avec informations de debug"""
    error: str
    step_name: str