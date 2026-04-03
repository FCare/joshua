import time
import threading
import requests
import os
from typing import Optional, Dict, Any

from pipeline_framework import PipelineStep
from messages.base_message import BaseMessage


class ChatterboxTTSStep(PipelineStep):
    
    def __init__(self, name: str = "ChatterboxTTS", config: Optional[Dict] = None):
        super().__init__(name, config, handler=self._handle_input_message)
        
        self.host = config.get("host", "https://chatterbox.caronboulme.fr/speech") if config else "https://chatterbox.caronboulme.fr/speech"
        self.voice = config.get("voice", "Fip4") if config else "Fip4"
        self.language_id = config.get("language_id", "fr") if config else "fr"
        
        # Configuration API Key
        self.api_key = config.get("api_key") if config else None
        if not self.api_key:
            self.api_key = os.getenv("TTS_API_KEY")
        
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
        
        # Ajouter l'API Key aux headers si disponible
        if self.api_key:
            self.headers["Authorization"] = f"Bearer {self.api_key}"
        
        self.interrupted = False
        self._lock = threading.Lock()
        self._current_response = None
        
        
        # Accumulateur pour collecter le texte complet avant synthesis
        self._text_accumulator = ""
        self._accumulator_metadata = {}
        
    
    def init(self) -> bool:
        return True
    
    def _handle_input_message(self, input_message):
        # Validation des types de messages autorisés - accepte tous les messages de sortie
        from messages.chat_message import ChatResponseMessage
        
        allowed_classes = (ChatResponseMessage)
        if not isinstance(input_message, allowed_classes):
            logger.warning(f"🔊 ChatterboxTTS: Type de message non autorisé: {type(input_message).__name__}")
            return
            
        try:
            # Préserver les métadonnées pour le routage (client_id)
            original_metadata = {}
            if hasattr(input_message, 'metadata') and input_message.metadata:
                original_metadata = input_message.metadata.copy()
            
            # 🎯 DÉTECTER LE SIGNAL FINISH DU CHAT (vient du duplicator)
            if (original_metadata.get('chunk_type') == 'finish'):
                print(f"🏁 TTS reçu signal FINISH du chat pour client: {original_metadata.get('original_client_id')}")
                self._handle_finish_signal(original_metadata)
                return
            
            # 🎯 IGNORER LES CHUNKS TEXTE DIRECTS DU CHAT (via duplicator)
            # Traiter seulement les phrases normalisées du sentence_normalizer
            source = original_metadata.get('source', '')
            chunk_type = original_metadata.get('chunk_type', '')
            
            # Si c'est un chunk partial du chat (pas passé par le normalizer), l'ignorer
            if chunk_type == 'partial' and source != 'SentenceNormalizerStep':
                print(f"🔄 TTS ignore chunk partial direct du chat, attend sentence normalizer")
                return
            
            # Tous les messages utilisent .data
            if hasattr(input_message, 'data'):
                text_data = str(input_message.data)
            else:
                text_data = str(input_message)
            
            # Traiter chaque phrase normalisée
            print(f"🔊 TTS reçu phrase normalisée: '{text_data}' from client: {original_metadata.get('original_client_id')}")
            
            self._current_metadata = original_metadata
            if text_data.strip():
                self._synthesize_text(text_data.strip())
        
        except Exception as e:
            print(f"❌ TTS error handling input: {e}")
    
    def _handle_finish_signal(self, finish_metadata):
        """
        Traite le signal finish du chat et l'envoie au websocket
        """
        try:
            # Envoyer directement le signal finish au websocket
            from messages.tts_message import AudioFinishedMessage
            finish_message = AudioFinishedMessage.create(
                total_chunks=0,
                total_bytes=0,
                metadata={
                    "type": "chat_finished",
                    "original_client_id": finish_metadata.get('original_client_id'),
                    "timestamp": time.time(),
                    "is_final_response": True  # 🎯 Signal final pour le client
                }
            )
            
            if self.output_queue:
                self.output_queue.enqueue(finish_message)
                print(f"🎉 TTS envoyé signal CHAT TERMINÉ pour client: {finish_metadata.get('original_client_id')}")
        
        except Exception as e:
            print(f"❌ Erreur envoi finish signal: {e}")
    
    def process_message(self, message) -> Optional[BaseMessage]:
        try:
            if hasattr(message, 'data') and message.data:
                text = str(message.data)
                self._synthesize_text(text)
                return None
            
        except Exception as e:
            from messages.error_message import ErrorMessage
            return ErrorMessage.create(error=str(e), step_name=self.name)
    
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
                            # Traitement direct du chunk audio (pas de queue intermédiaire)
                            self._send_audio_chunk(chunk)
                    
                    # Calcul des métriques finales
                    end_time = time.time()
                    total_generation_time = end_time - start_time
                    
                    # Durée audio estimée (PCM 24kHz, 16-bit = 48000 bytes/sec)
                    audio_duration_seconds = total_audio_bytes / 48000
                    
                    # Real Time Factor (RTF)
                    rtf = total_generation_time / audio_duration_seconds if audio_duration_seconds > 0 else 0
                    
                    print(f"📊 TTS Metrics: {total_audio_bytes} bytes ({audio_duration_seconds:.2f}s) - RTF: {rtf:.2f}x")
                    
                    # Envoyer un message de fin pour signaler que l'audio est terminé
                    self._send_audio_finished()
                            
        except Exception as e:
            pass
        finally:
            with self._lock:
                self._current_response = None
    
    def _send_audio_chunk(self, chunk):
        """Envoie directement un chunk audio à l'output_queue"""
        if not chunk:
            return
        
        # Premier chunk - supprimer l'en-tête WAV
        if hasattr(self, '_is_first_chunk') and self._is_first_chunk:
            self._is_first_chunk = False
            if len(chunk) > 44:
                chunk = chunk[44:]
            else:
                return
        
        # Créer les métadonnées audio
        audio_metadata = {
            "type": "audio_chunk",
            "format": "pcm",
            "timestamp": time.time()
        }
        
        # Préserver les métadonnées client (original_client_id, etc.)
        if hasattr(self, '_current_metadata') and self._current_metadata:
            audio_metadata.update(self._current_metadata)
            # S'assurer que le type reste "audio_chunk"
            audio_metadata["type"] = "audio_chunk"
        
        # Créer et envoyer le message audio
        from messages.tts_message import AudioChunkOutputMessage
        audio_message = AudioChunkOutputMessage.create(
            audio_data=chunk,
            chunk_index=0,  # Could be set from metadata if needed
            total_chunks=1,  # Could be set from metadata if needed
            metadata=audio_metadata
        )
        
        if self.output_queue:
            try:
                self.output_queue.enqueue(audio_message)
            except Exception as e:
                print(f"❌ Error sending audio chunk to output_queue: {e}")
    
    def _send_audio_finished(self):
        """Envoie un message de fin pour signaler que l'audio est terminé"""
        # Créer les métadonnées de fin
        finish_metadata = {
            "type": "audio_finished",
            "timestamp": time.time()
        }
        
        # Préserver les métadonnées client (original_client_id, etc.)
        if hasattr(self, '_current_metadata') and self._current_metadata:
            finish_metadata.update(self._current_metadata)
            # S'assurer que le type reste "audio_finished"
            finish_metadata["type"] = "audio_finished"
        
        # 🎯 Propager l'information de dernière phrase pour le client final
        is_last_phrase = finish_metadata.get('is_last_phrase', False)
        if is_last_phrase:
            finish_metadata["is_final_response"] = True
            print(f"🎉 TTS finished DERNIÈRE phrase - RÉPONSE COMPLÈTE TERMINÉE for client: {finish_metadata.get('original_client_id')}")
        else:
            print(f"🏁 TTS finished phrase for client: {finish_metadata.get('original_client_id')}")
        
        # Créer et envoyer le message de fin
        from messages.tts_message import AudioFinishedMessage
        finish_message = AudioFinishedMessage.create(
            total_chunks=0,
            total_bytes=0,
            metadata=finish_metadata
        )
        
        if self.output_queue:
            try:
                self.output_queue.enqueue(finish_message)
            except Exception as e:
                print(f"❌ Error sending audio finished signal: {e}")
    
    def cleanup(self):
        with self._lock:
            self.interrupted = True
            if self._current_response:
                try:
                    self._current_response.close()
                except:
                    pass
        