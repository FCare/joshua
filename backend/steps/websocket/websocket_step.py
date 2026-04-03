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
        
        self.connections = {}
        
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
        from backend.messages.chat_message import ChatResponseMessage
        from backend.messages.tts_message import AudioChunkOutputMessage, AudioFinishedMessage
        from backend.messages.duplicator_message import OutputMessage
        
        allowed_classes = (ChatResponseMessage, AudioChunkOutputMessage, AudioFinishedMessage, OutputMessage)
        if not isinstance(message_data, allowed_classes):
            logger.warning(f"🌐 WebSocket: Type de message non autorisé: {type(message_data).__name__}")
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
                # Envoyer à tous les clients connectés
                for client_id in list(self.connections.keys()):
                    try:
                        websocket = self.connections[client_id]
                        await websocket.send(finish_message)
                        logger.info(f"✅ Sent audio_finished to {client_id}")
                    except Exception as e:
                        logger.error(f"❌ Failed to send audio_finished to {client_id}: {e}")
                return
            
            # Essayer d'abord 'data', puis 'result' pour compatibilité avec différents steps
            data = None
            metadata = getattr(message_data, 'metadata', {})
            
            if hasattr(message_data, 'data'):
                data = message_data.data
            # Tous les messages utilisent .data
                
            if data is None:
                logger.warning(f"Message without data or result: {message_data}")
                return
                
            # Extraire le client_id original de la métadonnée
            original_client_id = metadata.get('original_client_id')
            
            if not original_client_id:
                logger.warning(f"No original_client_id in metadata, cannot route response: {metadata}")
                return
            
            # Différencier texte vs audio selon le type de données et metadata
            message_type = metadata.get('type', 'unknown')
            
            if message_type == 'audio_chunk' and isinstance(data, bytes):
                # Message audio - envoyer comme JSON avec base64
                logger.info(f"Sending audio chunk to client {original_client_id}: {len(data)} bytes")
                await self.send_audio_to_client(original_client_id, data, metadata)
                
            elif message_type == 'audio_finished' or (isinstance(data, dict) and data.get('type') == 'audio_finished'):
                # Signal de fin de streaming audio
                logger.info(f"Sending audio finished signal to client {original_client_id}")
                finish_message = {
                    "type": "audio_finished",
                    "total_chunks": data.get('total_chunks', 0) if isinstance(data, dict) else 0,
                    "total_bytes": data.get('total_bytes', 0) if isinstance(data, dict) else 0,
                    "timestamp": time.time(),
                    "metadata": metadata
                }
                await self.send_to_specific_client(original_client_id, json.dumps(finish_message), metadata)
                
            elif message_type == 'chat_finished':
                # 🎯 Signal de fin de chat complet (TTS a terminé)
                logger.info(f"Sending chat finished signal to client {original_client_id}")
                chat_finish_message = {
                    "type": "chat_finished",
                    "timestamp": time.time(),
                    "metadata": metadata
                }
                await self.send_to_specific_client(original_client_id, json.dumps(chat_finish_message), metadata)
                
            elif isinstance(data, str) and data.strip().startswith('{"type": "audio_finished"'):
                # Signal de fin audio sérialisé - parser et traiter
                try:
                    finish_data = json.loads(data)
                    if finish_data.get('type') == 'audio_finished':
                        logger.info(f"Sending parsed audio finished signal to client {original_client_id}")
                        await self.send_to_specific_client(original_client_id, data, metadata)
                        return
                except json.JSONDecodeError:
                    pass  # Continuer comme texte normal
                
                # Si parsing échoue, traiter comme texte normal
                logger.info(f"Sending chat response to client {original_client_id}: '{str(data)[:50]}{'...' if len(str(data)) > 50 else ''}'")
                await self.send_to_specific_client(original_client_id, str(data), metadata)
                
            elif isinstance(data, (str, int, float)):
                # Message texte normal - envoyer comme chat_response
                logger.info(f"Sending chat response to client {original_client_id}: '{str(data)[:50]}{'...' if len(str(data)) > 50 else ''}'")
                await self.send_to_specific_client(original_client_id, str(data), metadata)
                
            else:
                # Données inconnues - convertir en string par défaut
                logger.warning(f"Unknown data type for client {original_client_id}: {type(data)}")
                await self.send_to_specific_client(original_client_id, str(data), metadata)
                
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
        client_id = f"client_{id(websocket)}"
        self.connections[client_id] = websocket
        
        try:
            logger.info(f"WebSocket handler started for client {client_id}, mode={self.mode}")
            
            # Envoyer le message de connexion établie avec les capacités du pipeline
            connection_message = {
                "type": "connection_established",
                "client_id": client_id,
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
                    client_id=client_id,
                    username=username,
                    metadata={
                        "message_type": "user_connection",
                        "timestamp": time.time()
                    }
                )
                self.output_queue.enqueue(user_connection_message)
                logger.info(f"🔌 User connection notification sent for {username}")
            
            async for message in websocket:
                logger.info(f"Received message from {client_id}: type={type(message).__name__}, length={len(str(message)) if isinstance(message, str) else len(message) if isinstance(message, bytes) else 'unknown'}")
                
                if self.mode in ["audio_to_text", "audio_text_to_text_audio"] and isinstance(message, str):
                    # Mode audio : traiter les messages JSON avec audio encodé
                    try:
                        data = json.loads(message)
                        if data.get("type") == "audio" and "data" in data:
                            # Décoder l'audio base64
                            audio_b64 = data["data"]
                            audio_bytes = base64.b64decode(audio_b64)
                            metadata = data.get("metadata", {})
                            
                            logger.info(f"Processing JSON audio message from {client_id}: {len(audio_bytes)} bytes")
                            audio_message = AudioInputMessage(
                                audio_data=audio_bytes,
                                client_id=client_id,
                                format=metadata.get("format", self.audio_format),
                                sample_rate=metadata.get("sample_rate", self.sample_rate),
                                metadata={
                                    "channels": metadata.get("channels", 1),
                                    "chunk_index": metadata.get("chunk_index", 0),
                                    "timestamp": time.time()
                                }
                            )
                            self.output_queue.enqueue(audio_message)
                            logger.info(f"Audio message queued for processing")
                            continue  # Message traité, passer au suivant
                        # Si ce n'est pas un message audio, laisser passer à la section texte
                    except json.JSONDecodeError:
                        logger.error(f"Invalid JSON from {client_id}: {message[:200]}...")
                        continue
                    except Exception as e:
                        logger.error(f"Error processing audio JSON from {client_id}: {e}")
                        continue
                        
                elif self.mode == "audio_to_text" and isinstance(message, bytes):
                    logger.info(f"Processing raw audio message from {client_id}: {len(message)} bytes")
                    audio_message = AudioInputMessage(
                        audio_data=message,
                        client_id=client_id,
                        format=self.audio_format,
                        sample_rate=self.sample_rate,
                        metadata={
                            "timestamp": time.time()
                        }
                    )
                    self.output_queue.enqueue(audio_message)
                    logger.info(f"Audio message queued for processing")
                    
                if (self.mode in ["text_to_audio", "text_to_text", "audio_text_to_text_audio"]) and isinstance(message, str):
                    logger.info(f"Processing text message from {client_id}: '{message[:100]}{'...' if len(message) > 100 else ''}'")
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
                        client_id=client_id,
                        images=images,
                        metadata={
                            "timestamp": time.time()
                        }
                    )
                    self.output_queue.enqueue(text_message)
                    logger.info(f"Message queued - text: '{text_data}', images: {len(images)}")
                    
        except Exception as e:
            logger.error(f"Error in websocket_handler for {client_id}: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
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
    
    async def send_to_specific_client(self, client_id: str, text: str, metadata: dict):
        """Envoie un message texte à un client spécifique"""
        if client_id not in self.connections:
            logger.warning(f"❌ Client {client_id} not found in connections")
            return
            
        websocket = self.connections[client_id]
        
        # Détermine le type de message selon les métadonnées
        # Vérifier d'abord message_type explicite (pour ASR)
        explicit_message_type = metadata.get('message_type')
        if explicit_message_type in ['transcript_chunk', 'transcript_done']:
            message_type = explicit_message_type
        else:
            # Système basé sur response_type ou chunk_type
            response_type = metadata.get('response_type', 'transcription')
            chunk_type = metadata.get('chunk_type')
            
            # Si on a chunk_type (OpenAI Chat), utiliser ça pour déterminer le type
            if chunk_type in ['partial', 'finish']:
                message_type = "chat_response"
            elif response_type in ['partial', 'finish']:
                message_type = "chat_response"
            else:
                message_type = "transcription"
        
        message = {
            "type": message_type,
            "text": text,
            "timestamp": time.time(),
            "metadata": metadata
        }
        
        try:
            # Vérifier l'état de la connexion WebSocket
            if websocket.close_code is not None:
                logger.warning(f"WebSocket {client_id} is closed, removing from connections")
                if client_id in self.connections:
                    del self.connections[client_id]
                return
                
            await websocket.send(json.dumps(message))
            logger.debug(f"✅ Sent to {client_id}: '{text[:30]}{'...' if len(text) > 30 else ''}'")
        except Exception as e:
            logger.warning(f"⚠️  Temporary error sending to {client_id}: {e}")
            # Ne pas supprimer la connexion immédiatement - elle pourrait être temporairement occupée

    async def send_audio_to_client(self, client_id: str, audio_data: bytes, metadata: dict):
        """Envoie un chunk audio à un client spécifique au format JSON"""
        if client_id not in self.connections:
            logger.warning(f"Client {client_id} not connected for audio")
            return
            
        websocket = self.connections[client_id]
        
        try:
            # Vérifier l'état de la connexion WebSocket
            if websocket.close_code is not None:
                logger.warning(f"WebSocket {client_id} is closed, removing from connections")
                if client_id in self.connections:
                    del self.connections[client_id]
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
            logger.debug(f"✅ Sent audio chunk to {client_id}: {len(audio_data)} bytes")
        except Exception as e:
            logger.warning(f"⚠️  Temporary error sending audio to {client_id}: {e}")
            # Ne pas supprimer la connexion immédiatement - elle pourrait être temporairement occupée

    async def broadcast_text(self, text: str):
        """Broadcast simple text (pour compatibilité)"""
        await self.broadcast_text_with_metadata(text, {})
    
    async def broadcast_text_with_metadata(self, text: str, metadata: dict):
        """Broadcast text avec métadonnées"""
        logger.info(f"🔊 Broadcasting to {len(self.connections)} clients: '{text[:30]}{'...' if len(text) > 30 else ''}'")
        
        if not self.connections:
            logger.warning("❌ No connections to broadcast to")
            return
            
        message = {
            "type": "transcription",
            "text": text,
            "timestamp": time.time(),
            "metadata": metadata
        }
        
        disconnected = []
        sent_count = 0
        for client_id, websocket in self.connections.items():
            try:
                # Vérifier l'état de la connexion WebSocket
                if websocket.close_code is not None:
                    logger.warning(f"WebSocket {client_id} is closed")
                    disconnected.append(client_id)
                    continue
                    
                await websocket.send(json.dumps(message))
                sent_count += 1
                logger.debug(f"✅ Sent to {client_id}")
            except Exception as e:
                logger.warning(f"⚠️  Temporary error broadcasting to {client_id}: {e}")
                # Ne pas ajouter à disconnected - erreur temporaire possible
        
        logger.info(f"📤 Sent to {sent_count}/{len(self.connections)} clients")
        
        for client_id in disconnected:
            if client_id in self.connections:
                del self.connections[client_id]
                logger.info(f"🗑️ Removed disconnected client {client_id}")
    
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