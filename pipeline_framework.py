import asyncio
import threading
import queue
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from messages.base_message import Message, InputMessage, OutputMessage, ErrorMessage


class PipelineStep(ABC):
    
    def __init__(self, name: str, config: Optional[Dict] = None):
        self.name = name
        self.config = config or {}
        self.input_queue = asyncio.Queue()
        self.output_queue = asyncio.Queue()
        self.is_running = False
        self._task = None
    
    @abstractmethod
    def init(self) -> bool:
        pass
    
    @abstractmethod
    def process_message(self, message) -> Optional[OutputMessage]:
        pass
    
    @abstractmethod
    def cleanup(self):
        pass
    
    def set_output_queue(self, queue_instance):
        self.output_queue = queue_instance
    
    async def start(self):
        if not self.init():
            return False
        
        self.is_running = True
        self._task = asyncio.create_task(self._message_loop())
        return True
    
    async def stop(self):
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.cleanup()
    
    async def _message_loop(self):
        while self.is_running:
            try:
                message = await asyncio.wait_for(self.input_queue.get(), timeout=1.0)
                result = self.process_message(message)
                if result and self.output_queue:
                    await self.output_queue.put(result)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                error_msg = ErrorMessage(error=str(e), step_name=self.name)
                if self.output_queue:
                    await self.output_queue.put(error_msg)


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
        
        from_step.set_output_queue(to_step.input_queue)
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
            await self.steps[step_name].input_queue.put(message)