import time
import threading
import logging
import os
import json
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum

from pipeline_framework import PipelineStep
from messages.base_message import Message, InputMessage, OutputMessage, ErrorMessage, MessageType

try:
    import openai
    import dotenv
    OPENAI_DEPENDENCIES_AVAILABLE = True
except ImportError as e:
    print(f"""
    Missing dependencies for OpenAI Chat: {e}
    Install with: pip install openai python-dotenv
    """)
    OPENAI_DEPENDENCIES_AVAILABLE = False
    openai = None
    dotenv = None

logger = logging.getLogger(__name__)


class LLMEventType(Enum):
    """Types d'événements LLM"""
    INPUT = "input"              # Input: text + tools
    PARTIAL_RESPONSE = "partial_response"  # Partial response chunk
    FINISH_RESPONSE = "finish_response"    # Final response completion


@dataclass
class LLMEvent:
    """Événement LLM standardisé pour input/output"""
    type: LLMEventType
    data: Any = None
    timestamp: Optional[float] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()


@dataclass
class InputEvent(LLMEvent):
    """Événement input avec texte et outils"""
    text: str = ""
    tools: Optional[Dict] = None
    type: LLMEventType = None
    
    def __post_init__(self):
        if self.type is None:
            self.type = LLMEventType.INPUT
        super().__post_init__()
        self.data = {
            "text": self.text,
            "tools": self.tools
        }


@dataclass
class PartialResponseEvent(LLMEvent):
    """Événement de réponse partielle avec texte uniquement"""
    text: str = ""
    type: LLMEventType = None
    
    def __post_init__(self):
        if self.type is None:
            self.type = LLMEventType.PARTIAL_RESPONSE
        super().__post_init__()
        self.data = self.text


@dataclass
class FinishResponseEvent(LLMEvent):
    """Événement de fin de réponse sans contenu"""
    type: LLMEventType = None
    
    def __post_init__(self):
        if self.type is None:
            self.type = LLMEventType.FINISH_RESPONSE
        super().__post_init__()
        self.data = None


