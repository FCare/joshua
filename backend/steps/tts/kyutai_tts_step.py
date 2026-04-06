import time
import threading
import logging
import os
import json
import struct
import wave
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum
from urllib.parse import quote_plus

from pipeline_framework import PipelineStep
from messages.base_message import BaseMessage

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

# Variable globale pour activer/désactiver l'enregistrement WAV
DEBUG_WAV = os.environ.get('DEBUG_WAV', 'False').lower() == 'true'

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
        self.audio_chunks_sent = 0

        # Enregistrement WAV pour debug
        self.debug_wav_file = None
        self.debug_wav_lock = threading.Lock()
        if DEBUG_WAV:
            self._init_debug_wav()

        logger.info(f"{self.name}: Initialized")

    def _init_debug_wav(self):
        """Initialise le fichier WAV pour l'enregistrement debug du TTS"""
        try:
            # Créer le répertoire de debug s'il n'existe pas
            debug_dir = "debug_audio"
            os.makedirs(debug_dir, exist_ok=True)
            
            # Nom de fichier avec timestamp
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            wav_filename = os.path.join(debug_dir, f"kyutai_tts_{timestamp}.wav")
            
            # Ouvrir le fichier WAV
            self.debug_wav_file = wave.open(wav_filename, 'wb')
            self.debug_wav_file.setnchannels(1)  # Mono
            self.debug_wav_file.setsampwidth(2)  # 16-bit
            self.debug_wav_file.setframerate(SAMPLE_RATE)
            
            logger.info(f"{self.name}: 🎧 DEBUG_WAV: Enregistrement TTS dans {wav_filename}")
            
        except Exception as e:
            logger.error(f"{self.name}: ❌ DEBUG_WAV: Erreur initialisation fichier WAV: {e}")
            self.debug_wav_file = None

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
        # Essayer de décoder comme msgpack (messages de contrôle)
        try:
            message_dict = msgpack.unpackb(message)
            
            message_type = message_dict.get('type', 'unknown')

            if message_type == 'Ready':
                self._stream_active = True
                logger.info(f"{self.name}: TTS ready")
                return
            
            elif message_type == 'Audio':
                pcm_data = message_dict.get('pcm', [])
                if pcm_data and self.output_queue:
                    # Convertir float32 -> int16 avec utilisation optimale de la plage
                    # Clamping entre -1.0 et 1.0, puis conversion asymétrique pour utiliser toute la plage
                    pcm_int16 = []
                    for sample in pcm_data:
                        # Clamp entre -1.0 et 1.0
                        clamped = max(-1.0, min(1.0, sample))
                        # Conversion asymétrique pour utiliser toute la plage int16 (-32768 à 32767)
                        if clamped >= 0:
                            int16_val = min(32767, int(clamped * 32767))
                        else:
                            int16_val = max(-32768, int(clamped * 32768))
                        pcm_int16.append(int16_val)
                    
                    # Packer en bytes (format 'h' = signed short int16)
                    audio_bytes = struct.pack(f'{len(pcm_int16)}h', *pcm_int16)
                    self._enqueue_audio_chunk(audio_bytes)
                else:
                    logger.warning(f"{self.name}: No PCM data or no output queue available")
                    
            elif message_type == 'Error':
                error_msg = message_dict.get('message', 'Unknown TTS error')
                logger.error(f"{self.name}: TTS Error: {error_msg}")
            else:
                logger.error(f"{self.name}: TTS Error: unknown message: {message_dict}")
                
                
        except msgpack.exceptions.ExtraData:
            # Si c'est des données binaires non-msgpack, les traiter comme audio
            logger.info(f"{self.name}: Raw binary data ({len(message)} bytes)")
            self._enqueue_audio_chunk(message)
        except Exception as decode_error:
            # Si ce n'est pas du msgpack valide, traiter comme audio brut
            logger.info(f"{self.name}: Could not decode as msgpack, treating as raw audio: {decode_error}")

    def _write_to_debug_wav(self, audio_bytes: bytes):
        """Écrit les données audio PCM dans le fichier WAV de debug TTS"""
        if not DEBUG_WAV or not self.debug_wav_file:
            return
            
        try:
            with self.debug_wav_lock:
                self.debug_wav_file.writeframes(audio_bytes)
                self.debug_wav_file._file.flush()  # Force flush pour voir le contenu en temps réel
        except Exception as e:
            logger.error(f"{self.name}: ❌ DEBUG_WAV: Erreur écriture chunk TTS: {e}")

    def _enqueue_audio_chunk(self, audio_bytes: bytes):
        # 🎧 DEBUG: Enregistrer dans fichier WAV si activé
        if DEBUG_WAV:
            self._write_to_debug_wav(audio_bytes)
            
        if self.output_queue:
            from messages.tts_message import AudioChunkOutputMessage
            message = AudioChunkOutputMessage(
                audio_data=audio_bytes
            )
            self.output_queue.enqueue(message)
            logger.info(f"{self.name}: Audio chunk sent ({len(audio_bytes)} bytes, format: pcm_int16)")

    def on_error(self, ws, error):
        logger.error(f"{self.name}: WebSocket error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        self._connected = False
        self._stream_active = False
        logger.info(f"{self.name}: WebSocket disconnected")

    def _send_text(self, text: str):
        if not self._connected or not self.ws:
            logger.info(f"TTS kyutai: No more ws for: '{text[:50]}...' {self._connected}")
            return
        try:
            message = {
                "type": "Text",
                "text": text
            }
            
            packed_message = msgpack.packb(message, use_bin_type=True)
            logger.info(f"{self.name}: Sent text: '{text}'")
            self.ws.send(packed_message, opcode=websocket.ABNF.OPCODE_BINARY)
            
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

    def process_text(self, text: str):
        """Traite le texte avec EOS (méthode originale pour compatibilité)"""
        if not self._connected or not self._stream_active:
            logger.info("TTS not active")
            return
            
        try:
            self._send_text(text)
            
        except Exception as e:
            logger.error(f"{self.name}: Error processing text: {e}")

    def disconnect(self):
        logger.info(f"{self.name}: Disconnecting...")
        
        # 🎧 DEBUG: Fermer le fichier WAV proprement
        if DEBUG_WAV and self.debug_wav_file:
            try:
                with self.debug_wav_lock:
                    self.debug_wav_file.close()
                    logger.info(f"{self.name}: 🎧 DEBUG_WAV: Fichier WAV TTS fermé")
            except Exception as e:
                logger.error(f"{self.name}: ❌ DEBUG_WAV: Erreur fermeture fichier TTS: {e}")
            finally:
                self.debug_wav_file = None
        
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
            logger.info(f"{self.name}: Disconnected")

    def reset(self):
        try:
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
    
    def _handle_input_message(self, message: BaseMessage):
        from messages.text_message import SentenceMessage
        
        allowed_classes = (SentenceMessage)
        if not isinstance(message, allowed_classes):
            return
            
        try:
            logger.info(f"TTS: _handle_input_message called with type={type(message).__name__}")
            
            # Note: Métadonnées supprimées de l'architecture dataclass pure
            # Les finish signals sont maintenant gérés directement par le type de message
            logger.info(f"Message is {message.text},{message.is_last}")
            
            if not self.kyutai_tts:
                logger.error("TTS: KyutaiTTS not initialized")
                return
            
            # 🔄 RECONNEXION AUTOMATIQUE si déconnecté
            if not self.kyutai_tts._connected:
                logger.info(f"TTS: WebSocket déconnecté, reconnexion automatique...")
                try:
                    self.kyutai_tts.connect()
                    logger.info(f"TTS: Reconnexion réussie")
                except Exception as e:
                    logger.error(f"TTS: Erreur reconnexion: {e}")
                    return
            
            logger.info(f"TTS: KyutaiTTS connected: {self.kyutai_tts._connected}, active: {self.kyutai_tts._stream_active}")
            
            text_data = message.text
            if isinstance(text_data, str) and text_data.strip():
                # Envoyer seulement le texte, EOS sera envoyé au finish signal
                self.kyutai_tts.process_text(text_data.strip())
                logger.info(f"TTS: Text processed: '{text_data[:50]}...'")

            # 🎯 DÉTECTER LE SIGNAL FINISH DU CHAT
            is_finish_signal = message.is_last
            
            if is_finish_signal:
                logger.info(f"TTS: Received finish signal from chat")
                self.kyutai_tts._send_eos()
                return
            
        except Exception as e:
            logger.error(f"TTS: Error processing text: {e}")
            import traceback
            logger.error(f"TTS: Traceback: {traceback.format_exc()}")

    def _send_audio_finish_signal(self):
        """
        Traite le signal finish du chat et l'envoie au websocket
        """
        try:
            # Envoyer directement le signal finish au websocket
            from messages.tts_message import AudioFinishedMessage
            finish_message = AudioFinishedMessage(
                total_chunks=0,
                total_bytes=0
            )
            
            if self.output_queue:
                self.output_queue.enqueue(finish_message)
                print(f"🎉 TTS envoyé signal CHAT TERMINÉ")
        
        except Exception as e:
            print(f"❌ Erreur envoi finish signal: {e}")
    
    def cleanup(self):
        print(f"Cleaning up Kyutai TTS {self.name}")
        
        if self.kyutai_tts:
            try:
                self.kyutai_tts.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting KyutaiTTS: {e}")
            finally:
                self.kyutai_tts = None
        
        print(f"Kyutai TTS {self.name} cleaned up")