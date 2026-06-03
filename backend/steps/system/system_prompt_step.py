import logging
from typing import Optional, Dict
from datetime import datetime
from zoneinfo import ZoneInfo

from pipeline_framework import PipelineStep
from messages.base_message import BaseMessage

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
        prompt_config = config.get("prompt", "") if config else ""
        # Support pour array de strings OU string simple
        if isinstance(prompt_config, list):
            self.prompt = " ".join(prompt_config)  # Joint avec des espaces
        else:
            self.prompt = prompt_config
        
        logger.info(f"SystemPromptStep '{self.name}' configuré")
    
    def _handle_input_event(self, input_message):
        """Handler vide - ce step ignore volontairement tous les messages entrants"""
        logger.info(f"📝 SystemPrompt: Message ignoré volontairement: {type(input_message).__name__}")
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
            # Utiliser le template configuré + date/heure courante
            now = datetime.now(ZoneInfo("Europe/Paris"))
            date_str = now.strftime("%-d %B %Y").replace(
                "January", "janvier").replace("February", "février").replace(
                "March", "mars").replace("April", "avril").replace(
                "May", "mai").replace("June", "juin").replace(
                "July", "juillet").replace("August", "août").replace(
                "September", "septembre").replace("October", "octobre").replace(
                "November", "novembre").replace("December", "décembre")
            time_str = now.strftime("%Hh%M")
            datetime_info = f"Nous sommes le {date_str}, il est {time_str}."
            base_prompt = self.prompt or "Tu es un assistant virtuel intelligent et bienveillant."
            system_prompt = f"{datetime_info} {base_prompt}"
            
            # Créer le message de mise à jour
            from messages.chat_message import SystemPromptMessage
            prompt_message = SystemPromptMessage(
                prompt=system_prompt
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