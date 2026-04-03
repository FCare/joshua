from dataclasses import dataclass
from typing import Dict, Optional
from messages.base_message import BaseMessage


@dataclass(frozen=True)
class AudioChunkMessage(BaseMessage):
    """Message de chunk audio pour ASR"""
    
    def __init__(self, audio_data: bytes, client_id: str, sample_rate: int = 24000,
                 format: str = "pcm16", metadata: Optional[Dict] = None):
        data = {
            "audio_data": audio_data,
            "client_id": client_id,
            "sample_rate": sample_rate,
            "format": format
        }
        super().__init__(data=data, metadata=metadata)
    
    @property
    def audio_data(self) -> bytes:
        return self.data.get("audio_data", b"")
    
    @property
    def client_id(self) -> str:
        return self.data.get("client_id", "")
    
    @property
    def sample_rate(self) -> int:
        return self.data.get("sample_rate", 24000)
    
    @property
    def format(self) -> str:
        return self.data.get("format", "pcm16")


@dataclass(frozen=True)
class TranscriptionMessage(BaseMessage):
    """Message de transcription ASR"""
    
    def __init__(self, text: str, confidence: float = 1.0, is_final: bool = True,
                 metadata: Optional[Dict] = None):
        data = {
            "text": text,
            "confidence": confidence,
            "is_final": is_final
        }
        super().__init__(data=data, metadata=metadata)
    
    @property
    def text(self) -> str:
        return self.data.get("text", "")
    
    @property
    def confidence(self) -> float:
        return self.data.get("confidence", 1.0)
    
    @property
    def is_final(self) -> bool:
        return self.data.get("is_final", True)


@dataclass(frozen=True)
class SpeechEventMessage(BaseMessage):
    """Message d'événement de parole ASR"""
    
    def __init__(self, event_type: str, timestamp: float, metadata: Optional[Dict] = None):
        data = {
            "event_type": event_type,
            "timestamp": timestamp
        }
        super().__init__(data=data, metadata=metadata)
    
    @property
    def event_type(self) -> str:
        return self.data.get("event_type", "")
    
    @property
    def timestamp(self) -> float:
        return self.data.get("timestamp", 0.0)
