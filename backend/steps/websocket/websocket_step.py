import asyncio
import json

import logging
import threading
import time
import base64
import aiohttp
import wave
import os
from typing import Optional, Dict, Any

from pipeline_framework import PipelineStep
from messages.websocket_message import UserConnectionMessage, AudioInputMessage, TextInputMessage, ImageUploadMessage
from messages.asr_message import TranscriptionMessage
from utils.chunk_queue import ChunkQueue

logger = logging.getLogger(__name__)

# Variable globale pour activer/désactiver l'enregistrement WAV
DEBUG_WAV = os.environ.get('DEBUG_WAV', 'False').lower() == 'true'


class WebSocketStep(PipelineStep):
    
    def __init__(self, name: str = "WebSocketServer", config: Optional[Dict] = None):
        super().__init__(name, config)
        self.host = config.get("host", "0.0.0.0") if config else "0.0.0.0"
        self.port = config.get("port", 8765) if config else 8765

        self.audio_format = config.get("audio_format", "pcm16") if config else "pcm16"
        self.sample_rate = config.get("sample_rate", 24000) if config else 24000
        
        # Mode de fonctionnement : "audio_to_text" ou "text_to_audio"
        self.mode = config.get("mode", "audio_to_text") if config else "audio_to_text"
        
        # Capacités du pipeline (modalités supportées)
        self.pipeline_capabilities = config.get("pipeline_capabilities", {}) if config else {}
        self.pipeline_name = config.get("pipeline_name", "Unknown Pipeline") if config else "Unknown Pipeline"
        
        # Chaque step ne crée que son input_queue avec handler ASYNC
        # output_queue sera définie par le pipeline builder (= input_queue du step suivant)
        self.input_queue = ChunkQueue(handler=self._handle_input_message_async)
        self.ws_send = None
        
        # Enregistrement WAV pour debug
        self.debug_wav_file = None
        self.debug_wav_lock = threading.Lock()
        if DEBUG_WAV:
            self._init_debug_wav()

    def _init_debug_wav(self):
        """Initialise le fichier WAV pour l'enregistrement debug"""
        try:
            # Créer le répertoire de debug s'il n'existe pas
            debug_dir = "debug_audio"
            os.makedirs(debug_dir, exist_ok=True)
            
            # Nom de fichier avec timestamp
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            wav_filename = os.path.join(debug_dir, f"websocket_audio_{timestamp}.wav")
            
            # Ouvrir le fichier WAV
            self.debug_wav_file = wave.open(wav_filename, 'wb')
            self.debug_wav_file.setnchannels(1)  # Mono
            self.debug_wav_file.setsampwidth(2)  # 16-bit
            self.debug_wav_file.setframerate(self.sample_rate)
            
            logger.info(f"🎧 DEBUG_WAV: Enregistrement audio dans {wav_filename}")
            
        except Exception as e:
            logger.error(f"❌ DEBUG_WAV: Erreur initialisation fichier WAV: {e}")
            self.debug_wav_file = None

    def init(self) -> bool:
        return True

    def set_ws_callback(self, callback, username: str = "anonymous", nexus=None):
        if (not self.ws_send):
            self.ws_send = callback
            connection_message = {
                "type": "connection_established",
                "pipeline": self.pipeline_name,
                "mode": self.mode,
                "capabilities": self.pipeline_capabilities,
                "server_info": {
                    "host": self.host,
                    "port": self.port,
                    "audio_format": self.audio_format,
                    "sample_rate": self.sample_rate
                },
                "timestamp": time.time()
            }
            callback(json.dumps(connection_message))

            if self.output_queue:
                user_connection_message = UserConnectionMessage(username=username)
                self.output_queue.enqueue(user_connection_message)
                logger.info(f"🔌 User connection notification sent: {username}")

            if nexus:
                try:
                    loop = asyncio.get_event_loop()
                    loop.create_task(nexus.publish("common/user_connected", {
                        "event": "user_connected",
                        "username": nexus.username,
                        "password": nexus.password,
                    }))
                except Exception as e:
                    logger.warning(f"MQTT publish user_connected échoué: {e}")

    async def _handle_input_message_async(self, message_data):
        """Handler ASYNC pour traiter les réponses du ChatStep - ChunkQueue gère la boucle !"""
        # Validation des types de messages autorisés - accepte tous les messages de sortie
        from messages.chat_message import ChatResponseMessage, ChatFinishMessage
        from messages.tts_message import AudioChunkOutputMessage, AudioFinishedMessage
        
        allowed_classes = (ChatResponseMessage, ChatFinishMessage, AudioChunkOutputMessage, AudioFinishedMessage, TranscriptionMessage)
        if not isinstance(message_data, allowed_classes):
            return
            
        try:
            logger.info(f"WebSocket received message from ChatStep: type={type(message_data).__name__}")
            
            # Filtrer les messages d'outils qui ne doivent PAS aller au frontend
            from messages.tool_message import ToolCallMessage, ToolResponseMessage
            if isinstance(message_data, (ToolCallMessage, ToolResponseMessage)):
                logger.info(f"🚫 Filtering tool message from frontend: {type(message_data).__name__}")
                return
            
            # Gestion spéciale pour les messages de contrôle (comme audio_finished)
            if isinstance(message_data, dict) and message_data.get('type') == 'audio_finished':
                # Message de fin d'audio - le routage se fait via le duplicateur
                logger.info(f"Sending audio_finished signal to all connected clients")
                finish_message = json.dumps({
                    "type": "audio_finished",
                    "total_chunks": message_data.get('total_chunks', 0),
                    "total_bytes": message_data.get('total_bytes', 0),
                    "duration_seconds": message_data.get('duration_seconds', 0),
                    "timestamp": time.time()
                })
                try:
                    websocket = self.ws
                    await websocket.send(finish_message)
                    logger.info(f"✅ Sent audio_finished")
                except Exception as e:
                    logger.error(f"❌ Failed to send audio_finished: {e}")
                return
            
            # Architecture dataclass pure - accès direct aux propriétés selon le type
            data = None
            if isinstance(message_data, ChatFinishMessage):
                logger.info("Websocket received ChatFinished")
                message_type = "chat_finished"
            elif isinstance(message_data, ChatResponseMessage):
                data = message_data.text
                message_type = "chat_response"
            elif isinstance(message_data, TranscriptionMessage):
                data = message_data.text
                message_type = "transcription"
            elif isinstance(message_data, AudioChunkOutputMessage):
                data = message_data.audio_data
                message_type = "audio_chunk"
            elif isinstance(message_data, AudioFinishedMessage):
                data = {"type": "audio_finished"}
                message_type = "audio_finished"
            else:
                logger.warning(f"Unknown message type: {type(message_data)}")
                return
                
            if message_type == 'audio_chunk' and isinstance(data, bytes):
                # Message audio - envoyer comme JSON avec base64
                logger.info(f"Sending audio chunk: {len(data)} bytes")
                await self.send_audio_to_client(data)
                
            elif message_type == 'audio_finished':
                # Signal de fin de streaming audio
                logger.info(f"Sending audio finished signal")
                finish_message = {
                    "type": "audio_finished",
                    "total_chunks": data.get('total_chunks', 0) if isinstance(data, dict) else 0,
                    "total_bytes": data.get('total_bytes', 0) if isinstance(data, dict) else 0,
                    "timestamp": time.time()
                }
                await self.send_to_client(json.dumps(finish_message))
                
            elif message_type == 'chat_finished':
                # 🎯 Signal de fin de chat complet (TTS a terminé)
                logger.info(f"Sending chat finished signal")
                chat_finish_message = {
                    "type": "chat_finished",
                    "timestamp": time.time()
                }
                await self.send_to_client(json.dumps(chat_finish_message))
                
            elif message_type == "transcription":
                # Message de transcription ASR - envoyer comme asr_transcription pour différencier
                logger.info(f"Sending ASR transcription: '{str(data)[:50]}{'...' if len(str(data)) > 50 else ''}'")
                transcription_message = {
                    "type": "transcription",
                    "text": data,
                    "is_final": message_data.is_final if hasattr(message_data, 'is_final') else True,
                    "timestamp": time.time(),
                }
                await self.send_to_client(json.dumps(transcription_message))
                
            elif  message_type == "chat_response":
                # Message texte normal - envoyer comme transcription (comportement original)
                logger.info(f"Sending chat response: '{str(data)[:50]}{'...' if len(str(data)) > 50 else ''}'")
                chat_response_message = {
                    "type": "chat_response",
                    "text": data,
                    "timestamp": time.time(),
                }
                await self.send_to_client(json.dumps(chat_response_message))
                
        except Exception as e:
            logger.error(f"Error in _handle_input_message_async: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
    
    def cleanup(self):
        # 🎧 DEBUG: Fermer le fichier WAV proprement
        if DEBUG_WAV and self.debug_wav_file:
            try:
                with self.debug_wav_lock:
                    self.debug_wav_file.close()
                    logger.info(f"🎧 DEBUG_WAV: Fichier WAV fermé")
            except Exception as e:
                logger.error(f"❌ DEBUG_WAV: Erreur fermeture fichier: {e}")
            finally:
                self.debug_wav_file = None
                
        # Arrête les ChunkQueues
        if hasattr(self, 'input_queue') and self.input_queue:
            self.input_queue.stop()
      
    def handle_websocket(self, message):   
        try:
            logger.info(f"Received message: type={type(message).__name__}, length={len(str(message)) if isinstance(message, str) else len(message) if isinstance(message, bytes) else 'unknown'}")
            
            if self.mode in ["audio_to_text", "audio_text_to_text_audio"] and isinstance(message, str):
                # Mode audio : traiter les messages JSON avec audio encodé
                try:
                    data = json.loads(message)
                    if data.get("type") == "audio" and "data" in data:
                        # Décoder l'audio base64
                        audio_b64 = data["data"]
                        audio_bytes = base64.b64decode(audio_b64)
                        metadata = data.get("metadata", {})
                        
                        logger.info(f"Processing JSON audio message: {len(audio_bytes)} bytes")
                        audio_message = AudioInputMessage(
                            audio_data=audio_bytes,
                            format=metadata.get("format", self.audio_format),
                            sample_rate=metadata.get("sample_rate", self.sample_rate)
                        )
                        self.output_queue.enqueue(audio_message)
                        logger.info(f"Audio message queued for processing")
                        return  # Message traité, passer au suivant
                    # Si ce n'est pas un message audio, laisser passer à la section texte
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON: {message[:200]}...")
                    return
                except Exception as e:
                    logger.error(f"Error processing audio JSON: {e}")
                    return
                    
            elif self.mode == "audio_to_text" and isinstance(message, bytes):
                logger.info(f"Processing raw audio message: {len(message)} bytes")
                audio_message = AudioInputMessage(
                    audio_data=message,
                    format=self.audio_format,
                    sample_rate=self.sample_rate
                )
                self.output_queue.enqueue(audio_message)
                logger.info(f"Audio message queued for processing")
                
            if (self.mode in ["text_to_audio", "text_to_text", "audio_text_to_text_audio"]) and isinstance(message, str):
                logger.info(f"Processing text message: '{message[:100]}{'...' if len(message) > 100 else ''}'")
                try:
                    data = json.loads(message)
                    message_type = data.get("type")
                    
                    # Ne pas traiter les messages audio en mode texte
                    if message_type == "audio":
                        return
                        
                    # Gérer les uploads d'images séparément
                    if message_type == "image_upload":
                        image_data = data.get("image_data", "")
                        filename = data.get("filename", "")
                        
                        logger.info(f"Processing image upload: {filename}")
                        image_message = ImageUploadMessage(
                            image_data=image_data,
                            filename=filename
                        )
                        self.output_queue.enqueue(image_message)
                        logger.info(f"Image upload queued: {filename}")
                        return
                        
                    # Traitement des messages texte sans images
                    text_data = data.get("text", "")
                    logger.info(f"Parsed JSON text message: '{text_data}'")
                except:
                    text_data = message
                    logger.info(f"Using raw text message: '{text_data}'")
                
                text_message = TextInputMessage(text=text_data)
                self.output_queue.enqueue(text_message)
                logger.info(f"Text message queued: '{text_data}'")
                    
        except Exception as e:
            logger.error(f"Error in websocket_handler: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
        finally:
            self.ws = None
    
    async def send_to_client(self, text: str):
        """Envoie un message texte à un client spécifique"""
        try:
            self.ws_send(text)    
            logger.info(f"✅ Sent: '{text[:30]}{'...' if len(text) > 30 else ''}'")
        except Exception as e:
            logger.warning(f"⚠️  Temporary error sending: {e}")

    def _write_to_debug_wav(self, audio_data: bytes):
        """Écrit les données audio dans le fichier WAV de debug"""
        if not DEBUG_WAV or not self.debug_wav_file:
            return
            
        try:
            with self.debug_wav_lock:
                self.debug_wav_file.writeframes(audio_data)
                self.debug_wav_file._file.flush()  # Force flush pour voir le contenu en temps réel
        except Exception as e:
            logger.error(f"❌ DEBUG_WAV: Erreur écriture chunk: {e}")

    async def send_audio_to_client(self, audio_data: bytes):
        """Envoie un chunk audio à un client spécifique au format JSON"""
        try:
            # 🎧 DEBUG: Enregistrer dans fichier WAV si activé
            if DEBUG_WAV:
                self._write_to_debug_wav(audio_data)
            
            # Encoder l'audio en base64 pour transmission JSON
            audio_b64 = base64.b64encode(audio_data).decode()
            
            message = {
                "type": "audio_chunk",
                "data": audio_b64,
                "timestamp": time.time()
            }
            
            self.ws_send(json.dumps(message))
            logger.info(f"✅ Sent audio chunk: {len(audio_data)} bytes")
        except Exception as e:
            logger.warning(f"⚠️  Temporary error sending audio: {e}")
            # Ne pas supprimer la connexion immédiatement - elle pourrait être temporairement occupée