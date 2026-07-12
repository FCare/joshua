import threading
import logging
import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum

from pipeline_framework import PipelineStep
from messages.base_message import BaseMessage
from messages.websocket_message import TextInputMessage, ImageUploadMessage
from messages.tool_message import ToolResponseMessage
from messages.chat_message import SystemPromptMessage, ToolsReadyMessage, AgentTopicMessage, MqttToolUpdateMessage, MqttWriteMessage
from messages.asr_message import TranscriptionMessage

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


class OpenAIChatStep(PipelineStep):
    """
    Step de chat utilisant OpenAI avec streaming.
    Prend du texte en entrée (en plusieurs fois) et streame les réponses.
    """
    
    def __init__(self, name: str = "OpenAIChat", config: Optional[Dict] = None):
        super().__init__(name, config, handler=self._handle_input_message)
        
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
        self.max_tokens = config.get("max_tokens", 5000) if config else 5000
        # Cap réel (côté serveur vLLM) sur les tokens de "réflexion" — contrairement à la
        # consigne de prompt "300 mots max" (que le modèle peut ignorer), ce paramètre est
        # appliqué par le backend : une fois le budget atteint, le modèle est forcé de
        # passer proprement à la réponse plutôt que de continuer à réfléchir jusqu'à
        # épuiser tout le max_tokens (observé en pratique : finish_reason=length avec une
        # réponse totalement vide après une longue réflexion consécutive à une erreur d'outil).
        # Testé empiriquement à 400 avec reasoning_effort="low" : encore trop juste sur des
        # questions à plusieurs étapes (calcul, comparaisons) — remonté à 800 pour laisser
        # de la marge, épaulé par reasoning_effort (contrôle sémantique côté vLLM, cf.
        # _call_openai_streaming) et par le retry automatique sans thinking en dernier
        # recours si même ça ne suffit pas.
        self.thinking_token_budget = config.get("thinking_token_budget", 4000) if config else 4000
        self.system_prompt = ""
        self._original_system_prompt: str | None = None
        self.profile = ""

        # État de conversation
        self.conversation_history = []
        self.accumulated_text = ""  # Pour accumuler le texte reçu en plusieurs fois
        
        # NOUVEAU : Contexte persistant d'images
        self.persistent_images = []  # Liste des images uploadées dans la session
        
        # Tools management - nouveau
        self.client_tools = None
        self.client_prompts = None
        self._topic_response_map: dict = {}      # write_topic → response_topic
        self._pending_tool_responses: dict = {}  # response_topic → tool_call_id
        self._my_tool_call_ids: set = set()      # tool_call_ids générés par ce client
        self._next_system_addendum: str | None = None  # prompt temporaire injecté une fois après un tool result

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
                    base_url=endpoint,
                    timeout=90.0,
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
    
    
    def _handle_input_message(self, input_message):
        """Dispatcher propre basé uniquement sur isinstance()"""
        try:
            with self._lock:
                # Switch case propre basé sur le type
                if isinstance(input_message, TextInputMessage):
                    self._handle_text_input(input_message)
                elif isinstance(input_message, ImageUploadMessage):
                    self._handle_image_upload(input_message)
                elif isinstance(input_message, TranscriptionMessage):
                    self._handle_transcription(input_message)
                elif isinstance(input_message, ToolResponseMessage):
                    self._handle_tool_response(input_message)
                elif isinstance(input_message, SystemPromptMessage):
                    self._handle_system_prompt_message(input_message)
                elif isinstance(input_message, ToolsReadyMessage):
                    self._handle_tools_ready(input_message)
                elif isinstance(input_message, AgentTopicMessage):
                    self._handle_agent_topic(input_message)
                elif isinstance(input_message, MqttToolUpdateMessage):
                    self._handle_mqtt_tool_update(input_message)
        except Exception as e:
            logger.error(f"Erreur handling input event: {e}")

    def _handle_text_input(self, message: TextInputMessage):
        """Traite les messages texte du frontend"""
        logger.info(f"💬 Chat: Processing text message from frontend")
        
        text_data = message.text
        logger.info(f"Chat received text: '{text_data}'")
        
        if text_data:
            self._process_chat_request(text_data)
    
    def _handle_image_upload(self, message: ImageUploadMessage):
        """Traite les uploads d'images pour contexte persistant"""
        logger.info(f"Chat: Processing image upload: {message.filename}")
        
        # Ajouter l'image au contexte persistant
        self.persistent_images.append({
            "filename": message.filename,
            "data_url": message.image_data  # Déjà un data URL complet
        })
        
        # Buffer circulaire : garder seulement les 3 dernières images
        max_images = 3
        if len(self.persistent_images) > max_images:
            removed_image = self.persistent_images.pop(0)  # Supprimer la plus ancienne
            logger.info(f"Removed oldest image from buffer: {removed_image['filename']}")
        
        logger.info(f"Image added to persistent context. Total images: {len(self.persistent_images)}/{max_images}")

    def _handle_transcription(self, message: TranscriptionMessage):
        """Traite les messages de transcription de l'ASR"""
        # Dans l'architecture dataclass pure, les messages de transcription sont finals par défaut
        # Plus de distinction partial/complete via metadata
        logger.info(f"💬 Chat: Processing transcription - starting chat generation")
        
        if message.is_final:
            # Utiliser l'accès direct aux propriétés dataclass
            text_data = message.text
            
            if text_data:
                self._process_chat_request(text_data)

    def _handle_system_prompt_message(self, message: SystemPromptMessage):
        logger.info(f"💬 Chat: Processing system prompt update")
        if message.prompt is None:
            if self._original_system_prompt is not None:
                self.system_prompt = self._original_system_prompt
                logger.info("System prompt restauré (original)")
        else:
            if self._original_system_prompt is None:
                self._original_system_prompt = self.system_prompt
            self.system_prompt = message.prompt
            logger.info(f"System prompt mis à jour: {self.system_prompt[:100]}...")
        self.client_prompts = self._generate_enhanced_prompt(self.client_tools or [])

    def _handle_agent_topic(self, message: AgentTopicMessage):
        logger.info(f"💬 Chat: Agent topic received — {message.topic} (is_response={message.is_response})")
        if message.is_response:
            tool_call_id = self._pending_tool_responses.pop(message.topic, None)
            if tool_call_id and tool_call_id in self._my_tool_call_ids:
                self._my_tool_call_ids.discard(tool_call_id)
                payload = message.payload
                if isinstance(payload, dict) and "prompt_addendum" in payload:
                    self._next_system_addendum = payload["prompt_addendum"]
                    payload = {k: v for k, v in payload.items() if k != "prompt_addendum"}
                    logger.info(f"💬 Chat: prompt_addendum extrait du tool result ({message.topic})")
                self.conversation_history.append({
                    "role": "tool",
                    "content": json.dumps(payload, ensure_ascii=False),
                    "tool_call_id": tool_call_id,
                })
                logger.info(f"💬 Chat: résultat tool injecté pour {message.topic} (call_id={tool_call_id})")
                messages = self._prepare_messages()
                self._call_openai_streaming(messages)
            else:
                logger.info(f"💬 Chat: résultat ignoré sur {message.topic} (call_id={tool_call_id} non émis par ce client)")
        elif isinstance(message.payload, dict):
            summary = message.payload.get("summary", "")
            if summary:
                self.profile = summary
                self.client_prompts = self._generate_enhanced_prompt(self.client_tools or [])
                logger.info(f"Profile integrated into system prompt: {self.profile[:100]}...")
            else:
                logger.info(f"💬 Chat: No handler for topic payload — topic={message.topic}")

    def _handle_mqtt_tool_update(self, message: MqttToolUpdateMessage):
        new_tool = message.tool_definition
        tool_name = new_tool["function"]["name"]
        if self.client_tools is None:
            self.client_tools = []
        self.client_tools = [t for t in self.client_tools if t["function"]["name"] != tool_name]
        self.client_tools.append(new_tool)
        self.client_prompts = self._generate_enhanced_prompt(self.client_tools)
        self._topic_response_map.update(message.response_map)
        logger.info(f"🔧 Tool '{tool_name}' mis à jour — response_map: {message.response_map}")
    
    def _handle_system_prompt_update(self, input_message):
        """Traite les mises à jour de system prompt"""
        try:
            # Utiliser l'accès direct aux propriétés dataclass
            new_system_prompt = input_message.text
            
            # Mettre à jour le system prompt
            self.system_prompt = new_system_prompt
            logger.info(f"System prompt mis à jour: {new_system_prompt[:100]}...")
            
        except Exception as e:
            logger.error(f"Erreur lors de la mise à jour du system prompt: {e}")
    
    def _process_chat_request(self, text: str):
        """Traite une requête de chat avec texte et images persistantes"""
        try:
            # Construction du message utilisateur
            user_message = {"role": "user"}
            
            # Utiliser les images du contexte persistant
            if self.persistent_images and len(self.persistent_images) > 0:
                # Format OpenAI Vision API
                content = []          
                for image_info in self.persistent_images:
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": image_info["data_url"]}
                    })

                if text:
                    content.append({"type": "text", "text": text})
                
                user_message["content"] = content
                logger.info(f"Prepared vision message: text='{text}', persistent_images={len(self.persistent_images)}")
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
        if (self.client_prompts):
            system_prompt = self.client_prompts
        
        if system_prompt:
            messages.append({
                "role": "system",
                "content": system_prompt
            })

        messages.extend(self.conversation_history)

        # L'heure actuelle est ajoutée APRÈS l'historique, pas avant : elle change à
        # chaque appel (donc à chaque tour), et le cache de préfixe de vLLM exige une
        # correspondance exacte depuis le premier token — la placer avant l'historique
        # invalidait le cache de tout l'historique déjà envoyé à CHAQUE tour, même
        # quand son contenu n'avait pas changé. En la mettant après, seul ce message
        # (et le tour courant) est retraité, l'historique déjà envoyé reste un préfixe
        # stable et réutilisable d'un tour à l'autre. time.localtime() suit le fuseau
        # du système hôte (UTC ici), pas celui de l'utilisateur ; fixé sur Europe/Paris
        # comme dans system_prompt_step.py pour rester cohérent avec le "Nous sommes
        # le..." du prompt système.
        current_time = datetime.now(ZoneInfo("Europe/Paris")).strftime("%A %d %B %Y %H:%M")
        messages.append({
            "role": "system",
            "content": f"Current date and time: {current_time}"
        })

        if self._next_system_addendum:
            messages.append({"role": "system", "content": self._next_system_addendum})
            self._next_system_addendum = None

        logger.info(f"LLM called with {messages}")

        return messages
    
    def _call_openai_streaming(self, messages, enable_thinking: bool = True, _is_retry: bool = False):
        """Appel OpenAI en mode streaming avec support des tools.

        enable_thinking contrôle explicitement le mode réflexion du modèle
        (chat_template_kwargs.enable_thinking) — le template ne l'active jamais
        de lui-même par défaut. _is_retry marque un second appel automatique
        sans thinking après un premier essai qui a épuisé son budget de
        réflexion sans produire de contenu (voir _handle_streaming_response).
        """
        try:
            logger.info(f"💬 Calling OpenAI API with model {self.model} (enable_thinking={enable_thinking})")
            logger.info(f"💬 Messages to send: {len(messages)} messages")

            # Paramètres de base
            call_params = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "stream": True
            }

            # Ajouter les outils spécifiques au client actuel — self.client_tools vaut None
            # tant qu'aucun MqttToolUpdateMessage n'est encore arrivé (ex: session sans MQTT
            # connecté, ou fenêtre de course juste après la connexion) ; sans ce filet, un
            # simple len(None) fait planter CHAQUE appel pour toute la durée de la session.
            client_tools = self.client_tools or []
            if client_tools:
                call_params["tools"] = client_tools
                call_params["tool_choice"] = "auto"
            logger.info(f"🔧 Using {len(client_tools)} tools (tool_choice=auto)")

            extra_body = {"priority": 0, "chat_template_kwargs": {"enable_thinking": enable_thinking}}
            if enable_thinking and self.thinking_token_budget:
                extra_body["thinking_token_budget"] = self.thinking_token_budget
            with self.client.chat.completions.create(**call_params, extra_body=extra_body) as response:
                # Gestion du streaming avec support des tool calls
                self._handle_streaming_response(response, messages, enable_thinking, _is_retry)

        except Exception as e:
            logger.error(f"Erreur appel OpenAI: {e}")
            self._send_error_response(str(e))

    def _handle_streaming_response(self, response, messages=None, enable_thinking: bool = True,
                                    _is_retry: bool = False):
        """Gère la réponse streaming avec support des tool calls"""
        try:
            logger.info(f"💬 API response received, starting streaming...")
            assistant_response = ""
            reasoning_text = ""
            tool_calls = []
            chunk_count = 0

            for chunk in response:
                chunk_count += 1
                # Vérification de sécurité pour Azure OpenAI
                logger.info(f"💬 API response Chunk {chunk}")
                if not hasattr(chunk, 'choices') or not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                # Réflexion interne du modèle — jamais envoyée au client (TTS la lirait
                # à voix haute), uniquement loggée pour diagnostic. Champ "reasoning" (pas
                # "reasoning_content"), propre à ce build vLLM/gemma4. Le pydantic ChoiceDelta
                # du SDK openai (2.45.0) ne le déclare pas comme attribut typé — présent
                # uniquement via model_extra (constaté en pratique : getattr(delta,
                # "reasoning", None) renvoie toujours None malgré un flux réel confirmé
                # au niveau HTTP brut).
                reasoning_delta = (delta.model_extra or {}).get("reasoning") if delta else None
                if reasoning_delta:
                    reasoning_text += reasoning_delta

                # Gestion du contenu texte
                if delta and delta.content:
                    content = delta.content
                    assistant_response += content
                    logger.info(f"OpenAI stream chunk: '{content[:50]}{'...' if len(content) > 50 else ''}'")

                    # Envoie directement vers l'output_queue
                    if self.output_queue:
                        from messages.chat_message import ChatResponseMessage
                        output_message = ChatResponseMessage(
                            text=content,
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
                    elif choice.finish_reason in ("stop", "length", "eos", "end_of_text"):
                        logger.info(
                            f"End of response (finish_reason={choice.finish_reason}, "
                            f"reasoning={len(reasoning_text)} car., content={len(assistant_response)} car.)"
                        )
                        if not assistant_response and not tool_calls:
                            # Le budget de tokens a été entièrement consommé par la
                            # réflexion interne (reasoning) sans jamais produire de
                            # contenu ni d'appel d'outil — sans ce filet, l'utilisateur
                            # ne reçoit strictement rien (silence total, pas même une
                            # erreur), voir finish_reason=length observé en pratique
                            # après une erreur d'outil qui a fait sur-réfléchir le modèle.
                            if enable_thinking and not _is_retry and messages is not None:
                                logger.warning(
                                    f"Réponse vide malgré finish_reason={choice.finish_reason} "
                                    f"({chunk_count} chunks reçus, {len(reasoning_text)} car. de reasoning) "
                                    f"— relance automatique sans thinking"
                                )
                                self._call_openai_streaming(messages, enable_thinking=False, _is_retry=True)
                                return
                            logger.warning(
                                f"Réponse vide malgré finish_reason={choice.finish_reason} "
                                f"({chunk_count} chunks reçus{', relance sans thinking déjà tentée' if _is_retry else ''}) "
                                f"— fallback envoyé"
                            )
                            assistant_response = (
                                "Désolé, je n'ai pas réussi à formuler de réponse. "
                                "Tu peux reformuler ta question ?"
                            )
                            if self.output_queue:
                                from messages.chat_message import ChatResponseMessage
                                self.output_queue.enqueue(ChatResponseMessage(text=assistant_response))
                        if assistant_response:
                            self.conversation_history.append({
                                "role": "assistant",
                                "content": assistant_response
                            })
                        self._send_chat_finish_message()
                        break

        except Exception as e:
            logger.error(f"Erreur handling streaming response: {e}")
            self._send_error_response(str(e))
    
    def _send_chat_finish_message(self):
        """Envoie un marqueur de fin de réponse"""
        if self.output_queue:
            from messages.chat_message import ChatFinishMessage
            finish_message = ChatFinishMessage()
            self.output_queue.enqueue(finish_message)
    
    def _send_output_message(self, message: BaseMessage):
        if self.output_queue:
            try:
                self.output_queue.enqueue(message)
                logger.info(f"Message enqueued to output: {type(message).__name__}")
            except Exception as e:
                logger.error(f"Erreur envoi message: {e}")
    
    def _send_error_response(self, error_msg: str):
        """Envoie une réponse d'erreur"""
        from messages.error_message import ErrorMessage
        error_message = ErrorMessage(
            error=error_msg,
            step_name=self.name
        )
        self._send_output_message(error_message)
    
    def restore_history(self, history: list):
        """Reprend une conversation persistée (reconnexion websocket sur le même conversation_id)."""
        with self._lock:
            self.conversation_history = list(history)
        logger.info(f"Historique de conversation restauré ({len(self.conversation_history)} messages)")

    def export_history(self) -> list:
        """Copie de l'historique courant, pour persistance côté serveur avant fermeture.

        Si la déconnexion survient pendant un appel d'outil en cours (résultat MQTT
        pas encore reçu), le dernier message est un tool_calls sans réponse associée
        — invalide à rejouer à l'API. On le retire plutôt que de reprendre une
        conversation cassée.
        """
        with self._lock:
            history = list(self.conversation_history)
        if history and history[-1].get("role") == "assistant" and history[-1].get("tool_calls"):
            history.pop()
        return history

    def reset_conversation(self):
        """Remet à zéro la conversation"""
        try:
            with self._lock:
                self.conversation_history = []
                self.accumulated_text = ""
                self._pending_tool_responses.clear()
                self._my_tool_call_ids.clear()
            
            logger.info("Conversation reset")
            
        except Exception as e:
            logger.error(f"Erreur reset conversation: {e}")
    
    def get_chat_stats(self):
        """Retourne les statistiques du chat"""
        stats = {
            "chat_active": self.client is not None,
            "conversation_length": len(self.conversation_history),
            "accumulated_text": len(self.accumulated_text),
            "model": self.model
        }
        
        return stats
    
    def _handle_tools_ready(self, tools_ready_message):
        """Traite la réception de tous les outils disponibles"""
        try:
            logger.info(f"🔧 DEBUT _handle_tools_ready")
            # Utiliser les propriétés directes de ToolsReadyMessage (architecture dataclass pure)
            tools_definitions = tools_ready_message.tools_definitions
            registered_tools = list(tools_definitions.values()) if tools_definitions else []
            
            logger.info(f"🔧 Processing tools: {len(registered_tools)} outils")
            
            # Enregistrer les outils pour ce client - LOCK DEJA PRIS par _handle_input_message
            self.client_tools = registered_tools
            # Générer le prompt enrichi avec les descriptions d'outils
            logger.info(f"🔧 Generating enhanced prompt...")
            self.client_prompts= self._generate_enhanced_prompt(registered_tools)
            logger.info(f"🔧 Enhanced prompt generated successfully")
            
            logger.info(f"🛠️ Tools registration complet: {len(registered_tools)} outils")
            
            # Log des outils disponibles
            for tool_def in registered_tools:
                tool_name = tool_def['function']['name']
                logger.info(f"  - {tool_name}")
            
            # Log du prompt enrichi
            if registered_tools:
                logger.info(f"📝 Prompt enrichi généré")
            
            logger.info(f"🔧 FIN _handle_tools_ready - SUCCESS")
            
        except Exception as e:
            logger.error(f"🔧 ERREUR _handle_tools_ready: {e}")
            logger.error(f"🔧 FIN _handle_tools_ready - ERROR")
            raise
    
    def _generate_enhanced_prompt(self, tools_definitions):
        """Génère un prompt enrichi avec le profil utilisateur et les outils disponibles"""
        parts = [self.system_prompt]

        if self.profile:
            parts.append(
                f"\nUser profile (STRICT: report only what is written here, never infer, guess, or add details):\n{self.profile}"
            )

        # --reasoning-budget côté serveur ne coupe pas toujours la réflexion à temps en
        # pratique (constaté : parfois tout le budget de tokens y passe, réponse vide) —
        # cette consigne, lue par le modèle dès l'ouverture de son bloc de réflexion,
        # est la méthode documentée pour les backends où le paramètre serveur n'est pas fiable.
        # Ces deux consignes et la règle d'honnêteté ci-dessous sont TOUJOURS incluses, même
        # sans outil enregistré (self.client_tools vide) — sinon, sans elles, le modèle perd
        # toute contrainte anti-invention et se remet à fabuler de mémoire (constaté en
        # pratique : sans cette règle, il invente des classifications, des personnages et
        # des histoires entières comme si de rien n'était).
        common_rules = [
            "Garde ta réflexion interne très brève, quelques phrases maximum, jamais plus de "
            "300 mots, avant de répondre.",
            "Ne verbalise JAMAIS ton plan de réponse ou ta réflexion dans ta réponse elle-même — "
            "pas de phrases comme 'l'utilisateur veut...', 'je dois répondre...', 'voici mon "
            "plan', 'je vais rechercher...'. Commence directement par la réponse, sans préambule "
            "ni méta-commentaire sur ce que tu es en train de faire.",
            "Si un outil renvoie une erreur, ne rumine pas longuement dessus : corrige "
            "immédiatement l'appel fautif et réessaie tout de suite, ou si tu ne vois pas "
            "la correction, dis simplement à l'utilisateur qu'une erreur technique est "
            "survenue. Ne jamais laisser ta réflexion s'éterniser après une erreur d'outil.",
        ]

        if tools_definitions:
            common_rules.append(
                "RÈGLE ABSOLUE : tu ne réponds JAMAIS de mémoire à une question factuelle. Tu n'as JAMAIS le "
                "droit de répondre négativement (\"je ne trouve pas\", \"je n'ai pas cette information\", "
                "\"je ne sais pas\", \"il n'y a rien à ce sujet\") sans avoir D'ABORD appelé au moins un outil "
                "pertinent parmi TOUS ceux listés plus bas — cette liste n'est PAS limitée aux exemples "
                "ci-dessous : avant de conclure qu'aucun outil ne convient, relis chaque outil disponible et "
                "son domaine, y compris les outils spécialisés (catalogues, listes personnelles, etc.), même "
                "si le lien avec la question n'est pas évident au premier abord. "
                "Exemples de routage parmi les cas les plus fréquents : "
                "→ tout outil spécialisé listé plus bas (catalogues personnels, contes, etc.) dès que la "
                "question touche à son domaine, même de loin — à vérifier EN PREMIER, avant tout autre outil ; "
                "cela inclut un titre d'œuvre qui te semble correspondre à une connaissance générale ou "
                "encyclopédique connue (ex: un livre, un conte publié) : vérifie D'ABORD s'il existe dans le "
                "catalogue personnel de l'utilisateur avant de supposer qu'il s'agit d'une question externe — "
                "la version qu'il possède peut différer de l'œuvre originale ; "
                "→ '.../news/request' UNIQUEMENT pour les actualités et événements des derniers jours ; "
                "→ '.../search_preference' UNIQUEMENT pour les données personnelles de l'utilisateur ; "
                "→ '.../search/request' (SearXNG) SEULEMENT EN DERNIER RECOURS : utilise ta réflexion pour "
                "d'abord écarter explicitement chaque outil spécialisé disponible, et n'y a recours que si tu "
                "es certain qu'aucun ne peut raisonnablement s'appliquer à cette question. "
                "Cette règle s'applique aussi aux demandes de recommandation, suggestion ou choix parmi un "
                "contenu personnel réel ('propose-moi un conte', 'qu'est-ce que tu me conseilles') dès qu'un "
                "outil donne accès à ce catalogue : ne propose JAMAIS un titre de mémoire, même un titre déjà "
                "mentionné plus tôt dans la conversation ou qui te semble plausible — interroge D'ABORD l'outil "
                "pour ne suggérer que ce qui existe réellement dans le catalogue de l'utilisateur. "
                "STRICT : après avoir reçu le résultat d'un outil, base-toi UNIQUEMENT sur ce résultat. "
                "Si le résultat est vide ou insuffisant, dis-le explicitement — n'invente jamais. "
                "Un résultat d'outil obtenu pour une question précédente NE COUVRE PAS une nouvelle question, "
                "même sur un sujet proche ou déjà mentionné dans la conversation : par exemple, le flash "
                "d'actualités général du jour ne répond PAS à une question précise sur un événement particulier "
                "— refais TOUJOURS un appel d'outil ciblé pour chaque nouvelle question factuelle plutôt que de "
                "déduire une réponse à partir d'un résultat obtenu pour autre chose."
            )
            common_rules.append("Outils disponibles :")
            for tool_def in tools_definitions:
                func = tool_def['function']
                common_rules.append(f"- {func['name']}: {func['description']}")
        else:
            common_rules.append(
                "RÈGLE ABSOLUE : tu n'as ACTUELLEMENT accès à aucun outil externe (catalogue personnel, "
                "actualités, données personnelles...), probablement une interruption technique temporaire. "
                "Pour TOUTE question factuelle, de recommandation, ou portant sur un contenu personnel de "
                "l'utilisateur (contes, préférences, actualités...), NE FABRIQUE JAMAIS de réponse comme si "
                "tu avais accès à ces données — dis explicitement que tes outils sont temporairement "
                "indisponibles et propose de réessayer dans un instant. Ne réponds depuis ta mémoire "
                "générale que pour des questions génériques ne nécessitant clairement aucune donnée "
                "personnelle ou actuelle."
            )

        parts.append("\n" + "\n".join(common_rules))

        enhanced_prompt = "\n".join(parts)
        logger.info(f"Prompt enrichi généré: {enhanced_prompt[:100]}...")
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
            self.conversation_history.append({
                "role": "assistant",
                "content": assistant_response,
                "tool_calls": tool_calls,
            })

            logger.info(f"🛠️ Processing {len(tool_calls)} tool calls")

            external_calls = []
            for tool_call in tool_calls:
                tool_name = tool_call["function"]["name"]
                try:
                    parameters = json.loads(tool_call["function"]["arguments"])
                except json.JSONDecodeError as e:
                    logger.error(f"Erreur parsing arguments tool call: {e}")
                    from messages.tool_message import ToolResponseMessage
                    self._handle_tool_response(ToolResponseMessage(
                        tool_call_id=tool_call["id"],
                        tool_name=tool_name,
                        result=None,
                        error=f"Arguments invalides: {e}",
                    ))
                    continue

                if tool_name == "write_topic":
                    topic = parameters.get("topic", "")
                    # Les champs du payload sont des paramètres de premier niveau de
                    # l'appel (voir mqtt_step._send_write_tool_update) — pas un objet
                    # imbriqué que le modèle doit construire lui-même. On reconstitue le
                    # payload ici à partir de tout ce qui n'est pas 'topic'.
                    payload = {k: v for k, v in parameters.items() if k != "topic"}
                    # Filet de sécurité : si le modèle imbrique quand même par habitude
                    # ({"payload": {...}} comme unique champ), on le déballe plutôt que
                    # de l'envoyer tel quel à l'agent cible.
                    if set(payload.keys()) == {"payload"} and isinstance(payload["payload"], dict):
                        logger.warning(f"write_topic: payload imbriqué malgré le nouveau schéma à plat, déballage: {payload}")
                        payload = payload["payload"]
                    response_topic = self._topic_response_map.get(topic)
                    if self.output_queue:
                        self.output_queue.enqueue(MqttWriteMessage(topic=topic, payload=payload))
                    if response_topic:
                        # Defer: inject the real response when it arrives on response_topic
                        self._pending_tool_responses[response_topic] = tool_call["id"]
                        self._my_tool_call_ids.add(tool_call["id"])
                        logger.info(f"📤 write_topic → {topic} (deferred, awaiting {response_topic})")
                    else:
                        self.conversation_history.append({
                            "role": "tool",
                            "content": json.dumps({"status": "sent"}),
                            "tool_call_id": tool_call["id"],
                        })
                        logger.info(f"📤 write_topic → {topic} (fire-and-forget)")
                else:
                    from messages.tool_message import ToolCallMessage
                    if self.output_queue:
                        self.output_queue.enqueue(ToolCallMessage(
                            tool_name=tool_name,
                            tool_call_id=tool_call["id"],
                            parameters=parameters,
                        ))
                        logger.info(f"📤 Tool call sent: {tool_name}")
                    external_calls.append(tool_call)

            # Continue only if all write_topic calls are fire-and-forget (no deferred)
            # and there are no external tool calls waiting for responses.
            has_deferred = any(
                self._topic_response_map.get(
                    json.loads(tc["function"]["arguments"]).get("topic", "")
                )
                for tc in tool_calls
                if tc["function"]["name"] == "write_topic"
            )
            if not external_calls and not has_deferred:
                messages = self._prepare_messages()
                self._call_openai_streaming(messages)

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
            self.client_tools = None  # Nettoyer les outils clients
            self.client_prompts = None  # Nettoyer les prompts enrichis

        print(f"OpenAI Chat {self.name} nettoyé")