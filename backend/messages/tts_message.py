from dataclasses import dataclass
from typing import Dict, Any, Optional
from .base_message import BaseMessage


@dataclass(frozen=True)
class AudioChunkOutputMessage(BaseMessage):
    """Message de chunk audio émis par le TTS"""
    audio_data: bytes
    chunk_index: int = 0
    total_chunks: int = 1


@dataclass(frozen=True)
class AudioFinishedMessage(BaseMessage):
    """Message de fin d'audio émis par le TTS"""
    total_chunks: int
    total_bytes: int
    duration_seconds: float = 0.0