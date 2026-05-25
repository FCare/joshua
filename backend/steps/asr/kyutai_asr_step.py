import time
import urllib.parse
from messages.base_message import BaseMessage
import os
import sys
import logging
import math
import struct
from abc import abstractmethod
from dataclasses import dataclass
from collections import deque
from enum import Enum
from typing import Optional, Dict, Any

from pipeline_framework import PipelineStep

try:
    import websocket
    import msgpack
    MOSHI_DEPENDENCIES_AVAILABLE = True
except ImportError as e:
    print(f"""
    Missing dependencies for MoshiASR: {e}
    Install with: pip install websocket-client msgpack
    """)
    MOSHI_DEPENDENCIES_AVAILABLE = False
    websocket = None
    msgpack = None

logger = logging.getLogger(__name__)

# Constantes ASR
SAMPLE_RATE = 24000
SAMPLES_PER_FRAME = 1920
FRAME_TIME_SEC = SAMPLES_PER_FRAME / SAMPLE_RATE
PAUSE_THRESHOLD = 0.9
VAD_THRESHOLD = 0.8
FLUSH_LENGTH = 12


class ExponentialMovingAverage:
    """Exponential Moving Average for pause prediction smoothing."""
    def __init__(self, attack_time: float, release_time: float, initial_value: float = 0.0):
        self.attack_time = attack_time
        self.release_time = release_time
        self.value = initial_value

    def update(self, dt: float, new_value: float) -> float:
        if new_value > self.value:
            alpha = 1 - math.exp(-dt / self.attack_time * math.log(2))
        else:
            alpha = 1 - math.exp(-dt / self.release_time * math.log(2))
        
        self.value = float((1 - alpha) * self.value + alpha * new_value)
        return self.value

@dataclass
class ASREvent:
    """Événement ASR standardisé pour input/output"""
    timestamp: Optional[float] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()


@dataclass
class TextEvent(ASREvent):
    """Événement output texte"""
    text: str = ""


@dataclass
class StartEvent(ASREvent):
    """Événement voix détectée"""
    pass  # Classe vide - juste un signal


@dataclass
class EndEvent(ASREvent):
    """Événement fin/pause détectée"""
    pass  # Classe vide - juste un signal


