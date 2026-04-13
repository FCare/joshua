from dataclasses import dataclass
from typing import Dict, Any, Optional, List
from .base_message import BaseMessage


# ============================================
# INPUT MODALITIES
# ============================================

@dataclass(frozen=True)
class ModalityInputAudio(BaseMessage):
    """Message d'entrée audio (WebSocket → ASR)"""
    audio_data: bytes
    client_id: str
    format: str
    sample_rate: int
    
    def __init__(self, audio_data: bytes, client_id: str, format: str = "pcm16", 
                 sample_rate: int = 24000, metadata: Optional[Dict] = None):
        super().__init__(data=audio_data, metadata={
            "client_id": client_id,
            "format": format,
            "sample_rate": sample_rate,
            "channels": 1,
            **(metadata or {})
        })
        object.__setattr__(self, 'audio_data', audio_data)
        object.__setattr__(self, 'client_id', client_id)
        object.__setattr__(self, 'format', format)
        object.__setattr__(self, 'sample_rate', sample_rate)


@dataclass(frozen=True)
class ModalityInputText(BaseMessage):
    """Message d'entrée texte (WebSocket → Chat)"""
    text: str
    client_id: str
    
    def __init__(self, text: str, client_id: str, metadata: Optional[Dict] = None):
        data = {
            "text": text
        }
        super().__init__(data=data, metadata={
            "client_id": client_id,
            **(metadata or {})
        })
        object.__setattr__(self, 'text', text)
        object.__setattr__(self, 'client_id', client_id)


@dataclass(frozen=True)
class ModalityInputConnection(BaseMessage):
    """Message de connexion utilisateur (WebSocket → Tools)"""
    client_id: str
    username: str
    
    def __init__(self, client_id: str, username: str, metadata: Optional[Dict] = None):
        data = {
            "type": "user_connected",
            "client_id": client_id,
            "username": username
        }
        super().__init__(data=data, metadata=metadata)
        object.__setattr__(self, 'client_id', client_id)
        object.__setattr__(self, 'username', username)


# ============================================
# OUTPUT MODALITIES
# ============================================

@dataclass(frozen=True)
class ModalityOutputText(BaseMessage):
    """Message de sortie texte (Chat → TTS/WebSocket)"""
    text: str
    is_partial: bool
    
    def __init__(self, text: str, is_partial: bool = False, metadata: Optional[Dict] = None):
        data = {
            "text": text,
            "is_partial": is_partial,
            "type": "text_response"
        }
        super().__init__(data=data, metadata=metadata)
        object.__setattr__(self, 'text', text)
        object.__setattr__(self, 'is_partial', is_partial)


@dataclass(frozen=True)
class ModalityOutputAudio(BaseMessage):
    """Message de sortie audio (TTS → WebSocket)"""
    audio_data: bytes
    chunk_index: int
    total_chunks: int
    
    def __init__(self, audio_data: bytes, chunk_index: int = 0, total_chunks: int = 1, 
                 metadata: Optional[Dict] = None):
        super().__init__(data=audio_data, metadata={
            "type": "audio_chunk",
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            **(metadata or {})
        })
        object.__setattr__(self, 'audio_data', audio_data)
        object.__setattr__(self, 'chunk_index', chunk_index)
        object.__setattr__(self, 'total_chunks', total_chunks)


@dataclass(frozen=True)
class ModalityOutputTranscription(BaseMessage):
    """Message de sortie transcription (ASR → Chat)"""
    text: str
    is_final: bool
    
    def __init__(self, text: str, is_final: bool = True,
                 metadata: Optional[Dict] = None):
        data = {
            "text": text,
            "is_final": is_final,
            "type": "transcription"
        }
        super().__init__(data=data, metadata=metadata)
        object.__setattr__(self, 'text', text)
        object.__setattr__(self, 'is_final', is_final)
    
# ============================================
# TOOL MODALITIES
# ============================================

@dataclass(frozen=True)
class ModalityToolCall(BaseMessage):
    """Message d'appel d'outil (Chat → Tool)"""
    tool_name: str
    tool_call_id: str
    parameters: Dict[str, Any]
    
    def __init__(self, tool_name: str, tool_call_id: str, parameters: Dict[str, Any], 
                 metadata: Optional[Dict] = None):
        data = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "parameters": parameters
        }
        super().__init__(data=data, metadata=metadata)
        object.__setattr__(self, 'tool_name', tool_name)
        object.__setattr__(self, 'tool_call_id', tool_call_id)
        object.__setattr__(self, 'parameters', parameters)

@dataclass(frozen=True)
class ModalityToolResponse(BaseMessage):
    """Message de réponse d'outil (Tool → Chat)"""
    tool_call_id: str
    tool_name: str
    result: Any
    error: Optional[str]
    
    def __init__(self, tool_call_id: str, tool_name: str, result: Any = None, 
                 error: Optional[str] = None, metadata: Optional[Dict] = None):
        data = {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "result": result,
            "error": error
        }
        super().__init__(data=data, metadata=metadata)
        object.__setattr__(self, 'tool_call_id', tool_call_id)
        object.__setattr__(self, 'tool_name', tool_name)
        object.__setattr__(self, 'result', result)
        object.__setattr__(self, 'error', error)

@dataclass(frozen=True)
class ModalityToolRegistration(BaseMessage):
    """Message d'enregistrement d'outil (Tool → Registry)"""
    tool_definition: Dict[str, Any]
    source_step: str
    
    def __init__(self, tool_definition: Dict[str, Any], source_step: str, 
                 metadata: Optional[Dict] = None):
        data = {
            "tool_definition": tool_definition,
            "source_step": source_step
        }
        super().__init__(data=data, metadata=metadata)
        object.__setattr__(self, 'tool_definition', tool_definition)
        object.__setattr__(self, 'source_step', source_step)

# ============================================
# SYSTEM MODALITIES
# ============================================

@dataclass(frozen=True)
class ModalitySystemError(BaseMessage):
    """Message d'erreur système"""
    error: str
    step_name: str
    
    def __init__(self, error: str, step_name: str, metadata: Optional[Dict] = None):
        data = {
            "error": error,
            "step_name": step_name,
            "type": "error"
        }
        super().__init__(data=data, metadata=metadata)
        object.__setattr__(self, 'error', error)
        object.__setattr__(self, 'step_name', step_name)