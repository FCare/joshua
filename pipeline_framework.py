import asyncio
import threading
import queue
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from messages.base_message import Message, InputMessage, OutputMessage, ErrorMessage
from utils.chunk_queue import ChunkQueue


class PipelineStep(ABC):
    
    def __init__(self, name: str, config: Optional[Dict] = None, handler=None):
        self.name = name
        self.config = config or {}
        self.is_running = False
        
        # PipelineStep crée l'input_queue avec le handler fourni par le step enfant
        # output_queue sera définie par le pipeline builder (= input_queue du step suivant)
        self.input_queue = ChunkQueue(handler=handler) if handler else None
        self.output_queue = None
    
    @abstractmethod
    def init(self) -> bool:
        pass
    
    @abstractmethod
    def cleanup(self):
        pass
    
    async def start(self):
        if not self.init():
            return False
        self.is_running = True
        return True
    
    async def stop(self):
        self.is_running = False
        self.cleanup()


class Pipeline:
    
    def __init__(self, name: str):
        self.name = name
        self.steps = {}
        self.connections = []
        self.is_running = False
    
    def add_step(self, step: PipelineStep):
        self.steps[step.name] = step
    
    def connect_steps(self, from_step_name: str, to_step_name: str):
        if from_step_name not in self.steps or to_step_name not in self.steps:
            raise ValueError(f"Step not found")
        
        from_step = self.steps[from_step_name]
        to_step = self.steps[to_step_name]
        
        from_step.output_queue = to_step.input_queue
        self.connections.append((from_step_name, to_step_name))
    
    async def start(self):
        for step in self.steps.values():
            success = await step.start()
            if not success:
                return False
        
        self.is_running = True
        return True
    
    async def stop(self):
        self.is_running = False
        for step in self.steps.values():
            await step.stop()
    
    def get_step(self, step_name: str) -> Optional[PipelineStep]:
        return self.steps.get(step_name)
    
    async def send_message(self, step_name: str, message: Message):
        if step_name in self.steps:
            self.steps[step_name].input_queue.enqueue(message)