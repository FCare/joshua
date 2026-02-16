import time
import threading
import logging
import os
import json
import struct
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum
from urllib.parse import quote_plus

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


class KyutaiTTS:
    def __init__(self, host: str, port: int = 443, api_key: str = None, format: str = "PcmMessagePack", voice: str = "male_1", cfg_alpha: float = 1.5, **params):
        self.host = host
        self.port = port
        self.api_key = api_key
        self.format = format
        self.voice = voice
        self.cfg_alpha = cfg_alpha
        self.name = f"KyutaiTTS({self.host})"

        self.ws = None
        self._connected = False
        self._stream_active = False
        
        self.output_queue = None
        self.current_client_id = None
        self.audio_chunks_sent = 0

        logger.info(f"{self.name}: Initialized")

    def set_output_queue(self, queue):
        self.output_queue = queue

    def connect(self):
        if self._connected:
            return
        
        ws_url = self._build_websocket_url()
        logger.info(f"{self.name}: Connecting to {ws_url}")
        
        headers = ["kyutai-api-key: public_token"]
        
        if self.api_key:
            headers.append(f"X-API-Key: {self.api_key}")
            logger.info(f"{self.name}: Using API key for authentication")
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
        
        # Build parameters like unmute - filter None values first
        params = {}
        if self.format is not None:
            params["format"] = self.format
        if self.voice is not None:
            params["voice"] = self.voice
        if self.cfg_alpha is not None:
            params["cfg_alpha"] = self.cfg_alpha
        
        # Build query string with URL escaping
        if params:
            query_parts = [f"{key}={quote_plus(str(value))}" for key, value in params.items()]
            query_string = "&".join(query_parts)
            url = f"{protocol}://{self.host}:{self.port}{base_path}?{query_string}"
        else:
            url = f"{protocol}://{self.host}:{self.port}{base_path}"
            
        logger.info(f"{self.name}: Using URL with format={self.format}: {url}")
        return url

    def on_open(self, ws):
        self._connected = True
        logger.info(f"{self.name}: WebSocket connected")

    def on_message(self, ws, message):
        try:
            # Vérifier si c'est des données OGG (audio direct)
            if isinstance(message, bytes) and message.startswith(b'OggS'):
                logger.debug(f"{self.name}: Received OGG audio data ({len(message)} bytes)")
                self._enqueue_audio_chunk(message)
                return
                
            # Essayer de décoder comme msgpack (messages de contrôle)
            try:
                logger.debug(f"receive {message}")
                message_dict = msgpack.unpackb(message)
                logger.debug(f"decoded {message_dict}")
                
                message_type = message_dict.get('type', 'unknown')

                if message_type == 'Ready':
                    self._stream_active = True
                    logger.info(f"{self.name}: TTS ready")
                    return
                
                elif message_type == 'Audio':
                    pcm_data = message_dict.get('pcm', [])
                    if pcm_data and self.output_queue:
                        audio_bytes = struct.pack(f'{len(pcm_data)}f', *pcm_data)
                        self._enqueue_audio_chunk(audio_bytes)
                        
                elif message_type == 'Error':
                    error_msg = message_dict.get('message', 'Unknown TTS error')
                    logger.error(f"{self.name}: TTS Error: {error_msg}")
                    
            except msgpack.exceptions.ExtraData:
                # Si c'est des données binaires non-msgpack, les traiter comme audio
                logger.debug(f"{self.name}: Raw binary data ({len(message)} bytes)")
                self._enqueue_audio_chunk(message)
            except Exception as decode_error:
                # Si ce n'est pas du msgpack valide, traiter comme audio brut
                logger.debug(f"{self.name}: Could not decode as msgpack, treating as raw audio: {decode_error}")
                if isinstance(message, bytes):
                    self._enqueue_audio_chunk(message)
                else:
                    logger.warning(f"{self.name}: Unknown message type: {type(message)}")
                
        except Exception as e:
            logger.error(f"{self.name}: Error processing TTS message: {e}")

    def _enqueue_audio_chunk(self, audio_bytes: bytes):
        if self.output_queue:
            # Détecter le format audio
            audio_format = "ogg_vorbis" if audio_bytes.startswith(b'OggS') else "pcm_float32"
            
            message = OutputMessage(
                data=audio_bytes,
                metadata={
                    "original_client_id": self.current_client_id,
                    "type": "audio_chunk",
                    "format": audio_format,
                    "sample_rate": SAMPLE_RATE,
                    "chunk_index": self.audio_chunks_sent,
                    "timestamp": time.time()
                }
            )
            self.output_queue.enqueue(message)
            self.audio_chunks_sent += 1
            logger.info(f"{self.name}: Audio chunk sent ({len(audio_bytes)} bytes, format: {audio_format})")

    def on_error(self, ws, error):
        logger.error(f"{self.name}: WebSocket error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        self._connected = False
        self._stream_active = False
        logger.info(f"{self.name}: WebSocket disconnected")

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
            logger.info(f"{self.name}: Sent text: '{text[:50]}...'")
            
        except Exception as e:
            logger.error(f"{self.name}: Error sending text to TTS: {e}")

    def _send_eos(self):
        if not self._connected or not self.ws:
            return
        try:
            message = {"type": "Eos"}
            packed_message = msgpack.packb(message, use_bin_type=True)
            self.ws.send(packed_message, opcode=websocket.ABNF.OPCODE_BINARY)
            logger.info(f"{self.name}: Sent EOS")
            
        except Exception as e:
            logger.error(f"{self.name}: Error sending EOS to TTS: {e}")

    def process_text(self, text: str, client_id: str = None):
        """Traite le texte avec EOS (méthode originale pour compatibilité)"""
        if not self._connected or not self._stream_active:
            logger.info("TTS not active")
            return
            
        if client_id:
            self.current_client_id = client_id
            
        try:
            self._send_text(text)
            #self._send_eos()
            
        except Exception as e:
            logger.error(f"{self.name}: Error processing text: {e}")
    
    def process_text_only(self, text: str, client_id: str = None):
        """Traite le texte SANS envoyer EOS (pour streaming)"""
        if not self._connected:
            logger.error("TTS not connected")
            return
            
        if client_id:
            self.current_client_id = client_id
            
        try:
            self._send_text(text)
            logger.info(f"{self.name}: Sent text without EOS: '{text[:50]}...'")
            
        except Exception as e:
            logger.error(f"{self.name}: Error processing text only: {e}")

    def disconnect(self):
        logger.info(f"{self.name}: Disconnecting...")
        
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
            logger.info(f"{self.name}: Disconnected")

    def reset(self):
        try:
            self.audio_chunks_sent = 0
            logger.info(f"{self.name}: Reset completed")
            
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
        self.format = config.get("format", "pcm") if config else "pcm"
        self.voice = config.get("voice", "male_1") if config else "male_1"
        self.cfg_alpha = config.get("cfg_alpha", 1.5) if config else 1.5
        
        self.kyutai_tts = None
        self.current_client_id = None
        
        print(f"KyutaiTTSStep '{self.name}' configured for {self.host}:{self.port}")
        print(f"TTS API key: {self.api_key[:10]}...{self.api_key[-10:] if self.api_key and len(self.api_key) > 20 else self.api_key}")
    
    def init(self) -> bool:
        try:
            print(f"Initializing Kyutai TTS on {self.host}:{self.port}")
            
            self.kyutai_tts = KyutaiTTS(
                host=self.host,
                port=self.port,
                api_key=self.api_key,
                format=self.format,
                voice=self.voice,
                cfg_alpha=self.cfg_alpha
            )
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
            if message.type != MessageType.DATA:
                logger.warning(f"TTS: Unsupported message type: {message.type}")
                return
            
            # Extraire les métadonnées pour détecter les finish signals
            metadata = message.metadata or {}
            if message.metadata:
                self.current_client_id = message.metadata.get("client_id") or message.metadata.get("original_client_id")
                logger.info(f"TTS: Client ID: {self.current_client_id}")
            
            # 🎯 DÉTECTER LE SIGNAL FINISH DU CHAT
            is_finish_signal = (
                metadata.get('chunk_type') == 'finish' or
                not message.data or
                (isinstance(message.data, str) and message.data.strip() == "")
            )
            
            if is_finish_signal:
                logger.info(f"TTS: Received finish signal from chat for client: {self.current_client_id}")
                self._handle_finish_signal()
                return
            
            if not self.kyutai_tts:
                logger.error("TTS: KyutaiTTS not initialized")
                return
            
            logger.info(f"TTS: KyutaiTTS connected: {self.kyutai_tts._connected}, active: {self.kyutai_tts._stream_active}")
            
            text_data = message.data
            if isinstance(text_data, str) and text_data.strip():
                # Envoyer seulement le texte, EOS sera envoyé au finish signal
                self.kyutai_tts.process_text_only(text_data.strip(), self.current_client_id)
                logger.info(f"TTS: Text processed: '{text_data[:50]}...' for client {self.current_client_id}")
            else:
                logger.warning(f"TTS: Invalid text data: {type(text_data)}, content: '{text_data}'")
            
        except Exception as e:
            logger.error(f"TTS: Error processing text: {e}")
            import traceback
            logger.error(f"TTS: Traceback: {traceback.format_exc()}")
    
    def _handle_finish_signal(self):
        """Traite le signal finish du chat en envoyant EOS"""
        try:
            if self.kyutai_tts and self.kyutai_tts._connected:
                self.kyutai_tts._send_eos()
                logger.info(f"TTS: Sent EOS for finish signal to client: {self.current_client_id}")
            else:
                logger.warning("TTS: Cannot send EOS - KyutaiTTS not connected")
        except Exception as e:
            logger.error(f"TTS: Error sending finish EOS: {e}")
    
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