class OpenAIChatStep(PipelineStep):
    """
    Step de chat utilisant OpenAI avec streaming.
    Prend du texte en entrée (en plusieurs fois) et streame les réponses.
    """
    
    def __init__(self, name: str = "OpenAIChat", config: Optional[Dict] = None):
        super().__init__(name, config, handler=self._handle_input_event)
        
        # Charge le fichier .env
        if OPENAI_DEPENDENCIES_AVAILABLE and dotenv:
            dotenv.load_dotenv()
        
        # Configuration des clés API
        self.api_key = config.get("api_key") if config else None
        if not self.api_key:
            # Essayer d'abord la variable spécifique au provider
            provider = config.get("provider", "azure") if config else "azure"
            if provider == "llamacpp":
                self.api_key = os.getenv("LLAMACPP_API_KEY")
            else:
                self.api_key = os.getenv("OPENAI_API_KEY")
        
        self.model = config.get("model", "gpt-4o-mini") if config else "gpt-4o-mini"
        self.temperature = config.get("temperature", 0.7) if config else 0.7
        self.max_tokens = config.get("max_tokens", 1000) if config else 1000
        self.system_prompt = config.get("system_prompt", "You are a helpful assistant.") if config else "You are a helpful assistant."
        
        # État de conversation
        self.conversation_history = []
        self.accumulated_text = ""  # Pour accumuler le texte reçu en plusieurs fois
        self.current_client_id = None
        
        # Thread safety
        self._lock = threading.Lock()
        
        # Client OpenAI
        self.client = None
        
        print(f"OpenAIChatStep '{self.name}' configuré avec modèle {self.model}")
    
    def init(self) -> bool:
        """Initialise le client OpenAI"""
        try:
            if not self.api_key:
                logger.error("OpenAI API key non trouvée")
                return False
            
            # Utilise directement self.config (déjà fusionné : default_config + config_overrides)
            provider = self.config.get("provider")  # Pas de défaut ici, défini dans le JSON
            endpoint = self.config.get("endpoint")
            
            if provider == "llamacpp":
                # Configuration Llama.cpp (Qwen3 VL 8B Instruct)
                self.client = openai.OpenAI(
                    api_key=self.api_key,
                    base_url=endpoint
                )
                print(f"Llama.cpp (Qwen3 VL 8B) initialisé - endpoint: {endpoint}, modèle: {self.model}")
            elif provider == "azure":
                # Configuration Azure OpenAI
                api_version = self.config.get("api_version")
                self.client = openai.AzureOpenAI(
                    api_key=self.api_key,
                    azure_endpoint=endpoint,
                    api_version=api_version
                )
                print(f"Azure OpenAI initialisé - endpoint: {endpoint}, modèle: {self.model}")
            else:
                raise ValueError(f"Provider non supporté: {provider}")
            return True
            
        except Exception as e:
            print(f"Erreur initialisation OpenAI Chat: {e}")
            logger.error(f"OpenAI Chat init error: {e}")
            return False
    
    
    def _handle_input_event(self, input_message):
        try:
            with self._lock:
                # Vérifier si c'est une mise à jour de system prompt
                if (hasattr(input_message, 'metadata') and
                    input_message.metadata and
                    input_message.metadata.get('type') == 'system_prompt_update'):
                    self._handle_system_prompt_update(input_message)
                    return
                
                # FILTRER: Ignorer les transcript_chunk, ne traiter que transcript_done
                if (hasattr(input_message, 'metadata') and
                    input_message.metadata):
                    message_type = input_message.metadata.get('message_type')
                    if message_type == 'transcript_chunk':
                        logger.debug(f"💬 Chat: Ignoring transcript_chunk (streaming), waiting for transcript_done")
                        return
                    elif message_type == 'transcript_done':
                        logger.info(f"💬 Chat: Processing transcript_done - starting chat generation")
                    
                # Extraire le client_id des métadonnées du message entrant
                if hasattr(input_message, 'metadata') and input_message.metadata:
                    self.current_client_id = input_message.metadata.get('original_client_id') or input_message.metadata.get('client_id')
                
                # Extraire le texte depuis InputMessage ou InputEvent
                if hasattr(input_message, 'data'):
                    text_data = str(input_message.data)
                elif hasattr(input_message, 'text'):
                    text_data = input_message.text
                else:
                    text_data = str(input_message)
                
                logger.info(f"💬 Chat received input: '{text_data}' from client: {self.current_client_id}")
                
                # Accumule le texte reçu
                self.accumulated_text += text_data + " "
                
                # Décision: envoyer la requête maintenant ou attendre plus de texte ?
                # Pour simplifier, on traite chaque input immédiatement
                # Dans un vrai système, on pourrait attendre un timeout ou un signal de fin
                
                if self.accumulated_text.strip():
                    self._process_chat_request(self.accumulated_text.strip())
                    self.accumulated_text = ""  # Reset après traitement
        
        except Exception as e:
            logger.error(f"Erreur handling input event: {e}")
    
    def _handle_system_prompt_update(self, input_message):
        """Traite les mises à jour de system prompt"""
        try:
            # Extraire le nouveau system prompt
            if hasattr(input_message, 'data'):
                new_system_prompt = str(input_message.data)
            elif hasattr(input_message, 'text'):
                new_system_prompt = input_message.text
            else:
                new_system_prompt = str(input_message)
            
            # Mettre à jour le system prompt
            self.system_prompt = new_system_prompt
            logger.info(f"System prompt mis à jour: {new_system_prompt[:100]}...")
            
        except Exception as e:
            logger.error(f"Erreur lors de la mise à jour du system prompt: {e}")
    
    def _process_chat_request(self, text: str):
        """Traite une requête de chat complète"""
        try:
            # Ajoute le message utilisateur à l'historique
            self.conversation_history.append({
                "role": "user",
                "content": text
            })
            
            # Prépare les messages pour l'API
            messages = self._prepare_messages()
            
            # Appel API OpenAI en streaming
            self._call_openai_streaming(messages)
            
        except Exception as e:
            logger.error(f"Erreur traitement requête chat: {e}")
            self._send_error_response(str(e))
    
    def _prepare_messages(self):
        """Prépare les messages pour l'API OpenAI"""
        messages = []
        
        # Message système
        if self.system_prompt:
            messages.append({
                "role": "system",
                "content": self.system_prompt
            })
        
        # Ajoute l'heure actuelle
        current_time = time.strftime("%A %d %B %Y %H:%M", time.localtime())
        messages.append({
            "role": "system",
            "content": f"Current date and time: {current_time}"
        })
        
        # Ajoute l'historique de conversation (limité aux N derniers messages)
        max_history = 10  # Limite pour éviter des contextes trop longs
        recent_history = self.conversation_history[-max_history:]
        messages.extend(recent_history)
        
        return messages
    
    def _call_openai_streaming(self, messages):
        """Appel OpenAI en mode streaming"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stream=True
            )
            
            assistant_response = ""
            
            for chunk in response:
                # Vérification de sécurité pour Azure OpenAI
                if not hasattr(chunk, 'choices') or not chunk.choices:
                    continue
                    
                choice = chunk.choices[0]
                if hasattr(choice, 'delta') and choice.delta and choice.delta.content is not None:
                    content = choice.delta.content
                    assistant_response += content
                    logger.info(f"OpenAI stream chunk: '{content[:50]}{'...' if len(content) > 50 else ''}'")
                    
                    # Envoie directement vers l'output_queue
                    if self.output_queue:
                        output_message = OutputMessage(
                            result=content,
                            metadata={
                                "original_client_id": self.current_client_id,
                                "chunk_type": "partial",
                                "timestamp": time.time()
                            }
                        )
                        self.output_queue.enqueue(output_message)
                
                # Vérifie si c'est la fin
                if hasattr(choice, 'finish_reason') and choice.finish_reason == "stop":
                    # Ajoute la réponse complète à l'historique
                    if assistant_response:
                        self.conversation_history.append({
                            "role": "assistant", 
                            "content": assistant_response
                        })
                    
                    # Envoie un marqueur de fin (optionnel)
                    if self.output_queue:
                        finish_message = OutputMessage(
                            result="",
                            metadata={
                                "original_client_id": self.current_client_id,
                                "chunk_type": "finish",
                                "timestamp": time.time()
                            }
                        )
                        self.output_queue.enqueue(finish_message)
                    break
            
        except Exception as e:
            logger.error(f"Erreur appel OpenAI: {e}")
            self._send_error_response(str(e))
    
    def _handle_system_prompt_update(self, prompt_message):
        """Traite une mise à jour du system prompt"""
        try:
            new_system_prompt = prompt_message.data
            prompt_id = prompt_message.metadata.get('prompt_id', 0)
            source = prompt_message.metadata.get('source', 'unknown')
            
            # Mettre à jour le system prompt
            old_prompt = self.system_prompt
            self.system_prompt = new_system_prompt
            
            logger.info(f"System prompt mis à jour par {source} (ID: {prompt_id})")
            logger.debug(f"Ancien prompt: {old_prompt[:50]}...")
            logger.debug(f"Nouveau prompt: {new_system_prompt[:50]}...")
            
            # Optionnel: réinitialiser l'historique de conversation pour un fresh start
            reset_history = prompt_message.metadata.get('reset_history', False)
            if reset_history:
                self.conversation_history = []
                logger.info("Historique de conversation réinitialisé")
            
        except Exception as e:
            logger.error(f"Erreur mise à jour system prompt: {e}")
    
    def _handle_response_streaming(self, response_event: LLMEvent):
        try:
            if response_event.type == LLMEventType.PARTIAL_RESPONSE:
                logger.info(f"Handling partial response: '{response_event.data}'")
                response_message = OutputMessage(
                    result=response_event.data,
                    metadata={
                        "original_client_id": self.current_client_id,
                        "response_type": "partial",
                        "timestamp": time.time()
                    }
                )
                self._send_output_message(response_message)
                logger.info(f"Sent partial response to output queue")
                
            elif response_event.type == LLMEventType.FINISH_RESPONSE:
                logger.info(f"Handling finish response event")
                finish_message = OutputMessage(
                    result="",
                    metadata={
                        "original_client_id": self.current_client_id,
                        "response_type": "finish",
                        "timestamp": time.time()
                    }
                )
                self._send_output_message(finish_message)
                logger.info(f"Sent finish response to output queue")
        
        except Exception as e:
            logger.error(f"Error handling response streaming: {e}")
    
    def _send_output_message(self, message: OutputMessage):
        if self.output_queue:
            try:
                self.output_queue.enqueue(message)
                logger.info(f"Message enqueued to output: {type(message).__name__}")
            except Exception as e:
                logger.error(f"Erreur envoi message: {e}")
    
    def _send_error_response(self, error_msg: str):
        """Envoie une réponse d'erreur"""
        error_message = OutputMessage(
            result=f"Erreur: {error_msg}",
            metadata={
                "original_client_id": self.current_client_id,
                "response_type": "error",
                "timestamp": time.time()
            }
        )
        self._send_output_message(error_message)
    
    def reset_conversation(self):
        """Remet à zéro la conversation"""
        try:
            with self._lock:
                self.conversation_history = []
                self.accumulated_text = ""
                self.current_client_id = None
            
            logger.info("Conversation reset")
            
        except Exception as e:
            logger.error(f"Erreur reset conversation: {e}")
    
    def get_chat_stats(self):
        """Retourne les statistiques du chat"""
        stats = {
            "chat_active": self.client is not None,
            "conversation_length": len(self.conversation_history),
            "accumulated_text": len(self.accumulated_text),
            "current_client": self.current_client_id,
            "model": self.model
        }
        
        return stats
    
    def cleanup(self):
        """Nettoie les ressources du chat"""
        print(f"Nettoyage OpenAI Chat {self.name}")
        
        if hasattr(self, 'input_queue') and self.input_queue:
            self.input_queue.stop()
        
        # Nettoie l'état
        with self._lock:
            self.conversation_history = []
            self.accumulated_text = ""
            self.current_client_id = None
        
        print(f"OpenAI Chat {self.name} nettoyé")