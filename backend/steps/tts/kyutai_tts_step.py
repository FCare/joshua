import time
import threading
import logging
import os
import json
import struct
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum

from pipeline_framework import PipelineStep
from messages.base_message import Message, InputMessage, OutputMessage, ErrorMessage, MessageType

try:
    import websocket
    import msgpack
    UNMUTE_DEPENDENCIES_AVAILABLE = True
except ImportError as e:
    print(f"""
    Missing dependencies for KyutaiTTS: {e}
    Install with: pip install websocket-client msgpack
    """)
    UNMUTE_DEPENDENCIES_AVAILABLE = False
    websocket = None
    msgpack = None

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000
FRAME_TIME_SEC = 0.08


class TTSEventType(Enum):
    TEXT = "text"
    AUDIO = "audio"
    ERROR = "error"
    READY = "ready"


@dataclass
class TTSEvent:
    type: TTSEventType
    data: Any = None
    timestamp: Optional[float] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()


class KyutaiTTSStep:
    def __init__(self, host: str, port: int = 443, api_key: str = None, **params):
        self.host = host
        self.port = port
        self.api_key = api_key
        self.name = f"KyutaiTTS({self.host})"

        self.ws = None
        self._connected = False
        self._stream_active = False
        
        self.output_queue = None
        self.current_client_id = None
        self.audio_chunks_sent = 0

        logger.debug(f"{self.name}: Initialized")

    def set_output_queue(self, queue):
        self.output_queue = queue

    def connect(self):
        if self._connected:
            return
        
        ws_url = self._build_websocket_url()
        logger.debug(f"{self.name}: Connecting to {ws_url}")
        
        headers = ["kyutai-api-key: public_token"]
        
        if self.api_key:
            headers.append(f"X-API-Key: {self.api_key}")
            logger.debug(f"{self.name}: Using API key for authentication")
        else:
            logger.warning(f"{self.name}: No API key provided")
        
        self.ws = websocket.WebSocketApp(
            url=ws_url,
            header=headers,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )
        
        def start_ws():
            import ssl
            self.ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})
        
        threading.Thread(target=start_ws, daemon=True).start()
        
        timeout = 10.0
        start_time = time.time()
        while not self._connected and (time.time() - start_time) < timeout:
            time.sleep(0.1)
            
        if not self._connected:
            raise RuntimeError(f"Failed to connect to {ws_url} within {timeout}s")

    def _build_websocket_url(self):
        protocol = "wss" if self.port == 443 else "ws"
        base_path = "/api/tts_streaming"
        return f"{protocol}://{self.host}:{self.port}{base_path}"

    def on_open(self, ws):
        self._connected = True
        logger.debug(f"{self.name}: WebSocket connected")

    def on_message(self, ws, message):
        try:
            message_dict = msgpack.unpackb(message)
            
            message_type = message_dict.get('type', 'unknown')

            if message_type == 'Ready':
                self._stream_active = True
                logger.debug(f"{self.name}: TTS ready")
                return
            
            elif message_type == 'Audio':
                pcm_data = message_dict.get('pcm', [])
                if pcm_data and self.output_queue:
                    audio_bytes = struct.pack(f'{len(pcm_data)}f', *pcm_data)
                    self._enqueue_audio_chunk(audio_bytes)
                    
            elif message_type == 'Error':
                error_msg = message_dict.get('message', 'Unknown TTS error')
                logger.error(f"{self.name}: TTS Error: {error_msg}")
                
        except Exception as e:
            logger.error(f"{self.name}: Error processing TTS message: {e}")

    def _enqueue_audio_chunk(self, audio_bytes: bytes):
        if self.output_queue:
            message = OutputMessage(
                result=audio_bytes,
                metadata={
                    "original_client_id": self.current_client_id,
                    "type": "audio_chunk",
                    "format": "pcm_float32",
                    "sample_rate": SAMPLE_RATE,
                    "chunk_index": self.audio_chunks_sent,
                    "timestamp": time.time()
                }
            )
            self.output_queue.enqueue(message)
            self.audio_chunks_sent += 1
            logger.debug(f"{self.name}: Audio chunk sent ({len(audio_bytes)} bytes)")

    def on_error(self, ws, error):
        logger.error(f"{self.name}: WebSocket error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        self._connected = False
        self._stream_active = False
        logger.debug(f"{self.name}: WebSocket disconnected")

    def _send_text(self, text: str):
        if not self._connected or not self.ws:
            return
        try:
            message = {
                "type": "Text",
                "text": text
            }
            
            packed_message = msgpack.packb(message, use_bin_type=True)
            self.ws.send(packed_message, opcode=websocket.ABNF.OPCODE_BINARY)
            logger.debug(f"{self.name}: Sent text: '{text[:50]}...'")
            
        except Exception as e:
            logger.error(f"{self.name}: Error sending text to TTS: {e}")

    def _send_eos(self):
        if not self._connected or not self.ws:
            return
        try:
            message = {"type": "Eos"}
            packed_message = msgpack.packb(message, use_bin_type=True)
            self.ws.send(packed_message, opcode=websocket.ABNF.OPCODE_BINARY)
            logger.debug(f"{self.name}: Sent EOS")
            
        except Exception as e:
            logger.error(f"{self.name}: Error sending EOS to TTS: {e}")

    def process_text(self, text: str, client_id: str = None):
        if not self._connected or not self._stream_active:
            logger.debug("TTS not active")
            return
            
        if client_id:
            self.current_client_id = client_id
            
        try:
            self._send_text(text)
            self._send_eos()
            
        except Exception as e:
            logger.error(f"{self.name}: Error processing text: {e}")

    def disconnect(self):
        logger.debug(f"{self.name}: Disconnecting...")
        
        try:
            if self.ws:
                try:
                    self.ws.close()
                except Exception as e:
                    logger.error(f"{self.name}: Error while closing websocket - {e}")
            
        except Exception as e:
            logger.error(f"{self.name}: Disconnect error: {e}")
        finally:
            self._connected = False
            self._stream_active = False
            self.audio_chunks_sent = 0
            logger.debug(f"{self.name}: Disconnected")

    def reset(self):
        try:
            self.audio_chunks_sent = 0
            logger.debug(f"{self.name}: Reset completed")
            
        except Exception as e:
            logger.error(f"{self.name}: Reset error: {e}")


class KyutaiTTSStep(PipelineStep):
    def __init__(self, name: str = "KyutaiTTS", config: Optional[Dict] = None):
        super().__init__(name, config, handler=self._handle_input_message)
        
        self.host = config.get("host", "localhost") if config else "localhost"
        self.port = config.get("port", 8089) if config else 8089
        
        env_api_key = os.getenv("TTS_API_KEY")
        config_api_key = config.get("api_key") if config else None
        
        if env_api_key:
            self.api_key = env_api_key
        elif config_api_key and config_api_key != "your_tts_api_key_here":
            self.api_key = config_api_key
        else:
            self.api_key = "public_token"
            
        self.sample_rate = config.get("sample_rate", 24000) if config else 24000
        
        self.kyutai_tts = None
        self.current_client_id = None
        
        print(f"KyutaiTTSStep '{self.name}' configured for {self.host}:{self.port}")
        print(f"TTS API key: {self.api_key[:10]}...{self.api_key[-10:] if self.api_key and len(self.api_key) > 20 else self.api_key}")
    
    def init(self) -> bool:
        try:
            print(f"Initializing Kyutai TTS on {self.host}:{self.port}")
            
            self.kyutai_tts = KyutaiTTS(host=self.host, port=self.port, api_key=self.api_key)
            self.kyutai_tts.set_output_queue(self.output_queue)
            self.kyutai_tts.connect()
            
            print(f"Kyutai TTS initialized successfully")
            return True
            
        except Exception as e:
            print(f"Error initializing Kyutai TTS: {e}")
            logger.error(f"Kyutai TTS init error: {e}")
            return False
    
    def _handle_input_message(self, message: Message):
        try:
            logger.info(f"TTS: _handle_input_message called with type={message.type}")
            if message.type != MessageType.INPUT:
                logger.warning(f"TTS: Unsupported message type: {message.type}")
                return
            
            text_data = message.data
            if not text_data:
                logger.error("TTS: No text data in message")
                return
            
            if message.metadata:
                self.current_client_id = message.metadata.get("client_id") or message.metadata.get("original_client_id")
                logger.info(f"TTS: Client ID: {self.current_client_id}")
            
            if not self.kyutai_tts:
                logger.error("TTS: KyutaiTTS not initialized")
                return
            
            logger.info(f"TTS: KyutaiTTS connected: {self.kyutai_tts._connected}, active: {self.kyutai_tts._stream_active}")
            
            if isinstance(text_data, str):
                self.kyutai_tts.process_text(text_data.strip(), self.current_client_id)
                logger.info(f"TTS: Text processed: '{text_data[:50]}...' for client {self.current_client_id}")
            else:
                logger.error(f"TTS: Expected string text data, received: {type(text_data)}")
            
        except Exception as e:
            logger.error(f"TTS: Error processing text: {e}")
            import traceback
            logger.error(f"TTS: Traceback: {traceback.format_exc()}")
    
    def reset_tts(self):
        try:
            if self.kyutai_tts:
                self.kyutai_tts.reset()
            
            self.current_client_id = None
            logger.info("TTS reset")
            
        except Exception as e:
            logger.error(f"Error resetting TTS: {e}")
    
    def get_tts_stats(self):
        stats = {
            "tts_active": self.kyutai_tts is not None,
            "current_client": self.current_client_id,
            "host": f"{self.host}:{self.port}"
        }
        
        if self.kyutai_tts:
            stats.update({
                "connected": self.kyutai_tts._connected,
                "stream_active": self.kyutai_tts._stream_active,
                "audio_chunks_sent": self.kyutai_tts.audio_chunks_sent
            })
        
        return stats
    
    def cleanup(self):
        print(f"Cleaning up Kyutai TTS {self.name}")
        
        if self.kyutai_tts:
            try:
                self.kyutai_tts.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting KyutaiTTS: {e}")
            finally:
                self.kyutai_tts = None
        
        self.current_client_id = None
        print(f"Kyutai TTS {self.name} cleaned up")