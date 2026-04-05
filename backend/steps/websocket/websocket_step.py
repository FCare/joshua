import asyncio
import json
import logging
import threading
import time
import base64
import aiohttp
from typing import Optional, Dict, Any

from pipeline_framework import PipelineStep
from messages.websocket_message import UserConnectionMessage, AudioInputMessage, TextInputMessage
from utils.chunk_queue import ChunkQueue

logger = logging.getLogger(__name__)


class WebSocketStep(PipelineStep):
    
    def __init__(self, name: str = "WebSocketServer", config: Optional[Dict] = None):
        super().__init__(name, config)
        
        self.host = config.get("host", "0.0.0.0") if config else "0.0.0.0"
        self.port = config.get("port", 8765) if config else 8765
        
        self.websocket_server = None
        self.event_loop = None
        self.server_thread = None
        self.running = False
        
        self.ws = {}
        
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
    
    async def _handle_input_message_async(self, message_data):
        """Handler ASYNC pour traiter les réponses du ChatStep - ChunkQueue gère la boucle !"""
        # Validation des types de messages autorisés - accepte tous les messages de sortie
        from messages.chat_message import ChatResponseMessage, ChatFinishMessage
        from messages.tts_message import AudioChunkOutputMessage, AudioFinishedMessage
        
        allowed_classes = (ChatResponseMessage, ChatFinishMessage, AudioChunkOutputMessage, AudioFinishedMessage)
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
            metadata = {}  # Plus de metadata dans architecture pure
            if isinstance(message_data, ChatFinishMessage):
                logger.info("Websocket received ChatFinished")
                message_type = "chat_finished"
            elif isinstance(message_data, ChatResponseMessage):
                data = message_data.text
                message_type = "chat_response"
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
                await self.send_audio_to_client(data, metadata)
                
            elif message_type == 'audio_finished':
                # Signal de fin de streaming audio
                logger.info(f"Sending audio finished signal")
                finish_message = {
                    "type": "audio_finished",
                    "total_chunks": data.get('total_chunks', 0) if isinstance(data, dict) else 0,
                    "total_bytes": data.get('total_bytes', 0) if isinstance(data, dict) else 0,
                    "timestamp": time.time()
                }
                await self.send_to_specific_client(json.dumps(finish_message))
                
            elif message_type == 'chat_finished':
                # 🎯 Signal de fin de chat complet (TTS a terminé)
                logger.info(f"Sending chat finished signal")
                chat_finish_message = {
                    "type": "chat_finished",
                    "timestamp": time.time()
                }
                await self.send_to_specific_client(json.dumps(chat_finish_message))
                
            elif  message_type == "chat_response":
                # Message texte normal - envoyer comme chat_response
                logger.info(f"Sending chat response: '{str(data)[:50]}{'...' if len(str(data)) > 50 else ''}'")
                chat_response_message = {
                    "type": "transcription",
                    "text": data,
                    "timestamp": time.time(),
                }
                await self.send_to_specific_client(json.dumps(chat_response_message))
                
        except Exception as e:
            logger.error(f"Error in _handle_input_message_async: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
    
    def cleanup(self):
        self.running = False
        
        # Arrête les ChunkQueues
        if hasattr(self, 'input_queue') and self.input_queue:
            self.input_queue.stop()
        
        if self.websocket_server:
            self.websocket_server.close()
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=2.0)
    
    async def verify_authentication(self, websocket, path=None):
        """Vérifie l'authentification avec API key temporaire depuis Voight-Kampff"""
        try:
            # Extraire l'API key depuis les paramètres de query de l'URL WebSocket
            import urllib.parse as urlparse
            
            # Essayer d'obtenir l'URI complète avec query params depuis websocket.request
            uri = None
            if hasattr(websocket, 'request'):
                if hasattr(websocket.request, 'path'):
                    uri = websocket.request.path
                    logger.info(f"🔍 websocket.request.path: {uri}")
                elif hasattr(websocket.request, 'uri'):
                    uri = websocket.request.uri
                    logger.info(f"🔍 websocket.request.uri: {uri}")
            
            if not uri:
                uri = path if path else "/"
                logger.info(f"🔍 Fallback to path parameter: {uri}")
            
            parsed_url = urlparse.urlparse(uri)
            query_params = urlparse.parse_qs(parsed_url.query)
            api_key = query_params.get('api_key', [None])[0]
            
            if not api_key:
                logger.warning("❌ No API key provided in WebSocket connection")
                return False, None
                
            logger.info(f"🔑 API key provided for WebSocket authentication: {api_key[:8]}...")
            
            # Vérifier l'API key avec Voight-Kampff
            # Ajouter les headers pour identifier le service Joshua
            headers = {
                'X-API-Key': api_key,
                'X-Forwarded-Host': 'joshua.caronboulme.fr',
                'X-Forwarded-Uri': '/verify'
            }
            async with aiohttp.ClientSession() as session:
                async with session.get('http://voight-kampff:8080/verify', headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        username = data.get('user')
                        logger.info(f"✅ API key authentication successful for user: {username}")
                        return True, username
                    else:
                        logger.warning(f"❌ API key authentication failed with status {response.status}")
                        return False, None
        except Exception as e:
            logger.error(f"❌ API key authentication error: {e}")
            return False, None
    
    async def websocket_handler(self, websocket, path=None):
        # Vérifier l'authentification avant d'accepter la connexion
        is_authenticated, username = await self.verify_authentication(websocket, path)
        if not is_authenticated:
            logger.warning("WebSocket connection rejected: authentication failed")
            await websocket.close(code=4001, reason="Authentication required")
            return
        self.ws = websocket
        
        try:
            logger.info(f"WebSocket handler started, mode={self.mode}")
            
            # Envoyer le message de connexion établie avec les capacités du pipeline
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
            await websocket.send(json.dumps(connection_message))
            
            # 🚀 NOUVEAU : Notifier le pipeline de la nouvelle connexion
            if self.output_queue:
                user_connection_message = UserConnectionMessage(
                    username=username
                )
                self.output_queue.enqueue(user_connection_message)
                logger.info(f"🔌 User connection notification sent for {username}")
            
            async for message in websocket:
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
                            continue  # Message traité, passer au suivant
                        # Si ce n'est pas un message audio, laisser passer à la section texte
                    except json.JSONDecodeError:
                        logger.error(f"Invalid JSON: {message[:200]}...")
                        continue
                    except Exception as e:
                        logger.error(f"Error processing audio JSON: {e}")
                        continue
                        
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
                        # Ne pas traiter les messages audio en mode texte
                        if data.get("type") == "audio":
                            continue
                            
                        # Support d'images avec API simplifiée
                        text_data = data.get("text", "")
                        image = data.get("image")  # Une seule image
                        images = data.get("images", [])  # Ou plusieurs images
                        
                        # Normaliser vers une liste
                        if image:
                            images = [image]
                        
                        logger.info(f"Parsed JSON message - text: '{text_data}', images: {len(images)}")
                    except:
                        text_data = message
                        images = []
                        logger.info(f"Using raw text message: '{text_data}'")
                    
                    text_message = TextInputMessage(
                        text=text_data,
                        images=images
                    )
                    self.output_queue.enqueue(text_message)
                    logger.info(f"Message queued - text: '{text_data}', images: {len(images)}")
                    
        except Exception as e:
            logger.error(f"Error in websocket_handler: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
        finally:
            self.ws = None
    
    async def start_server(self):
        try:
            import websockets
            self.websocket_server = await websockets.serve(
                self.websocket_handler, self.host, self.port
            )
            print(f"WebSocket server started on {self.host}:{self.port}")
            if self.pipeline_capabilities:
                print(f"Pipeline: {self.pipeline_name}")
                modalities = self.pipeline_capabilities.get("modalities", {})
                if modalities:
                    print(f"  Input modalities: {modalities.get('input', [])}")
                    print(f"  Output modalities: {modalities.get('output', [])}")
                    print(f"  Processing capabilities: {modalities.get('processing', [])}")
        except ImportError as e:
            raise
        except Exception as e:
            raise
    
    async def send_to_specific_client(self, text: str):
        """Envoie un message texte à un client spécifique"""
        websocket = self.ws
        
        try:
            # Vérifier l'état de la connexion WebSocket
            if websocket.close_code is not None:
                logger.warning(f"WebSocket is closed, removing from connections")
                self.ws = None
                return
                
            await websocket.send(text)
            logger.info(f"✅ Sent: '{text[:30]}{'...' if len(text) > 30 else ''}'")
        except Exception as e:
            logger.warning(f"⚠️  Temporary error sending: {e}")
            # Ne pas supprimer la connexion immédiatement - elle pourrait être temporairement occupée

    async def send_audio_to_client(self, audio_data: bytes, metadata: dict):
        """Envoie un chunk audio à un client spécifique au format JSON"""
        websocket = self.ws
        
        try:
            # Vérifier l'état de la connexion WebSocket
            if websocket.close_code is not None:
                logger.warning(f"WebSocket is closed, removing from connections")
                self.ws = None
                return
            
            # Encoder l'audio en base64 pour transmission JSON
            audio_b64 = base64.b64encode(audio_data).decode()
            
            message = {
                "type": "audio_chunk",
                "data": audio_b64,
                "timestamp": time.time(),
                "metadata": metadata
            }
            
            await websocket.send(json.dumps(message))
            logger.info(f"✅ Sent audio chunk: {len(audio_data)} bytes")
        except Exception as e:
            logger.warning(f"⚠️  Temporary error sending audio: {e}")
            # Ne pas supprimer la connexion immédiatement - elle pourrait être temporairement occupée

    async def broadcast_text(self, text: str):
        """Broadcast simple text (pour compatibilité)"""
        await self.broadcast_text_with_metadata(text, {})
    
    async def broadcast_text_with_metadata(self, text: str, metadata: dict):
        """Broadcast text avec métadonnées"""
        logger.info(f"🔊 Broadcasting to {len(self.ws)} clients: '{text[:30]}{'...' if len(text) > 30 else ''}'")
        
        if not self.ws:
            logger.warning("❌ No connections to broadcast to")
            return
            
        message = {
            "type": "transcription",
            "text": text,
            "timestamp": time.time(),
            "metadata": metadata
        }
        
        sent_count = 0
        websocket = self.ws
        try:
            # Vérifier l'état de la connexion WebSocket
            if websocket.close_code is not None:
                logger.warning(f"WebSocket is closed")
                    
            await websocket.send(json.dumps(message))
            sent_count += 1
        except Exception as e:
            logger.warning(f"⚠️  Temporary error broadcasting: {e}")
            # Ne pas ajouter à disconnected - erreur temporaire possible
        
        self.ws = None
        logger.info(f"🗑️ Removed disconnected")
    
    async def broadcast_audio(self, audio_data: bytes):
        if not self.ws:
            return
        
        audio_b64 = base64.b64encode(audio_data).decode()
        
        message = {
            "type": "audio_chunk",
            "data": audio_b64,
            "format": "pcm",
            "timestamp": time.time()
        }
        
        websocket = self.ws
        try:
            await websocket.send(json.dumps(message))
        except:
            pass

        self.ws = None