import asyncio
import json
import queue
import threading
import time
import base64
from typing import Optional, Dict, Any

from pipeline_framework import PipelineStep
from messages.base_message import Message, InputMessage, OutputMessage, ErrorMessage, MessageType


class WebSocketStep(PipelineStep):
    
    def __init__(self, name: str = "WebSocketServer", config: Optional[Dict] = None):
        super().__init__(name, config)
        
        self.host = config.get("host", "localhost") if config else "localhost"
        self.port = config.get("port", 8765) if config else 8765
        
        self.websocket_server = None
        self.event_loop = None
        self.server_thread = None
        self.running = False
        
        self.connections = {}
        
        self.audio_format = config.get("audio_format", "pcm16") if config else "pcm16"
        self.sample_rate = config.get("sample_rate", 24000) if config else 24000
        
        # Mode de fonctionnement : "audio_to_text" ou "text_to_audio"
        self.mode = config.get("mode", "audio_to_text") if config else "audio_to_text"
    
    def init(self) -> bool:
        """Démarre le serveur WebSocket dans un thread séparé"""
        try:
            self.running = True
            self.server_thread = threading.Thread(target=self._run_server, daemon=True)
            self.server_thread.start()
            
            # Attendre que le serveur soit prêt (maximum 5 secondes)
            for i in range(50):
                if self.websocket_server is not None:
                    return True
                time.sleep(0.1)
            
            return False
            
        except Exception as e:
            return False
    
    def _run_server(self):
        """Lance le serveur WebSocket dans sa propre boucle d'événements"""
        self.event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.event_loop)
        
        try:
            self.event_loop.run_until_complete(self.start_server())
            self.event_loop.run_forever()
        except Exception as e:
            pass
        finally:
            self.event_loop.close()
    
    def process_message(self, message) -> Optional[OutputMessage]:
        try:
            if message.type == MessageType.OUTPUT:
                if self.mode == "audio_to_text":
                    # Mode ASR: diffuser du texte transcrit
                    text_data = message.data
                    asyncio.run_coroutine_threadsafe(
                        self.broadcast_text(str(text_data)),
                        self.event_loop
                    )
                elif self.mode == "text_to_audio":
                    # Mode TTS: diffuser des chunks audio
                    audio_data = message.data
                    asyncio.run_coroutine_threadsafe(
                        self.broadcast_audio(audio_data),
                        self.event_loop
                    )
                
                return None
                
        except Exception as e:
            return ErrorMessage(error=str(e), step_name=self.name)
    
    def cleanup(self):
        self.running = False
        if self.websocket_server:
            self.websocket_server.close()
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=2.0)
    
    async def websocket_handler(self, websocket):
        client_id = f"client_{id(websocket)}"
        self.connections[client_id] = websocket
        
        try:
            async for message in websocket:
                if self.mode == "audio_to_text" and isinstance(message, bytes):
                    # Mode ASR: recevoir de l'audio
                    audio_message = InputMessage(
                        data=message,
                        metadata={
                            "client_id": client_id,
                            "format": self.audio_format,
                            "sample_rate": self.sample_rate,
                            "timestamp": time.time()
                        }
                    )
                    await self.output_queue.put(audio_message)
                    
                elif self.mode == "text_to_audio" and isinstance(message, str):
                    # Mode TTS: recevoir du texte
                    try:
                        data = json.loads(message)
                        text_data = data.get("text", message)
                    except:
                        text_data = message
                    
                    text_message = InputMessage(
                        data=text_data,
                        metadata={
                            "client_id": client_id,
                            "timestamp": time.time()
                        }
                    )
                    await self.output_queue.put(text_message)
                    
        except Exception as e:
            pass
        finally:
            if client_id in self.connections:
                del self.connections[client_id]
    
    async def start_server(self):
        try:
            import websockets
            self.websocket_server = await websockets.serve(
                self.websocket_handler, self.host, self.port
            )
            print(f"WebSocket server started on {self.host}:{self.port}")
        except ImportError as e:
            raise
        except Exception as e:
            raise
    
    async def broadcast_text(self, text: str):
        if not self.connections:
            return
            
        message = {
            "type": "transcription",
            "text": text,
            "timestamp": time.time()
        }
        
        disconnected = []
        for client_id, websocket in self.connections.items():
            try:
                await websocket.send(json.dumps(message))
            except:
                disconnected.append(client_id)
        
        for client_id in disconnected:
            if client_id in self.connections:
                del self.connections[client_id]
    
    async def broadcast_audio(self, audio_data: bytes):
        if not self.connections:
            return
        
        audio_b64 = base64.b64encode(audio_data).decode()
        
        message = {
            "type": "audio_chunk",
            "data": audio_b64,
            "format": "pcm",
            "timestamp": time.time()
        }
        
        disconnected = []
        for client_id, websocket in self.connections.items():
            try:
                await websocket.send(json.dumps(message))
            except:
                disconnected.append(client_id)
        
        for client_id in disconnected:
            if client_id in self.connections:
                del self.connections[client_id]