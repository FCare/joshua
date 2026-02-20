from dataclasses import dataclass
from typing import Any, Dict, Optional
from enum import Enum


class MessageType(Enum):
    DATA = "data"
    ERROR = "error"
    CONTROL = "control"
    TOOL_CALL = "tool_call"
    TOOL_RESPONSE = "tool_response"
    TOOL_REGISTRATION = "tool_registration"


@dataclass
class Message:
    type: MessageType
    data: Any
    metadata: Optional[Dict] = None


@dataclass
class InputMessage(Message):
    def __init__(self, data: Any, metadata: Optional[Dict] = None):
        super().__init__(MessageType.DATA, data, metadata)


@dataclass
class OutputMessage(Message):
    def __init__(self, data: Any, metadata: Optional[Dict] = None):
        super().__init__(MessageType.DATA, data, metadata)


@dataclass
class ErrorMessage(Message):
    def __init__(self, error: str, step_name: str, metadata: Optional[Dict] = None):
        data = {"error": error, "step_name": step_name}
        super().__init__(MessageType.ERROR, data, metadata)


@dataclass
class ToolCallMessage(Message):
    """Message émis par le LLM pour appeler un outil"""
    tool_name: str
    tool_call_id: str
    parameters: Dict[str, Any]
    
    def __init__(self, tool_name: str, tool_call_id: str, parameters: Dict[str, Any], metadata: Optional[Dict] = None):
        self.tool_name = tool_name
        self.tool_call_id = tool_call_id
        self.parameters = parameters
        data = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "parameters": parameters
        }
        super().__init__(MessageType.TOOL_CALL, data, metadata)


@dataclass
class ToolResponseMessage(Message):
    """Message de réponse d'un outil vers le LLM"""
    tool_call_id: str
    tool_name: str
    result: Any
    error: Optional[str] = None
    
    def __init__(self, tool_call_id: str, tool_name: str, result: Any = None, error: Optional[str] = None, metadata: Optional[Dict] = None):
        self.tool_call_id = tool_call_id
        self.tool_name = tool_name
        self.result = result
        self.error = error
        data = {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "result": result,
            "error": error
        }
        super().__init__(MessageType.TOOL_RESPONSE, data, metadata)


@dataclass
class ToolRegistrationMessage(Message):
    """Message pour enregistrer un outil auprès du LLM"""
    tool_definition: Dict[str, Any]
    source_step: str
    
    def __init__(self, tool_definition: Dict[str, Any], source_step: str, metadata: Optional[Dict] = None):
        self.tool_definition = tool_definition
        self.source_step = source_step
        data = {
            "tool_definition": tool_definition,
            "source_step": source_step
        }
        super().__init__(MessageType.TOOL_REGISTRATION, data, metadata)