import time
import logging
from typing import Optional, Dict
from datetime import datetime

from pipeline_framework import PipelineStep
from messages.base_message import Message, OutputMessage, MessageType

logger = logging.getLogger(__name__)


class SystemPromptStep(PipelineStep):
    """
    Step qui génère et envoie des system prompts au chat.
    
    Ce step n'a pas d'input_queue - il génère des prompts de manière autonome
    et les envoie vers le chat via son output_queue.
    """
    
    def __init__(self, name: str, config: Optional[Dict] = None):
        # Handler vide - ce step a une input_queue mais n'y réagit pas
        super().__init__(name, config, handler=self._handle_input_event)
        
        # Configuration simple : juste le template
        self.prompt_template = config.get("prompt_template", "") if config else ""
        
        logger.info(f"SystemPromptStep '{self.name}' configuré")
    
    def _handle_input_event(self, input_message):
        """Handler vide - ce step ignore les messages entrants"""
        pass
    
    def init(self) -> bool:
        """Initialise le step et génère le premier system prompt"""
        try:
            # Envoyer le premier system prompt immédiatement
            self._generate_and_send_system_prompt()
            return True
            
        except Exception as e:
            logger.error(f"Erreur initialisation SystemPromptStep: {e}")
            return False
    
    def _generate_and_send_system_prompt(self):
        """Génère et envoie un system prompt au chat"""
        try:
            # Utiliser le template configuré
            system_prompt = self.prompt_template or "Tu es un assistant virtuel intelligent et bienveillant."
            
            # Créer le message de mise à jour
            prompt_message = OutputMessage(
                result=system_prompt,
                metadata={
                    "type": "system_prompt_update",
                    "source": self.name
                }
            )
            
            # Envoyer vers l'output_queue (qui sera connectée à l'input_queue du chat)
            if self.output_queue:
                self.output_queue.enqueue(prompt_message)
                logger.info(f"System prompt envoyé: {system_prompt[:100]}...")
            else:
                logger.warning("Pas d'output_queue configurée pour SystemPromptStep")
            
        except Exception as e:
            logger.error(f"Erreur génération system prompt: {e}")
    
    def cleanup(self):
        """Nettoyage des ressources"""
        logger.info(f"SystemPromptStep '{self.name}' nettoyé")