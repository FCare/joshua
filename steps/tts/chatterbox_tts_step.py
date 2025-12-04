import time
import threading
import requests
import base64
from typing import Optional, Dict, Any

from pipeline_framework import PipelineStep
from messages.base_message import Message, InputMessage, OutputMessage, ErrorMessage, MessageType


class ChatterboxTTSStep(PipelineStep):
    
    def __init__(self, name: str = "ChatterboxTTS", config: Optional[Dict] = None):
        super().__init__(name, config)
        
        self.host = config.get("host", "https://caronboulme.fr/chatterbox/speech") if config else "https://caronboulme.fr/chatterbox/speech"
        self.voice = config.get("voice", "Fip4") if config else "Fip4"
        self.language_id = config.get("language_id", "fr") if config else "fr"
        
        self.speed = config.get("speed", 1.0) if config else 1.0
        self.exaggeration = config.get("exaggeration", 0.5) if config else 0.5
        self.cfg_weight = config.get("cfg_weight", 1.0) if config else 1.0
        self.temperature = config.get("temperature", 0.05) if config else 0.05
        self.quality_mode = config.get("quality_mode", "quality") if config else "quality"
        self.stream_chunk_size = config.get("stream_chunk_size", [100]) if config else [100]
        self.response_format = config.get("response_format", "pcm") if config else "pcm"
        
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "audio/wav"
        }
        
        self.interrupted = False
        self._lock = threading.Lock()
        self._current_response = None
    
    def init(self) -> bool:
        return True
    
    def process_message(self, message) -> Optional[OutputMessage]:
        try:
            if message.type == MessageType.INPUT:
                text = str(message.data)
                self._synthesize_text(text)
                return None
            
        except Exception as e:
            return ErrorMessage(error=str(e), step_name=self.name)
    
    def _synthesize_text(self, text: str):
        # Métriques de performance
        start_time = time.time()
        first_chunk_time = None
        total_audio_bytes = 0
        chunk_count = 0
        
        with self._lock:
            self.interrupted = False
        
        self._is_first_chunk = True
        
        payload = {
            "input": text,
            "response_format": self.response_format,
            "speed": self.speed,
            "stream": True,
            "stream_format": "audio",
            "exaggeration": self.exaggeration,
            "cfg_weight": self.cfg_weight,
            "temperature": self.temperature,
            "quality_mode": self.quality_mode,
            "stream_chunk_size": self.stream_chunk_size,
            "voice": self.voice
        }
        
        try:
            request_time = time.time()
            
            with requests.post(
                self.host,
                json=payload,
                headers=self.headers,
                timeout=60,
                stream=True,
                verify=False
            ) as response:
                response_time = time.time()
                
                with self._lock:
                    self._current_response = response
                
                if response.status_code == 200:
                    for chunk in response.iter_content(chunk_size=None):
                        with self._lock:
                            if self.interrupted:
                                break
                        
                        if chunk:
                            chunk_count += 1
                            chunk_time = time.time()
                            
                            # Time to First Token (TTFT)
                            if first_chunk_time is None:
                                first_chunk_time = chunk_time
                                ttft_ms = (first_chunk_time - request_time) * 1000
                                print(f"🚀 TTFT: {ttft_ms:.1f}ms")
                            
                            total_audio_bytes += len(chunk)
                            self._process_audio_chunk(chunk)
                    
                    # Calcul des métriques finales
                    end_time = time.time()
                    total_generation_time = end_time - start_time
                    
                    # Durée audio estimée (PCM 24kHz, 16-bit = 48000 bytes/sec)
                    audio_duration_seconds = total_audio_bytes / 48000
                    
                    # Real Time Factor (RTF)
                    rtf = total_generation_time / audio_duration_seconds if audio_duration_seconds > 0 else 0
                    
                    print(f"📊 TTS Metrics: {total_audio_bytes} bytes ({audio_duration_seconds:.2f}s) - RTF: {rtf:.2f}x")
                            
        except Exception as e:
            pass
        finally:
            with self._lock:
                self._current_response = None
    
    def _process_audio_chunk(self, chunk):
        if chunk:
            if hasattr(self, '_is_first_chunk') and self._is_first_chunk:
                self._is_first_chunk = False
                if len(chunk) > 44:
                    chunk = chunk[44:]
                else:
                    return
            
            audio_message = OutputMessage(
                result=chunk,
                metadata={
                    "type": "audio_chunk",
                    "format": "pcm",
                    "timestamp": time.time()
                }
            )
            
            if self.output_queue:
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.create_task(self.output_queue.put(audio_message))
                    else:
                        loop.run_until_complete(self.output_queue.put(audio_message))
                except:
                    pass
    
    def cleanup(self):
        with self._lock:
            self.interrupted = True
            if self._current_response:
                try:
                    self._current_response.close()
                except:
                    pass