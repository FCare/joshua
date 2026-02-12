from dataclasses import dataclass
from typing import Any, Dict, Optional
from .base_message import Message, MessageType


@dataclass
class AudioChunkMessage(Message):
    def __init__(self, audio_data: bytes, client_id: str, sample_rate: int = 24000, 
                 format: str = "pcm16", metadata: Optional[Dict] = None):
        data = {
            "audio_data": audio_data,
            "client_id": client_id,
            "sample_rate": sample_rate,
            "format": format
        }
        super().__init__(MessageType.INPUT, data, metadata)


@dataclass
class TranscriptionMessage(Message):
    def __init__(self, text: str, confidence: float = 1.0, is_final: bool = True,
                 metadata: Optional[Dict] = None):
        data = {
            "text": text,
            "confidence": confidence,
            "is_final": is_final
        }
        super().__init__(MessageType.OUTPUT, data, metadata)


@dataclass
class SpeechEventMessage(Message):
    def __init__(self, event_type: str, timestamp: float, metadata: Optional[Dict] = None):
        data = {
            "event_type": event_type,
            "timestamp": timestamp
        }
        super().__init__(MessageType.OUTPUT, data, metadata)