from dataclasses import dataclass
from typing import Dict, Any, Optional
from .base_message import BaseMessage


@dataclass(frozen=True)
class SentenceMessage(BaseMessage):
    """Message de phrase normalisée émis par SentenceNormalizerStep"""
    text: str
    is_last: bool = False