from dataclasses import dataclass
from typing import Dict, Any, Optional, List
from messages.base_message import BaseMessage


@dataclass(frozen=True)
class UserConnectionMessage(BaseMessage):
    """Message de connexion d'un utilisateur via WebSocket"""
    
    @classmethod
    def create(cls, client_id: str, username: str, metadata: Optional[Dict] = None):
        data = {
            "type": "user_connected",
            "client_id": client_id,
            "username": username,
            "timestamp": metadata.get("timestamp") if metadata else None
        }
        return cls(data=data, metadata=metadata)
    
    @property
    def client_id(self) -> str:
        return self.data.get("client_id", "")
    
    @property
    def username(self) -> str:
        return self.data.get("username", "")


@dataclass(frozen=True)
class AudioInputMessage(BaseMessage):
    """Message audio reçu via WebSocket"""
    
    @classmethod
    def create(cls, audio_data: bytes, client_id: str, format: str = "pcm16",
                 sample_rate: int = 24000, metadata: Optional[Dict] = None):
        # Créer data avec les données audio
        data = audio_data  # Les données audio restent en bytes
        
        # Ajouter les métadonnées audio
        audio_metadata = {
            "client_id": client_id,
            "message_type": "audio",
            "format": format,
            "sample_rate": sample_rate,
            "channels": 1,
            **(metadata or {})
        }
        
        return cls(data=data, metadata=audio_metadata)
    
    @property
    def audio_data(self) -> bytes:
        return self.data
    
    @property
    def client_id(self) -> str:
        return self.metadata.get("client_id", "")
    
    @property
    def format(self) -> str:
        return self.metadata.get("format", "pcm16")
    
    @property
    def sample_rate(self) -> int:
        return self.metadata.get("sample_rate", 24000)


@dataclass(frozen=True)
class TextInputMessage(BaseMessage):
    """Message texte reçu via WebSocket (avec support images)"""
    
    @classmethod
    def create(cls, text: str, client_id: str, images: Optional[List[str]] = None,
                 metadata: Optional[Dict] = None):
        # Créer data comme dict avec texte et images
        data = {
            "text": text,
            "images": images or []
        }
        
        # Ajouter les métadonnées texte
        text_metadata = {
            "client_id": client_id,
            "message_type": "text",
            "has_images": len(images or []) > 0,
            "image_count": len(images or []),
            **(metadata or {})
        }
        
        return cls(data=data, metadata=text_metadata)
    
    @property
    def text(self) -> str:
        return self.data.get("text", "")
    
    @property
    def client_id(self) -> str:
        return self.metadata.get("client_id", "")
    
    @property
    def images(self) -> List[str]:
        return self.data.get("images", [])