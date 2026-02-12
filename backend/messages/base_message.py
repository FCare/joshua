from dataclasses import dataclass
from typing import Any, Dict, Optional
from enum import Enum


class MessageType(Enum):
    INPUT = "input"
    OUTPUT = "output"
    ERROR = "error"
    CONTROL = "control"


@dataclass
class Message:
    type: MessageType
    data: Any
    metadata: Optional[Dict] = None


@dataclass 
class InputMessage(Message):
    def __init__(self, data: Any, metadata: Optional[Dict] = None):
        super().__init__(MessageType.INPUT, data, metadata)


@dataclass
class OutputMessage(Message):
    def __init__(self, result: Any, metadata: Optional[Dict] = None):
        super().__init__(MessageType.OUTPUT, result, metadata)


@dataclass
class ErrorMessage(Message):
    def __init__(self, error: str, step_name: str, metadata: Optional[Dict] = None):
        data = {"error": error, "step_name": step_name}
        super().__init__(MessageType.ERROR, data, metadata)