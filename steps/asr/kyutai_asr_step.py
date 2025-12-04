import time
import threading
import queue
import urllib.parse
import os
import sys
import logging
from typing import Optional, Dict, Any

from pipeline_framework import PipelineStep
from messages.base_message import Message, InputMessage, OutputMessage, ErrorMessage, MessageType

# Import du code Moshi existant
sys.path.append('/Work/Inno/SOCServer')
from deployments.cascading.asr.moshi_asr import MoshiASR, ExponentialMovingAverage

logger = logging.getLogger(__name__)


class KyutaiASRStep(PipelineStep):
    
    def __init__(self, name: str = "KyutaiASR", config: Optional[Dict] = None):
        super().__init__(name, config)
        
        self.host = config.get("host", "caronboulme.fr") if config else "caronboulme.fr"
        self.port = config.get("port", 443) if config else 443
        self.api_key = config.get("api_key", "public_token") if config else "public_token"
        
        self.sample_rate = config.get("sample_rate", 24000) if config else 24000
        self.samples_per_frame = config.get("samples_per_frame", 1920) if config else 1920
        
        self.pause_threshold = config.get("pause_threshold", 0.9) if config else 0.9
        self.vad_threshold = config.get("vad_threshold", 0.8) if config else 0.8
        
        self.moshi_asr = None
        self.text_buffer = []
        self.current_client_id = None
        self.transcription_active = False
        
        # Queue interne pour recevoir les événements Moshi
        self.moshi_output_queue = queue.Queue()
        
        print(f"KyutaiASRStep '{self.name}' configuré pour {self.host}")
    
    def init(self) -> bool:
        """Initialise l'ASR Kyutai"""
        try:
            print(f"Initialisation Kyutai ASR sur {self.host}")
            
            # Crée l'instance MoshiASR avec notre queue
            moshi_params = {
                "host": self.host,
                # Autres paramètres si nécessaire
            }
            
            self.moshi_asr = MoshiASR(**moshi_params)
            
            # Configure la queue de sortie de MoshiASR pour qu'elle pointe vers notre queue interne
            self.moshi_asr.set_output_queue(self.moshi_output_queue)
            
            # Démarre le thread de traitement des événements Moshi
            self._start_moshi_event_processor()
            
            print(f"Kyutai ASR initialisé avec succès")
            return True
            
        except Exception as e:
            print(f"Erreur initialisation Kyutai ASR: {e}")
            logger.error(f"Kyutai ASR init error: {e}")
            return False
    
    def _start_moshi_event_processor(self):
        """Démarre le thread qui traite les événements de MoshiASR"""
        def process_moshi_events():
            while self.transcription_active:
                try:
                    # Attend les événements de MoshiASR avec timeout
                    moshi_event = self.moshi_output_queue.get(timeout=1.0)
                    
                    # Traite l'événement selon son type
                    self._handle_moshi_event(moshi_event)
                    
                    self.moshi_output_queue.task_done()
                    
                except queue.Empty:
                    continue
                except Exception as e:
                    logger.error(f"Erreur traitement événement Moshi: {e}")
        
        self.transcription_active = True
        self.moshi_thread = threading.Thread(target=process_moshi_events, daemon=True)
        self.moshi_thread.start()
    
    def _handle_moshi_event(self, moshi_event):
        """Traite un événement reçu de MoshiASR"""
        try:
            if hasattr(moshi_event, 'type'):
                event_type = moshi_event.type
                
                if event_type == self.moshi_asr.ASREventType.TEXT:
                    # Texte transcrit reçu
                    text = moshi_event.data if hasattr(moshi_event, 'data') else ""
                    if text:
                        self._handle_transcribed_text(text)
                
                elif event_type == self.moshi_asr.ASREventType.START:
                    # Début de parole détecté
                    self._handle_speech_start()
                
                elif event_type == self.moshi_asr.ASREventType.END:
                    # Fin de parole détectée
                    self._handle_speech_end()
                
                else:
                    logger.debug(f"Événement Moshi non géré: {event_type}")
            
        except Exception as e:
            logger.error(f"Erreur handling événement Moshi: {e}")
    
    def _handle_transcribed_text(self, text: str):
        """Traite le texte transcrit"""
        try:
            # Ajoute au buffer de texte
            self.text_buffer.append(text)
            
            # Prépare le message de sortie
            transcription_message = OutputMessage(
                result=text,
                metadata={
                    "original_client_id": self.current_client_id,
                    "transcription_type": "partial",
                    "buffer_length": len(self.text_buffer),
                    "timestamp": time.time()
                }
            )
            
            # Envoie vers la queue de sortie (retour au WebSocket)
            if self.output_queue:
                try:
                    self.output_queue.put_nowait(transcription_message)
                    logger.debug(f"Texte transcrit envoyé: '{text}'")
                except queue.Full:
                    print("ATTENTION: Queue de sortie ASR pleine")
            
        except Exception as e:
            logger.error(f"Erreur traitement texte transcrit: {e}")
    
    def _handle_speech_start(self):
        """Traite le début de parole"""
        try:
            logger.debug("Début de parole détecté")
            
            # Réinitialise le buffer
            self.text_buffer = []
            
            # Optionnel: Envoie un événement de début
            start_message = OutputMessage(
                result="",
                metadata={
                    "original_client_id": self.current_client_id,
                    "event_type": "speech_start",
                    "timestamp": time.time()
                }
            )
            
            if self.output_queue:
                try:
                    self.output_queue.put_nowait(start_message)
                except queue.Full:
                    pass
            
        except Exception as e:
            logger.error(f"Erreur traitement début parole: {e}")
    
    def _handle_speech_end(self):
        """Traite la fin de parole"""
        try:
            logger.debug("Fin de parole détectée")
            
            # Compile le texte final
            final_text = " ".join(self.text_buffer).strip()
            
            if final_text:
                # Envoie la transcription finale
                final_message = OutputMessage(
                    result=final_text,
                    metadata={
                        "original_client_id": self.current_client_id,
                        "transcription_type": "final",
                        "word_count": len(final_text.split()),
                        "timestamp": time.time()
                    }
                )
                
                if self.output_queue:
                    try:
                        self.output_queue.put_nowait(final_message)
                        logger.info(f"Transcription finale: '{final_text}'")
                    except queue.Full:
                        print("ATTENTION: Queue de sortie ASR pleine")
            
            # Envoie un événement de fin
            end_message = OutputMessage(
                result="",
                metadata={
                    "original_client_id": self.current_client_id,
                    "event_type": "speech_end",
                    "final_text": final_text,
                    "timestamp": time.time()
                }
            )
            
            if self.output_queue:
                try:
                    self.output_queue.put_nowait(end_message)
                except queue.Full:
                    pass
            
        except Exception as e:
            logger.error(f"Erreur traitement fin parole: {e}")
    
    def process_message(self, message: Message) -> Optional[Message]:
        """
        Traite un message contenant un chunk audio
        """
        try:
            if message.type != MessageType.INPUT:
                return None
            
            # Récupère les données audio
            audio_data = message.data
            if not audio_data:
                return ErrorMessage(error_message="Pas de données audio")
            
            # Récupère l'ID du client pour le routage de retour
            if message.metadata:
                self.current_client_id = message.metadata.get("client_id")
            
            # Vérifie que MoshiASR est initialisé
            if not self.moshi_asr:
                return ErrorMessage(error_message="MoshiASR non initialisé")
            
            # Traite le chunk audio avec MoshiASR
            if isinstance(audio_data, bytes):
                # Audio binaire brut - utilise la méthode interne de MoshiASR
                self.moshi_asr._process_audio_chunk(audio_data)
            else:
                return ErrorMessage(error_message="Format audio non supporté (attendu: bytes)")
            
            # Retourne un message de confirmation (optionnel)
            return OutputMessage(
                result="Audio chunk traité",
                metadata={
                    "chunk_size": len(audio_data),
                    "client_id": self.current_client_id,
                    "timestamp": time.time()
                }
            )
            
        except Exception as e:
            error_msg = f"Erreur traitement audio ASR: {e}"
            logger.error(error_msg)
            return ErrorMessage(
                error=e,
                error_message=error_msg
            )
    
    def reset_transcription(self):
        """Remet à zéro l'état de transcription"""
        try:
            if self.moshi_asr:
                self.moshi_asr.reset()
            
            self.text_buffer = []
            self.current_client_id = None
            
            logger.info("Transcription reset")
            
        except Exception as e:
            logger.error(f"Erreur reset transcription: {e}")
    
    def get_asr_stats(self):
        """Retourne les statistiques ASR"""
        stats = {
            "asr_active": self.moshi_asr is not None,
            "transcription_active": self.transcription_active,
            "buffer_length": len(self.text_buffer),
            "current_client": self.current_client_id,
            "host": self.host
        }
        
        if self.moshi_asr:
            stats.update({
                "connected": self.moshi_asr._connected,
                "stream_active": self.moshi_asr._stream_active,
                "packets_sent": getattr(self.moshi_asr, 'packets_sent', 0),
                "packets_received": getattr(self.moshi_asr, 'packets_received', 0)
            })
        
        return stats
    
    def cleanup(self):
        """Nettoie les ressources ASR"""
        print(f"Nettoyage Kyutai ASR {self.name}")
        
        # Arrête le traitement des événements
        self.transcription_active = False
        
        # Attend que le thread se termine
        if hasattr(self, 'moshi_thread'):
            self.moshi_thread.join(timeout=2.0)
        
        # Nettoie MoshiASR
        if self.moshi_asr:
            try:
                self.moshi_asr.disconnect()
            except Exception as e:
                logger.error(f"Erreur déconnexion MoshiASR: {e}")
            finally:
                self.moshi_asr = None
        
        # Vide la queue
        try:
            while not self.moshi_output_queue.empty():
                self.moshi_output_queue.get_nowait()
        except:
            pass
        
        self.text_buffer = []
        self.current_client_id = None
        
        print(f"Kyutai ASR {self.name} nettoyé")


