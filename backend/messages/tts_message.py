from dataclasses import dataclass
from typing import Dict, Any, Optional
from messages.base_message import BaseMessage


@dataclass(frozen=True)
class AudioChunkOutputMessage(BaseMessage):
    """Message de chunk audio émis par le TTS"""
    
    def __init__(self, audio_data: bytes, chunk_index: int = 0, total_chunks: int = 1,
                 metadata: Optional[Dict] = None):
        # Créer data avec les données audio
        data = audio_data  # Les données audio restent en bytes
        
        # Ajouter les métadonnées TTS
        tts_metadata = {
            "type": "audio_chunk",
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            **(metadata or {})
        }
        
        super().__init__(data=data, metadata=tts_metadata)
    
    @property
    def audio_data(self) -> bytes:
        return self.data
    
    @property
    def chunk_index(self) -> int:
        return self.metadata.get("chunk_index", 0)
    
    @property
    def total_chunks(self) -> int:
        return self.metadata.get("total_chunks", 1)


@dataclass(frozen=True)
class AudioFinishedMessage(BaseMessage):
    """Message de fin d'audio émis par le TTS"""
    
    def __init__(self, total_chunks: int, total_bytes: int, duration_seconds: float = 0.0,
                 metadata: Optional[Dict] = None):
        # Créer data avec les statistiques audio
        data = {
            "type": "audio_finished",
            "total_chunks": total_chunks,
            "total_bytes": total_bytes,
            "duration_seconds": duration_seconds
        }
        
        super().__init__(data=data, metadata=metadata)
    
    @property
    def total_chunks(self) -> int:
        return self.data.get("total_chunks", 0)
    
    @property
    def total_bytes(self) -> int:
        return self.data.get("total_bytes", 0)
    
    @property
    def duration_seconds(self) -> float:
        return self.data.get("duration_seconds", 0.0)