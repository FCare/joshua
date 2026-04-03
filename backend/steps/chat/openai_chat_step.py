import time
import threading
import logging
import os
import json
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum

from pipeline_framework import PipelineStep
from messages.base_message import BaseMessage

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
        
        # Tools management - nouveau
        self.client_tools = {}  # {client_id: [tool_definitions]}
        self.client_prompts = {}  # {client_id: enhanced_prompt} - prompts enrichis par client
        
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
            
            # Utilise directement self.config (déjà fusionné : config + config_overrides)
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
        # Validation des types de messages autorisés
        from backend.messages.websocket_message import TextInputMessage, AudioInputMessage
        from backend.messages.tool_message import ToolResponseMessage
        from backend.messages.chat_message import SystemPromptMessage, ToolsReadyMessage
        from backend.messages.duplicator_message import InputMessage
        
        allowed_classes = (TextInputMessage, AudioInputMessage, ToolResponseMessage, SystemPromptMessage, ToolsReadyMessage, InputMessage)
        if not isinstance(input_message, allowed_classes):
            logger.warning(f"💬 Chat: Type de message non autorisé: {type(input_message).__name__}")
            return
            
        try:
            # DEBUG: Logger au tout début pour identifier le problème
            logger.info(f"🐛 DEBUG: openai_chat._handle_input_event called with message type: {type(input_message)}")
            logger.info(f"🐛 DEBUG: message data: {getattr(input_message, 'data', 'NO_DATA')}")
            logger.info(f"🐛 DEBUG: message metadata: {getattr(input_message, 'metadata', 'NO_METADATA')}")
            
            with self._lock:
                # Vérifier si c'est une mise à jour de system prompt
                if (hasattr(input_message, 'metadata') and
                    input_message.metadata and
                    input_message.metadata.get('type') == 'system_prompt_update'):
                    self._handle_system_prompt_update(input_message)
                    return
                
                # Gestion des messages tools_ready - nouveau
                if (hasattr(input_message, 'metadata') and
                    input_message.metadata and
                    input_message.metadata.get('message_type') == 'tools_ready'):
                    try:
                        logger.info(f"🔧 AVANT _handle_tools_ready call")
                        self._handle_tools_ready(input_message)
                        logger.info(f"🔧 APRES _handle_tools_ready call - SUCCESS")
                        return
                    except Exception as e:
                        logger.error(f"🔧 EXCEPTION dans _handle_tools_ready: {e}")
                        logger.error(f"🔧 Exception type: {type(e).__name__}")
                        import traceback
                        logger.error(f"🔧 Stack trace: {traceback.format_exc()}")
                        return  # Continue même en cas d'erreur pour éviter de tuer le worker
                
                # Gestion des réponses d'outils - nouveau
                if isinstance(input_message, ToolResponseMessage):
                    self._handle_tool_response(input_message)
                    return
                
                # FILTRER: Ignorer les messages audio et transcript_chunk, ne traiter que text et transcript_done
                if (hasattr(input_message, 'metadata') and
                    input_message.metadata):
                    message_type = input_message.metadata.get('message_type')
                    if message_type == 'audio':
                        logger.debug(f"💬 Chat: Ignoring audio message, should be handled by ASR")
                        return
                    elif message_type == 'transcript_chunk':
                        logger.debug(f"💬 Chat: Ignoring transcript_chunk (streaming), waiting for transcript_done")
                        return
                    elif message_type == 'transcript_done':
                        logger.info(f"💬 Chat: Processing transcript_done - starting chat generation")
                    elif message_type == 'text':
                        logger.info(f"💬 Chat: Processing text message from frontend")
                    
                # Extraire le client_id des métadonnées du message entrant
                if hasattr(input_message, 'metadata') and input_message.metadata:
                    self.current_client_id = input_message.metadata.get('original_client_id') or input_message.metadata.get('client_id')
                
                # Extraire le contenu (texte + images éventuelles)
                if hasattr(input_message, 'data'):
                    if isinstance(input_message.data, dict):
                        # Nouveau format avec support d'images
                        text_data = input_message.data.get('text', '')
                        images = input_message.data.get('images', [])
                    else:
                        # Format existant : texte simple
                        text_data = str(input_message.data)
                        images = []
                elif hasattr(input_message, 'text'):
                    text_data = input_message.text
                    images = []
                else:
                    text_data = str(input_message)
                    images = []
                
                logger.info(f"💬 Chat received input: '{text_data}' with {len(images)} images from client: {self.current_client_id}")
                
                # Traiter la requête avec texte et/ou images
                if text_data.strip() or images:
                    self._process_chat_request(text_data.strip(), images)
        
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
    
    def _process_chat_request(self, text: str, images: list = None):
        """Traite une requête de chat avec texte et images optionnelles"""
        if images is None:
            images = []
            
        try:
            # Construction du message utilisateur
            user_message = {"role": "user"}
            
            if images and len(images) > 0:
                # Format OpenAI Vision API
                content = []
                if text:
                    content.append({"type": "text", "text": text})
                
                for image_url in images:  # Déjà des data URLs complets !
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": image_url}
                    })
                
                user_message["content"] = content
                logger.info(f"💬 Prepared vision message: text='{text}', images={len(images)}")
            else:
                # Message texte simple (format existant)
                user_message["content"] = text
                logger.info(f"💬 Prepared text message: '{text}'")
            
            self.conversation_history.append(user_message)
            
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
        
        # Message système - utiliser le prompt enrichi si disponible pour ce client
        system_prompt = self.system_prompt
        if (self.current_client_id and
            self.current_client_id in self.client_prompts):
            system_prompt = self.client_prompts[self.current_client_id]
        
        if system_prompt:
            messages.append({
                "role": "system",
                "content": system_prompt
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
        """Appel OpenAI en mode streaming avec support des tools"""
        try:
            logger.info(f"💬 Calling OpenAI API with model {self.model}")
            logger.info(f"💬 Messages to send: {len(messages)} messages")
            
            # Paramètres de base
            call_params = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "stream": True
            }
            
            # Ajouter les outils spécifiques au client actuel
            if (self.current_client_id and
                self.current_client_id in self.client_tools):
                call_params["tools"] = self.client_tools[self.current_client_id]
                logger.info(f"🔧 Using {len(call_params['tools'])} tools for client {self.current_client_id}")
            
            response = self.client.chat.completions.create(**call_params)
            
            # Gestion du streaming avec support des tool calls
            self._handle_streaming_response(response)
            
        except Exception as e:
            logger.error(f"Erreur appel OpenAI: {e}")
            self._send_error_response(str(e))
    
    def _handle_streaming_response(self, response):
        """Gère la réponse streaming avec support des tool calls"""
        try:
            logger.info(f"💬 API response received, starting streaming...")
            assistant_response = ""
            tool_calls = []
            current_tool_call = None
            chunk_count = 0
            
            for chunk in response:
                chunk_count += 1
                # Vérification de sécurité pour Azure OpenAI
                if not hasattr(chunk, 'choices') or not chunk.choices:
                    continue
                    
                choice = chunk.choices[0]
                delta = choice.delta
                
                # Gestion du contenu texte
                if delta and delta.content:
                    content = delta.content
                    assistant_response += content
                    logger.debug(f"OpenAI stream chunk: '{content[:50]}{'...' if len(content) > 50 else ''}'")
                    
                    # Envoie directement vers l'output_queue
                    if self.output_queue:
                        from backend.messages.chat_message import ChatResponseMessage
                        output_message = ChatResponseMessage(
                            text=content,
                            is_partial=True,
                            metadata={
                                "original_client_id": self.current_client_id,
                                "chunk_type": "partial",
                                "timestamp": time.time()
                            }
                        )
                        self.output_queue.enqueue(output_message)
                
                # Gestion des tool calls
                if delta and delta.tool_calls:
                    for tool_call_delta in delta.tool_calls:
                        if tool_call_delta.index >= len(tool_calls):
                            # Nouveau tool call
                            tool_calls.append({
                                "id": tool_call_delta.id,
                                "type": "function",
                                "function": {
                                    "name": tool_call_delta.function.name,
                                    "arguments": tool_call_delta.function.arguments or ""
                                }
                            })
                        else:
                            # Continuer un tool call existant
                            if tool_call_delta.function and tool_call_delta.function.arguments:
                                tool_calls[tool_call_delta.index]["function"]["arguments"] += tool_call_delta.function.arguments
                
                # Vérifie si c'est la fin
                if hasattr(choice, 'finish_reason'):
                    if choice.finish_reason == "tool_calls":
                        logger.info(f"🛠️ Tool calls detected, processing {len(tool_calls)} calls")
                        self._handle_tool_calls(tool_calls, assistant_response)
                        break
                    elif choice.finish_reason == "stop":
                        # Fin normale
                        if assistant_response:
                            self.conversation_history.append({
                                "role": "assistant",
                                "content": assistant_response
                            })
                        self._send_finish_message()
                        break
            
        except Exception as e:
            logger.error(f"Erreur handling streaming response: {e}")
            self._send_error_response(str(e))
    
    def _send_finish_message(self):
        """Envoie un marqueur de fin de réponse"""
        if self.output_queue:
            from backend.messages.chat_message import ChatResponseMessage
            finish_message = ChatResponseMessage(
                text="",
                is_partial=False,
                metadata={
                    "original_client_id": self.current_client_id,
                    "chunk_type": "finish",
                    "timestamp": time.time()
                }
            )
            self.output_queue.enqueue(finish_message)
    
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
                from backend.messages.chat_message import ChatResponseMessage
                response_message = ChatResponseMessage(
                    text=response_event.data,
                    is_partial=True,
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
                from backend.messages.chat_message import ChatResponseMessage
                finish_message = ChatResponseMessage(
                    text="",
                    is_partial=False,
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
    
    def _send_output_message(self, message: BaseMessage):
        if self.output_queue:
            try:
                self.output_queue.enqueue(message)
                logger.info(f"Message enqueued to output: {type(message).__name__}")
            except Exception as e:
                logger.error(f"Erreur envoi message: {e}")
    
    def _send_error_response(self, error_msg: str):
        """Envoie une réponse d'erreur"""
        from backend.messages.error_message import ErrorMessage
        error_message = ErrorMessage(
            error=error_msg,
            step_name=self.name,
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
    
    def _handle_tools_ready(self, tools_ready_message):
        """Traite la réception de tous les outils disponibles"""
        try:
            logger.info(f"🔧 DEBUT _handle_tools_ready")
            data = tools_ready_message.data
            client_id = data.get('client_id')
            username = data.get('username')
            registered_tools = data.get('registered_tools', [])
            timed_out = data.get('timed_out', False)
            
            logger.info(f"🔧 Processing tools for client {client_id}: {len(registered_tools)} outils")
            
            # Enregistrer les outils pour ce client - LOCK DEJA PRIS par _handle_input_event
            self.client_tools[client_id] = registered_tools
            # Générer le prompt enrichi avec les descriptions d'outils
            logger.info(f"🔧 Generating enhanced prompt...")
            self.client_prompts[client_id] = self._generate_enhanced_prompt(registered_tools)
            logger.info(f"🔧 Enhanced prompt generated successfully")
            
            status = "avec timeout" if timed_out else "complet"
            logger.info(f"🛠️ Tools registration {status} pour {username}: {len(registered_tools)} outils")
            
            # Log des outils disponibles
            for tool_def in registered_tools:
                tool_name = tool_def['function']['name']
                logger.info(f"  - {tool_name}")
            
            # Log du prompt enrichi
            if registered_tools:
                logger.info(f"📝 Prompt enrichi généré pour {username}")
            
            logger.info(f"🔧 FIN _handle_tools_ready - SUCCESS")
            
        except Exception as e:
            logger.error(f"🔧 ERREUR _handle_tools_ready: {e}")
            logger.error(f"🔧 FIN _handle_tools_ready - ERROR")
            raise
    
    def _generate_enhanced_prompt(self, tools_definitions):
        """Génère un prompt enrichi avec les descriptions des outils disponibles"""
        if not tools_definitions:
            return self.system_prompt
        
        # Construire la section des outils
        tools_descriptions = ["Tu as accès aux outils suivants :"]
        
        for tool_def in tools_definitions:
            func = tool_def['function']
            name = func['name']
            description = func['description']
            tools_descriptions.append(f"- {name}: {description}")
        
        tools_descriptions.append("Utilise ces outils quand cela peut aider à répondre aux questions de l'utilisateur.")
        
        # Combiner le prompt de base avec les descriptions d'outils
        tools_section = "\n".join(tools_descriptions)
        enhanced_prompt = f"{self.system_prompt}\n\n{tools_section}"
        
        logger.debug(f"Prompt enrichi généré: {enhanced_prompt[:100]}...")
        return enhanced_prompt
    
    def _handle_tool_response(self, tool_response: BaseMessage):
        """Traite la réponse d'un outil"""
        try:
            logger.info(f"🔧 Received tool response: {tool_response.tool_name} -> {tool_response.tool_call_id}")
            
            # Ajouter à l'historique
            content = json.dumps(tool_response.result) if not tool_response.error else f"Erreur: {tool_response.error}"
            self.conversation_history.append({
                "role": "tool",
                "content": content,
                "tool_call_id": tool_response.tool_call_id
            })
            
            # Continuer la conversation avec le résultat de l'outil
            messages = self._prepare_messages()
            self._call_openai_streaming(messages)
            
        except Exception as e:
            logger.error(f"Erreur traitement tool response: {e}")
    
    def _handle_tool_calls(self, tool_calls, assistant_response):
        """Gère les appels d'outils demandés par le LLM"""
        try:
            # Ajouter à l'historique avec les tool calls
            self.conversation_history.append({
                "role": "assistant",
                "content": assistant_response,
                "tool_calls": tool_calls
            })
            
            logger.info(f"🛠️ Processing {len(tool_calls)} tool calls")
            
            # Envoyer les appels d'outils
            for tool_call in tool_calls:
                try:
                    parameters = json.loads(tool_call["function"]["arguments"])
                    from backend.messages.tool_message import ToolCallMessage
                    tool_call_message = ToolCallMessage(
                        tool_name=tool_call["function"]["name"],
                        tool_call_id=tool_call["id"],
                        parameters=parameters,
                        metadata={"original_client_id": self.current_client_id}
                    )
                    
                    if self.output_queue:
                        self.output_queue.enqueue(tool_call_message)
                        logger.info(f"📤 Tool call sent: {tool_call['function']['name']}")
                        
                except json.JSONDecodeError as e:
                    logger.error(f"Erreur parsing arguments tool call: {e}")
                    # Envoyer une réponse d'erreur pour ce tool call
                    from backend.messages.tool_message import ToolResponseMessage
                    error_response = ToolResponseMessage(
                        tool_call_id=tool_call["id"],
                        tool_name=tool_call["function"]["name"],
                        result=None,
                        error=f"Arguments invalides: {e}"
                    )
                    self._handle_tool_response(error_response)
                    
        except Exception as e:
            logger.error(f"Erreur handling tool calls: {e}")
    
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
            self.client_tools = {}  # Nettoyer les outils clients
            self.client_prompts = {}  # Nettoyer les prompts enrichis
        
        print(f"OpenAI Chat {self.name} nettoyé")