# Classe wrapper pour une meilleure intégration
class MoshiOutputQueueWrapper:
    """Wrapper pour adapter l'interface de queue de MoshiASR"""
    
    def __init__(self, target_queue: queue.Queue):
        self.target_queue = target_queue
    
    def enqueue(self, event):
        """Méthode attendue par MoshiASR"""
        try:
            self.target_queue.put_nowait(event)
        except queue.Full:
            logger.warning("Queue wrapper pleine")


if __name__ == "__main__":
    # Test basique de l'étape ASR
    import time
    
    config = {
        "host": "stt.kyutai.org",
        "api_key": "public_token",
        "sample_rate": 24000
    }
    
    asr_step = KyutaiASRStep("TestKyutaiASR", config)
    
    # Simule une queue de sortie
    output_queue = queue.Queue()
    asr_step.set_output_queue(output_queue)
    
    if asr_step.init():
        print("ASR Kyutai initialisé avec succès")
        print("Prêt à recevoir des chunks audio...")
        
        # Simule quelques chunks audio vides pour tester
        for i in range(3):
            test_audio = b'\x00' * 1920 * 2  # Silence de 1920 samples
            audio_msg = InputMessage(
                content=test_audio,
                metadata={"client_id": "test_client"}
            )
            
            result = asr_step.process_message(audio_msg)
            print(f"Résultat {i+1}: {result}")
            time.sleep(0.1)
        
        # Vérifie les stats
        stats = asr_step.get_asr_stats()
        print(f"Statistiques ASR: {stats}")
        
        time.sleep(2)  # Attend les éventuelles transcriptions
        
        # Vérifie la queue de sortie
        while not output_queue.empty():
            output_msg = output_queue.get()
            print(f"Message de sortie: {output_msg.data}")
        
        asr_step.cleanup()
    else:
        print("Erreur: Impossible d'initialiser l'ASR Kyutai")