class MoshiASR:
    """
    ASR implementation using Moshi STT server via synchronous WebSocket.
    """

    def __init__(self, host: str, vk_api_key: str = None, **params):
        self.host = host
        self.vk_api_key = vk_api_key  # API key pour Voight-Kampff/Traefik
        self.name = f"MoshiASR({self.host or 'auto'})"

        self.ws = None
        self._connected = False
        self._stream_active = False
        
        self.is_speaking = False
        self.last_word_time = 0.0
        
        self.pause_prediction = ExponentialMovingAverage(
            attack_time=0.01, release_time=0.01, initial_value=1.0
        )
        self.steps_to_wait = 10

        self.pause_threshold = PAUSE_THRESHOLD
        self.vad_threshold = VAD_THRESHOLD

        self.flushing_mode = False
        self.silence_packets_count = FLUSH_LENGTH
        self.flushing_limit = 0
        
        self.packets_sent = 0
        self.packets_received = 0

        self.output_queue = None
        self.text_buffer = []
        self.current_session_id = None

        # Queue pour les paquets audio en attente de connexion
        self.pending_audio_queue = deque()
        self.connection_in_progress = False

        logger.info(f"{self.name}: Initialized with VK API key: {vk_api_key[:10]}..." if vk_api_key else f"{self.name}: Initialized without VK API key")

    def set_output_queue(self, queue):
        self.output_queue = queue

    def connect(self):
        """Establish WebSocket connection with Moshi STT server."""
        if self.connection_in_progress:
            return
            
        if self._connected:
            return
        
        ws_url = self._build_websocket_url()
        logger.info(f"{self.name}: Connecting to {ws_url}")
        
        # Préparer les headers pour la connexion WebSocket
        headers = ["kyutai-api-key: public_token"]
        
        # Ajouter l'API key Voight-Kampff pour l'authentification Traefik
        if self.vk_api_key:
            headers.append(f"X-API-Key: {self.vk_api_key}")
            logger.info(f"{self.name}: Using VK API key for Traefik authentication")
        else:
            logger.warning(f"{self.name}: No VK API key provided, connection may fail")
        
        self.ws = websocket.WebSocketApp(
            url=ws_url,
            header=headers,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )
        
        def start_ws():
            self.connection_in_progress = True
            import ssl
            self.ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})
        
        import threading
        threading.Thread(target=start_ws, daemon=True).start()
        
        timeout = 10.0
        start_time = time.time()
        while not self._connected and (time.time() - start_time) < timeout:
            time.sleep(0.1)
            
        if not self._connected:
            raise RuntimeError(f"Failed to connect to {ws_url} within {timeout}s")
        self.connection_in_progress = False

    def _build_websocket_url(self):
        """Build WebSocket URL for STT connection."""
        protocol = "wss"
        base_path = "/api/asr-streaming"  # Chemin officiel du service Unmute STT
        return f"{protocol}://{self.host}{base_path}"

    def on_open(self, ws):
        """WebSocket connection opened callback."""
        self._connected = True
        logger.info(f"{self.name}: WebSocket connected successfully")
        
        # Vider la queue des paquets en attente
        if self.pending_audio_queue:
            logger.info(f"{self.name}: Processing {len(self.pending_audio_queue)} queued audio packets")
            while self.pending_audio_queue:
                audio_data, timestamp = self.pending_audio_queue.popleft()
                try:
                    self._process_audio_chunk_internal(audio_data, timestamp)
                except Exception as e:
                    logger.error(f"{self.name}: Error processing queued audio: {e}")
                    break
            logger.info(f"{self.name}: Finished processing queued audio packets")

    def on_message(self, ws, message):
        """WebSocket message received callback."""
        try:
            message_dict = msgpack.unpackb(message)
            logger.info(f"ASR msg is {message_dict}")
            if message_dict.get("type") == "Ready":
                self._stream_active = True
                return
            
            message_type = message_dict.get('type', 'unknown')

            if message_type == 'Word':
                word_text = message_dict.get('text', '')
                start_time = message_dict.get('start_time', 0)
                if self.flushing_mode:
                    self.flushing_mode = False
                    logger.info("Cancel flushing mode")
                    if not self.is_speaking:
                        self._enqueue_event(StartEvent())
                
                self.pause_prediction.value = 0.0
                
                if self.output_queue:
                    self._enqueue_event(TextEvent(text=word_text))
                
                self.last_word_time = start_time
                self.is_speaking = True
                
                logger.info(f"{self.name}: Word: '{word_text}' at {start_time:.2f}s")
                
            elif message_type == 'EndWord':
                stop_time = message_dict.get('stop_time', 0)
                logger.info(f"{self.name}: Word ended at {stop_time:.2f}s")
                
            elif message_type == 'Marker':
                marker_id = message_dict.get('id', 0)
                logger.info(f"{self.name}: Marker received: {marker_id}")
                
            elif message_type == 'Step':
                step_idx = message_dict.get('step_idx', 0)
                prs = message_dict.get('prs', [])

                self.packets_received += 1

                if len(prs) >= 3:
                    if self.steps_to_wait > 0:
                        self.steps_to_wait -= 1
                        return
                    
                    self.pause_prediction.update(FRAME_TIME_SEC, prs[2])
        
                    if self.pause_prediction.value > self.pause_threshold and self.is_speaking:
                        if not self.flushing_mode:
                            logger.info("Starting flushing mode")
                            self._enter_flushing_mode()
                    
                    elif self.pause_prediction.value < self.vad_threshold:
                        self.pause_prediction.value = 0.0
                        if self.flushing_mode:
                            logger.info("Cancel flushing mode")
                            self.flushing_mode = False
                        if not self.is_speaking:
                            self._enqueue_event(StartEvent())
                            
            if self.flushing_mode:
                if self.flushing_limit == self.packets_received:
                    logger.info("Flushing Done")
                    self.is_speaking = False
                    self.flushing_mode = False
                    if self.output_queue:
                        logger.info("Flushing Done : Trigger LLM")
                        self._enqueue_event(EndEvent())
                    return
                
        except Exception as e:
            logger.error(f"{self.name}: Error processing message: {e}")

    def _enqueue_event(self, event):
        """Architecture dataclass pure : utilise isinstance() au lieu de .type"""
        logger.info(f"{self.name}: _enqueue_event called with {type(event).__name__}")
        if self.output_queue:
            if isinstance(event, StartEvent):
                from messages.message_uuid import MessageUUID
                from messages.asr_message import SpeechStartMessage
                self.current_session_id = MessageUUID()
                logger.info(f"{self.name}: Voice start detected - new session {self.current_session_id}")
                self.output_queue.enqueue(SpeechStartMessage(id=self.current_session_id))

            elif isinstance(event, TextEvent):
                self.text_buffer.append(event.text)
                logger.info(f"{self.name}: Added word '{event.text}' to buffer, buffer now: {self.text_buffer}")
                from messages.asr_message import TranscriptionMessage
                message = TranscriptionMessage(
                    text=event.text,
                    is_final=False,
                    id=self.current_session_id,
                )
                self.output_queue.enqueue(message)
                logger.info(f"{self.name}: Sent transcript_chunk: '{event.text}'")

            elif isinstance(event, EndEvent):
                full_text = ' '.join(self.text_buffer).strip()
                logger.info(f"{self.name}: Creating transcript_done from buffer: '{full_text}'")
                from messages.asr_message import TranscriptionMessage
                message = TranscriptionMessage(
                    text=full_text,
                    is_final=True,
                    id=self.current_session_id,
                )
                self.output_queue.enqueue(message)
                logger.info(f"{self.name}: Sent transcript_done: '{full_text}'")
                self.text_buffer = []

            else:
                logger.info(f"{self.name}: Ignoring event type {type(event).__name__}")
        else:
            logger.error(f"{self.name}: No output_queue to send event!")

    def on_error(self, ws, error):
        """WebSocket error callback."""
        logger.error(f"{self.name}: WebSocket error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        """WebSocket close callback."""
        self.connection_in_progress = False
        self._connected = False
        self._stream_active = False
        logger.info(f"{self.name}: WebSocket disconnected")

    def _send_audio(self, audio_data: list, timestamp: float):
        """Send audio packet directly to STT server."""
        if not self._connected or not self.ws:
            return
        try:
            message = {
                "type": "Audio",
                "timestamp": timestamp,
                "pcm": audio_data if isinstance(audio_data, list) else list(audio_data)
            }
            
            packed_message = msgpack.packb(message, use_bin_type=True, use_single_float=True)
            self.ws.send(packed_message, opcode=websocket.ABNF.OPCODE_BINARY)
            self.packets_sent += 1
            
        except Exception as e:
            logger.error(f"{self.name}: Error sending packet to STT: {e}")

    def _enter_flushing_mode(self):
        """Enter flushing mode and send silence packets."""
        self.flushing_mode = True
        
        silence_samples = [0.0] * SAMPLES_PER_FRAME
        self.flushing_limit = self.packets_sent + self.silence_packets_count
        for i in range(self.silence_packets_count):
            try:
                timestamp = time.time()
                self._send_audio(silence_samples, timestamp)
            except Exception as e:
                logger.error(f"{self.name}: Error sending silence packet {i}: {e}")
                break

    def _process_audio_chunk(self, audio_chunk: str):
        """
        Main entry point - handle queuing when not connected.
        """
        timestamp = time.time()
        
        if not self._connected:
            logger.info(f"{self.name}: Not connected, queuing audio packet")
            self.pending_audio_queue.append((audio_chunk, timestamp))
            
            # Initier la connexion si pas déjà en cours
            if not self.connection_in_progress:
                try:
                    self.connect()
                except Exception as e:
                    logger.error(f"{self.name}: Failed to initiate connection: {e}")
            return
            
        self._process_audio_chunk_internal(audio_chunk, timestamp)

    def _process_audio_chunk_internal(self, audio_chunk: str, timestamp: float = None):
        """
        Internal processing - direct synchronous sending.
        
        Receives audio from client → Split into 80ms → Send directly to STT
        """
        if not self._stream_active:
            logger.info(f"{self.name}: Stream not active yet")
            
        try:
            # Decode binary audio data using struct.unpack
            num_samples = len(audio_chunk) // 2  # 2 bytes per int16
            audio_ints = struct.unpack(f'<{num_samples}h', audio_chunk)
            
            # Convert int16 to float32 and normalize
            audio_float32 = [float(sample) / 32767.0 for sample in audio_ints]
            
            # Use provided timestamp or current time
            base_timestamp = timestamp if timestamp is not None else time.time()
            packet_index = 0
            
            for i in range(0, len(audio_float32), SAMPLES_PER_FRAME):
                chunk = audio_float32[i:i + SAMPLES_PER_FRAME]
                
                if len(chunk) < SAMPLES_PER_FRAME:
                    logger.warning(f"Audio packet should be multiple of {SAMPLES_PER_FRAME}")
                
                packet_timestamp = base_timestamp + (packet_index * FRAME_TIME_SEC)
                self._send_audio(chunk, packet_timestamp)
                packet_index += 1
            
        except Exception as e:
            logger.error(f"{self.name}: Error processing audio chunk: {e}")

    def disconnect(self):
        """Clean disconnection."""
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
            self.connection_in_progress = False
            self._connected = False
            self._stream_active = False
            self.flushing_mode = False
            self.packets_sent = 0
            self.packets_received = 0
            logger.info(f"{self.name}: Disconnected successfully")

    def reset(self):
        """Reset the user input buffer and transcription state."""
        try:
            self.is_speaking = False
            self.pause_prediction.value = 1.0
            self.flushing_limit = 0
            self.flushing_mode = False
            self.current_session_id = None

            logger.info(f"{self.name}: Reset completed")
            # Vider la queue en cas de reset
            self.pending_audio_queue.clear()
            
        except Exception as e:
            logger.error(f"{self.name}: Reset error: {e}")


class KyutaiASRStep(PipelineStep):
    """
    Step ASR utilisant Moshi.
    """
    
    def __init__(self, name: str = "KyutaiASR", config: Optional[Dict] = None):
        super().__init__(name, config, handler=self._handle_input_message)
        
        self.host = config.get("host", "stt.kyutai.org") if config else "stt.kyutai.org"
        self.port = config.get("port", 443) if config else 443
        # Prioriser la variable d'environnement sur la config par défaut
        env_api_key = os.getenv("ASR_API_KEY")
        config_api_key = config.get("api_key") if config else None
        
        # Si env existe, l'utiliser; sinon fallback sur config sauf si c'est la valeur placeholder
        if env_api_key:
            self.api_key = env_api_key
        elif config_api_key and config_api_key != "your_asr_api_key_here":
            self.api_key = config_api_key
        else:
            self.api_key = "public_token"  # Fallback par défaut
            
        print(f"DEBUG: config.get('api_key') = {repr(config_api_key)}")
        print(f"DEBUG: os.getenv('ASR_API_KEY') = {repr(env_api_key)}")
        print(f"DEBUG: Final API key = {repr(self.api_key[:12] + '...' + self.api_key[-8:] if self.api_key and len(self.api_key) > 20 else self.api_key)}")
        
        self.sample_rate = config.get("sample_rate", 24000) if config else 24000
        self.samples_per_frame = config.get("samples_per_frame", 1920) if config else 1920
        
        self.pause_threshold = config.get("pause_threshold", 0.9) if config else 0.9
        self.vad_threshold = config.get("vad_threshold", 0.8) if config else 0.8
        
        self.moshi_asr = None
        self.text_buffer = []
        
        
        print(f"KyutaiASRStep '{self.name}' configuré pour {self.host}")
        print(f"ASR API key: {self.api_key[:10]}...{self.api_key[-10:] if self.api_key and len(self.api_key) > 20 else self.api_key}")
    
    def init(self) -> bool:
        """Initialise l'ASR Kyutai"""
        try:
            print(f"Initialisation Kyutai ASR sur {self.host}")
            
            # Crée l'instance MoshiASR avec l'API key pour Voight-Kampff
            self.moshi_asr = MoshiASR(host=self.host, vk_api_key=self.api_key)
            
            # Configure MoshiASR pour utiliser l'output_queue du step
            self.moshi_asr.set_output_queue(self.output_queue)
            
            # Connexion WebSocket
            self.moshi_asr.connect()
            
            print(f"Kyutai ASR initialisé avec succès")
            return True
            
        except Exception as e:
            print(f"Erreur initialisation Kyutai ASR: {e}")
            logger.error(f"Kyutai ASR init error: {e}")
            return False
    
    def _handle_input_message(self, message: BaseMessage):
        # Validation des types de messages autorisés - accepte tous les messages d'entrée audio
        from messages.websocket_message import AudioInputMessage
        
        allowed_classes = (AudioInputMessage)
        if not isinstance(message, allowed_classes):
            return
            
        try:
            logger.info(f"🎤 ASR: _handle_input_message called with AudioInputMessage")
            
            # Récupère les données audio directement depuis la propriété dataclass
            audio_data = message.audio_data
            if not audio_data:
                logger.info("🎤 ASR: Pas de données dans le message, ignoré")
                return
            
            # Vérifie que c'est bien des données audio (bytes) - ignore le reste silencieusement
            if not isinstance(audio_data, bytes):
                logger.info(f"🎤 ASR: Données non-audio reçues ({type(audio_data)}), ignorées (probablement du texte pour tools/LLM)")
                return
            
            # Vérifie que MoshiASR est initialisé
            if not self.moshi_asr:
                logger.error("🎤 ASR: MoshiASR non initialisé")
                return
            
            logger.info(f"🎤 ASR: MoshiASR connecté: {self.moshi_asr._connected}, actif: {self.moshi_asr._stream_active}")
            
            # Traite le chunk audio avec MoshiASR
            self.moshi_asr._process_audio_chunk(audio_data)
            logger.info(f"🎤 ASR: Chunk audio traité ({len(audio_data)} bytes)")
            
        except Exception as e:
            logger.error(f"🎤 ASR: Erreur traitement audio ASR: {e}")
            import traceback
            logger.error(f"🎤 ASR: Traceback: {traceback.format_exc()}")
    
    def reset_transcription(self):
        """Remet à zéro l'état de transcription"""
        try:
            if self.moshi_asr:
                self.moshi_asr.reset()
            
            self.text_buffer = []
            
            logger.info("Transcription reset")
            
        except Exception as e:
            logger.error(f"Erreur reset transcription: {e}")
    
    def get_asr_stats(self):
        """Retourne les statistiques ASR"""
        stats = {
            "asr_active": self.moshi_asr is not None,
            "buffer_length": len(self.text_buffer),
            "host": self.host
        }
        
        if self.moshi_asr:
            stats.update({
                "connected": self.moshi_asr._connected,
                "stream_active": self.moshi_asr._stream_active,
                "packets_sent": self.moshi_asr.packets_sent,
                "packets_received": self.moshi_asr.packets_received
            })
        
        return stats
    
    def cleanup(self):
        """Nettoie les ressources ASR"""
        print(f"Nettoyage Kyutai ASR {self.name}")
        
        
        # Nettoie MoshiASR
        if self.moshi_asr:
            try:
                self.moshi_asr.disconnect()
            except Exception as e:
                logger.error(f"Erreur déconnexion MoshiASR: {e}")
            finally:
                self.moshi_asr = None
        
        self.text_buffer = []
        
        print(f"Kyutai ASR {self.name} nettoyé")