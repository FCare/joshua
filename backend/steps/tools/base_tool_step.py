import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from pipeline_framework import PipelineStep
from messages.base_message import ToolCallMessage, ToolResponseMessage, ToolRegistrationMessage

logger = logging.getLogger(__name__)


class BaseToolStep(PipelineStep, ABC):
    """Classe de base pour tous les outils"""
    
    def __init__(self, name: str, config: Optional[Dict] = None):
        super().__init__(name, config, handler=self._handle_messages)
        self.tool_definition = self._create_tool_definition()
        self.user_permissions = config.get("user_permissions", {}) if config else {}
        
        print(f"🔧 Tool '{self.name}' initialized with definition: {self.tool_definition['function']['name']}")
    
    @abstractmethod
    def _create_tool_definition(self) -> Dict[str, Any]:
        """Retourne la définition OpenAI de l'outil"""
        pass
    
    @abstractmethod
    def _execute_tool(self, parameters: Dict[str, Any]) -> Any:
        """Exécute l'outil avec les paramètres donnés"""
        pass
    
    def init(self) -> bool:
        """Initialise l'outil"""
        return True
    
    def _handle_messages(self, message):
        """Gère les messages entrants (tool calls et connexions)"""
        try:
            # Vérifier si c'est un message de connexion utilisateur (depuis WebSocketStep)
            if hasattr(message, 'data') and isinstance(message.data, dict):
                message_type = message.data.get('type')
                if message_type == 'user_connected':
                    self._handle_user_connection(message)
                    return
            
            # Vérifier si c'est un message avec metadata
            if hasattr(message, 'metadata') and message.metadata:
                message_type = message.metadata.get('message_type')
                if message_type == 'user_connection':
                    self._handle_user_connection(message)
                    return
            
            # Vérifier si c'est un appel d'outil
            if isinstance(message, ToolCallMessage):
                self._handle_tool_call(message)
                
        except Exception as e:
            logger.error(f"Erreur dans _handle_messages pour {self.name}: {e}")
    
    def _handle_user_connection(self, connection_message):
        """Traite les nouvelles connexions d'utilisateurs"""
        try:
            # Extraire les données selon le format du message
            if hasattr(connection_message, 'data') and isinstance(connection_message.data, dict):
                # Format WebSocketStep: data contient les infos de connexion
                username = connection_message.data.get('username')
                client_id = connection_message.data.get('client_id')
            else:
                # Format metadata classique
                username = connection_message.metadata.get('username') if connection_message.metadata else None
                client_id = connection_message.metadata.get('client_id') if connection_message.metadata else None
            
            logger.info(f"🔌 Tool '{self.name}': nouvelle connexion utilisateur {username}")
            
            # Vérifier si cet utilisateur a accès à cet outil
            if self._user_has_access(username):
                # Envoyer la déclaration d'outil
                registration_message = ToolRegistrationMessage(
                    tool_definition=self.tool_definition,
                    source_step=self.name,
                    metadata={
                        "target_client_id": client_id,
                        "target_username": username
                    }
                )
                
                if self.output_queue:
                    self.output_queue.enqueue(registration_message)
                    logger.info(f"🔧 Tool '{self.name}' registered for user {username}")
            else:
                logger.info(f"🚫 Tool '{self.name}' not available for user {username}")
                
        except Exception as e:
            logger.error(f"Erreur lors de la gestion de connexion utilisateur pour {self.name}: {e}")
    
    def _user_has_access(self, username: str) -> bool:
        """Vérifie si l'utilisateur a accès à cet outil"""
        # Si aucune restriction, accessible à tous
        if not self.user_permissions:
            return True
        
        # Vérifier les permissions spécifiques
        allowed_users = self.user_permissions.get("allowed_users", [])
        denied_users = self.user_permissions.get("denied_users", [])
        
        if denied_users and username in denied_users:
            return False
        
        if allowed_users and username not in allowed_users:
            return False
        
        return True
    
    def _handle_tool_call(self, message):
        """Gère les appels d'outils"""
        if not isinstance(message, ToolCallMessage):
            return
        
        # Vérifier si cet outil est concerné
        if message.tool_name != self.tool_definition["function"]["name"]:
            return
        
        logger.info(f"🛠️ Tool '{self.name}' processing call: {message.tool_call_id}")
        
        try:
            # Exécuter l'outil
            result = self._execute_tool(message.parameters)
            
            # Créer le message de réponse
            response = ToolResponseMessage(
                tool_call_id=message.tool_call_id,
                tool_name=message.tool_name,
                result=result,
                metadata={"source_step": self.name}
            )
            
            # Envoyer la réponse
            if self.output_queue:
                self.output_queue.enqueue(response)
                logger.info(f"✅ Tool '{self.name}' response sent for call {message.tool_call_id}")
                
        except Exception as e:
            logger.error(f"Erreur lors de l'exécution de l'outil {self.name}: {e}")
            # Envoyer une erreur
            error_response = ToolResponseMessage(
                tool_call_id=message.tool_call_id,
                tool_name=message.tool_name,
                result=None,
                error=str(e),
                metadata={"source_step": self.name}
            )
            
            if self.output_queue:
                self.output_queue.enqueue(error_response)
    
    def cleanup(self):
        """Nettoie les ressources de l'outil"""
        print(f"🧹 Nettoyage de l'outil {self.name}")