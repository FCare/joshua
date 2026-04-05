from dataclasses import dataclass
from typing import Dict, Any, Optional, List
from .base_message import BaseMessage


@dataclass(frozen=True)
class UserConnectionMessage(BaseMessage):
    """Message de connexion d'un utilisateur via WebSocket"""
    username: str


@dataclass(frozen=True)
class AudioInputMessage(BaseMessage):
    """Message audio reçu via WebSocket"""
    audio_data: bytes
    format: str = "pcm16"
    sample_rate: int = 24000


@dataclass(frozen=True)
class TextInputMessage(BaseMessage):
    """Message texte reçu via WebSocket (avec support images)"""
    text: str
    images: Optional[List[str]] = None