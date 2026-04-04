import time
import threading
import logging
from typing import Dict, Set, Optional
from pipeline_framework import PipelineStep
from messages.websocket_message import UserConnectionMessage
from messages.tool_message import ToolRegistrationMessage
from messages.chat_message import ToolsReadyMessage

logger = logging.getLogger(__name__)


class ToolRegistryStep(PipelineStep):
    """Registry centralisé qui coordonne l'enregistrement des outils"""
    
    def __init__(self, name: str = "ToolRegistry", config: Optional[Dict] = None):
        super().__init__(name, config, handler=self._handle_message)
        
        # Configuration
        self.expected_tools = set(config.get("expected_tools", []) if config else [])
        self.registration_timeout = config.get("registration_timeout", 5.0) if config else 5.0
        self.llm_step_name = config.get("llm_step_name", "chat_step") if config else "chat_step"
        
        # État par client
        self.pending_registrations = {}  # {client_id: {expected_tools, received_tools, registered_tools, timeout_timer}}
        
        # Thread safety
        self._lock = threading.Lock()
        
        logger.info(f"🏗️ ToolRegistry initialized - expecting tools: {self.expected_tools}")
    
    def init(self) -> bool:
        return True
    
    def _handle_message(self, message):
        """Gère les messages entrants"""
        # Validation des types de messages autorisés
        allowed_classes = (UserConnectionMessage, ToolRegistrationMessage)
        if not isinstance(message, allowed_classes):
            return
            
        try:
            # Vérifier si c'est un message de connexion utilisateur
            if isinstance(message, UserConnectionMessage):
                self._handle_user_connection(message)
                return
            
            # Vérifier si c'est un message d'enregistrement d'outil
            if isinstance(message, ToolRegistrationMessage):
                self._handle_tool_registration(message)
                
        except Exception as e:
            logger.error(f"Erreur dans _handle_message: {e}")
    
    def _handle_user_connection(self, connection_message):
        """Démarre le processus d'enregistrement pour un nouveau client"""
        try:
            # Utiliser les propriétés directes de UserConnectionMessage (architecture dataclass pure)
            username = connection_message.username
            client_id = connection_message.client_id
            
            logger.info(f"🔌 User {username} connected, starting tool registration process")
            
            with self._lock:
                # Initialiser l'état de registration pour ce client
                self.pending_registrations[client_id] = {
                    "username": username,
                    "expected_tools": self.expected_tools.copy(),
                    "received_tools": set(),
                    "registered_tools": [],
                    "start_time": time.time()
                }
                
                # Démarrer le timeout
                if self.registration_timeout > 0:
                    self._start_timeout_for_client(client_id)
            
            # Transmettre la notification de connexion aux outils (via output_queue)
            if self.output_queue:
                self.output_queue.enqueue(connection_message)
                logger.info(f"📡 User connection forwarded to tools")
                
        except Exception as e:
            logger.error(f"Erreur lors de la gestion de connexion utilisateur: {e}")
    
    def _handle_tool_registration(self, registration_message):
        """Traite l'enregistrement d'un outil"""
        try:
            # Utiliser les propriétés directes de ToolRegistrationMessage (architecture dataclass pure)
            tool_definition = registration_message.tool_definition
            source_step = registration_message.source_step
            target_client_id = None  # Plus de metadata dans architecture pure
            tool_name = tool_definition['function']['name'] if tool_definition else 'unknown'
            
            if not target_client_id:
                logger.warning("⚠️ Tool registration without target_client_id")
                return
            
            with self._lock:
                if target_client_id not in self.pending_registrations:
                    logger.warning(f"⚠️ Tool registration for unknown/expired client: {target_client_id}")
                    return
                
                client_info = self.pending_registrations[target_client_id]
                
                # Marquer cet outil comme reçu
                client_info["received_tools"].add(source_step)
                client_info["registered_tools"].append(registration_message)
                
                logger.info(f"🔧 Tool '{tool_name}' registered for client {target_client_id} ({len(client_info['received_tools'])}/{len(self.expected_tools)})")
                
                # Vérifier si tous les outils ont répondu
                if client_info["received_tools"] >= self.expected_tools:
                    self._complete_registration(target_client_id)
                    
        except Exception as e:
            logger.error(f"Erreur lors de l'enregistrement d'outil: {e}")
    
    def _start_timeout_for_client(self, client_id: str):
        """Démarre le timeout pour un client"""
        def timeout_callback():
            with self._lock:
                if client_id in self.pending_registrations:
                    logger.info(f"⏰ Tool registration timeout for client {client_id}")
                    self._complete_registration(client_id, timed_out=True)
        
        timer = threading.Timer(self.registration_timeout, timeout_callback)
        timer.start()
        
        # Stocker le timer dans les infos du client
        if client_id in self.pending_registrations:
            self.pending_registrations[client_id]["timeout_timer"] = timer
    
    def _complete_registration(self, client_id: str, timed_out: bool = False):
        """Finalise l'enregistrement d'un client"""
        try:
            if client_id not in self.pending_registrations:
                return
            
            client_info = self.pending_registrations[client_id]
            
            # Annuler le timeout si pas déjà expiré
            if not timed_out and "timeout_timer" in client_info:
                client_info["timeout_timer"].cancel()
            
            registered_tools = client_info["registered_tools"]
            username = client_info["username"]
            
            if timed_out:
                missing_tools = self.expected_tools - client_info["received_tools"]
                logger.warning(f"⏰ Registration timeout for {username}: missing tools {missing_tools}")
            else:
                logger.info(f"✅ All tools registered for {username}")
            
            # Envoyer le message de tools_ready au LLM
            tools_definitions = {}
            for tool in registered_tools:
                tool_def = tool.tool_definition
                if tool_def and 'function' in tool_def:
                    tools_definitions[tool_def['function']['name']] = tool_def
            
            tools_ready_message = ToolsReadyMessage(
                tools_definitions=tools_definitions
            )
            
            if self.output_queue:
                self.output_queue.enqueue(tools_ready_message)
                logger.info(f"📤 Tools ready message sent for {username}")
            
            # Nettoyer l'état
            del self.pending_registrations[client_id]
            
        except Exception as e:
            logger.error(f"Erreur lors de la finalisation d'enregistrement: {e}")
    
    def cleanup(self):
        """Nettoie les timeouts en cours"""
        logger.info(f"🧹 Nettoyage de ToolRegistry {self.name}")
        
        with self._lock:
            for client_info in self.pending_registrations.values():
                if "timeout_timer" in client_info:
                    client_info["timeout_timer"].cancel()
            self.pending_registrations.clear()
        
        if hasattr(self, 'input_queue') and self.input_queue:
            self.input_queue.stop()