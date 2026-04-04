from dataclasses import dataclass
from typing import Dict, Optional
from .base_message import BaseMessage


@dataclass(frozen=True)
class AudioChunkMessage(BaseMessage):
    """Message de chunk audio pour ASR"""
    audio_data: bytes
    client_id: str
    sample_rate: int = 24000
    format: str = "pcm16"


@dataclass(frozen=True)
class TranscriptionMessage(BaseMessage):
    """Message de transcription ASR"""
    text: str
    is_final: bool = True


@dataclass(frozen=True)
class SpeechEventMessage(BaseMessage):
    """Message d'événement de parole ASR"""
    event_type: str
    timestamp: float
